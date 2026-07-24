"""Notes 与 diary 测试数据。"""


def _valid_note(**over) -> dict:
    base = {
        "type": "note",
        "title": "浏览器缓存导致 build 不生效",
        "tags": ["frontend", "build"],
        "summary": "build 没生效多半是浏览器缓存",
        "confidence": "evolving",
        "verification": "event-data-supported",
        "refs": 0,
        "last_ref": "",
        "retired": False,
        "pain": 2,
        "created": "2026-07-08",
        "updated": "2026-07-08",
    }
    base.update(over)
    return base


def _diary_text(date: str, layer: str) -> str:
    return (
        "---\n"
        f"type: diary\ndate: '{date}'\nlayer: {layer}\n"
        f"period: '{date}'\npromoted_knowledge: []\n"
        "---\nbody\n"
    )
