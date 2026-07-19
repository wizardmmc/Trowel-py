/**
 * SSE client for POST /api/agent/sessions/{id}/messages (slice-074).
 *
 * EventSource cannot POST (and the existing sse.ts is GET-only, hardcoded to
 * extraction-progress), so this streams the response body manually: read
 * chunks, buffer them, split on the SSE frame delimiter (blank line), and
 * parse each `data:` line as an AgentEvent v1 envelope.
 *
 * The host always emits exactly one AgentEvent per SSE `data:` frame, but the
 * wire boundary can split a frame across read chunks — hence the carry-over
 * buffer.
 */
import type { AgentEvent } from "./agentTypes";

/** SSE frame delimiter per the spec: a blank line. */
const FRAME_DELIMITER = "\n\n";

interface SendMessageBody {
  readonly text: string;
}

interface PostStreamOptions {
  /** Abort the in-flight stream (user hit interrupt / unmounted). */
  readonly signal?: AbortSignal;
}

/**
 * Parse a complete SSE-wire buffer into the AgentEvents it carries.
 *
 * Exposed for tests and for feeding history-style buffered input. Handles
 * multi-line frames (only `data:` lines contribute) and skips malformed
 * JSON lines rather than throwing.
 */
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
        out.push(JSON.parse(payload) as AgentEvent);
      } catch {
        // malformed line — skip, the host may emit debug noise
      }
    }
  }
  return out;
}

/**
 * POST a message and drive the SSE response, calling onEvent for each
 * AgentEvent. Resolves when the stream closes; never rejects on user-initiated
 * abort (AbortError is silent — interrupt is a normal user action).
 */
export async function postMessageStream(
  url: string,
  body: SendMessageBody,
  onEvent: (event: AgentEvent) => void,
  options: PostStreamOptions = {},
): Promise<void> {
  let response: Response;
  try {
    response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: options.signal,
    });
  } catch (err) {
    if (err instanceof Error && err.name === "AbortError") {
      return; // silent on user abort
    }
    throw err;
  }
  if (!response.ok) {
    throw new Error(`CC stream request failed: ${response.status}`);
  }
  if (!response.body) {
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      // Process only complete frames; keep the trailing partial in buffer.
      let idx: number;
      while ((idx = buffer.indexOf(FRAME_DELIMITER)) !== -1) {
        const frame = buffer.slice(0, idx);
        buffer = buffer.slice(idx + FRAME_DELIMITER.length);
        for (const ev of parseSseFrames(frame + FRAME_DELIMITER)) {
          onEvent(ev);
        }
      }
    }
    // flush any trailing frame without a delimiter
    if (buffer.trim()) {
      for (const ev of parseSseFrames(buffer + FRAME_DELIMITER)) {
        onEvent(ev);
      }
    }
  } catch (err) {
    if (err instanceof Error && err.name === "AbortError") {
      return; // silent on user abort
    }
    throw err;
  }
}
