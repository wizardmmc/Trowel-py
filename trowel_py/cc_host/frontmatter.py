"""解析 Claude Code skill 和 slash command Markdown 开头的简化 frontmatter。"""

from __future__ import annotations


def parse_frontmatter(text: str) -> dict[str, str]:
    """解析文本开头的简化 frontmatter。

    这里只识别 ``key: value`` 和 ``|``、``>``、``|-``、``|+``、``>-``、``>+``
    六种块标量标记，不是完整的 YAML 解析器。值的首尾是相同的单引号或双引号时
    会去掉引号，但不会处理转义。六种块标量都会忽略空行，并把非空缩进行用空格
    连接。重复键以最后一次出现的值为准。

    Args:
        text: 待解析的 Markdown 文本。

    Returns:
        解析出的字符串键值；文本不以 ``---`` 开头或找不到后续 ``---`` 时为空
        字典。
    """

    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}

    lines = parts[1].splitlines()
    parsed: dict[str, str] = {}
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if not line or line.startswith("#") or line.startswith("- "):
            index += 1
            continue
        if ":" not in line:
            index += 1
            continue

        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if value in ("|", ">", "|-", "|+", ">-", ">+"):
            block: list[str] = []
            index += 1
            while index < len(lines):
                if not lines[index].strip():
                    index += 1
                    continue
                if not (lines[index].startswith(" ") or lines[index].startswith("\t")):
                    break
                block.append(lines[index].strip())
                index += 1
            parsed[key] = " ".join(block)
            continue

        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        parsed[key] = value
        index += 1
    return parsed
