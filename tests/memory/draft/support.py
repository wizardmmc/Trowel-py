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
                    "items": [
                        {
                            "kind": "open_loop",
                            "summary": "浏览器缓存问题仍待处理",
                            "reason": "尚未完成复测",
                            "status": "active",
                            "source_refs": ["L000001"],
                        }
                    ],
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
                    "items": [
                        {
                            "kind": "outcome",
                            "summary": "完成了 daily 重写",
                            "detail": "",
                            "source_refs": ["L000001"],
                        },
                        {
                            "kind": "outcome",
                            "summary": "验证到全量测试通过",
                            "detail": "",
                            "source_refs": ["L000002"],
                        },
                        {
                            "kind": "decision",
                            "summary": "固定三问结构（进展/更正/待续）",
                            "reason": "方便第二天恢复",
                            "status": "active",
                            "source_refs": ["L000003"],
                        },
                        {
                            "kind": "correction",
                            "before": "单 $ 零误伤",
                            "after": "实测就近配对吞整段",
                            "reason": "真实渲染复现",
                            "source_refs": ["L000004"],
                        },
                        {
                            "kind": "open_loop",
                            "summary": "weekly 表达重写未做",
                            "reason": "不在当前 slice",
                            "status": "active",
                            "source_refs": ["L000005"],
                        },
                    ],
                }
            ]
        }
    )
