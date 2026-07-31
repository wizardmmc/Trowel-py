import type { AgentSession } from "../agent/transport";

const LARGE_SESSION_ID = "fixture-large-turn";
const LIGHT_SESSION_ID = "fixture-light-session";
const WORKDIR = "/fixture/repo";

function fixtureAgentSession(
  id: string,
  runtime: "claude_code" | "codex",
  connected: boolean,
): AgentSession {
  return {
    session_id: id,
    runtime,
    native_session_id: `${id}-native`,
    workdir: WORKDIR,
    model: "fixture-model",
    effort: runtime === "codex" ? "medium" : null,
    permission: null,
    memory_enabled: true,
    profile_enabled: true,
    capabilities: ["tools", "interrupt"],
    name: id,
    display_title:
      id === LARGE_SESSION_ID ? "Large turn replay" : "Light session",
    title_source: "manual",
    connected,
    running: false,
  };
}

function envelope(
  data: unknown,
  meta?: Readonly<Record<string, unknown>>,
): Response {
  return new Response(
    JSON.stringify({ success: true, data, error: null, meta }),
    {
      status: 200,
      headers: { "Content-Type": "application/json" },
    },
  );
}

/** 让浏览器基线页独立运行，不连接真实后端或本机会话。 */
export function installLargeTurnFetchMock(): void {
  globalThis.fetch = async (input) => {
    const raw =
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.href
          : input.url;
    const url = new URL(raw, window.location.origin);
    const path = url.pathname;

    if (path === "/api/agent/sessions/active") {
      return envelope({
        sessions: [
          fixtureAgentSession(LARGE_SESSION_ID, "codex", false),
          fixtureAgentSession(LIGHT_SESSION_ID, "claude_code", true),
        ],
        active_id: LARGE_SESSION_ID,
      });
    }
    if (path === "/api/agent/runtimes") return envelope([]);
    if (path === "/api/agent/models") return envelope({ models: [] });
    if (path === "/api/agent/session-defaults") return envelope(null);
    if (path === "/api/cc/models" || path === "/api/cc/slash-items") {
      return envelope([]);
    }
    if (path.endsWith("/commands")) return envelope({ commands: [] });
    if (path.endsWith("/requests")) return envelope({ requests: [] });
    if (path.endsWith("/goal")) return envelope({ goal: null });
    if (path.endsWith("/history")) return envelope([]);
    if (path.endsWith("/interrupt")) {
      return envelope({ interrupted: true });
    }
    if (path.endsWith("/activate")) {
      const sessionId = path.split("/").at(-2) ?? LARGE_SESSION_ID;
      return envelope({ active_id: sessionId });
    }
    if (path === "/api/agent/sessions" && url.searchParams.has("workdir")) {
      return envelope([], { limit: 20, next_cursor: null });
    }
    if (path.startsWith("/api/agent/")) return envelope({});
    if (path.startsWith("/api/cc/")) return envelope({});
    return new Response("Not found", { status: 404 });
  };
}
