"""实现 Memory MCP 的搜索、正文读取和读取反馈。

处理器不接触 MCP 请求对象，只接收已经拆出的业务参数、存储对象和依赖回调并
返回字典；``trowel_py.memory.mcp_server`` 负责协议包装和异常响应。搜索会
写入查询及命中日志，读取会更新 Note 引用并写入访问日志，反馈则通过
``read_id`` 关联先前的读取记录。时间、日志和 URI 等依赖通过回调传入，使稳定
入口中已有的 monkeypatch 替换仍然生效。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from trowel_py.memory.access_log import (
    AccessRecord,
    Outcome,
    OutcomeRecord,
    log_access,
    log_outcome,
    read_access_log,
)
from trowel_py.memory.store import MemoryStore
from trowel_py.memory.types import Note

_TOOL_SEARCH = "search"
_TOOL_READ = "read"
_TOOL_OUTCOME = "outcome"
_DICT_L0 = "dictionary-L0.md"
_REQUIRES_READ_KINDS = {"gotcha", "procedure", "hypothesis"}
_URI_PREFIX = "memory://notes/"


def parse_memory_uri(uri: str) -> str:
    """解析 ``memory://notes/`` URI 并返回原样的 Note 文件 stem。

    函数不做 URL 解码。前缀后的内容不能为空，不能包含 ``/`` 或 ``..``，也
    不能以 ``.`` 开头；其他字符保持不变。

    Args:
        uri: 要解析的 Memory Note URI。

    Returns:
        URI 前缀后的 Note 文件 stem。

    Raises:
        ValueError: URI 前缀不匹配，或文件 stem 不符合上述限制。
    """
    if not uri.startswith(_URI_PREFIX):
        raise ValueError(f"not a memory URI: {uri!r}")
    note_id = uri[len(_URI_PREFIX) :]
    if not note_id or "/" in note_id or ".." in note_id or note_id.startswith("."):
        raise ValueError(f"illegal note id: {note_id!r}")
    return note_id


def requires_read(note: Note) -> bool:
    """判断搜索候选是否必须读取正文后才能使用。

    ``gotcha``、``procedure``、``hypothesis`` 三类 Note，以及验证状态为
    ``inferred-untested`` 的任何 Note 均需要读取；活动状态不参与判断。

    Args:
        note: 要判断的搜索候选。

    Returns:
        调用方必须先读取正文时为 True。
    """
    return note.kind in _REQUIRES_READ_KINDS or note.verification == "inferred-untested"


def _now() -> str:
    """返回精确到秒的当前 UTC ISO 8601 时间。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today() -> str:
    """返回当前 UTC 日期的 ISO 8601 字符串。"""
    return datetime.now(timezone.utc).date().isoformat()


def _hit(
    note_id: str,
    note: Note,
    rank: int,
    *,
    requires_read_fn: Any = requires_read,
) -> dict[str, Any]:
    """把一条 Note 转换为 MCP 搜索候选。

    分数固定为 ``1 / (rank + 1)``，其中 ``rank`` 是检索器原始候选中的零起始
    位置，不会因后续过滤而重新编号。

    Args:
        note_id: Note 文件 stem，也是结果中的 ``memory_id`` 和 URI 尾部。
        note: 提供标题、摘要及状态字段的 Note。
        rank: 检索器给出的零起始候选位置。
        requires_read_fn: 计算 ``requires_read`` 标志的回调。

    Returns:
        MCP 搜索结果使用的固定字段字典。
    """
    return {
        "memory_id": note_id,
        "title": note.title,
        "summary": note.summary,
        "uri": f"memory://notes/{note_id}",
        "score": 1.0 / (rank + 1),
        "kind": note.kind,
        "verification": note.verification,
        "status": note.status,
        "requires_read": requires_read_fn(note),
    }


