"""把不同会话运行时的增量记录适配为 Profile 提炼候选。"""

from trowel_py.profile.distill.adapters.claude import (
    ClaudeDistillCandidate as ClaudeDistillCandidate,
    build_claude_backlog as build_claude_backlog,
)
from trowel_py.profile.distill.adapters.codex import (
    CodexDistillCandidate as CodexDistillCandidate,
    build_codex_backlog as build_codex_backlog,
)
from trowel_py.profile.distill.adapters.discussion import (
    DiscussionDistillCandidate as DiscussionDistillCandidate,
    build_discussion_backlog as build_discussion_backlog,
)

__all__ = [
    "ClaudeDistillCandidate",
    "CodexDistillCandidate",
    "DiscussionDistillCandidate",
    "build_claude_backlog",
    "build_codex_backlog",
    "build_discussion_backlog",
]
