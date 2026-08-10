/** 封装三位参与者研讨的创建、共同公开和资源收敛用户链。 */

import path from "node:path";
import {
  snapshotRecords,
} from "./resource-assertions.mjs";

/** 从研讨首页创建固定三位参与者，并自动开始第一轮。 */
export async function createThreeParticipantDiscussion(desktop, topic) {
  await desktop.api.post("/api/agent/workspaces/recent", {
    path: desktop.environment.workspaceDirectory,
  });
  await desktop.page.reload();
  await desktop.page.getByRole("button", { name: "研讨" }).click();
  desktop.trace.record("discussion.create_page_opened");
  await desktop.page
    .locator("main.discussion-blank")
    .getByRole("button", { name: "新建研讨", exact: true })
    .click();
  desktop.trace.record("discussion.create_dialog_opened");
  const dialog = desktop.page.getByRole("dialog", { name: "新建研讨" });
  await dialog.getByRole("textbox", { name: "讨论问题" }).fill(topic);
  const workspaceName = path.basename(desktop.environment.workspaceDirectory);
  await dialog.getByRole("button", { name: new RegExp(workspaceName) }).waitFor();
  desktop.trace.record("discussion.create_workspace_ready");

  for (let index = 0; index < 3; index += 1) {
    await dialog.getByRole("button", { name: "创建参与者" }).click();
    const editor = desktop.page.getByRole("dialog", { name: "创建参与者" });
    await editor.getByRole("button", { name: "E2E Claude", exact: true }).click();
    await editor.getByRole("button", { name: "保存参与者" }).click();
    desktop.trace.record("discussion.create_participant_saved", {
      participant_number: index + 1,
    });
  }
  const createRequest = observeDiscussionRequest(
    desktop.page,
    "create",
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname === "/api/discussions",
  );
  const startRequest = observeDiscussionRequest(
    desktop.page,
    "start",
    (response) =>
      response.request().method() === "POST" &&
      /^\/api\/discussions\/[^/]+\/start$/.test(new URL(response.url()).pathname),
  );
  await dialog.getByRole("button", { name: "创建并开始第 1 轮" }).click();
  desktop.trace.record("discussion.create_submitted");
  const rawCreateResponse = await requireDiscussionResponse(createRequest);
  const created = await successfulDiscussionResponse(
    rawCreateResponse,
    "create",
    desktop.trace,
  );
  desktop.trace.record("discussion.create_accepted", {
    discussion_id: created.id,
    status: created.status,
    participant_status_counts: countValues(
      created.participants.map((item) => item.status),
    ),
  });
  if (created.status !== "draft") {
    startRequest.cancel();
    throw new Error(`discussion_create_returned_${created.status}`);
  }
  const rawStartResponse = await requireDiscussionResponse(startRequest);
  const detail = await successfulDiscussionResponse(
    rawStartResponse,
    "start",
    desktop.trace,
  );
  if (detail.id !== created.id) {
    throw new Error("discussion start response changed the accepted discussion ID");
  }
  desktop.trace.record("discussion.start_accepted", {
    discussion_id: detail.id,
    status: detail.status,
  });
  desktop.trace.record("discussion.created", {
    discussion_id: detail.id,
    participant_count: detail.participants.length,
    topic,
    workdir: detail.workdir,
  });
  return detail;
}

/** 观察页面请求并提供显式取消，避免前序命令失败后留下超时 Promise。 */
function observeDiscussionRequest(page, operation, predicate) {
  let finish = () => {};
  const promise = new Promise((resolve) => {
    const timeout = setTimeout(
      () => finish({
        response: null,
        error: new Error(`discussion_${operation}_response_timeout`),
      }),
      30_000,
    );
    const onResponse = (response) => {
      if (predicate(response)) finish({ response, error: null });
    };
    finish = (outcome) => {
      clearTimeout(timeout);
      page.off("response", onResponse);
      resolve(outcome);
    };
    page.on("response", onResponse);
  });
  return {
    promise,
    cancel: () => finish({ response: null, error: null }),
  };
}

/** 取得已观测响应，超时按稳定操作名失败。 */
async function requireDiscussionResponse(observation) {
  const outcome = await observation.promise;
  if (outcome.error) throw outcome.error;
  if (!outcome.response) throw new Error("discussion_response_observation_cancelled");
  return outcome.response;
}

/** 等待第一轮由三位参与者形成一次共同发布，并对账服务端快照。 */
export async function waitForFirstPublication(desktop, discussionId) {
  const round = desktop.page.getByRole("region", { name: "第 1 轮" });
  await round.locator('b[data-published="true"]').waitFor();
  const detail = await desktop.api.get(`/api/discussions/${discussionId}`);
  const first = detail.rounds.find((item) => item.number === 1);
  if (
    detail.status !== "waiting_user" ||
    first?.status !== "published" ||
    first?.participants?.length !== 3
  ) {
    throw new Error("three-participant round did not reach one complete publication");
  }
  desktop.trace.record("discussion.round_published", {
    discussion_id: discussionId,
    round_number: first.number,
    participant_count: first.participants.length,
    status: first.status,
  });
  return detail;
}

