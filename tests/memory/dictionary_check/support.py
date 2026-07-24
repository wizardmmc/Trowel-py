from pathlib import Path

from trowel_py.memory.dictionary_check import (
    compute_rendered_hash,
    compute_source_hash,
    derive_active_corpus,
)
from trowel_py.memory.dictionary_state import DictionaryState, save_state
from trowel_py.memory.store import MemoryStore


def write_note(
    root: Path,
    title: str,
    summary: str = "s",
    tags=None,
    status: str = "active",
) -> str:
    store = MemoryStore(root)
    stem = store.write_note(
        {
            "type": "note",
            "title": title,
            "summary": summary,
            "tags": tags or [],
            "kind": "fact",
            "verification": "verified",
            "refs": 0,
            "last_ref": "",
        }
    )
    if status != "active":
        store.update_note_fields(stem, {"status": status})
    return stem


def write_l1(
    root: Path,
    domain: str,
    entries: list[tuple[str, str, str]],
) -> None:
    directory = root / "dictionary-L1"
    directory.mkdir(parents=True, exist_ok=True)
    lines = [f"# {domain}", ""]
    for stem, title, summary in entries:
        lines.append(
            f"- **{title}** → `notes/{stem}.md`：{summary}｜触发词：{title}"
            f" <!-- @stem {stem} -->"
        )
    (directory / f"{domain}.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def write_l0(root: Path, domains: list[tuple[str, int]]) -> None:
    lines = ["# dictionary L0", ""]
    for name, count in domains:
        lines.extend(
            [
                f"### {name}（{count} 条 → read dictionary-L1/{name}.md）",
                "",
            ]
        )
    (root / "dictionary-L0.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def read_l1_files(root: Path) -> dict[str, str]:
    directory = root / "dictionary-L1"
    if not directory.exists():
        return {}
    return {
        path.stem: path.read_text(encoding="utf-8") for path in directory.glob("*.md")
    }


def stamp_consistent(root: Path) -> None:
    corpus = derive_active_corpus(root)
    l0_path = root / "dictionary-L0.md"
    l0_text = l0_path.read_text(encoding="utf-8") if l0_path.exists() else ""
    l1_files = read_l1_files(root)
    save_state(
        root,
        DictionaryState().with_success(
            compute_source_hash(corpus),
            compute_rendered_hash(l0_text, l1_files),
            "2026-07-18T10:00:00",
        ),
    )
