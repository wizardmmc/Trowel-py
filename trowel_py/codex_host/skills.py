"""校验并脱敏 Codex app-server 返回的技能目录。"""

from __future__ import annotations

from typing import Any, Mapping

from trowel_py.codex_host.errors import ProtocolViolationError

_SCOPES = frozenset({"user", "repo", "system", "admin"})


def parse_skill_catalog(result: object, *, cwd: str) -> dict[str, Any]:
    """提取指定工作目录的技能，并移除本机绝对路径。

    Args:
        result: ``skills/list`` 的原生响应结果。
        cwd: 本次请求绑定的会话工作目录。

    Returns:
        包含 ``skills`` 与脱敏 ``errors`` 的目录；技能保留名称、说明、来源和启用状态。

    Raises:
        ProtocolViolationError: 响应结构、工作目录或技能字段不符合已验证协议。
    """

    if not isinstance(result, Mapping):
        raise ProtocolViolationError(
            "skills/list result must be an object", payload=result
        )
    data = result.get("data")
    if not isinstance(data, list) or len(data) != 1:
        raise ProtocolViolationError(
            "skills/list result must contain one cwd entry", payload=result
        )
    entry = data[0]
    if not isinstance(entry, Mapping) or entry.get("cwd") != cwd:
        raise ProtocolViolationError(
            "skills/list cwd does not match the requested session", payload=result
        )
    raw_skills = entry.get("skills")
    raw_errors = entry.get("errors")
    if not isinstance(raw_skills, list) or not isinstance(raw_errors, list):
        raise ProtocolViolationError(
            "skills/list entry must contain skills and errors arrays", payload=result
        )

    skills: list[dict[str, Any]] = []
    for raw_skill in raw_skills:
        if not isinstance(raw_skill, Mapping):
            raise ProtocolViolationError(
                "skill metadata must be an object", payload=result
            )
        name = raw_skill.get("name")
        description = raw_skill.get("description")
        scope = raw_skill.get("scope")
        enabled = raw_skill.get("enabled")
        if (
            not isinstance(name, str)
            or not name
            or not isinstance(description, str)
            or scope not in _SCOPES
            or not isinstance(enabled, bool)
        ):
            raise ProtocolViolationError("skill metadata is invalid", payload=result)
        skills.append(
            {
                "name": name,
                "description": description,
                "scope": scope,
                "enabled": enabled,
            }
        )

    errors: list[str] = []
    for raw_error in raw_errors:
        if not isinstance(raw_error, Mapping) or not isinstance(
            raw_error.get("message"), str
        ):
            raise ProtocolViolationError(
                "skill error metadata is invalid", payload=result
            )
        # 原生 path 和 message 都可能包含用户目录或项目绝对路径。HTTP 只公开
        # 错误数量和固定文案，详细 payload 留在后端协议异常诊断边界内。
        errors.append("技能配置加载失败")
    return {"skills": skills, "errors": errors}