/** 从等待用户状态停止研讨，并确认每个 participant session owner 已归零。 */
export async function stopDiscussionAndAssertResources(desktop, discussionId) {
  const before = snapshotRecords(await desktop.environment.readResourceSnapshot());
  const participantOwnerIds = new Set(
    before
      .filter(
        (record) =>
          record.owner_scope === "session" && typeof record.owner_id === "string",
      )
      .map((record) => record.owner_id),
  );
  desktop.trace.record("discussion.resources_before_stop", {
    live_resource_count: before.filter((record) => record.state !== "closed").length,
    session_owner_count: participantOwnerIds.size,
    resource_kinds: [...new Set(before.map((record) => record.resource_kind))],
  });
  const stopOutcome = observeDiscussionStop(desktop.page, discussionId);
  await desktop.page.getByRole("button", { name: "停止", exact: true }).click();
  const observed = await stopOutcome;
  if (observed.kind !== "response") {
    desktop.trace.record("discussion.stop_request_observed", {
      outcome: observed.kind,
    });
    throw new Error(`discussion_stop_${observed.kind}`);
  }
  let stopEnvelope;
  try {
    stopEnvelope = await observed.response.json();
  } catch {
    throw new Error("discussion_stop_invalid_json");
  }
  desktop.trace.record("discussion.stop_request_observed", {
    outcome: "response",
    status_code: observed.response.status(),
    success: stopEnvelope?.success === true,
    discussion_status: stopEnvelope?.data?.status ?? "missing",
    participant_status_counts: countValues(
      stopEnvelope?.data?.participants?.map((item) => item.status) ?? [],
    ),
    error_code: stopEnvelope?.error?.code ?? null,
  });
  if (!observed.response.ok() || stopEnvelope?.success !== true) {
    throw new Error("discussion_stop_response_failed");
  }
  await desktop.page.locator('.discussion-status[data-status="stopped"]').waitFor();
  const detail = await desktop.api.get(`/api/discussions/${discussionId}`);
  const records = snapshotRecords(await desktop.environment.readResourceSnapshot());
  const liveRecords = records.filter((record) => record.state !== "closed");
  desktop.trace.record("discussion.resources_after_stop", {
    discussion_status: detail.status,
    participant_status_counts: countValues(
      detail.participants.map((item) => item.status),
    ),
    live_resource_count: liveRecords.length,
    owner_scope_counts: countValues(
      liveRecords.map((record) => record.owner_scope),
    ),
    resource_kind_counts: countValues(
      liveRecords.map((record) => record.resource_kind),
    ),
  });
  for (const ownerId of participantOwnerIds) {
    const live = records.filter(
      (record) =>
        record.state !== "closed" &&
        record.owner_scope === "session" &&
        record.owner_id === ownerId,
    );
    if (live.length > 0) {
      throw new Error("stopped discussion still owns a participant session resource");
    }
  }
  const remainingSessionResources = records.filter(
    (record) => record.state !== "closed" && record.owner_scope === "session",
  );
  if (remainingSessionResources.length > 0) {
    throw new Error(
      `stopped discussion still owns ${remainingSessionResources.length} session resource(s)`,
    );
  }
  desktop.trace.record("discussion.stopped", {
    discussion_id: discussionId,
    participant_count: detail.participants.length,
    status: detail.status,
  });
  return detail;
}

/** 观察停止 POST 的响应或网络失败，不读取请求正文和 URL 参数。 */
function observeDiscussionStop(page, discussionId) {
  const expectedPath = `/api/discussions/${encodeURIComponent(discussionId)}/stop`;
  return new Promise((resolve) => {
    const timeout = setTimeout(() => finish({ kind: "timeout" }), 30_000);
    const matches = (request) =>
      request.method() === "POST" &&
      new URL(request.url()).pathname === expectedPath;
    const onResponse = (response) => {
      if (matches(response.request())) finish({ kind: "response", response });
    };
    const onRequestFailed = (request) => {
      if (matches(request)) finish({ kind: "request_failed" });
    };
    const finish = (outcome) => {
      clearTimeout(timeout);
      page.off("response", onResponse);
      page.off("requestfailed", onRequestFailed);
      resolve(outcome);
    };
    page.on("response", onResponse);
    page.on("requestfailed", onRequestFailed);
  });
}

/** 把低基数字段压成计数，不保存 participant 或资源身份。 */
function countValues(values) {
  return values.reduce((counts, value) => {
    const key = typeof value === "string" && value ? value : "missing";
    counts[key] = (counts[key] ?? 0) + 1;
    return counts;
  }, {});
}

/** 解开创建或启动响应，只在错误中保留稳定操作名、状态码和错误码。 */
async function successfulDiscussionResponse(response, operation, trace) {
  let envelope;
  try {
    envelope = await response.json();
  } catch {
    throw new Error(`discussion_${operation}_invalid_json`);
  }
  if (!response.ok() || envelope?.success !== true || !envelope.data?.id) {
    const code = envelope?.error?.code ?? `http_${response.status()}`;
    trace.record("discussion.response_failed", {
      operation,
      status_code: response.status(),
      error_code: code,
    });
    throw new Error(`discussion_${operation}_${code}`);
  }
  return envelope.data;
}
