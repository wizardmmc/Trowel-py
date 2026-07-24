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
        """是否已解析到 CC 会话。"""
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
        self._by_trowel = by_trowel
        self._cc_kinds = cc_kinds

    @classmethod
    def empty(cls) -> "AttributionIndex":
        return cls({}, {})

    @classmethod
    def from_repo(cls, repo: SessionsRepository) -> "AttributionIndex":
        """从已打开的仓储加载绑定和会话类型。"""
        by_trowel = {b.trowel_session_id: b for b in repo.all_bindings()}
        return cls(by_trowel, repo.all_cc_kinds())

    @classmethod
    def from_root(cls, root: Path | str) -> "AttributionIndex":
        """从 Memory 根目录加载索引；数据库缺失时不创建文件并降级为空索引。"""
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
        """返回绑定到同一 CC 会话的全部 Trowel 会话标识。"""
        return {
            b.trowel_session_id
            for b in self._by_trowel.values()
            if b.cc_session_id == cc_session_id
        }
