from __future__ import annotations

import inspect

from trowel_py.cc_host.frontmatter import parse_frontmatter
from trowel_py.cc_host.slash_items import _parse_frontmatter


class TestParseFrontmatterBlockScalar:
    def test_literal_block_pipe(self) -> None:
        parsed = _parse_frontmatter(
            "---\nname: x\ndescription: |\n  line one\n  line two\n---\n"
        )
        assert parsed["description"] == "line one line two"

    def test_folded_block_greater(self) -> None:
        parsed = _parse_frontmatter(
            "---\nname: x\ndescription: >\n  alpha\n  beta\n---\n"
        )
        assert parsed["description"] == "alpha beta"

    def test_block_with_strip_chomping(self) -> None:
        parsed = _parse_frontmatter("---\nname: x\ndescription: |-\n  a\n  b\n---\n")
        assert parsed["description"] == "a b"

    def test_block_blank_line_is_paragraph_break(self) -> None:
        parsed = _parse_frontmatter(
            "---\nname: x\ndescription: |\n"
            "  first paragraph\n\n  second paragraph\n---\n"
        )
        assert parsed["description"] == "first paragraph second paragraph"

    def test_single_line_still_works(self) -> None:
        parsed = _parse_frontmatter("---\nname: x\ndescription: short one-liner\n---\n")
        assert parsed["description"] == "short one-liner"

    def test_inner_quotes_preserved(self) -> None:
        parsed = _parse_frontmatter('---\nname: x\ndescription: "don\'t stop"\n---\n')
        assert parsed["description"] == "don't stop"

    def test_no_frontmatter_returns_empty(self) -> None:
        assert _parse_frontmatter("just body, no frontmatter") == {}


def test_frontmatter_facade_contract() -> None:
    assert _parse_frontmatter.__module__ == "trowel_py.cc_host.slash_items"
    assert str(inspect.signature(_parse_frontmatter)) == (
        "(text: 'str') -> 'dict[str, str]'"
    )


def test_frontmatter_facade_matches_direct_parser() -> None:
    samples = (
        "",
        "---",
        "---\nname: example\n---\nbody",
        "---\ndescription: >+\n  first\n\n  second\nnext: value\n---\n",
        "---\n# note\n- item\ninvalid\nquoted: 'value'\n---\n",
    )
    for sample in samples:
        assert _parse_frontmatter(sample) == parse_frontmatter(sample)
