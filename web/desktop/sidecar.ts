/** 启动 Python sidecar，并在 renderer 加载前完成实例和版本握手。 */

import { spawn as spawnProcess } from "node:child_process";
import { createServer } from "node:net";
import type { DesktopTransportConfig } from "../shared/desktop-contracts";
import { DESKTOP_PROTOCOL_VERSION } from "../shared/desktop-contracts";

export interface SidecarReadiness {
  readonly status: "ready";
  readonly app_version: string;
  readonly protocol_version: number;
  readonly instance_id: string;
  readonly capabilities: readonly string[];
}

export interface SidecarExit {
  readonly code: number | null;
  readonly signal: string | null;
  readonly errorCode?: string;
}

export interface SidecarProcess {
  readonly pid: number | undefined;
  readonly exited: Promise<SidecarExit>;
  readonly stop: () => void;
}

export interface SidecarSpawnSpec {
  readonly executable: string;
  readonly args: readonly string[];
  readonly cwd: string;
  readonly environment: Readonly<Record<string, string | undefined>>;
}

export interface SidecarStartDependencies {
  readonly reservePort: () => Promise<number>;
  readonly spawn: (spec: SidecarSpawnSpec) => SidecarProcess;
  readonly readReadiness: (
    baseUrl: string,
    credential: string,
  ) => Promise<SidecarReadiness>;
  readonly delay: (milliseconds: number) => Promise<void>;
}

export interface SidecarStartOptions {
  readonly executable: string;
  readonly cwd: string;
  readonly dataDirectory: string;
  readonly logDirectory: string;
  readonly instanceId: string;
  readonly credential: string;
  readonly expectedAppVersion: string;
  readonly rendererOrigin: string;
  readonly readinessTimeoutMs?: number;
  readonly pollIntervalMs?: number;
}

export interface RunningSidecar {
  readonly process: SidecarProcess;
  readonly transport: DesktopTransportConfig;
  readonly readiness: SidecarReadiness;
}

export class SidecarStartError extends Error {
  readonly category:
    | "executable_missing"
    | "port_or_permission"
    | "version_mismatch"
    | "readiness_timeout"
    | "early_exit";
  readonly exitCode: number | null;

  constructor(
    category: SidecarStartError["category"],
    message: string,
    exitCode: number | null = null,
  ) {
    super(message);
    this.name = "SidecarStartError";
    this.category = category;
    this.exitCode = exitCode;
  }
}

const DEFAULT_DEPENDENCIES: SidecarStartDependencies = {
  reservePort,
  spawn: spawnSidecar,
  readReadiness,
  delay: (milliseconds) =>
    new Promise((resolve) => setTimeout(resolve, milliseconds)),
};

export async function launchSidecar(
  options: SidecarStartOptions,
  dependencies: SidecarStartDependencies = DEFAULT_DEPENDENCIES,
): Promise<RunningSidecar> {
  let port: number;
  try {
    port = await dependencies.reservePort();
  } catch {
    throw new SidecarStartError(
      "port_or_permission",
      "The desktop host could not reserve a private loopback port.",
    );
  }
  const baseUrl = `http://127.0.0.1:${port}`;
  let process: SidecarProcess;
  try {
    process = dependencies.spawn({
      executable: options.executable,
      args: ["-m", "trowel_py.desktop.sidecar"],
      cwd: options.cwd,
      environment: {
        ...globalThis.process.env,
        TROWEL_APP_INSTANCE_ID: options.instanceId,
        TROWEL_DESKTOP_CREDENTIAL: options.credential,
        TROWEL_SERVER_PORT: String(port),
        TROWEL_DESKTOP_DATA_DIR: options.dataDirectory,
        TROWEL_DESKTOP_LOG_DIR: options.logDirectory,
        TROWEL_DESKTOP_RENDERER_ORIGIN: options.rendererOrigin,
      },
    });
  } catch (error) {
    throw classifySpawnError(error);
  }

  try {
    const readiness = await waitForReadiness(
      process,
      baseUrl,
      options,
      dependencies,
    );
    validateReadiness(readiness, options);
    return {
      process,
      readiness,
      transport: { baseUrl, credential: options.credential },
    };
  } catch (error) {
    process.stop();
    throw error;
  }
}

