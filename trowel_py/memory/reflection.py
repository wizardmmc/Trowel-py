"""the review-reflection prompt template (slice-038).

This is the online recall-miss detector — but it does NOT run on its own. It is
one step inside the daily write-review job (040): after replaying a session,
the model asks itself whether it took a detour because it failed to use an
existing note. The raw access/outcome logs (``logging.py``) are the insurance it
can fall back to.

038 only pins WHAT to ask; 040 runs it. LLM-judge hallucination risk (S4) is
mitigated three ways: this is reflection not a hard count, the raw log is the
recomputeable insurance, and the offline eval gives an objective cross-check.
"""
from __future__ import annotations

#: The reflection prompt. {session_summary} / {existing_notes_index} are filled
#: by the write-review job (040) at run time.
REFLECTION_PROMPT_TEMPLATE = """\
你在做每日温故反思（recall 复盘）。先读这个会话的经过，再对照已存在的笔记索引，
回答一个狠问题：这个会话里，有没有「已存在笔记」我没用上、导致绕了弯路？

【会话经过】
{session_summary}

【已存在笔记索引（dictionary）】
{existing_notes_index}

请输出：
1. 是否存在「已存在笔记没用上导致绕弯路」的情况（是/否）。
2. 若是，列出那条没被用上的笔记 id，以及绕弯路的具体证据（会话里哪一步本可避免）。
3. 归因：是目录结构/dictionary 让你没找到（召回 miss），还是你找到了但没意识到能用（注入/意识问题），还是当时根本没相关笔记（新颖问题，该写入而非改结构）。

只基于会话与索引里的客观事实判断，不臆测。给不出证据就标「无定论」，不要硬编。
"""
