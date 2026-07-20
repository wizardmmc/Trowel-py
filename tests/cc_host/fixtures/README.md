# cc_host fixtures (slice-077-prefix)

Raw, redacted CC stream-json recordings used by the background-task tests.

## `bg_local_bash_completed.jsonl`

A `local_bash` background task that completes successfully, used to pin the
"mid-turn result does not end the logical turn" invariant (slice-077-prefix
失败测试 1 / 通过标准 1).

**Event order** (the slice's 隔离复现 C, recorded against real CC):

```
system/init
assistant("后台跑着，等通知")
assistant tool_use(Bash, "sleep 6")
user tool_result("Command running in background. Task ID: b7cgk2tn3")
system/task_started(task_id=b7cgk2tn3, tool_use_id=call_bg1, task_type=local_bash)
result/success                         ← mid-turn boundary (task still pending)
system/task_updated(task_id=b7cgk2tn3)
system/task_notification(task_id=b7cgk2tn3, status=completed)
assistant("DONE")                      ← CC's auto-continuation after the notification
result/success                         ← logical turn terminal
```

**Recording conditions:**
- CC version: 2.1.197 (the version this machine runs)
- Mode: stream-json, isolated non-Git workdir, no-op session registrar
- Source: slice-077-prefix §"隔离复现 C" (the raw order is recorded there from
  a real CC run; this file turns that order into a replayable, redacted
  stream-json fixture)

**Redaction:** no real paths, keys, or large tool outputs. Task ids and
tool_use ids are stable identifiers kept verbatim (they are the tracking key
— replacing them with placeholders would defeat the test). Event shapes come
from `tests/cc_host/test_translator.py` sample 030 (real reverse_cc capture)
and `docs/design/front-end/cc-workflow-event-model.md`.

**Blocked (not recorded, not guessed — slice 测试方法):**
- `local_bash` failed / cancelled terminal status
- `local_agent` (Agent tool) background completion
- two truly concurrent background tasks (the two-task unit test reconstructs
  the order from the single-task recording + slice §通过标准 5)
- interrupt mid-background

These stay as unit tests only (constructed from the confirmed status values
completed / failed / cancelled); real end-to-end fixtures are deferred until
the corresponding CC scenarios can be recorded.
