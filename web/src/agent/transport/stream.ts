/** 解析 Agent SSE 帧，并提供消息发送与持续订阅 transport。 */

import type { AgentEvent } from "./agentEvent";
import { transportFetch } from "../../platform/transport";

const FRAME_DELIMITER = "\n\n";
const DEFAULT_INACTIVITY_TIMEOUT_MS = 45_000;

interface SendMessageBody {
  readonly text: string;
}

export type AgentStreamControl =
  | {
      readonly type: "ready";
      readonly generation: string;
      readonly heartbeatIntervalMs?: number;
    }
  | { readonly type: "gap"; readonly sessionId: string };

interface PostStreamOptions {
  readonly signal?: AbortSignal;
  readonly onOpen?: (generation: string | null) => void;
  readonly onControl?: (control: AgentStreamControl) => void;
}

export function parseSseFrames(buffer: string): AgentEvent[] {
  const out: AgentEvent[] = [];
  const frames = buffer.split(FRAME_DELIMITER);
  for (const frame of frames) {
    if (!frame.trim()) continue;
    for (const line of frame.split("\n")) {
      if (!line.startsWith("data:")) continue;
      const payload = line.slice("data:".length).trim();
      if (!payload) continue;
      try {
        const parsed: unknown = JSON.parse(payload);
        if (isAgentEvent(parsed)) out.push(parsed);
      } catch {
        continue;
      }
    }
  }
  return out;
}

export async function postMessageStream(
  url: string,
  body: SendMessageBody,
  onEvent: (event: AgentEvent) => void,
  options: PostStreamOptions = {},
): Promise<void> {
  let response: Response;
  try {
    response = await transportFetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: options.signal,
    });
  } catch (err) {
    if (err instanceof Error && err.name === "AbortError") {
      return;
    }
    throw err;
  }
  if (!response.ok) {
    throw new Error(`Agent 流式请求失败：${response.status}`);
  }
  options.onOpen?.(response.headers.get("X-Trowel-Agent-Generation"));
  await readEventStream(response, onEvent, options);
}

export async function getEventStream(
  url: string,
  onEvent: (event: AgentEvent) => void,
  options: PostStreamOptions = {},
): Promise<void> {
  let response: Response;
  try {
    response = await transportFetch(url, {
      method: "GET",
      signal: options.signal,
    });
  } catch (err) {
    if (err instanceof Error && err.name === "AbortError") return;
    throw err;
  }
  if (!response.ok) {
    throw new Error(`Agent event stream request failed: ${response.status}`);
  }
  options.onOpen?.(response.headers.get("X-Trowel-Agent-Generation"));
  await readEventStream(response, onEvent, options);
}

async function readEventStream(
  response: Response,
  onEvent: (event: AgentEvent) => void,
  options: PostStreamOptions,
): Promise<void> {
  if (!response.body) {
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let inactivityTimeoutMs = DEFAULT_INACTIVITY_TIMEOUT_MS;

  try {
    for (;;) {
      const { done, value } = await readBeforeInactivityDeadline(
        reader,
        inactivityTimeoutMs,
      );
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let idx: number;
      while ((idx = buffer.indexOf(FRAME_DELIMITER)) !== -1) {
        const frame = buffer.slice(0, idx);
        buffer = buffer.slice(idx + FRAME_DELIMITER.length);
        const heartbeatIntervalMs = dispatchFrame(
          frame,
          onEvent,
          options.onControl,
        );
        if (heartbeatIntervalMs !== null) {
          inactivityTimeoutMs = Math.max(
            heartbeatIntervalMs * 3,
            heartbeatIntervalMs + 1_000,
          );
        }
      }
    }
    if (buffer.trim()) {
      dispatchFrame(buffer, onEvent, options.onControl);
    }
  } catch (err) {
    if (
      options.signal?.aborted ||
      (err instanceof Error && err.name === "AbortError")
    ) {
      return;
    }
    throw err;
  }
}

function dispatchFrame(
  frame: string,
  onEvent: (event: AgentEvent) => void,
  onControl?: (control: AgentStreamControl) => void,
): number | null {
  const eventType = frame
    .split("\n")
    .find((line) => line.startsWith("event:"))
    ?.slice("event:".length)
    .trim();
  const data = frame
    .split("\n")
    .find((line) => line.startsWith("data:"))
    ?.slice("data:".length)
    .trim();
  if (!data) return null;
  try {
    const payload: unknown = JSON.parse(data);
    if (isAgentEvent(payload)) {
      onEvent(payload);
      return null;
    }
    if (eventType === "ready" && hasString(payload, "generation")) {
      const heartbeatIntervalMs = readPositiveNumber(
        payload,
        "heartbeat_interval_ms",
      );
      onControl?.({
        type: "ready",
        generation: payload.generation,
        ...(heartbeatIntervalMs === null ? {} : { heartbeatIntervalMs }),
      });
      return heartbeatIntervalMs;
    } else if (eventType === "gap" && hasString(payload, "session_id")) {
      onControl?.({ type: "gap", sessionId: payload.session_id });
    }
  } catch {
    // 损坏控制帧和事件帧都由 seq gap/snapshot 恢复，不中断整条应用流。
  }
  return null;
}

async function readBeforeInactivityDeadline(
  reader: ReadableStreamDefaultReader<Uint8Array>,
  timeoutMs: number,
): Promise<ReadableStreamReadResult<Uint8Array>> {
  let timeout: ReturnType<typeof globalThis.setTimeout> | null = null;
  try {
    return await Promise.race([
      reader.read(),
      new Promise<never>((_resolve, reject) => {
        timeout = globalThis.setTimeout(() => {
          reject(new Error("Agent event stream heartbeat timed out"));
          void reader.cancel("Agent event stream heartbeat timed out");
        }, timeoutMs);
      }),
    ]);
  } finally {
    if (timeout !== null) globalThis.clearTimeout(timeout);
  }
}

function isAgentEvent(value: unknown): value is AgentEvent {
  if (!value || typeof value !== "object") return false;
  const record = value as Record<string, unknown>;
  return (
    record.schema === "agent-event-v1" &&
    typeof record.session_id === "string" &&
    typeof record.seq === "number"
  );
}

function hasString<T extends string>(
  value: unknown,
  key: T,
): value is Record<T, string> {
  return (
    value !== null &&
    typeof value === "object" &&
    typeof (value as Record<string, unknown>)[key] === "string"
  );
}

function readPositiveNumber<T extends string>(
  value: unknown,
  key: T,
): number | null {
  if (value === null || typeof value !== "object") return null;
  const candidate = (value as Record<string, unknown>)[key];
  return typeof candidate === "number" && candidate > 0 ? candidate : null;
}
