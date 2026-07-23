import { describe, it, expect } from "vitest";
import {
  reduceEvent,
  INITIAL_REDUCER_STATE,
  withOpenTurn,
  installReducerTestReset,
} from "./support";

installReducerTestReset();

describe("reduceEvent — tasks", () => {
  it("TaskCreate tool_call appends a pending task (taskId null until result)", () => {
    let state = withOpenTurn("do stuff");
    state = reduceEvent(state, {
      type: "tool_call",
      tool_use_id: "tu_1",
      tool_name: "TaskCreate",
      input: {
        subject: "后端多开 API",
        description: "registry + workdir 索引",
        activeForm: "写后端多开",
      },
    });
    expect(state.tasks).toHaveLength(1);
    const task = state.tasks[0];
    expect(task).toMatchObject({
      subject: "后端多开 API",
      description: "registry + workdir 索引",
      activeForm: "写后端多开",
      status: "pending",
      toolUseId: "tu_1",
      taskId: null,
    });
  });

  it("TaskCreate tool_call still appends a ToolItem (message stream keeps it)", () => {
    let state = withOpenTurn();
    state = reduceEvent(state, {
      type: "tool_call",
      tool_use_id: "tu_1",
      tool_name: "TaskCreate",
      input: { subject: "x" },
    });
    const last = state.turns[state.turns.length - 1];
    expect(
      last.items.some((i) => i.kind === "tool" && i.toolName === "TaskCreate"),
    ).toBe(true);
  });

  it("TaskCreate tool_result assigns taskId parsed from 'Task #N created'", () => {
    let state = withOpenTurn();
    state = reduceEvent(state, {
      type: "tool_call",
      tool_use_id: "tu_1",
      tool_name: "TaskCreate",
      input: { subject: "研究" },
    });
    state = reduceEvent(state, {
      type: "tool_result",
      tool_use_id: "tu_1",
      content: "Task #3 created successfully: 研究",
    });
    expect(state.tasks[0].taskId).toBe("3");
  });

  it("TaskUpdate tool_call flips the matching task's status", () => {
    let state = withOpenTurn();
    state = reduceEvent(state, {
      type: "tool_call",
      tool_use_id: "tu_1",
      tool_name: "TaskCreate",
      input: { subject: "a" },
    });
    state = reduceEvent(state, {
      type: "tool_result",
      tool_use_id: "tu_1",
      content: "Task #1 created successfully: a",
    });
    state = reduceEvent(state, {
      type: "tool_call",
      tool_use_id: "tu_2",
      tool_name: "TaskUpdate",
      input: { taskId: "1", status: "in_progress" },
    });
    expect(state.tasks[0].status).toBe("in_progress");
  });

  it("TaskUpdate completed transitions stick", () => {
    let state = withOpenTurn();
    state = reduceEvent(state, {
      type: "tool_call",
      tool_use_id: "tu_1",
      tool_name: "TaskCreate",
      input: { subject: "a" },
    });
    state = reduceEvent(state, {
      type: "tool_result",
      tool_use_id: "tu_1",
      content: "Task #1 created successfully: a",
    });
    state = reduceEvent(state, {
      type: "tool_call",
      tool_use_id: "tu_2",
      tool_name: "TaskUpdate",
      input: { taskId: "1", status: "completed" },
    });
    expect(state.tasks[0].status).toBe("completed");
  });

  it("multiple tasks: each TaskCreate result assigns its own taskId", () => {
    let state = withOpenTurn();
    state = reduceEvent(state, {
      type: "tool_call",
      tool_use_id: "tu_1",
      tool_name: "TaskCreate",
      input: { subject: "a" },
    });
    state = reduceEvent(state, {
      type: "tool_call",
      tool_use_id: "tu_2",
      tool_name: "TaskCreate",
      input: { subject: "b" },
    });
    state = reduceEvent(state, {
      type: "tool_result",
      tool_use_id: "tu_1",
      content: "Task #1 created successfully: a",
    });
    state = reduceEvent(state, {
      type: "tool_result",
      tool_use_id: "tu_2",
      content: "Task #2 created successfully: b",
    });
    expect(state.tasks.map((t) => t.taskId)).toEqual(["1", "2"]);
    expect(state.tasks.map((t) => t.subject)).toEqual(["a", "b"]);
  });

  it("TaskUpdate for an unknown taskId is a no-op (no crash)", () => {
    let state = withOpenTurn();
    state = reduceEvent(state, {
      type: "tool_call",
      tool_use_id: "tu_1",
      tool_name: "TaskCreate",
      input: { subject: "a" },
    });
    const before = state.tasks;
    state = reduceEvent(state, {
      type: "tool_call",
      tool_use_id: "tu_2",
      tool_name: "TaskUpdate",
      input: { taskId: "999", status: "in_progress" },
    });
    expect(state.tasks).toBe(before);
  });

  it("tasks persist across turns (session-scoped, not reset on new user turn)", () => {
    let state = withOpenTurn("q1");
    state = reduceEvent(state, {
      type: "tool_call",
      tool_use_id: "tu_1",
      tool_name: "TaskCreate",
      input: { subject: "a" },
    });
    state = reduceEvent(state, { type: "user", text: "q2" });
    expect(state.tasks).toHaveLength(1);
    expect(state.tasks[0].subject).toBe("a");
  });

  it("a non-task tool_result does not disturb tasks", () => {
    let state = withOpenTurn();
    state = reduceEvent(state, {
      type: "tool_call",
      tool_use_id: "tu_1",
      tool_name: "TaskCreate",
      input: { subject: "a" },
    });
    state = reduceEvent(state, {
      type: "tool_call",
      tool_use_id: "bash_1",
      tool_name: "Bash",
      input: { command: "ls" },
    });
    state = reduceEvent(state, {
      type: "tool_result",
      tool_use_id: "bash_1",
      content: "file.txt",
    });
    expect(state.tasks).toHaveLength(1);
    expect(state.tasks[0].taskId).toBeNull();
  });
});

describe("reduceEvent — session_exited", () => {
  it("marks meta.exited + returncode", () => {
    const state = reduceEvent(INITIAL_REDUCER_STATE, {
      type: "session_exited",
      returncode: 0,
    });
    expect(state.meta.exited).toBe(true);
    expect(state.meta.exitReturncode).toBe(0);
  });

  it("does not drop turns or tasks (exited is a session-lifecycle flag)", () => {
    let state = withOpenTurn("hi");
    state = reduceEvent(state, {
      type: "tool_call",
      tool_use_id: "tu_1",
      tool_name: "TaskCreate",
      input: { subject: "a" },
    });
    const before = state.turns.length;
    state = reduceEvent(state, { type: "session_exited", returncode: 0 });
    expect(state.turns).toHaveLength(before);
    expect(state.tasks).toHaveLength(1);
    expect(state.meta.exited).toBe(true);
  });
});