async function waitForReadiness(
  process: SidecarProcess,
  baseUrl: string,
  options: SidecarStartOptions,
  dependencies: SidecarStartDependencies,
): Promise<SidecarReadiness> {
  const timeout = options.readinessTimeoutMs ?? 15_000;
  const pollInterval = options.pollIntervalMs ?? 100;
  const deadline = Date.now() + timeout;

  for (;;) {
    const outcome = await Promise.race([
      dependencies
        .readReadiness(baseUrl, options.credential)
        .then((readiness) => ({ kind: "ready" as const, readiness }))
        .catch(() => ({ kind: "not-ready" as const })),
      process.exited.then((exit) => ({ kind: "exit" as const, exit })),
    ]);
    if (outcome.kind === "ready") return outcome.readiness;
    if (outcome.kind === "exit") throw classifyExit(outcome.exit);
    if (Date.now() >= deadline) {
      throw new SidecarStartError(
        "readiness_timeout",
        "Python sidecar did not become ready in time.",
      );
    }
    const delayed = await Promise.race([
      dependencies.delay(pollInterval).then(() => null),
      process.exited,
    ]);
    if (delayed !== null) throw classifyExit(delayed);
  }
}

function validateReadiness(
  readiness: SidecarReadiness,
  options: SidecarStartOptions,
): void {
  if (
    readiness.status !== "ready" ||
    readiness.instance_id !== options.instanceId ||
    readiness.app_version !== options.expectedAppVersion ||
    readiness.protocol_version !== DESKTOP_PROTOCOL_VERSION
  ) {
    throw new SidecarStartError(
      "version_mismatch",
      "Python sidecar identity or version does not match this desktop app.",
    );
  }
}

function classifySpawnError(error: unknown): SidecarStartError {
  const code = errorCode(error);
  if (code === "ENOENT") {
    return new SidecarStartError(
      "executable_missing",
      "The configured Python sidecar executable could not be found.",
    );
  }
  if (code === "EACCES" || code === "EADDRINUSE") {
    return new SidecarStartError(
      "port_or_permission",
      "The Python sidecar could not use its executable or private port.",
    );
  }
  return new SidecarStartError(
    "early_exit",
    "The Python sidecar could not be started.",
  );
}

function classifyExit(exit: SidecarExit): SidecarStartError {
  if (exit.errorCode) return classifySpawnError({ code: exit.errorCode });
  return new SidecarStartError(
    "early_exit",
    "The Python sidecar exited before it became ready.",
    exit.code,
  );
}

function errorCode(error: unknown): string | undefined {
  if (!error || typeof error !== "object" || !("code" in error)) return undefined;
  return typeof error.code === "string" ? error.code : undefined;
}

function reservePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = createServer();
    server.unref();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      if (!address || typeof address === "string") {
        server.close();
        reject(new Error("Could not reserve a loopback port."));
        return;
      }
      server.close((error) => {
        if (error) reject(error);
        else resolve(address.port);
      });
    });
  });
}

function spawnSidecar(spec: SidecarSpawnSpec): SidecarProcess {
  const child = spawnProcess(spec.executable, [...spec.args], {
    cwd: spec.cwd,
    env: spec.environment,
    stdio: "ignore",
  });
  const exited = new Promise<SidecarExit>((resolve) => {
    child.once("error", (error) => {
      resolve({ code: null, signal: null, errorCode: errorCode(error) });
    });
    child.once("exit", (code, signal) => {
      resolve({ code, signal });
    });
  });
  return {
    pid: child.pid,
    exited,
    stop: () => {
      if (!child.killed) child.kill("SIGTERM");
    },
  };
}

async function readReadiness(
  baseUrl: string,
  credential: string,
): Promise<SidecarReadiness> {
  const response = await fetch(`${baseUrl}/api/desktop/readiness`, {
    headers: { Authorization: `Bearer ${credential}` },
    signal: AbortSignal.timeout(1_000),
  });
  if (!response.ok) throw new Error(`readiness returned ${response.status}`);
  const envelope = (await response.json()) as {
    readonly success?: boolean;
    readonly data?: SidecarReadiness;
  };
  if (!envelope.success || !envelope.data) {
    throw new Error("readiness response did not contain data");
  }
  return envelope.data;
}
