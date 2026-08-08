"""把持久连接解析为仅供 runtime 消费的冻结启动配置。"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit, urlunsplit

from trowel_py.configuration.models import (
    CodexCatalogEntry,
    ConnectionKind,
    ProtocolKind,
    RuntimeKind,
)


@dataclass(frozen=True)
class RuntimeLaunchConfiguration:
    """保存一次 Agent 会话创建已经冻结的连接身份和秘密启动材料。

    该对象只能停留在后端进程内。API key 和代理密码从 ``repr`` 中排除，也不得
    写入 binding、日志、异常或前端响应。

    Attributes:
        connection_id: 设置域分配的稳定连接 ID。
        connection_version: 最近选择写回使用的乐观并发版本。
        connection_identity_version: 会影响 runtime 身份的单调版本。
        connection_name: 创建时冻结的脱敏展示名。
        runtime: 消费连接的执行引擎。
        kind: 连接字段与认证方式。
        protocol: 上游请求协议。
        model: Codex 的真实模型 ID，或 Claude 主会话角色别名。
        effort: 本会话选定的思考强度；runtime 不使用时为 None。
        capability_version: 放行该组合的只读能力表版本。
        capability_source: 放行该组合的真实验证证据说明。
        base_url: 自定义上游根地址；official Codex 为 None。
        login_directory: Codex official 原生登录目录引用。
        claude_config_dir: Claude 兼容连接独占的用户配置与原生状态根。
        claude_plugin_dir: Claude 连接共享物理安装的 plugin 根。
        codex_config_dir: 新版 Codex 会话冻结的连接配置家；None 保留旧会话启动语义。
        proxy_url: 已补齐可选认证信息的连接级代理地址。
        claude_role_models: Claude 各角色的真实模型映射。
        codex_catalog: Codex 连接保存的模型元数据。
        api_key: 只供子进程环境使用的连接凭据。
        claude_auto_memory_disabled: 是否关闭 Claude Code 原生 auto-memory。
    """

    connection_id: str
    connection_version: int
    connection_identity_version: int
    connection_name: str
    runtime: RuntimeKind
    kind: ConnectionKind
    protocol: ProtocolKind
    model: str
    effort: str | None
    capability_version: str
    base_url: str | None
    login_directory: str | None
    proxy_url: str | None = field(repr=False)
    claude_role_models: Mapping[str, str]
    codex_catalog: tuple[CodexCatalogEntry, ...]
    claude_config_dir: str | None = None
    claude_plugin_dir: str | None = None
    codex_config_dir: str | None = None
    api_key: str | None = field(default=None, repr=False)
    capability_source: str | None = None
    claude_auto_memory_disabled: bool = False

    @property
    def pool_key(self) -> str:
        """返回不含凭据原值的 Codex manager pool 身份。"""

        proxy_endpoint = None
        proxy_username = None
        if self.proxy_url:
            parsed_proxy = urlsplit(self.proxy_url)
            proxy_endpoint = urlunsplit(
                (
                    parsed_proxy.scheme,
                    parsed_proxy.netloc.rsplit("@", 1)[-1],
                    parsed_proxy.path,
                    "",
                    "",
                )
            )
            proxy_username = parsed_proxy.username
        payload = {
            "connection_id": self.connection_id,
            "identity_version": self.connection_identity_version,
            "kind": self.kind.value,
            "protocol": self.protocol.value,
            "base_url": self.base_url,
            "login_directory": self.login_directory,
            "codex_config_dir": self.codex_config_dir,
            "proxy_endpoint": proxy_endpoint,
            "proxy_username": proxy_username,
        }
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
        return hashlib.sha256(encoded).hexdigest()

    def claude_settings(self, *, proxy_base_url: str) -> dict[str, object]:
        """构造只含当前连接字段的 Claude Code 私有 settings。"""

        if self.kind is not ConnectionKind.CLAUDE_COMPATIBLE:
            raise ValueError("Claude settings require a Claude-compatible connection")
        if not self.api_key or not self.base_url:
            raise ValueError("Claude-compatible connection is missing credentials")
        env = {
            "ANTHROPIC_BASE_URL": proxy_base_url,
            "ANTHROPIC_AUTH_TOKEN": self.api_key,
            "CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST": "1",
        }
        role_env = {
            "default": "ANTHROPIC_MODEL",
            "sonnet": "ANTHROPIC_DEFAULT_SONNET_MODEL",
            "opus": "ANTHROPIC_DEFAULT_OPUS_MODEL",
            "fable": "ANTHROPIC_DEFAULT_FABLE_MODEL",
            "haiku": "ANTHROPIC_DEFAULT_HAIKU_MODEL",
            "subagent": "CLAUDE_CODE_SUBAGENT_MODEL",
        }
        for role, variable in role_env.items():
            configured = self.claude_role_models.get(role)
            if configured:
                env[variable] = configured
        settings: dict[str, object] = {"env": env, "model": self.model}
        if self.claude_auto_memory_disabled:
            settings["autoMemoryEnabled"] = False
        return settings

    def codex_environment(self, *, shared_state_root: Path) -> dict[str, str]:
        """构造不会继承其他连接凭据、用户 skill 或代理的 Codex 环境。"""

        if self.codex_config_dir is not None:
            codex_home = Path(self.codex_config_dir).expanduser()
        else:
            # 旧冻结档案没有 codex_config_dir：Official 继续使用原账号槽，
            # Custom 继续使用旧共享根，避免恢复时悄悄换配置身份。
            codex_home = (
                Path(self.login_directory).expanduser()
                if self.kind is ConnectionKind.CODEX_OFFICIAL and self.login_directory
                else shared_state_root
            )
        env = {
            "CODEX_HOME": str(codex_home),
            "CODEX_SQLITE_HOME": str(shared_state_root),
            "OPENAI_API_KEY": "",
            "CODEX_API_KEY": "",
            "TROWEL_CODEX_PROVIDER_KEY": self.api_key or "",
        }
        if self.codex_config_dir is not None:
            # Codex 原生始终扫描 $HOME/.agents/skills。把 app-server 的 HOME
            # 限定到连接家，shell 工具再由 codex_overrides 恢复真实 HOME。
            env["HOME"] = str(codex_home)
            if os.name == "nt":
                env["USERPROFILE"] = str(codex_home)
        for name in (
            "ALL_PROXY",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "NO_PROXY",
            "all_proxy",
            "http_proxy",
            "https_proxy",
            "no_proxy",
        ):
            env[name] = ""
        if self.proxy_url:
            for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
                env[name] = self.proxy_url
        return env

    def codex_overrides(self) -> dict[str, object]:
        """构造固定 provider 的 app-server ``-c`` 覆盖项。"""

        shell_environment = {"HOME": str(Path.home())}
        if os.name == "nt":
            shell_environment["USERPROFILE"] = str(Path.home())
        shell_policy = (
            {"shell_environment_policy": {"set": shell_environment}}
            if self.codex_config_dir is not None
            else {}
        )
        if self.kind is ConnectionKind.CODEX_OFFICIAL:
            return {"model_provider": "openai", **shell_policy}
        if self.kind is not ConnectionKind.CODEX_CUSTOM or not self.base_url:
            raise ValueError("Codex overrides require a usable Codex connection")
        provider_id = f"trowel_{self.connection_id.replace('-', '_')}"
        return {
            "model_provider": provider_id,
            "disable_response_storage": True,
            "model_providers": {
                provider_id: {
                    "name": self.connection_name,
                    "base_url": self.base_url,
                    "wire_api": "responses",
                    "env_key": "TROWEL_CODEX_PROVIDER_KEY",
                    "requires_openai_auth": False,
                    "request_max_retries": 1,
                    "stream_max_retries": 1,
                }
            },
            **shell_policy,
        }


def cleanup_private_claude_settings(directory: Path) -> None:
    """清理应用自有目录中上次异常退出遗留的 Claude 私有 settings。"""

    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    for path in directory.glob("trowel-claude-connection-*.json"):
        if path.is_file():
            path.unlink(missing_ok=True)


def write_private_claude_settings(
    settings: Mapping[str, object],
    *,
    directory: Path,
) -> Path:
    """在应用自有 ``0700`` 目录中以 ``0600`` 写入私有 settings 文件。"""

    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)

    descriptor, raw_path = tempfile.mkstemp(
        prefix="trowel-claude-connection-",
        suffix=".json",
        dir=directory,
    )
    path = Path(raw_path)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(settings, stream, ensure_ascii=False, separators=(",", ":"))
            stream.write("\n")
        path.chmod(0o600)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path
