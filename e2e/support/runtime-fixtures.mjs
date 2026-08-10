/** 创建使用仓内真实录制 shape 的 Claude 与 Codex 可执行 runtime fixture。 */

import { chmod, mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const supportDirectory = path.dirname(fileURLToPath(import.meta.url));
const repositoryRoot = path.resolve(supportDirectory, "../..");

/**
 * 在单次隔离根中创建两种 runtime CLI，并返回 sidecar 应继承的 PATH。
 *
 * @param {string} root E2E 临时根。
 * @param {string} fallbackPath 宿主原 PATH，保留 Python、shell 等系统工具。
 */
export async function createRuntimeFixtures(root, fallbackPath) {
  const binDirectory = path.join(root, "runtime-bin");
  await mkdir(binDirectory, { recursive: true });
  const claudeExecutable = path.join(binDirectory, "claude");
  const codexExecutable = path.join(binDirectory, "codex");
  const recordedApproval = JSON.parse(
    await readFile(
      path.join(
        repositoryRoot,
        "tests/codex_host/fixtures/server-request-approval.jsonl",
      ),
      "utf8",
    ),
  );
  const recordedClaude = JSON.parse(
    await readFile(
      path.join(
        repositoryRoot,
        "e2e/fixtures/claude-ask-user-question-2.1.197.json",
      ),
      "utf8",
    ),
  );
  const recordedModelList = JSON.parse(
    await readFile(
      path.join(repositoryRoot, "tests/codex_host/fixtures/model-list-0.144.0.json"),
      "utf8",
    ),
  );
  const recordedCodex = JSON.parse(
    await readFile(
      path.join(repositoryRoot, "e2e/fixtures/codex-app-server-0.144.0.json"),
      "utf8",
    ),
  );
  await Promise.all([
    writeExecutable(claudeExecutable, claudeScript(recordedClaude.events)),
    writeExecutable(
      codexExecutable,
      codexScript(recordedApproval, recordedModelList, recordedCodex.events),
    ),
  ]);
  return {
    binDirectory,
    claudeExecutable,
    codexExecutable,
    path: `${binDirectory}${path.delimiter}${fallbackPath}`,
  };
}

/** 写入只对当前测试用户可执行的 fixture。 */
async function writeExecutable(filePath, body) {
  await writeFile(filePath, body, { encoding: "utf8", mode: 0o700 });
  await chmod(filePath, 0o700);
}

/** 返回等待 AskUserQuestion 回包后才结束 turn 的 stream-json CLI。 */
function claudeScript(recordedEvents) {
  return `#!/usr/bin/env node
if (process.argv.includes("--output-format") && process.argv.includes("json")) {
  const title = ${JSON.stringify(recordedEvents.title_response)};
  title.structured_output.title = "E2E Claude session";
  process.stdout.write(JSON.stringify(title) + "\\n");
  process.exit(0);
}
const readline = require("node:readline");
const fs = require("node:fs");
const path = require("node:path");
const recordedEvents = ${JSON.stringify(recordedEvents)};
const sessionId = "e2e-claude-" + process.pid;
const ordinal = claimOrdinal();
let requestNumber = 0;
let pending = null;
const send = (value) => process.stdout.write(JSON.stringify(value) + "\\n");
const instantiate = (value, replacements) => JSON.parse(
  JSON.stringify(value).replace(/\\$[A-Z_]+/g, (token) => replacements[token] ?? token),
);
const finish = () => {
  send(instantiate(recordedEvents.assistant_text, {
    "$SESSION_ID": sessionId,
    "$MESSAGE_ID": "msg-e2e-" + requestNumber,
    "$TEXT": "E2E_CLAUDE_COMPLETED",
  }));
  send(instantiate(recordedEvents.result, { "$SESSION_ID": sessionId }));
  pending = null;
};
const fail = (errors) => {
  send(instantiate(recordedEvents.error_result, {
    "$SESSION_ID": sessionId,
    "$ERROR": errors[0] ?? "recorded fixture runtime failure",
  }));
};
function claimOrdinal() {
  const root = process.env.TROWEL_E2E_RUNTIME_STATE;
  if (!root) return 1;
  for (let index = 1; index <= 32; index += 1) {
    try {
      const descriptor = fs.openSync(path.join(root, "claude-" + index), "wx");
      fs.closeSync(descriptor);
      return index;
    } catch (error) {
      if (error.code !== "EEXIST") throw error;
    }
  }
  throw new Error("could not claim a Claude E2E ordinal");
}
readline.createInterface({ input: process.stdin }).on("line", (line) => {
  let message;
  try { message = JSON.parse(line); } catch { return; }
  if (message.type === "control_response" && pending !== null) {
    const response = message.response;
    const resolution = response?.response;
    const updatedInput = resolution?.updatedInput;
    const question = pending.questions[0]?.question;
    if (
      response?.subtype === "success" &&
      response.request_id === pending.requestId &&
      resolution?.behavior === "allow" &&
      JSON.stringify(updatedInput?.questions) === JSON.stringify(pending.questions) &&
      typeof question === "string" &&
      updatedInput?.answers?.[question] === "Continue"
    ) finish();
    return;
  }
  if (message.type !== "user") return;
  requestNumber += 1;
  send(instantiate(recordedEvents.init, {
    "$SESSION_ID": sessionId,
    "$CWD": process.cwd(),
  }));
  const serializedInput = JSON.stringify(message);
  if (serializedInput.includes("E2E_DISCUSSION_ERROR") && ordinal === 2) {
    fail(["recorded fixture runtime failure"]);
    return;
  }
  if (serializedInput.includes("E2E_DISCUSSION_LIMIT") && ordinal === 2) {
    fail(["429 usage limit from recorded fixture"]);
    return;
  }
  if (
    serializedInput.includes("E2E_DISCUSSION_PROCESS_RESTART") &&
    (ordinal === 2 || ordinal === 3)
  ) {
    send(instantiate(recordedEvents.assistant_text, {
      "$SESSION_ID": sessionId,
      "$MESSAGE_ID": "msg-e2e-partial-" + requestNumber,
      "$TEXT": "E2E_OLD_PARTIAL_MUST_NOT_PUBLISH",
    }));
    return;
  }
  const asksDiscussionQuestion =
    serializedInput.includes("E2E_DISCUSSION_APPROVAL") && ordinal === 2;
  if (
    !serializedInput.includes("E2E_CLAUDE_ASK") &&
    !asksDiscussionQuestion
  ) {
    finish();
    return;
  }
  const requestId = "request-e2e-" + requestNumber;
  const request = instantiate(recordedEvents.control_request, {
    "$REQUEST_ID": requestId,
    "$TOOL_USE_ID": "tool-e2e-" + requestNumber,
  });
  send(request);
  pending = {
    requestId,
    questions: request.request.input.questions,
  };
});
for (const signal of ["SIGINT", "SIGTERM", "SIGHUP"]) {
  process.on(signal, () => process.exit(0));
}
`;
}

/** 返回支持模型目录、thread、turn 和真实 command approval shape 的 app-server。 */
function codexScript(recordedApproval, recordedModelList, recordedEvents) {
  return `#!/usr/bin/env node
const fs = require("node:fs");
const path = require("node:path");
const readline = require("node:readline");
if (process.argv.includes("--version")) {
  process.stdout.write("codex-cli 0.144.0\\n");
  process.exit(0);
}
const recordedApproval = ${JSON.stringify(recordedApproval)};
const recordedModelList = ${JSON.stringify(recordedModelList)};
const recordedEvents = ${JSON.stringify(recordedEvents)};
let threadNumber = 0;
let turnNumber = 0;
const titleThreads = new Set();
const pendingApprovals = new Map();
const threadStateFile = process.env.TROWEL_E2E_RUNTIME_STATE
  ? path.join(process.env.TROWEL_E2E_RUNTIME_STATE, "codex-threads.json")
  : null;
const threads = new Map();
const send = (value) => process.stdout.write(JSON.stringify(value) + "\\n");
const instantiate = (value, replacements) => JSON.parse(
  JSON.stringify(value).replace(/\\$[A-Z_]+/g, (token) => replacements[token] ?? token),
);
const reloadThreads = () => {
  if (!threadStateFile) return;
  try {
    const stored = JSON.parse(fs.readFileSync(threadStateFile, "utf8"));
    threads.clear();
    for (const [threadId, thread] of Object.entries(stored)) threads.set(threadId, thread);
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
};
const persistThreads = () => {
  if (!threadStateFile) return;
  const temporary = threadStateFile + "." + process.pid + ".tmp";
  fs.writeFileSync(temporary, JSON.stringify(Object.fromEntries(threads)), "utf8");
  fs.renameSync(temporary, threadStateFile);
};
reloadThreads();
const threadResponse = (requestId, threadId, cwd, ephemeral) => {
  const response = instantiate(recordedEvents.thread_response, {
    "$REQUEST_ID": String(requestId),
    "$THREAD_ID": threadId,
    "$CWD": cwd,
  });
  response.id = requestId;
  response.result.thread.ephemeral = ephemeral;
  return response;
};
readline.createInterface({ input: process.stdin }).on("line", (line) => {
  let message;
  try { message = JSON.parse(line); } catch { return; }
  if (
    message.result?.decision === "cancel" &&
    pendingApprovals.has(message.id)
  ) {
    const pending = pendingApprovals.get(message.id);
    pendingApprovals.delete(message.id);
    const thread = threads.get(pending.threadId);
    const turn = thread && thread.turns.find((item) => item.id === pending.turnId);
    if (turn) turn.status = "completed";
    persistThreads();
    send(instantiate(recordedEvents.turn_completed, {
      "$THREAD_ID": pending.threadId,
      "$TURN_ID": pending.turnId,
    }));
    return;
  }
  if (Object.prototype.hasOwnProperty.call(message, "result")) return;
  const method = message.method;
  if (method === "initialized") return;
  if (!Object.prototype.hasOwnProperty.call(message, "id")) return;
  if (method === "initialize") {
    const response = instantiate(recordedEvents.initialize_response, {
      "$REQUEST_ID": String(message.id),
    });
    response.id = message.id;
    send(response);
    return;
  }
  if (method === "model/list") {
    send({ id: message.id, result: recordedModelList });
    return;
  }
  if (method === "thread/list") {
    const response = instantiate(recordedEvents.thread_list_response, {
      "$REQUEST_ID": String(message.id),
    });
    response.id = message.id;
    send(response);
    return;
  }
  if (method === "thread/start" || method === "thread/resume") {
    const threadId = message.params?.threadId ||
      "e2e-thread-" + process.pid + "-" + (++threadNumber);
    const cwd = message.params?.cwd || process.cwd();
    const ephemeral = message.params?.ephemeral === true;
    if (!threads.has(threadId)) threads.set(threadId, { id: threadId, cwd, ephemeral, turns: [] });
    persistThreads();
    if (message.params?.ephemeral === true || message.params?.model === "gpt-5.6-luna") {
      titleThreads.add(threadId);
    }
    send(threadResponse(message.id, threadId, cwd, ephemeral));
    return;
  }
  if (method === "thread/read") {
    reloadThreads();
    const threadId = message.params.threadId;
    const stored = threads.get(threadId) || {
      id: threadId,
      cwd: process.cwd(),
      ephemeral: false,
      turns: [],
    };
    const response = instantiate(recordedEvents.thread_read_response, {
      "$REQUEST_ID": String(message.id),
      "$THREAD_ID": threadId,
      "$CWD": stored.cwd,
    });
    response.id = message.id;
    response.result.thread.ephemeral = stored.ephemeral;
    response.result.thread.turns = stored.turns;
    send(response);
    return;
  }
  if (method === "thread/archive") {
    const threadId = message.params.threadId;
    const stored = threads.get(threadId);
    const template = stored?.ephemeral
      ? recordedEvents.thread_archive_missing_rollout_error
      : recordedEvents.thread_archive_response;
    const response = instantiate(template, {
      "$REQUEST_ID": String(message.id),
      "$THREAD_ID": threadId,
    });
    response.id = message.id;
    send(response);
    return;
  }
  if (method === "thread/unarchive") {
    const threadId = message.params.threadId;
    const stored = threads.get(threadId) || { cwd: process.cwd() };
    const response = instantiate(recordedEvents.thread_unarchive_response, {
      "$REQUEST_ID": String(message.id),
      "$THREAD_ID": threadId,
      "$CWD": stored.cwd,
    });
    response.id = message.id;
    send(response);
    return;
  }
  if (method === "turn/start") {
    const threadId = message.params.threadId;
    const turnId = "e2e-turn-" + process.pid + "-" + (++turnNumber);
    const input = Array.isArray(message.params.input) ? message.params.input : [];
    const userText = input
      .map((item) => item && typeof item.text === "string" ? item.text : "")
      .filter(Boolean)
      .join("\\n");
    const thread = threads.get(threadId) || {
      id: threadId,
      cwd: message.params.cwd || process.cwd(),
      ephemeral: false,
      turns: [],
    };
    if (!threads.has(threadId)) threads.set(threadId, thread);
    const turn = instantiate(recordedEvents.turn_state, { "$TURN_ID": turnId });
    if (userText) {
      turn.items.push(instantiate(recordedEvents.user_message_item, {
        "$ITEM_ID": "e2e-user-" + turnNumber,
        "$TEXT": userText,
      }));
    }
    thread.turns.push(turn);
    persistThreads();
    const turnResponse = instantiate(recordedEvents.turn_start_response, {
      "$REQUEST_ID": String(message.id),
      "$TURN_ID": turnId,
    });
    turnResponse.id = message.id;
    send(turnResponse);
    if (titleThreads.has(threadId)) {
      thread.turns[thread.turns.length - 1].status = "completed";
      thread.turns[thread.turns.length - 1].items.push(
        instantiate(recordedEvents.agent_message_item, {
          "$ITEM_ID": "e2e-title-message",
          "$TEXT": "{\\\"title\\\":\\\"E2E Codex session\\\"}",
        }),
      );
      persistThreads();
      send(instantiate(recordedEvents.item_completed, {
        "$THREAD_ID": threadId,
        "$TURN_ID": turnId,
        "$ITEM_ID": "e2e-title-message",
        "$TEXT": "{\\\"title\\\":\\\"E2E Codex session\\\"}",
      }));
      send(instantiate(recordedEvents.turn_completed, {
        "$THREAD_ID": threadId,
        "$TURN_ID": turnId,
      }));
      return;
    }
    const approvalId = 0;
    pendingApprovals.set(approvalId, { threadId, turnId });
    send({
      ...recordedApproval,
      id: approvalId,
      params: {
        ...recordedApproval.params,
        threadId,
        turnId,
        itemId: "e2e-command-" + turnNumber,
        cwd: message.params.cwd || process.cwd(),
      },
    });
    return;
  }
  // 未录制的方法不猜测成功回包；调用方会按自己的有界超时暴露协议缺口。
});
for (const signal of ["SIGINT", "SIGTERM", "SIGHUP"]) {
  process.on(signal, () => process.exit(0));
}
`;
}
