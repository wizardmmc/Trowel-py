"""解析 CC skill 与 slash command 使用的轻量 frontmatter。"""

from __future__ import annotations


def parse_frontmatter(text: str) -> dict[str, str]:
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
