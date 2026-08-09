"""解析共享上下文中的 Markdown 结构，不负责仓库策略与文件系统判断。"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from html.parser import HTMLParser

from markdown_it import MarkdownIt
from markdown_it.token import Token


_CLAUDE_IMPORT_PATTERN = re.compile(r"(?:^|\s)@((?:[^\s\\]|\\ )+)")
_ALLOWED_RAW_HTML: dict[str, frozenset[str]] = {
    "p": frozenset({"align"}),
    "strong": frozenset(),
    "img": frozenset({"src", "alt", "width"}),
}


@dataclass(frozen=True)
class MarkdownTarget:
    """记录一个结构化 Markdown 目标及其导航语义。

    Attributes:
        target: Markdown 解析器返回的原始目标。
        is_image: 目标来自图片时为 True，来自可导航链接时为 False。
    """

    target: str
    is_image: bool


class _AuditMarkdownIt(MarkdownIt):
    """保留所有链接协议，交给仓库审计策略统一裁决。"""

    def validateLink(self, url: str) -> bool:  # noqa: N802
        """允许解析器保留链接，危险协议稍后由路径审计明确报告。

        Args:
            url: Markdown 链接的原始目标；协议和路径此处不做判断。

        Returns:
            始终为 True，确保链接不会在结构化审计前被解析器丢弃。
        """

        del url
        return True


class _RawHtmlTargetParser(HTMLParser):
    """从 CommonMark 原始 HTML token 中提取可加载的本地或外部目标。

    Attributes:
        targets: 按 HTML 属性出现顺序保存的结构化目标。
        unsupported: 不属于当前 README 白名单的标签、属性或声明。
        visible_text: HTML 正文和图片替代文字组成的可见文本片段。
        attribute_values: HTMLParser 已完成字符引用解码的全部属性值。
    """

    def __init__(self) -> None:
        """初始化启用字符引用解码的标准库 HTML 解析器。"""

        super().__init__(convert_charrefs=True)
        self.targets: list[MarkdownTarget] = []
        self.unsupported: list[str] = []
        self.visible_text: list[str] = []
        self.attribute_values: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """收集开始标签里的 ``href`` 和 ``src`` 属性。

        Args:
            tag: HTML 标签名；当前策略不按标签白名单过滤。
            attrs: HTMLParser 解析后的属性名和值。
        """

        allowed_attrs = _ALLOWED_RAW_HTML.get(tag)
        if allowed_attrs is None:
            self.unsupported.append(f"tag:{tag}")
            allowed_attrs = frozenset()
        for name, value in attrs:
            if value is not None:
                self.attribute_values.append(value)
            if name not in allowed_attrs:
                self.unsupported.append(f"attribute:{tag}.{name}")
            if name in {"href", "src"} and value:
                self.targets.append(MarkdownTarget(value, is_image=name == "src"))
            if tag == "img" and name == "alt" and value:
                self.visible_text.append(value)

    def handle_endtag(self, tag: str) -> None:
        """拒绝不在公共 README 白名单中的结束标签。

        Args:
            tag: HTML 结束标签名。
        """

        if tag not in _ALLOWED_RAW_HTML:
            self.unsupported.append(f"tag:{tag}")

    def handle_comment(self, data: str) -> None:
        """拒绝公共可达 Markdown 中的 HTML 注释。

        Args:
            data: 注释正文；只用于满足 HTMLParser 回调契约。
        """

        del data
        self.unsupported.append("comment")

    def handle_data(self, data: str) -> None:
        """收集 HTMLParser 已完成字符引用解码的可见正文。

        Args:
            data: 标签之间对读者可见的正文。
        """

        self.visible_text.append(data)

    def handle_decl(self, decl: str) -> None:
        """拒绝公共可达 Markdown 中的 HTML 声明。

        Args:
            decl: 声明正文；只用于诊断类别，不进入输出。
        """

        del decl
        self.unsupported.append("declaration")

    def handle_pi(self, data: str) -> None:
        """拒绝公共可达 Markdown 中的 HTML processing instruction。

        Args:
            data: processing instruction 正文；不进入公共上下文输出。
        """

        del data
        self.unsupported.append("processing-instruction")


def _walk_tokens(tokens: Iterable[Token]) -> Iterable[Token]:
    """递归遍历 Markdown token 及其行内子 token。

    Args:
        tokens: Markdown 解析器产生的当前层 token 序列。

    Yields:
        按文档顺序遍历的当前 token 及全部子 token。
    """

    for token in tokens:
        yield token
        if token.children:
            yield from _walk_tokens(token.children)


def markdown_targets(markdown: str) -> tuple[MarkdownTarget, ...]:
    """用 CommonMark 解析器返回带导航语义的链接和图片目标。

    Args:
        markdown: 需要解析的公共 Markdown 正文。

    Returns:
        按出现顺序排列的结构化目标。
    """

    parser = _AuditMarkdownIt("commonmark")
    targets: list[MarkdownTarget] = []
    for token in _walk_tokens(parser.parse(markdown)):
        if token.type == "link_open":
            href = token.attrGet("href")
            if isinstance(href, str) and href:
                targets.append(MarkdownTarget(href, is_image=False))
        elif token.type == "image":
            src = token.attrGet("src")
            if isinstance(src, str) and src:
                targets.append(MarkdownTarget(src, is_image=True))
        elif token.type in {"html_block", "html_inline"}:
            html_parser = _RawHtmlTargetParser()
            html_parser.feed(token.content)
            targets.extend(html_parser.targets)
    return tuple(targets)


def markdown_links(markdown: str) -> tuple[str, ...]:
    """返回正文中所有需要做路径和隐私审计的链接及图片目标。

    Args:
        markdown: 需要解析的公共 Markdown 正文。

    Returns:
        按出现顺序排列的链接与图片原始目标。
    """

    return tuple(item.target for item in markdown_targets(markdown))


def _inline_visible_text(tokens: Iterable[Token]) -> str:
    """把一组 CommonMark 行内 token 投影为连续可见文本。

    Args:
        tokens: 同一行内容器的子 token；强调和链接边界不会打断相邻文本。

    Returns:
        字符引用和反斜杠转义已解码，并包含代码、图片替代文字和允许 HTML 的正文。
    """

    parts: list[str] = []
    parser = MarkdownIt("commonmark")
    for token in tokens:
        if token.type in {"text", "code_inline"}:
            parts.append(token.content)
        elif token.type in {"softbreak", "hardbreak"}:
            parts.append("\n")
        elif token.type == "image":
            inline_tokens = parser.parseInline(token.content)
            for inline in inline_tokens:
                parts.append(_inline_visible_text(inline.children or ()))
        elif token.type == "html_inline":
            html_parser = _RawHtmlTargetParser()
            html_parser.feed(token.content)
            parts.extend(html_parser.visible_text)
    return "".join(parts)


def markdown_visible_text(markdown: str) -> str:
    """返回 CommonMark 解码并去掉展示标记后的完整可见正文。

    Args:
        markdown: 需要投影的公共 Markdown 正文。

    Returns:
        各块以换行分隔的正文，包含行内代码、围栏代码、图片替代文字和 HTML 正文。
    """

    blocks: list[str] = []
    for token in MarkdownIt("commonmark").parse(markdown):
        if token.type == "inline":
            blocks.append(_inline_visible_text(token.children or ()))
        elif token.type in {"fence", "code_block"}:
            blocks.append(token.content)
        elif token.type == "html_block":
            html_parser = _RawHtmlTargetParser()
            html_parser.feed(token.content)
            blocks.append("".join(html_parser.visible_text))
    return "\n".join(blocks)


def markdown_attribute_values(markdown: str) -> tuple[str, ...]:
    """返回 CommonMark 与允许 HTML 已解码的全部结构化属性值。

    Args:
        markdown: 需要提取链接、图片和原始 HTML 属性的公共 Markdown。

    Returns:
        按文档顺序排列的属性值；包含链接与图片 title，以及 HTML 的全部属性。
    """

    values: list[str] = []
    parser = _AuditMarkdownIt("commonmark")
    for token in _walk_tokens(parser.parse(markdown)):
        if token.type in {"link_open", "image"}:
            values.extend(
                value
                for value in token.attrs.values()
                if isinstance(value, str) and value
            )
        elif token.type in {"html_block", "html_inline"}:
            html_parser = _RawHtmlTargetParser()
            html_parser.feed(token.content)
            values.extend(html_parser.attribute_values)
    return tuple(values)


def markdown_headings(markdown: str) -> tuple[str, ...]:
    """返回 CommonMark 文档中按顺序出现的可见标题正文。

    Args:
        markdown: 需要读取章节结构的公共 Markdown。

    Returns:
        去掉展示标记并完成字符引用解码的标题正文。
    """

    tokens = MarkdownIt("commonmark").parse(markdown)
    headings: list[str] = []
    for index, token in enumerate(tokens[:-1]):
        if token.type != "heading_open" or tokens[index + 1].type != "inline":
            continue
        headings.append(_inline_visible_text(tokens[index + 1].children or ()))
    return tuple(headings)


def markdown_section_tables(
    markdown: str, heading_text: str
) -> tuple[tuple[tuple[str, ...], ...], ...]:
    """返回指定 H2 章节内全部 Markdown 表格的可见单元格。

    Args:
        markdown: 需要读取结构化表格的公共 Markdown。
        heading_text: 目标二级标题的可见正文。

    Returns:
        按出现顺序排列的表格；每张表包含表头，章节或表格缺失时返回空元组。
    """

    parser = MarkdownIt("commonmark").enable("table")
    tokens = parser.parse(markdown)
    section_start: int | None = None
    section_end = len(tokens)
    for index, token in enumerate(tokens[:-1]):
        if token.type != "heading_open" or token.tag != "h2":
            continue
        heading = tokens[index + 1]
        if heading.type != "inline":
            continue
        current_heading = _inline_visible_text(heading.children or ())
        if section_start is None and current_heading == heading_text:
            section_start = index + 3
        elif section_start is not None:
            section_end = index
            break
    if section_start is None:
        return ()

    tables: list[tuple[tuple[str, ...], ...]] = []
    rows: list[tuple[str, ...]] | None = None
    current_row: list[str] | None = None
    in_cell = False
    for token in tokens[section_start:section_end]:
        if token.type == "table_open":
            rows = []
        elif token.type == "table_close":
            if rows is not None:
                tables.append(tuple(rows))
            rows = None
            current_row = None
            in_cell = False
        elif token.type == "tr_open" and rows is not None:
            current_row = []
        elif token.type == "tr_close" and rows is not None:
            if current_row is not None:
                rows.append(tuple(current_row))
            current_row = None
        elif token.type in {"th_open", "td_open"}:
            in_cell = True
        elif token.type in {"th_close", "td_close"}:
            in_cell = False
        elif token.type == "inline" and current_row is not None and in_cell:
            current_row.append(_inline_visible_text(token.children or ()))
    return tuple(tables)


def module_status_evidence_fields(evidence: str) -> dict[str, str] | None:
    """解析运行事实单元格中的分号分隔 ``key=value`` 证据字段。

    Args:
        evidence: 状态表第二列的可见正文。

    Returns:
        字段和值均非空且键不重复时返回映射，格式无效时返回 None。
    """

    fields: dict[str, str] = {}
    for item in evidence.split(";"):
        key, separator, value = item.strip().partition("=")
        if not separator or not key or not value.strip() or key in fields:
            return None
        fields[key] = value.strip()
    return fields


def root_module_index_entries(markdown: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """返回根 ``按领域继续读`` 表格中的任务摘要和公共入口目标。

    Args:
        markdown: 根 ``AGENTS.md`` 正文。

    Returns:
        每个数据行的首列可见任务摘要，以及第二列中的全部导航目标。
    """

    parser = MarkdownIt("commonmark").enable("table")
    tokens = parser.parse(markdown)
    section_start: int | None = None
    section_end = len(tokens)
    for index, token in enumerate(tokens[:-1]):
        if token.type != "heading_open" or token.tag != "h2":
            continue
        heading = tokens[index + 1]
        if heading.type != "inline":
            continue
        text = _inline_visible_text(heading.children or ())
        if section_start is None and text == "按领域继续读":
            section_start = index + 3
        elif section_start is not None:
            section_end = index
            break
    if section_start is None:
        return ()

    table_start = next(
        (
            index
            for index in range(section_start, section_end)
            if tokens[index].type == "table_open"
        ),
        None,
    )
    if table_start is None:
        return ()

    rows: list[tuple[str, tuple[str, ...]]] = []
    current_cells: list[tuple[str, tuple[str, ...]]] | None = None
    in_cell = False
    for token in tokens[table_start + 1 : section_end]:
        if token.type == "table_close":
            break
        if token.type == "tr_open":
            current_cells = []
        elif token.type == "tr_close":
            if current_cells is not None and len(current_cells) >= 2 and rows:
                rows.append((current_cells[0][0], current_cells[1][1]))
            elif current_cells is not None and not rows:
                rows.append(("", ()))
            current_cells = None
        elif token.type in {"th_open", "td_open"}:
            in_cell = True
        elif token.type in {"th_close", "td_close"}:
            in_cell = False
        elif token.type == "inline" and current_cells is not None and in_cell:
            targets = tuple(
                target
                for child in _walk_tokens(token.children or ())
                if child.type == "link_open"
                for target in (child.attrGet("href"),)
                if isinstance(target, str) and target
            )
            current_cells.append((_inline_visible_text(token.children or ()), targets))
    return tuple(rows[1:])


def root_entrypoint_targets(markdown: str) -> tuple[str | None, ...]:
    """返回根 AGENTS ``开工入口`` 章节首个有序列表的逐项链接目标。

    Args:
        markdown: 根 ``AGENTS.md`` 正文。

    Returns:
        每个一级列表项唯一导航链接的目标；无链接或有多个链接时该项为 None。
        章节或有序列表缺失时返回空元组。
    """

    tokens = MarkdownIt("commonmark").parse(markdown)
    section_start: int | None = None
    section_end = len(tokens)
    for index, token in enumerate(tokens[:-1]):
        if token.type != "heading_open" or token.tag != "h2":
            continue
        heading = tokens[index + 1]
        if heading.type != "inline":
            continue
        heading_text = _inline_visible_text(heading.children or ())
        if section_start is None and heading_text == "开工入口":
            section_start = index + 3
        elif section_start is not None:
            section_end = index
            break
    if section_start is None:
        return ()

    list_start = next(
        (
            index
            for index in range(section_start, section_end)
            if tokens[index].type == "ordered_list_open" and tokens[index].level == 0
        ),
        None,
    )
    if list_start is None:
        return ()

    item_targets: list[str | None] = []
    current_targets: list[str] | None = None
    for token in tokens[list_start + 1 : section_end]:
        if token.type == "ordered_list_close" and token.level == 0:
            break
        if token.type == "list_item_open" and token.level == 1:
            current_targets = []
            continue
        if token.type == "list_item_close" and token.level == 1:
            if current_targets is not None:
                item_targets.append(
                    current_targets[0] if len(current_targets) == 1 else None
                )
            current_targets = None
            continue
        if token.type != "inline" or current_targets is None:
            continue
        current_targets.extend(
            target
            for child in _walk_tokens(token.children or ())
            if child.type == "link_open"
            for target in (child.attrGet("href"),)
            if isinstance(target, str) and target
        )
    return tuple(item_targets)


def claude_imports(markdown: str) -> tuple[str, ...]:
    """返回 Claude Code 会从 Markdown 正文解释出的 ``@path`` 导入。

    Args:
        markdown: 需要按 Claude memory import 语义检查的公共正文。

    Returns:
        按出现顺序排列、去掉 fragment 并还原转义空格的导入路径。代码块和行内代码
        不属于 Claude 导入，因此不会返回。
    """

    imports: list[str] = []
    for token in _walk_tokens(MarkdownIt("commonmark").parse(markdown)):
        if token.type != "text":
            continue
        for match in _CLAUDE_IMPORT_PATTERN.finditer(token.content):
            path = match.group(1).split("#", 1)[0].replace(r"\ ", " ")
            if not path:
                continue
            valid = (
                path.startswith("./")
                or path.startswith("~/")
                or (path.startswith("/") and path != "/")
                or (
                    not path.startswith("@")
                    and not re.match(r"^[#%^&*()]+", path)
                    and re.match(r"^[A-Za-z0-9._-]", path) is not None
                )
            )
            if valid:
                imports.append(path)
    return tuple(imports)


def has_raw_html(markdown: str) -> bool:
    """判断公共 Markdown 是否含链接审计无法结构化解释的原始 HTML。"""

    return any(
        token.type in {"html_block", "html_inline"}
        for token in _walk_tokens(MarkdownIt("commonmark").parse(markdown))
    )


def unsupported_raw_html(markdown: str) -> tuple[str, ...]:
    """返回公共 README 白名单无法解释的原始 HTML 结构。"""

    unsupported: list[str] = []
    for token in _walk_tokens(MarkdownIt("commonmark").parse(markdown)):
        if token.type not in {"html_block", "html_inline"}:
            continue
        parser = _RawHtmlTargetParser()
        parser.feed(token.content)
        unsupported.extend(parser.unsupported)
    return tuple(unsupported)
