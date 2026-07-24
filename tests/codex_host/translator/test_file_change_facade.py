from __future__ import annotations

import ast
import inspect
import textwrap

from trowel_py.codex_host import file_change_codec, translator
from trowel_py.codex_host.translator import CodexTranslator


def test_file_change_facades_keep_signatures_identity_and_header() -> None:
    expected = {
        "_parse_unified_diff": "(patch: 'str') -> 'tuple[dict[str, Any], ...]'",
        "_full_file_hunk": (
            "(text: 'str', marker: 'str') -> 'tuple[dict[str, Any], ...]'"
        ),
        "_file_change_write_diff": (
            "(kind_type: 'Any', diff: 'Any') -> 'dict[str, Any]'"
        ),
        "_file_change_to_change": (
            "(change: 'Mapping[str, Any]', method: 'str') -> 'dict[str, Any]'"
        ),
    }

    for name, signature in expected.items():
        value = getattr(translator, name)
        assert str(inspect.signature(value)) == signature
        assert value.__module__ == translator.__name__
        assert value.__qualname__ == name
    assert translator._HUNK_HEADER is file_change_codec.HUNK_HEADER


def test_file_change_facades_inject_runtime_dependencies(monkeypatch) -> None:
    seen: dict[str, object] = {}
    hunk_header = object()
    add_type = object()
    delete_type = object()
    update_type = object()
    mapping_type = object()
    protocol_violation_type = object()
    full_file_hunk_fn = object()
    parse_unified_diff_fn = object()
    as_str = object()
    write_diff_fn = object()

    def fake_parse(patch, **dependencies):
        seen["parse"] = (patch, dependencies)
        return ("parsed",)

    def fake_full(text, marker):
        seen["full"] = (text, marker)
        return ("full",)

    def fake_write(kind_type, diff, **dependencies):
        seen["write"] = (kind_type, diff, dependencies)
        return {"type": "encoded"}

    def fake_change(change, method, **dependencies):
        seen["change"] = (change, method, dependencies)
        return {"path": "encoded"}

    monkeypatch.setattr(translator, "_run_parse_unified_diff", fake_parse)
    monkeypatch.setattr(translator, "_run_full_file_hunk", fake_full)
    monkeypatch.setattr(translator, "_run_file_change_write_diff", fake_write)
    monkeypatch.setattr(translator, "_run_file_change_to_change", fake_change)
    monkeypatch.setattr(translator, "_HUNK_HEADER", hunk_header)

    assert translator._parse_unified_diff("patch") == ("parsed",)
    assert translator._full_file_hunk("text", "+") == ("full",)

    monkeypatch.setattr(translator, "_FC_ADD", add_type)
    monkeypatch.setattr(translator, "_FC_DELETE", delete_type)
    monkeypatch.setattr(translator, "_FC_UPDATE", update_type)
    monkeypatch.setattr(translator, "Mapping", mapping_type)
    monkeypatch.setattr(translator, "ProtocolViolationError", protocol_violation_type)
    monkeypatch.setattr(translator, "_full_file_hunk", full_file_hunk_fn)
    monkeypatch.setattr(translator, "_parse_unified_diff", parse_unified_diff_fn)

    assert translator._file_change_write_diff("kind", "diff") == {"type": "encoded"}

    monkeypatch.setattr(translator, "_as_str", as_str)
    monkeypatch.setattr(translator, "_file_change_write_diff", write_diff_fn)
    change = {"kind": {"type": "update"}}
    assert translator._file_change_to_change(change, "method") == {"path": "encoded"}

    assert seen == {
        "parse": ("patch", {"hunk_header": hunk_header}),
        "full": ("text", "+"),
        "write": (
            "kind",
            "diff",
            {
                "add_type": add_type,
                "delete_type": delete_type,
                "update_type": update_type,
                "full_file_hunk_fn": full_file_hunk_fn,
                "parse_unified_diff_fn": parse_unified_diff_fn,
                "protocol_violation_type": protocol_violation_type,
            },
        ),
        "change": (
            change,
            "method",
            {
                "add_type": add_type,
                "delete_type": delete_type,
                "update_type": update_type,
                "mapping_type": mapping_type,
                "as_str": as_str,
                "write_diff_fn": write_diff_fn,
                "protocol_violation_type": protocol_violation_type,
            },
        ),
    }


def test_translator_class_keeps_both_file_change_calls_on_facade() -> None:
    tree = ast.parse(textwrap.dedent(inspect.getsource(CodexTranslator)))
    calls = [
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_file_change_to_change"
    ]

    assert len(calls) == 2
