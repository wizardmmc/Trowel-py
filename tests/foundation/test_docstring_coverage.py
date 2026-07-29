from pathlib import Path

from scripts.docstring_coverage import audit


def test_audit_reports_each_undocumented_definition(tmp_path: Path) -> None:
    source = tmp_path / "missing.py"
    source.write_text(
        "class Example:\n"
        "    def method(self):\n"
        "        def nested():\n"
        "            return None\n"
        "        return nested()\n",
        encoding="utf-8",
    )

    files, missing = audit([tmp_path])

    assert files == [source]
    assert [(item.kind, item.name) for item in missing] == [
        ("module", "missing"),
        ("class", "Example"),
        ("function", "Example.method"),
        ("function", "Example.method.nested"),
    ]


def test_audit_accepts_a_fully_documented_file(tmp_path: Path) -> None:
    source = tmp_path / "complete.py"
    source.write_text(
        '"""示例模块。"""\n'
        "class Example:\n"
        '    """示例对象。"""\n'
        "    def method(self):\n"
        '        """执行示例操作。"""\n'
        "        return None\n",
        encoding="utf-8",
    )

    files, missing = audit([source])

    assert files == [source]
    assert missing == []
