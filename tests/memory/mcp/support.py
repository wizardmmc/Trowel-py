"""memory MCP 测试数据。"""

from trowel_py.memory.types import Note

IDENTITY = {
    "trowel_session_id": "t-1",
    "cc_session_id": "c-1",
    "host_kind": "cc",
    "native_session_id": "c-1",
}


class FakeRetriever:
    def __init__(self, stems: list[str]) -> None:
        self._stems = stems

    def __call__(self, query, *, corpus_dir, dictionary_path):
        return list(self._stems)


def note(**overrides) -> Note:
    return Note(
        type="note",
        title="t",
        kind=overrides.get("kind", "fact"),
        verification=overrides.get("verification", "verified"),
    )
