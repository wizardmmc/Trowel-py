"""编排 Dictionary 的派生、校验、发布和一致性状态更新。"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trowel_py.llm.client import LLMProvider
from trowel_py.memory.dictionary_check import (
    _check_dictionary_locked,
    _evaluate,
    compute_rendered_hash,
    compute_source_hash,
    derive_active_corpus,
)
from trowel_py.memory.dictionary_index import publish, render
from trowel_py.memory.dictionary_lock import dictionary_lock
from trowel_py.memory.dictionary_state import (
    DictionaryState,
    load_state,
    save_state,
)

logger = logging.getLogger(__name__)


def derive_dictionary_full(
    root: Path | str,
    provider: LLMProvider,
) -> dict[str, Any]:
    """从 active Note 派生完整 L0/L1，不写索引或状态文件。

    本函数不捕获读取 active Note 或调用 provider 时的异常，调用方负责处理。

    Args:
        root: active Note 所在的 Memory 根目录。
        provider: 对 Note 进行领域聚类的模型客户端。

    Returns:
        包含 L0 文本、各领域 L1 文本和领域结构的映射。
    """

    return render.derive_dictionary_full(root, provider)


def rebuild_dictionary(
    root: Path | str,
    *,
    apply: bool,
    provider: LLMProvider,
) -> dict[str, Any]:
    """全量派生并校验 Dictionary，可选择原子发布。

    ``apply=False`` 只返回预览，不写索引或状态。``apply=True`` 发布前尽力把
    状态标为 stale。``derive_dictionary_full`` 抛出异常、暂存报告不一致或
    原子替换失败时，函数返回带 ``error`` 的结果并保留旧索引；重新读取
    active Note、评估暂存结果或获取文件锁时的异常会向上传播。

    Args:
        root: Note、Dictionary 和状态文件所在的 Memory 根目录。
        apply: 是否发布通过校验的新索引。
        provider: 对 active Note 进行领域聚类的模型客户端。

    Returns:
        预览、发布结果或失败信息。成功结果包含领域数和 L1 领域名；发布成功时
        还包含来源与渲染摘要。
    """

    root_path = Path(root)
    if apply:
        # 发布前先尽力标为 stale；若进程中途退出，后续维护可据此重试。
        _mark_stale(root_path, "rebuild in progress")

    staged = _derive_and_stage(root_path, provider, apply)
    if "error" in staged:
        if apply:
            _mark_stale(root_path, staged.get("reason", staged["error"]))
        logger.warning("dictionary rebuild did not publish: %s", staged["error"])
        return staged
    if not apply:
        return {
            "apply": False,
            "L0": staged["l0_text"],
            "L1_keys": list(staged["l1_files"].keys()),
            "domain_count": staged["domain_count"],
            "check": staged["check"],
        }

    with dictionary_lock(root, exclusive=True):
        try:
            publish.atomic_replace(
                root_path,
                staged["l0_text"],
                staged["l1_files"],
            )
        except Exception as exc:  # noqa: BLE001 - 原子替换的任意异常都转换为失败结果。
            _mark_stale(root_path, f"replace failed: {exc}")
            logger.warning("dictionary replace failed (old index kept): %s", exc)
            return {
                "apply": True,
                "error": "replace_failed",
                "reason": str(exc),
            }
        _stamp_success_state(root_path, staged)

    return {
        "apply": True,
        "domain_count": staged["domain_count"],
        "L1_keys": list(staged["l1_files"].keys()),
        "source_hash": staged["source_hash"],
        "rendered_hash": staged["rendered_hash"],
        "check": staged["check"],
    }


def ensure_dictionary_consistent(
    root: Path | str,
    provider: LLMProvider,
) -> dict[str, Any]:
    """检查 Dictionary，并在不一致时重新派生和发布。

    一致时不调用 provider。初检和发布分别持有锁，慢派生与暂存校验在两次
    加锁之间执行。派生调用失败、暂存报告不一致或原子替换失败时保留旧索引
    并返回 stale；状态写入失败不回滚已发布的索引，发布后复检失败也可能在
    新索引已经落盘后返回 stale。初检、暂存语料读取或评估以及锁操作抛出的
    异常会向上传播。

    Args:
        root: Note、Dictionary 和状态文件所在的 Memory 根目录。
        provider: 重建 Dictionary 使用的模型客户端。

    Returns:
        一致性状态、是否重建，以及重建前后的检查或失败信息。
    """

    root_path = Path(root)
    with dictionary_lock(root, exclusive=True):
        before = _check_dictionary_locked(root)
        if before["status"] == "consistent":
            return {
                "dictionary_status": "consistent",
                "rebuilt": False,
                "check": before,
            }

    _mark_stale(root_path, "rebuild in progress")
    staged = _derive_and_stage(root_path, provider, apply=True)
    if "error" in staged:
        _mark_stale(root_path, staged.get("reason", staged["error"]))
        logger.warning("dictionary rebuild did not publish: %s", staged["error"])
        return {
            "dictionary_status": "stale",
            "rebuild": staged,
            "check_before": before,
        }

    with dictionary_lock(root, exclusive=True):
        try:
            publish.atomic_replace(
                root_path,
                staged["l0_text"],
                staged["l1_files"],
            )
            _stamp_success_state(root_path, staged)
            after = _check_dictionary_locked(root)
        except Exception as exc:  # noqa: BLE001 - 原子替换或发布后复检异常都转换为 stale 结果。
            _mark_stale(root_path, f"publish failed: {exc}")
            logger.warning("dictionary publish failed: %s", exc)
            return {
                "dictionary_status": "stale",
                "rebuild": {
                    "error": "publish_failed",
                    "reason": str(exc),
                },
                "check_before": before,
            }

    return {
        "dictionary_status": after["status"],
        "rebuilt": True,
        "check_before": before,
        "check_after": after,
        "rebuild": {
            "apply": True,
            "source_hash": staged["source_hash"],
            "rendered_hash": staged["rendered_hash"],
        },
    }


def mark_dictionary_stale_if_drifted(
    root: Path | str,
) -> dict[str, Any]:
    """在没有 provider 时检查 Dictionary 漂移，但不重建。

    检查结果不为 consistent 时尽力把状态标为 stale。检查本身抛出异常时只
    记录告警，并返回 stale 和错误信息。

    Args:
        root: 要检查的 Memory 根目录。

    Returns:
        当前一致性状态、固定为 False 的 ``rebuilt`` 标记和检查报告；检查异常
        时改为返回错误信息。
    """

    from trowel_py.memory.dictionary_check import check_dictionary

    root_path = Path(root)
    try:
        report = check_dictionary(root_path)
    except Exception as exc:  # noqa: BLE001 - 检查异常不能阻塞调用方。
        logger.warning("dictionary check raised: %s", exc)
        return {"dictionary_status": "stale", "error": str(exc)}
    if report["status"] != "consistent":
        _mark_stale(
            root_path,
            f"drift detected ({report['status']}); no provider to rebuild",
        )
    return {
        "dictionary_status": report["status"],
        "rebuilt": False,
        "check": report,
    }


def _derive_and_stage(
    root: Path,
    provider: LLMProvider,
    apply: bool,
) -> dict[str, Any]:
    """供调用方在锁外派生索引，并检查结果是否可发布。

    本函数只把 ``derive_dictionary_full`` 抛出的异常和不一致的暂存报告转换为
    失败结果；重新读取 active Note、评估暂存内容或计算摘要时的异常继续向上
    传播。

    Args:
        root: active Note 所在的 Memory 根目录。
        provider: 对 Note 进行领域聚类的模型客户端。
        apply: 原样写入失败结果的模式标记；不决定是否写盘，本函数始终不写盘。

    Returns:
        通过校验的 L0/L1、摘要和检查报告；派生调用抛出异常或暂存报告不一致
        时返回带 ``error`` 的失败信息。
    """
    # provider 调用必须留在锁外，避免慢请求阻塞检索和一致性检查。
    try:
        result = derive_dictionary_full(root, provider)
    except Exception as exc:  # noqa: BLE001 - 派生调用的任意异常都转换为可重试结果。
        return {
            "apply": apply,
            "error": "derive_failed",
            "reason": f"derive failed: {exc}",
        }

    l0_text = result["L0"]
    l1_files = result["L1"]
    corpus = derive_active_corpus(root)
    staging_report = _evaluate(
        corpus,
        l0_text,
        l1_files,
        state_hash=None,
        baseline_required=False,
    )
    if staging_report["status"] != "consistent":
        # 已知不一致的渲染结果不能进入 live 索引。
        return {
            "apply": apply,
            "error": "staging_inconsistent",
            "reason": "staging check rejected the rendered index",
            "check": staging_report,
        }
    return {
        "l0_text": l0_text,
        "l1_files": l1_files,
        "source_hash": compute_source_hash(corpus),
        "rendered_hash": compute_rendered_hash(l0_text, l1_files),
        "domain_count": len(result["domains"]),
        "check": staging_report,
    }


def _mark_stale(root: Path, reason: str) -> None:
    """尽力将 Dictionary 状态标为 stale，且不清除上次成功构建的摘要和时间。

    Args:
        root: Dictionary 状态文件所在的 Memory 根目录。
        reason: 要记录到状态文件的失败或漂移原因。
    """
    try:
        previous = load_state(root)
        save_state(root, previous.with_failure(reason, _now_iso()))
    except Exception:  # noqa: BLE001 - 状态写入失败不能阻塞 Dictionary 检查或重建。
        logger.warning(
            "could not stamp dictionary stale: %s",
            reason,
            exc_info=True,
        )


def _stamp_success_state(
    root: Path,
    staged: dict[str, Any],
) -> None:
    """在索引发布成功后尽力记录来源和渲染摘要。

    Args:
        root: Dictionary 状态文件所在的 Memory 根目录。
        staged: 已发布索引的暂存结果，须包含来源与渲染摘要。
    """
    # 索引发布已完成；状态写入失败不回滚索引，但可能让后续检查再次触发重建。
    try:
        save_state(
            root,
            DictionaryState().with_success(
                staged["source_hash"],
                staged["rendered_hash"],
                _now_iso(),
            ),
        )
    except Exception as exc:  # noqa: BLE001 - 状态写入失败不回滚已发布索引。
        logger.warning("dictionary state stamp failed (best-effort): %s", exc)


def _now_iso() -> str:
    """返回精确到秒的当前 UTC ISO 8601 时间。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
