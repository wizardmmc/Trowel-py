/** 验证可执行 fixture 按真实 Claude stream-json 与 Codex app-server shape 对话。 */

import { afterEach, describe, expect, test } from "bun:test";
import { spawn } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { createRuntimeFixtures } from "./runtime-fixtures.mjs";

const temporaryRoots = [];

afterEach(async () => {
  await Promise.all(
    temporaryRoots.splice(0).map((root) => rm(root, { recursive: true, force: true })),
  );
});

describe("runtime executable fixtures", () => {
  test("Claude waits for the recorded AskUserQuestion control response", async () => {
    const root = await mkdtemp(path.join(os.tmpdir(), "trowel-runtime-test-"));
    temporaryRoots.push(root);
    const fixtures = await createRuntimeFixtures(root, process.env.PATH ?? "");
    const child = spawn(fixtures.claudeExecutable, ["-p"], {
      stdio: ["pipe", "pipe", "inherit"],
    });
    const lines = lineReader(child.stdout);
    child.stdin.write(`${JSON.stringify({ type: "user", message: "E2E_CLAUDE_ASK" })}\n`);

    expect((await lines.next()).value.type).toBe("system");
    const request = (await lines.next()).value;
    expect(request.type).toBe("control_request");
    expect(request.request.tool_name).toBe("AskUserQuestion");
    const premature = lines.next();
    child.stdin.write(
      `${JSON.stringify({
        type: "control_response",
        response: {
          subtype: "success",
          request_id: "wrong-request",
          response: { behavior: "allow" },
        },
      })}\n`,
    );
    child.stdin.write(
      `${JSON.stringify({
        type: "control_response",
        response: {
          subtype: "success",
          request_id: request.request_id,
          response: { behavior: "allow", updatedInput: { questions: [], answers: {} } },
        },
      })}\n`,
    );
    expect(await settlesWithin(premature, 50)).toBe(false);
    const questions = request.request.input.questions;
    child.stdin.write(
      `${JSON.stringify({
        type: "control_response",
        response: {
          subtype: "success",
          request_id: request.request_id,
          response: {
            behavior: "allow",
            updatedInput: {
              questions,
              answers: { [questions[0].question]: "Continue" },
              annotations: {},
            },
          },
        },
      })}\n`,
    );
    expect((await premature).value.type).toBe("assistant");
    expect((await lines.next()).value).toMatchObject({ type: "result", subtype: "success" });
    child.kill("SIGTERM");
  });

  test("Codex reuses the native approval request ID and completes the turn", async () => {
    const root = await mkdtemp(path.join(os.tmpdir(), "trowel-runtime-test-"));
    temporaryRoots.push(root);
    const fixtures = await createRuntimeFixtures(root, process.env.PATH ?? "");
    expect(await commandOutput(fixtures.codexExecutable, ["--version"])).toContain("0.144.0");
    const child = spawn(fixtures.codexExecutable, ["app-server", "--stdio"], {
      stdio: ["pipe", "pipe", "inherit"],
    });
    const lines = lineReader(child.stdout);
    child.stdin.write(`${JSON.stringify({ id: 1, method: "initialize", params: {} })}\n`);
    expect((await lines.next()).value).toMatchObject({ id: 1, result: { platformOs: "macos" } });
    child.stdin.write(`${JSON.stringify({ method: "initialized", params: {} })}\n`);
    child.stdin.write(`${JSON.stringify({ id: 2, method: "thread/start", params: { cwd: root } })}\n`);
    const thread = (await lines.next()).value.result.thread.id;
    child.stdin.write(
      `${JSON.stringify({
        id: 3,
        method: "turn/start",
        params: { threadId: thread, input: [{ type: "text", text: "run it" }] },
      })}\n`,
    );
    const turn = (await lines.next()).value.result.turn.id;
    const approval = (await lines.next()).value;
    expect(approval).toMatchObject({
      id: 0,
      method: "item/commandExecution/requestApproval",
      params: { threadId: thread, turnId: turn },
    });
    const premature = lines.next();
    child.stdin.write(`${JSON.stringify({ id: 0, result: { decision: "decline" } })}\n`);
    expect(await settlesWithin(premature, 50)).toBe(false);
    child.stdin.write(`${JSON.stringify({ id: 0, result: { decision: "cancel" } })}\n`);
    expect((await premature).value).toMatchObject({
      method: "turn/completed",
      params: { threadId: thread, turn: { id: turn, status: "completed" } },
    });
    child.stdin.write(
      `${JSON.stringify({ id: 4, method: "thread/read", params: { threadId: thread } })}\n`,
    );
    expect((await lines.next()).value).toMatchObject({
      id: 4,
      result: {
        thread: {
          id: thread,
          turns: [{
            id: turn,
            status: "completed",
            items: [{
              type: "userMessage",
              content: [{ type: "text", text: "run it" }],
            }],
          }],
        },
      },
    });
    child.stdin.write(
      `${JSON.stringify({ id: 5, method: "thread/archive", params: { threadId: thread } })}\n`,
    );
    expect((await lines.next()).value).toEqual({ id: 5, result: {} });
    child.stdin.write(
      `${JSON.stringify({ id: 6, method: "thread/unarchive", params: { threadId: thread } })}\n`,
    );
    expect((await lines.next()).value).toMatchObject({
      id: 6,
      result: {
        thread: {
          id: thread,
          status: { type: "notLoaded" },
          cwd: root,
          cliVersion: "0.144.0",
        },
      },
    });
    child.kill("SIGTERM");
  });

  test("Claude leaves only the selected restart attempts incomplete", async () => {
    const root = await mkdtemp(path.join(os.tmpdir(), "trowel-runtime-test-"));
    temporaryRoots.push(root);
    const fixtures = await createRuntimeFixtures(root, process.env.PATH ?? "");
    const environment = { ...process.env, TROWEL_E2E_RUNTIME_STATE: root };
    const first = spawn(fixtures.claudeExecutable, ["-p"], {
      stdio: ["pipe", "pipe", "inherit"],
      env: environment,
    });
    const firstLines = lineReader(first.stdout);
    first.stdin.write(
      `${JSON.stringify({ type: "user", message: "E2E_DISCUSSION_PROCESS_RESTART" })}\n`,
    );
    expect((await firstLines.next()).value.type).toBe("system");
    expect((await firstLines.next()).value.message.content[0].text).toBe(
      "E2E_CLAUDE_COMPLETED",
    );
    expect((await firstLines.next()).value.type).toBe("result");
    first.kill("SIGTERM");

    const second = spawn(fixtures.claudeExecutable, ["-p"], {
      stdio: ["pipe", "pipe", "inherit"],
      env: environment,
    });
    const secondLines = lineReader(second.stdout);
    second.stdin.write(
      `${JSON.stringify({ type: "user", message: "E2E_DISCUSSION_PROCESS_RESTART" })}\n`,
    );
    expect((await secondLines.next()).value.type).toBe("system");
    expect((await secondLines.next()).value.message.content[0].text).toBe(
      "E2E_OLD_PARTIAL_MUST_NOT_PUBLISH",
    );
    second.kill("SIGTERM");
  });
});

/** 把 stdout 转为逐行 JSON 异步生成器。 */
async function* lineReader(stream) {
  let buffer = "";
  for await (const chunk of stream) {
    buffer += chunk.toString("utf8");
    let newline;
    while ((newline = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, newline);
      buffer = buffer.slice(newline + 1);
      if (line) yield JSON.parse(line);
    }
  }
}

/** 执行短命 CLI 并返回 stdout。 */
async function commandOutput(executable, args) {
  const child = spawn(executable, args, { stdio: ["ignore", "pipe", "inherit"] });
  let output = "";
  for await (const chunk of child.stdout) output += chunk.toString("utf8");
  const code = await new Promise((resolve) => child.once("exit", resolve));
  if (code !== 0) throw new Error(`fixture command exited with ${code}`);
  return output;
}

/** 判断 pending 读取是否在给定窗口内错误地产生了终态。 */
async function settlesWithin(promise, timeoutMs) {
  return Promise.race([
    promise.then(() => true),
    new Promise((resolve) => setTimeout(() => resolve(false), timeoutMs)),
  ]);
}