def handle_search(
    query: str,
    top_k: int,
    include_inactive: bool,
    store: MemoryStore,
    dictionary_path: Path,
    identity: dict[str, str],
    toolUseId: str = "",
    retriever: Any = None,
    *,
    now_fn: Any = _now,
    hit_fn: Any = _hit,
    log_access_fn: Any = log_access,
) -> dict[str, Any]:
    """通过两层 Dictionary 检索 Note，并记录查询和返回的命中。

    每次调用先分配 ``search_id`` 并记录查询，即使根索引不存在也会留下该记录。
    根索引存在时，函数在 Dictionary 共享锁内读取一致性状态并运行检索器。它先
    截取检索器返回的前 ``top_k`` 个 stem，再跳过不存在的 Note；当
    ``include_inactive`` 为 False 时也跳过非活动 Note。因此不会用后续候选
    补位，命中的 rank 也保留原始位置。每条实际返回的 Note 另写一条命中记录。

    除 L0 缺失这一正常错误返回外，日志、锁、状态读取、检索、Note 读取和结果
    转换异常均直接传播。

    Args:
        query: 传给检索器并写入查询日志的搜索文本。
        top_k: 过滤前保留的候选数量；函数不校验其范围。
        include_inactive: 是否允许返回状态不是 ``active`` 的 Note。
        store: 提供 Memory 根目录和 Note 读取能力的存储。
        dictionary_path: 两层 Dictionary 的 L0 根索引路径。
        identity: 写入日志的会话身份，必须包含 Trowel、旧版 CC、运行端和原生
            会话 ID 四个键。
        toolUseId: 宿主提供的工具调用 ID；字段名沿用 Claude Code 协议。
        retriever: 返回 Note stem 序列的检索器；为 None 时从当前 LLM 配置构造
            ``LLMRetriever``。
        now_fn: 生成每条访问日志时间的回调。
        hit_fn: 把 Note 和原始 rank 转成结果字典的回调。
        log_access_fn: 追加查询及命中记录的回调。

    Returns:
        含 ``search_id`` 和结果列表的字典。L0 不存在时附带
        ``dictionary_empty`` 错误与重建提示；状态不是 ``consistent`` 时附带
        stale warning。
    """
    search_id = uuid.uuid4().hex
    log_access_fn(
        store.root,
        AccessRecord(
            ts=now_fn(),
            trowel_session_id=identity["trowel_session_id"],
            cc_session_id=identity["cc_session_id"],
            host_kind=identity["host_kind"],
            native_session_id=identity["native_session_id"],
            toolUseId=toolUseId,
            action="search",
            search_id=search_id,
            query=query,
        ),
    )
    if not dictionary_path.exists():
        return {
            "search_id": search_id,
            "results": [],
            "error": "dictionary_empty",
            "hint": "run: trowel memory dict-rebuild --apply",
        }
    if retriever is None:
        from trowel_py.config import load_llm_config
        from trowel_py.llm.client import AnthropicProvider
        from trowel_py.memory.retrievers import LLMRetriever

        retriever = LLMRetriever(AnthropicProvider(load_llm_config()))

    from trowel_py.memory.dictionary_lock import dictionary_lock
    from trowel_py.memory.dictionary_state import load_state

    # 状态读取和检索共用共享锁，避免发布换代时混读两代 Dictionary。
    with dictionary_lock(store.root, exclusive=False):
        stale_warning = (
            "dictionary index is stale; results may be incomplete. "
            "run: trowel memory dict-rebuild --apply"
            if load_state(store.root).status != "consistent"
            else None
        )
        stems = retriever(
            query,
            corpus_dir=str(store.root / "notes"),
            dictionary_path=dictionary_path,
        )

    hits: list[dict[str, Any]] = []
    for rank, stem in enumerate(stems[:top_k]):
        note = store.load_note(stem)
        if note is None:
            continue
        if not include_inactive and note.status != "active":
            continue
        hits.append(hit_fn(stem, note, rank))
        log_access_fn(
            store.root,
            AccessRecord(
                ts=now_fn(),
                trowel_session_id=identity["trowel_session_id"],
                cc_session_id=identity["cc_session_id"],
                host_kind=identity["host_kind"],
                native_session_id=identity["native_session_id"],
                toolUseId=toolUseId,
                action="search",
                search_id=search_id,
                memory_id=stem,
                rank=rank,
            ),
        )

    result: dict[str, Any] = {"search_id": search_id, "results": hits}
    if stale_warning:
        result["warning"] = stale_warning
    return result


