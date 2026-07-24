"""编排 memory dictionary 的派生、校验、发布与状态收敛。"""

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
    """从 active notes 派生完整 L0/L1，不写文件或 state。"""

    return render.derive_dictionary_full(root, provider)


def rebuild_dictionary(
    root: Path | str,
    *,
    apply: bool,
    provider: LLMProvider,
) -> dict[str, Any]:
    """全量派生；apply=False 只预览，apply=True 原子发布。"""

    root_path = Path(root)
    if apply:
        # 提前标 stale，使进程在派生或发布中崩溃时仍可由下次任务自愈。
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
        except Exception as exc:  # noqa: BLE001 - IO 失败需要返回给调用方重试。
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
    """索引漂移时在锁外重新派生，并在短独占锁内发布。"""

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
        except Exception as exc:  # noqa: BLE001 - 发布失败允许重试。
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
    """没有 provider 时只检查漂移并标 stale，不触发重建。"""

    from trowel_py.memory.dictionary_check import check_dictionary

    root_path = Path(root)
    try:
        report = check_dictionary(root_path)
    except Exception as exc:  # noqa: BLE001 -- 检查失败不能阻塞调用者
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
    # provider 调用必须留在锁外，避免慢请求阻塞检索和一致性检查。
    try:
        result = derive_dictionary_full(root, provider)
    except Exception as exc:  # noqa: BLE001 - provider 或网络失败允许重试。
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
    # state 只负责可观察性；写入失败不能覆盖原始派生/发布错误。
    try:
        previous = load_state(root)
        save_state(root, previous.with_failure(reason, _now_iso()))
    except Exception:  # noqa: BLE001 - 可观察性写入仅作尽力尝试。
        logger.warning(
            "could not stamp dictionary stale: %s",
            reason,
            exc_info=True,
        )


def _stamp_success_state(
    root: Path,
    staged: dict[str, Any],
) -> None:
    # 索引已经正确发布时，state 失败最多导致一次冗余重建，不能回滚索引。
    try:
        save_state(
            root,
            DictionaryState().with_success(
                staged["source_hash"],
                staged["rendered_hash"],
                _now_iso(),
            ),
        )
    except Exception as exc:  # noqa: BLE001 - state 标记仅作尽力尝试。
        logger.warning("dictionary state stamp failed (best-effort): %s", exc)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
