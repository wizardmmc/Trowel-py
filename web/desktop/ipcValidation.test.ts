/** 验证桌面 IPC 对来源、网址和本地路径实行白名单约束。 */

// @vitest-environment node

import { expect, it } from "vitest";
import {
  assertAllowedExternalUrl,
  assertPathInsideRoot,
  isTrustedRendererUrl,
} from "./ipcValidation";

it("accepts only the configured renderer origin and diagnostic file", () => {
  expect(
    isTrustedRendererUrl(
      "http://127.0.0.1:5173/agent",
      "http://127.0.0.1:5173",
      "file:///app/diagnostic.html",
    ),
  ).toBe(true);
  expect(
    isTrustedRendererUrl(
      "file:///app/diagnostic.html",
      "http://127.0.0.1:5173",
      "file:///app/diagnostic.html",
    ),
  ).toBe(true);
  expect(
    isTrustedRendererUrl(
      "https://attacker.example/",
      "http://127.0.0.1:5173",
      "file:///app/diagnostic.html",
    ),
  ).toBe(false);
  expect(
    isTrustedRendererUrl(
      "file:///tmp/attacker.html",
      "file:///app/index.html",
      "file:///app/diagnostic.html",
    ),
  ).toBe(false);
});

it("rejects script URLs while allowing explicit external schemes", () => {
  expect(assertAllowedExternalUrl("https://example.com")).toBe(
    "https://example.com/",
  );
  expect(assertAllowedExternalUrl("mailto:dev@example.com")).toBe(
    "mailto:dev@example.com",
  );
  expect(() => assertAllowedExternalUrl("javascript:alert(1)")).toThrow(
    "unsupported external URL",
  );
});

it("rejects local paths outside the renderer-provided workdir root", () => {
  expect(assertPathInsideRoot("/repo/docs/readme.md", "/repo")).toBe(
    "/repo/docs/readme.md",
  );
  expect(() => assertPathInsideRoot("/repo-other/secret", "/repo")).toThrow(
    "outside allowed root",
  );
  expect(() => assertPathInsideRoot("../secret", "/repo")).toThrow(
    "absolute",
  );
});
