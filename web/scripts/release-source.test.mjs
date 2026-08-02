/** 验证正式发布的源码身份不依赖 Apple 签名方式。 */

// @vitest-environment node

import { createRequire } from "node:module";
import { expect, it } from "vitest";

const require = createRequire(import.meta.url);
const { assertReleaseSourceFacts } = require("./release-source.cjs");

it("accepts a clean main commit carrying the current version tag", () => {
  expect(() =>
    assertReleaseSourceFacts({
      branch: "main",
      sourceChanges: "",
      tags: ["v0.2.0"],
      version: "0.2.0",
    }),
  ).not.toThrow();
});

it.each([
  {
    facts: {
      branch: "feature/release",
      sourceChanges: "",
      tags: ["v0.2.0"],
      version: "0.2.0",
    },
    message: "main branch",
  },
  {
    facts: {
      branch: "main",
      sourceChanges: " M web/package.json",
      tags: ["v0.2.0"],
      version: "0.2.0",
    },
    message: "clean build source paths",
  },
  {
    facts: {
      branch: "main",
      sourceChanges: "",
      tags: ["v0.1.0"],
      version: "0.2.0",
    },
    message: "tag v0.2.0",
  },
])("rejects a release source without $message", ({ facts, message }) => {
  expect(() => assertReleaseSourceFacts(facts)).toThrow(message);
});
