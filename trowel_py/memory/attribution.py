"""把访问记录解析为所属 CC 会话及会话类型。

解析优先使用 ``trowel_session_id`` 绑定，其次使用记录中的非空
``cc_session_id``；没有可验证映射时保持未归属，不猜测所有者。索引一次读取
绑定与会话类型，随后在内存中批量解析。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from trowel_py.memory.sessions_repo import (
    SessionBinding,
    SessionsRepository,
    create_sessions_repository,
    open_sessions_db,
)

AttributionBasis = Literal["trowel_binding", "cc_session_id", "unattributed"]


@dataclass(frozen=True)
class Attribution:
    """一条访问记录的已解析归属。"""

    cc_session_id: str | None
    session_kind: str
    basis: AttributionBasis

    @property
    def attributed(self) -> bool:
        """判断记录是否已归到某个 Claude Code 会话。"""
        return self.basis != "unattributed"

    @property
    def is_user(self) -> bool:
        """是否属于用户会话；内部任务与未归属记录不进入用户指标。"""
        return self.attributed and self.session_kind == "user"


class AttributionIndex:
    """基于会话绑定和类型快照的内存批量解析器。"""

    def __init__(
        self,
        by_trowel: dict[str, SessionBinding],
        cc_kinds: dict[str, str],
    ) -> None:
        """保存解析访问记录所需的会话绑定和类型映射。

        Args:
            by_trowel: Trowel 会话 ID 到持久化会话绑定的映射。
            cc_kinds: Claude Code 会话 ID 到会话用途的映射。
        """

        self._by_trowel = by_trowel
        self._cc_kinds = cc_kinds

    @classmethod
    def empty(cls) -> "AttributionIndex":
        """返回不包含任何会话映射的归因索引。"""

        return cls({}, {})

    @classmethod
    def from_repo(cls, repo: SessionsRepository) -> "AttributionIndex":
        """从已打开的仓储加载绑定和会话类型。"""
        by_trowel = {
            binding.trowel_session_id: binding
            for binding in repo.claude.all_bindings()
        }
        return cls(by_trowel, repo.claude.all_cc_kinds())

    @classmethod
    def from_root(cls, root: Path | str) -> "AttributionIndex":
        """从 Memory 根目录的会话数据库加载归属索引。

        数据库缺失时不创建文件；数据库存在时，会按会话仓储的初始化逻辑补齐
        schema。现有数据库无法打开、初始化或加载时返回空索引。

        Args:
            root: Memory 根目录。

        Returns:
            数据库当前内容对应的索引；无法读取数据库时为空索引。
        """
        if not (Path(root) / "meta" / "sessions.db").exists():
            return cls.empty()
        try:
            conn = open_sessions_db(Path(root))
        except Exception:
            return cls.empty()
        try:
            return cls.from_repo(create_sessions_repository(conn))
        except Exception:
            return cls.empty()
        finally:
            conn.close()

    def resolve(self, trowel_session_id: str, cc_session_id: str) -> Attribution:
        """按绑定优先、记录内 CC 标识次之的顺序解析归属。"""
        if trowel_session_id:
            binding = self._by_trowel.get(trowel_session_id)
            if binding is not None:
                return Attribution(
                    cc_session_id=binding.cc_session_id,
                    session_kind=binding.session_kind,
                    basis="trowel_binding",
                )
        if cc_session_id:
            return Attribution(
                cc_session_id=cc_session_id,
                session_kind=self._cc_kinds.get(cc_session_id, "unknown"),
                basis="cc_session_id",
            )
        return Attribution(
            cc_session_id=None, session_kind="unknown", basis="unattributed"
        )

    def trowel_ids_for_cc(self, cc_session_id: str) -> set[str]:
        """查找绑定到指定 Claude Code 会话的全部 Trowel 会话 ID。

        Args:
            cc_session_id: 要查询的 Claude Code 会话 ID。

        Returns:
            当前索引中绑定到该会话的 Trowel 会话 ID 集合。
        """
        return {
            b.trowel_session_id
            for b in self._by_trowel.values()
            if b.cc_session_id == cc_session_id
        }
