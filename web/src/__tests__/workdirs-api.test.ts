/** 验证 Recent 工作区 wire shape 不会泄漏到 UI 层。 */

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  listRecentWorkspaces,
  rememberRecentWorkspace,
} from "../agent/application/workdirs";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Recent workspace API", () => {
  it("maps persisted workspace fields into application names", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          success: true,
          data: [
            {
              path: "/repo",
              name: "repo",
              last_opened_at: "2026-08-01T10:00:00+00:00",
              available: true,
            },
          ],
          error: null,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(listRecentWorkspaces()).resolves.toEqual([
      {
        path: "/repo",
        name: "repo",
        lastOpenedAt: "2026-08-01T10:00:00+00:00",
        available: true,
      },
    ]);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/agent/workspaces/recent",
      undefined,
    );
  });

  it("remembers the selected directory through the shared backend", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          success: true,
          data: {
            path: "/repo",
            name: "repo",
            last_opened_at: "2026-08-01T10:00:00+00:00",
            available: true,
          },
          error: null,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const workspace = await rememberRecentWorkspace("/repo");

    expect(workspace.name).toBe("repo");
    const [url, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/agent/workspaces/recent");
    expect(options.method).toBe("POST");
    expect(JSON.parse(String(options.body))).toEqual({ path: "/repo" });
  });

  it("preserves the backend detail when a workspace cannot be opened", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({ detail: "workspace does not exist: /missing" }),
          { status: 400, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );

    await expect(rememberRecentWorkspace("/missing")).rejects.toThrow(
      "workspace does not exist: /missing",
    );
  });
});
