from __future__ import annotations

import json


def valid_draft_json() -> str:
    return json.dumps(
        {
            "notes": [
                {
                    "title": "浏览器缓存导致 build 不生效",
                    "verification": "event-data-supported",
                    "pain": 3,
                    "tags": ["frontend"],
                }
            ],
            "diary": [
                {
                    "date": "2026-07-09",
                    "open_loops": ["浏览器缓存问题仍待处理"],
                }
            ],
            "reflection": "无绕弯",
            "escalate_to_human": [],
        }
    )


def structured_diary_json() -> str:
    return json.dumps(
        {
            "diary": [
                {
                    "date": "2026-07-17",
                    "outcomes": ["完成了 daily 重写", "验证到全量测试通过"],
                    "decisions": ["固定三问结构（进展/更正/待续）"],
                    "corrections": ["原来以为单 $ 零误伤 -> 实测就近配对吞整段"],
                    "open_loops": ["weekly 表达重写未做"],
                }
            ]
        }
    )
