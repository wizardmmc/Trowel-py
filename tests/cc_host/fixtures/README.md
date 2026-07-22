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
user tool_result("Command running in background. Task ID: task_fixture_local_bash")
system/task_started(task_id=task_fixture_local_bash, tool_use_id=tool_fixture_local_bash, task_type=local_bash)
result/success                         ← mid-turn boundary (task still pending)
system/task_updated(task_id=task_fixture_local_bash)
system/task_notification(task_id=task_fixture_local_bash, status=completed)
assistant("DONE")                      ← CC's auto-continuation after the notification
result/success                         ← logical turn terminal
```

**Recording conditions:**
- CC version: 2.1.197 (the version this machine runs)
- Mode: stream-json, isolated non-Git workdir, no-op session registrar
- Source: slice-077-prefix §"隔离复现 C" (the raw order is recorded there from
  a real CC run; this file turns that order into a replayable, redacted
  stream-json fixture)

**Redaction:** no real paths, keys, or large tool outputs. Session, task and
tool-use identifiers, model names and telemetry are deterministic synthetic
values; equality relationships between identifiers are preserved so tracking
behavior remains testable. Event shapes come from
`tests/cc_host/test_translator.py` sample 030 (real reverse_cc capture) and
`docs/design/front-end/cc-workflow-event-model.md`.

**Blocked (not recorded, not guessed — slice 测试方法):**
- `local_bash` failed / cancelled terminal status
- `local_agent` (Agent tool) background completion
- two truly concurrent background tasks (the two-task unit test reconstructs
  the order from the single-task recording + slice §通过标准 5)
- interrupt mid-background

These stay as unit tests only (constructed from the confirmed status values
completed / failed / cancelled); real end-to-end fixtures are deferred until
the corresponding CC scenarios can be recorded.

## `bg_taskoutput_completed.jsonl`

A real CC 2.1.197 recording of the terminal path that does **not** emit
`system/task_notification`: Bash starts in the background and the assistant
immediately waits with `TaskOutput(block=true)`. CC reports completion through
`system/task_updated(patch.status=completed)` and the `TaskOutput` tool result,
then emits the final assistant text and `result/success`.

```text
system/task_started(task_id=task_fixture_background)
assistant tool_use(TaskOutput, block=true)
system/task_updated(task_id=task_fixture_background, patch.status=completed)
user tool_result(TaskOutput, status=completed, exit_code=0)
assistant("EXACT_DONE")
result/success
```

**Recording conditions:**
- CC version: 2.1.197
- Recorded: 2026-07-23 through trowel's stable local reverse proxy
- Isolated temporary non-Git workdir; no memory/profile/session registrar
- Raw source: `/tmp/trowel-bg-taskoutput-raw-20260723.jsonl` during diagnosis

The fixture is a selected, fully anonymized subset of that raw stdout
recording. Machine paths and unrelated thinking/ToolSearch events were
removed; session/message/task/tool ids, timestamps, model names, cost and token
telemetry were replaced with deterministic synthetic values. Only the event
order, field shape and terminal status semantics are preserved. In particular, a
`system/task_notification` line was not removed—it did not exist in the raw
recording.
