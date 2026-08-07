/** 封装 discussion HTTP 命令、配置目录与独立 SSE。 */

import { transportFetch } from "../../platform/transport";
import type {
  CreateDiscussionInput,
  Discussion,
  DiscussionEvent,
  DiscussionSessionConfiguration,
  HandoffAgentInput,
  HandoffResult,
} from "../domain";

const API = "/api/discussions";

interface Envelope<T> {
  readonly success: boolean;
  readonly data: T | null;
  readonly error: { readonly code: string; readonly message: string } | null;
}

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await transportFetch(url, options);
  const envelope = (await response.json()) as Envelope<T>;
  if (!response.ok || !envelope.success || envelope.data === null) {
    throw new Error(envelope.error?.message ?? `研讨请求失败：${response.status}`);
  }
  return envelope.data;
}

export function listDiscussions(): Promise<readonly Discussion[]> {
  return request<readonly Discussion[]>(API);
}

export function getDiscussion(id: string): Promise<Discussion> {
  return request<Discussion>(`${API}/${encodeURIComponent(id)}`);
}

export function createDiscussion(input: CreateDiscussionInput): Promise<Discussion> {
  return request<Discussion>(API, jsonRequest("POST", input));
}

export function startDiscussion(id: string, version: number): Promise<Discussion> {
  return versionedCommand(id, "start", version);
}

export function continueDiscussion(
  id: string,
  version: number,
  progressionMode: "automatic" | "user_guided" = "user_guided",
  additionalRounds: number | null = null,
): Promise<Discussion> {
  return request<Discussion>(
    `${API}/${encodeURIComponent(id)}/continue`,
    jsonRequest("POST", {
      command_id: commandId("continue"),
      expected_version: version,
      progression_mode: progressionMode,
      additional_rounds: progressionMode === "automatic" ? additionalRounds : null,
    }),
  );
}

export function finishDiscussion(id: string, version: number): Promise<Discussion> {
  return versionedCommand(id, "finish", version);
}

export function resumeDiscussion(id: string, version: number): Promise<Discussion> {
  return versionedCommand(id, "resume", version);
}

export function stopDiscussion(
  id: string,
  version: number,
  reason = "用户停止研讨",
): Promise<Discussion> {
  return request<Discussion>(
    `${API}/${encodeURIComponent(id)}/stop`,
    jsonRequest("POST", {
      command_id: commandId("stop"),
      expected_version: version,
      reason,
    }),
  );
}

export function deleteDiscussion(id: string, version: number): Promise<Discussion> {
  return request<Discussion>(
    `${API}/${encodeURIComponent(id)}`,
    jsonRequest("DELETE", {
      command_id: commandId("delete"),
      expected_version: version,
    }),
  );
}

export function addDiscussionMessage(
  id: string,
  version: number,
  body: string,
  targetParticipantId: string | null,
): Promise<Discussion> {
  return request<Discussion>(
    `${API}/${encodeURIComponent(id)}/messages`,
    jsonRequest("POST", {
      command_id: commandId("message"),
      expected_version: version,
      body,
      target_participant_id: targetParticipantId,
    }),
  );
}

export function markDiscussionResult(
  id: string,
  version: number,
  roundNumber: number,
  participantId: string,
  marked: boolean,
): Promise<Discussion> {
  return request<Discussion>(
    `${API}/${encodeURIComponent(id)}/marks`,
    jsonRequest("POST", {
      command_id: commandId(marked ? "mark" : "unmark"),
      expected_version: version,
      round_number: roundNumber,
      participant_id: participantId,
      marked,
    }),
  );
}

export function handoffDiscussion(
  id: string,
  version: number,
  agent: HandoffAgentInput,
  instruction: string,
): Promise<HandoffResult> {
  return request<HandoffResult>(
    `${API}/${encodeURIComponent(id)}/handoffs`,
    jsonRequest("POST", {
      command_id: commandId("handoff"),
      expected_version: version,
      instruction,
      agent,
    }),
  );
}

export function listDiscussionSessionConfigurations(): Promise<
  readonly DiscussionSessionConfiguration[]
> {
  return request<readonly DiscussionSessionConfiguration[]>(
    "/api/configuration/session-configurations",
  );
}

export async function watchDiscussionEvents(
  id: string,
  after: number,
  onEvent: (event: DiscussionEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  const response = await transportFetch(
    `${API}/${encodeURIComponent(id)}/events?after=${after}`,
    { signal },
  );
  if (!response.ok || !response.body) {
    throw new Error(`研讨事件流不可用：${response.status}`);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (!signal.aborted) {
    const { done, value } = await reader.read();
    if (done) return;
    buffer += decoder.decode(value, { stream: true });
    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      dispatchFrame(buffer.slice(0, boundary), onEvent);
      buffer = buffer.slice(boundary + 2);
      boundary = buffer.indexOf("\n\n");
    }
  }
}

function dispatchFrame(
  frame: string,
  onEvent: (event: DiscussionEvent) => void,
): void {
  const line = frame.split("\n").find((item) => item.startsWith("data:"));
  if (!line) return;
  try {
    const value = JSON.parse(line.slice(5).trim()) as DiscussionEvent;
    if (typeof value.sequence === "number" && typeof value.type === "string") {
      onEvent(value);
    }
  } catch {
    // 持久 sequence 和后续 GET 会恢复损坏帧，不中断整条订阅。
  }
}

function versionedCommand(
  id: string,
  command: string,
  version: number,
): Promise<Discussion> {
  return request<Discussion>(
    `${API}/${encodeURIComponent(id)}/${command}`,
    jsonRequest("POST", {
      command_id: commandId(command),
      expected_version: version,
    }),
  );
}

function jsonRequest(method: string, body: unknown): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

function commandId(kind: string): string {
  return `${kind}:${crypto.randomUUID()}`;
}
