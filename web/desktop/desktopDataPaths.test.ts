/** 验证正式 App 与开发 Host 的长期数据和隔离数据路径。 */

// @vitest-environment node

import { describe, expect, it } from "vitest";
import {
  resolveDesktopDataMode,
  resolveDesktopPaths,
} from "./desktopDataPaths";

const INPUT = {
  appDataDirectory: "/Users/developer/Library/Application Support",
  logsDirectory: "/Users/developer/Library/Logs/Trowel",
};

describe("desktop data paths", () => {
  it("uses the packaged App data directory for normal development", () => {
    expect(resolveDesktopDataMode(undefined, false)).toBe("canonical-dev");
    expect(resolveDesktopPaths({ ...INPUT, mode: "canonical-dev" })).toEqual({
      dataDirectory:
        "/Users/developer/Library/Application Support/Trowel/data",
      logDirectory: "/Users/developer/Library/Logs/Trowel",
      electronUserDataDirectory: null,
    });
  });

  it("keeps explicit isolated development under a sibling product root", () => {
    expect(resolveDesktopDataMode("isolated-dev", false)).toBe("isolated-dev");
    expect(resolveDesktopPaths({ ...INPUT, mode: "isolated-dev" })).toEqual({
      dataDirectory:
        "/Users/developer/Library/Application Support/Trowel Dev/data",
      logDirectory:
        "/Users/developer/Library/Application Support/Trowel Dev/logs",
      electronUserDataDirectory:
        "/Users/developer/Library/Application Support/Trowel Dev/electron",
    });
  });

  it("uses the canonical data subdirectory for packaged builds", () => {
    expect(resolveDesktopDataMode(undefined, true)).toBe("packaged");
    expect(resolveDesktopPaths({ ...INPUT, mode: "packaged" }).dataDirectory).toBe(
      "/Users/developer/Library/Application Support/Trowel/data",
    );
  });

  it("preserves explicit smoke path overrides", () => {
    expect(
      resolveDesktopPaths({
        ...INPUT,
        mode: "isolated-dev",
        dataDirectoryOverride: "/tmp/data",
        logDirectoryOverride: "/tmp/logs",
        electronUserDataDirectoryOverride: "/tmp/electron",
      }),
    ).toEqual({
      dataDirectory: "/tmp/data",
      logDirectory: "/tmp/logs",
      electronUserDataDirectory: "/tmp/electron",
    });
  });

  it("does not derive canonical business data from overridden Electron userData", () => {
    expect(
      resolveDesktopPaths({
        ...INPUT,
        mode: "packaged",
        electronUserDataDirectoryOverride: "/tmp/electron",
      }),
    ).toEqual({
      dataDirectory:
        "/Users/developer/Library/Application Support/Trowel/data",
      logDirectory: "/Users/developer/Library/Logs/Trowel",
      electronUserDataDirectory: "/tmp/electron",
    });
  });
});
