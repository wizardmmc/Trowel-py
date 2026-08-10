/** 封装 Agent 用户链中的工作区、会话、turn 和三层资源断言。 */

import path from "node:path";
import { expect } from "@playwright/test";
import {
  assertSessionResourcesClosed,
  liveTurnResourceCount,
  snapshotRecords,
} from "./resource-assertions.mjs";

/** 从 Agent 首页的 Recent 工作区创建指定 runtime 会话。 */
export async function createAgentSessionFromUi(desktop, runtime) {
  await desktop.api.post("/api/agent/workspaces/recent", {
    path: desktop.environment.workspaceDirectory,
  });
  const saved = await desktop.api.get("/api/agent/workspaces/recent");
  if (!Array.isArray(saved) || !saved.some((item) => item.available)) {
    throw new Error("production Recent API did not preserve the E2E workspace");
  }
  // AgentWorkspace 会在首次挂载时读取 Recent；seed 完成后重载，建立干净的用户起点。
  await desktop.page.reload();
  await desktop.page.getByRole("button", { name: "Agent" }).waitFor();
  desktop.trace.record("setup.renderer_reloaded");
  await desktop.page.getByRole("button", { name: "Agent" }).click();
  const workspaceName = path.basename(desktop.environment.workspaceDirectory);
  const start = desktop.page.getByRole("region", { name: "Agent 开始页" });
  await start.getByRole("button", { name: new RegExp(workspaceName) }).click();
  const workspace = desktop.page.getByRole("region", { name: "当前工作区" });
  await workspace.getByRole("button", { name: "新建会话" }).click();
  const dialog = desktop.page.getByRole("dialog", { name: "新建 Agent 会话" });
  await dialog.waitFor();
  const runtimeLabel = runtime === "claude_code" ? /^Claude Code / : /^Codex /;
  await dialog.getByRole("radio", { name: runtimeLabel }).click();
  const connectionName = runtime === "claude_code" ? "E2E Claude" : "E2E Codex";
  await dialog.getByRole("button", { name: connectionName, exact: true }).click();
  await dialog
    .getByRole("button", {
      name: runtime === "claude_code" ? "创建 Claude Code 会话" : "创建 Codex 会话",
    })
    .click();
  await desktop.page.getByRole("textbox", { name: "Agent 消息输入" }).waitFor();
  const active = await desktop.api.get("/api/agent/sessions/active");
  const session = active.sessions.find((item) => item.session_id === active.active_id);
  if (!session || session.runtime !== runtime) {
    throw new Error(`UI did not create the requested ${runtime} session`);
  }
  desktop.trace.record("session.created", {
    session_id: session.session_id,
    runtime: session.runtime,
    workdir: session.workdir,
    connection_id: session.connection_id,
  });
  return session;
}

/** 发送一轮并从同一生产 HTTP 响应取得 accepted turn ID。 */
export async function sendAgentTurn(desktop, text) {
  const acceptedResponse = desktop.page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname.endsWith("/turns"),
  );
  const input = desktop.page.getByRole("textbox", { name: "Agent 消息输入" });
  await input.fill(text);
  await input.press("Enter");
  const envelope = await (await acceptedResponse).json();
  const turnId = envelope?.data?.turn_id;
  if (typeof turnId !== "string" || !turnId) {
    throw new Error("turn acceptance response omitted the stable turn ID");
  }
  desktop.trace.record("turn.accepted", { turn_id: turnId, prompt: text });
  return turnId;
}

/** 在 UI 完成后对账会话水位、悬挂交互和资源快照三种独立事实。 */
export async function assertTurnClosed(desktop, sessionId, turnId) {
  await desktop.page.locator('[data-turn-status="done"]').last().waitFor();
  const terminalStates = new Set(["completed", "failed", "interrupted"]);
  await expect
    .poll(async () => {
      const active = await desktop.api.get("/api/agent/sessions/active");
      const session = active.sessions.find((item) => item.session_id === sessionId);
      const requests = await desktop.api.get(
        `/api/agent/sessions/${sessionId}/requests`,
      );
      return {
        terminal: terminalStates.has(session?.turn_state),
        terminal_turn_id_matches: session?.current_turn_id === turnId,
        pending_requests: requests.requests.filter(
          (request) => request.turn_id === turnId && request.status === "pending",
        ).length,
      };
    })
    .toEqual({
      terminal: true,
      terminal_turn_id_matches: true,
      pending_requests: 0,
    });
  await expect(
    desktop.page.getByRole("textbox", { name: "Agent 消息输入" }),
  ).toBeEnabled();
  await expect
    .poll(async () =>
      liveTurnResourceCount(
        snapshotRecords(await desktop.environment.readResourceSnapshot()),
        turnId,
      ),
    )
    .toBe(0);
  const active = await desktop.api.get("/api/agent/sessions/active");
  const session = active.sessions.find((item) => item.session_id === sessionId);
  desktop.trace.record("turn.closed", {
    session_id: sessionId,
    turn_id: turnId,
    turn_state: session?.turn_state ?? "missing",
    pending_requests: 0,
  });
}

/** 通过会话栏关闭当前会话，并对账响应与资源快照。 */
export async function closeAgentSessionFromUi(desktop, sessionId) {
  const closeResponse = desktop.page.waitForResponse(
    (response) =>
      response.request().method() === "DELETE" &&
      new URL(response.url()).pathname.endsWith(`/sessions/${sessionId}`),
  );
  await desktop.page.getByRole("button", { name: /^关闭 / }).click();
  const envelope = await (await closeResponse).json();
  if (
    envelope?.data?.status !== "closed" ||
    envelope?.data?.remaining_resource_count !== 0
  ) {
    throw new Error("session close did not return a verified zero-resource result");
  }
  assertSessionResourcesClosed(
    snapshotRecords(await desktop.environment.readResourceSnapshot()),
    sessionId,
  );
  desktop.trace.record("session.closed", {
    session_id: sessionId,
    status: envelope.data.status,
    remaining_resource_count: envelope.data.remaining_resource_count,
  });
}