def handle_read(
    uri: str,
    search_id: str,
    store: MemoryStore,
    identity: dict[str, str],
    toolUseId: str = "",
    *,
    parse_uri_fn: Any = parse_memory_uri,
    now_fn: Any = _now,
    today_fn: Any = _today,
    log_access_fn: Any = log_access,
) -> dict[str, Any]:
    """读取 Note 正文，更新引用计数并记录本次读取。

    URI 不合法，或 ``store.load_note()`` 因文件不存在或内容无效而返回空值时，
    函数返回错误字典，不更新引用或日志。成功路径先更新 ``refs`` 和
    ``last_ref``，再生成并写入访问记录；此后的任意异常均直接传播，且不会回滚
    已经完成的引用更新。

    Args:
        uri: 指向 Note 文件 stem 的 ``memory://notes/`` URI。
        search_id: 来源搜索的 ID；函数不校验它是否存在，空字符串也会原样记录。
        store: 提供 Note 读取及引用更新能力的存储。
        identity: 写入日志的会话身份，必须包含 Trowel、旧版 CC、运行端和原生
            会话 ID 四个键。
        toolUseId: 宿主提供的工具调用 ID；字段名沿用 Claude Code 协议。
        parse_uri_fn: 解析并校验 URI 的回调。
        now_fn: 生成访问日志时间的回调。
        today_fn: 生成 ``last_ref`` 日期的回调。
        log_access_fn: 追加正文读取记录的回调。

    Returns:
        成功时返回 ``read_id`` 和 Note 的正文及分类字段；URI 非法，或 Note
        文件不存在、内容无效时返回错误字典。
    """
    try:
        note_id = parse_uri_fn(uri)
    except ValueError as exc:
        return {"error": str(exc)}
    note = store.load_note(note_id)
    if note is None:
        return {"error": "not_found", "uri": uri}
    store.record_ref(note_id, today_fn())
    read_id = uuid.uuid4().hex
    log_access_fn(
        store.root,
        AccessRecord(
            ts=now_fn(),
            trowel_session_id=identity["trowel_session_id"],
            cc_session_id=identity["cc_session_id"],
            host_kind=identity["host_kind"],
            native_session_id=identity["native_session_id"],
            toolUseId=toolUseId,
            action="read",
            search_id=search_id,
            read_id=read_id,
            memory_id=note_id,
        ),
    )
    return {
        "read_id": read_id,
        "title": note.title,
        "body": note.body,
        "kind": note.kind,
        "verification": note.verification,
        "status": note.status,
        "tags": list(note.tags),
    }


def handle_outcome(
    read_id: str,
    outcome: str,
    reason: str,
    root: Path,
    identity: dict[str, str],
    toolUseId: str = "",
    *,
    now_fn: Any = _now,
    read_access_log_fn: Any = read_access_log,
    log_outcome_fn: Any = log_outcome,
) -> dict[str, Any]:
    """为一次已记录的正文读取追加模型反馈。

    ``outcome`` 只接受四个固定值。函数按访问日志顺序选择首个 ``read_id`` 相同
    且 action 为 ``read`` 的记录，并沿用其中的 Note 文件 stem；它不要求反馈
    身份与读取身份相同。无效结果值或未知 ``read_id`` 返回错误字典，不写反馈
    日志；日志读取和写入异常直接传播。

    Args:
        read_id: 被评价的正文读取 ID。
        outcome: ``helpful``、``harmful``、``unused`` 或 ``unknown``。
        reason: 模型给出的反馈理由；函数不校验内容。
        root: 查找访问日志并写入反馈日志的 Memory 根目录。
        identity: 写入反馈记录的当前会话身份，必须包含 Trowel、旧版 CC、运行端
            和原生会话 ID 四个键。
        toolUseId: 宿主提供的工具调用 ID；字段名沿用 Claude Code 协议。
        now_fn: 生成反馈时间的回调。
        read_access_log_fn: 按写入顺序读取访问记录的回调。
        log_outcome_fn: 追加反馈记录的回调。

    Returns:
        成功时返回确认字典；结果值无效或找不到读取记录时返回错误字典。
    """
    if outcome not in ("helpful", "harmful", "unused", "unknown"):
        return {"error": f"invalid outcome: {outcome!r}"}
    records = read_access_log_fn(root)
    match = next(
        (r for r in records if r.read_id == read_id and r.action == "read"), None
    )
    if match is None:
        return {"error": "unknown_read_id", "read_id": read_id}
    log_outcome_fn(
        root,
        OutcomeRecord(
            ts=now_fn(),
            trowel_session_id=identity["trowel_session_id"],
            cc_session_id=identity["cc_session_id"],
            host_kind=identity["host_kind"],
            native_session_id=identity["native_session_id"],
            toolUseId=toolUseId,
            read_id=read_id,
            memory_id=match.memory_id,
            outcome=cast(Outcome, outcome),
            reason=reason,
        ),
    )
    return {"ok": True, "read_id": read_id, "outcome": outcome}
