/** 验证 renderer session 保留本地偏好之外的 Cookie 禁用边界。 */

// @vitest-environment node

import { expect, it, vi } from "vitest";
import type { Session } from "electron";
import {
  configureRendererSession,
  removeCookieHeaders,
} from "./rendererSession";

it("removes cookie response headers without changing other headers", () => {
  expect(
    removeCookieHeaders({
      "Content-Type": ["application/json"],
      "Set-Cookie": ["session=secret"],
      "set-cookie2": ["legacy=secret"],
    }),
  ).toEqual({ "Content-Type": ["application/json"] });
});

it("clears only cookies and rejects future cookie responses", async () => {
  let listener:
    | ((
        details: { responseHeaders?: Record<string, string[]> },
        callback: (response: {
          responseHeaders?: Record<string, string | string[]>;
        }) => void,
      ) => void)
    | null = null;
  const clearStorageData = vi.fn().mockResolvedValue(undefined);
  const rendererSession = {
    clearStorageData,
    webRequest: {
      onHeadersReceived: vi.fn((registered) => {
        listener = registered;
      }),
    },
  } as unknown as Session;

  await configureRendererSession(rendererSession);

  expect(clearStorageData).toHaveBeenCalledWith({ storages: ["cookies"] });
  expect(listener).not.toBeNull();
  const callback = vi.fn();
  listener?.(
    {
      responseHeaders: {
        "set-cookie": ["token=secret"],
        "x-trowel": ["ok"],
      },
    },
    callback,
  );
  expect(callback).toHaveBeenCalledWith({
    responseHeaders: { "x-trowel": ["ok"] },
  });
});
