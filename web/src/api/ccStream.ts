import type { AgentEvent } from "./agentTypes";

const FRAME_DELIMITER = "\n\n";

interface SendMessageBody {
  readonly text: string;
}

interface PostStreamOptions {
  readonly signal?: AbortSignal;
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
        out.push(JSON.parse(payload) as AgentEvent);
      } catch {
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
    response = await fetch(url, {
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
      let idx: number;
      while ((idx = buffer.indexOf(FRAME_DELIMITER)) !== -1) {
        const frame = buffer.slice(0, idx);
        buffer = buffer.slice(idx + FRAME_DELIMITER.length);
        for (const ev of parseSseFrames(frame + FRAME_DELIMITER)) {
          onEvent(ev);
        }
      }
    }
    if (buffer.trim()) {
      for (const ev of parseSseFrames(buffer + FRAME_DELIMITER)) {
        onEvent(ev);
      }
    }
  } catch (err) {
    if (err instanceof Error && err.name === "AbortError") {
      return;
    }
    throw err;
  }
}
