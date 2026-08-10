/** 验证 E2E 产物只保存去敏事实，不泄漏输入、凭据和本机路径。 */

import { afterEach, describe, expect, test } from "bun:test";
import { createHash } from "node:crypto";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import {
  ArtifactPrivacyError,
  FailureDiagnostics,
  StructuredTrace,
  auditArtifactText,
  classifyFailureErrors,
  removeUnexpectedPlaywrightArtifacts,
  redactArtifactValue,
} from "./artifacts.mjs";

const temporaryRoots = [];

afterEach(async () => {
  await Promise.all(
    temporaryRoots.splice(0).map((root) => rm(root, { recursive: true, force: true })),
  );
});

describe("artifact privacy", () => {
  test("redacts secret, path and user content fields recursively", () => {
    expect(
      redactArtifactValue({
        credential: "bearer-secret",
        workdir: "/Users/example/private-project",
        prompt: "private prompt",
        nested: { status: "completed", token_usage: 42 },
      }),
    ).toEqual({
      credential: "[REDACTED]",
      workdir: "[PATH]",
      prompt: "[CONTENT]",
      nested: { status: "completed", token_usage: "[REDACTED]" },
    });
  });

  test("rejects raw absolute paths, credentials and known scenario content", () => {
    expect(() =>
      auditArtifactText(
        "open /Users/example/private-project with Bearer abc and E2E_PRIVATE_PROMPT",
        { forbiddenContents: ["E2E_PRIVATE_PROMPT"] },
      ),
    ).toThrow(ArtifactPrivacyError);
  });

  test("records only named steps and redacted details", () => {
    const trace = new StructuredTrace("agent.claude.core");
    trace.record("turn.accepted", {
      prompt: "E2E_PRIVATE_PROMPT",
      turn_id: "turn-1",
    });
    expect(trace.toJSON()).toEqual({
      schema: "trowel-e2e-trace-v1",
      scenario_id: "agent.claude.core",
      steps: [
        {
          name: "turn.accepted",
          details: { prompt: "[CONTENT]", turn_id: "turn-1" },
        },
      ],
    });
  });

  test("deletes Playwright failure context instead of retaining raw DOM", async () => {
    const root = await mkdtemp(path.join(os.tmpdir(), "trowel-artifact-test-"));
    temporaryRoots.push(root);
    await writeFile(path.join(root, "summary.json"), "{}\n", "utf8");
    const rawContext = path.join(root, "error-context.md");
    await writeFile(rawContext, "open /Users/example/private and E2E_PRIVATE_PROMPT", "utf8");

    await expect(
      removeUnexpectedPlaywrightArtifacts(root, { allowedFiles: ["summary.json"] }),
    ).rejects.toMatchObject({ reasons: ["unexpected_playwright_artifact"] });
    await expect(readFile(rawContext, "utf8")).rejects.toMatchObject({ code: "ENOENT" });
  });

  test("failure diagnostics keep only counters and product route groups", () => {
    const diagnostics = new FailureDiagnostics();
    diagnostics.recordConsole("error", "must not be accepted as message text");
    diagnostics.recordResponse(
      503,
      "http://127.0.0.1:43123/api/agent/sessions/private-session/turns?token=secret",
    );
    diagnostics.recordRequestFailure(
      "http://127.0.0.1:43123/api/discussions/private-topic",
    );

    expect(diagnostics.consoleCounts).toEqual({ error: 1 });
    expect(diagnostics.responseCounts).toEqual({ "5xx": 1 });
    expect(diagnostics.routeCounts).toEqual({ agent: 1 });
    expect(diagnostics.requestFailureCounts).toEqual({ discussion: 1 });
    expect(JSON.stringify(diagnostics)).not.toContain("private-session");
    expect(JSON.stringify(diagnostics)).not.toContain("secret");
  });

  test("classifies failures without retaining paths or raw messages", () => {
    expect(classifyFailureErrors([
      new Error("Timeout 10000ms exceeded at /Users/example/private-project"),
      new Error("resource still running with Bearer private-secret"),
    ])).toEqual(["timeout", "resource_assertion"]);
  });

  test("writes only allowlisted lifecycle aggregates", async () => {
    const root = await mkdtemp(path.join(os.tmpdir(), "trowel-artifact-test-"));
    temporaryRoots.push(root);
    const output = path.join(root, "failure-summary.json");
    const descriptorPath = path.join(root, "agent-service.json");
    await writeFile(
      descriptorPath,
      JSON.stringify({
        base_url: "http://127.0.0.1:43123",
        credential: "descriptor-secret",
        service_instance_id: "instance-1",
      }),
      "utf8",
    );
    const diagnostics = new FailureDiagnostics();
    diagnostics.recordConsole("warning");
    diagnostics.recordResponse(202, "http://127.0.0.1/api/agent");

    await diagnostics.write(output, {
      page: {
        evaluate: async () => ({
          available: true,
          turn_status_counts: { interrupted: 1 },
          dialog_count: 1,
          alert_count: 0,
          disabled_textbox_count: 1,
        }),
      },
      api: {
        baseUrl: "http://127.0.0.1:43123",
        credential: "descriptor-secret",
        appInstanceIdentity: createHash("sha256")
          .update("instance-1", "utf8")
          .digest("hex")
          .slice(0, 20),
        get: async () => ({
          sessions: [
            {
              session_id: "private-session",
              turn_state: "completed",
              resource_state: "needs_reconcile",
            },
          ],
        }),
      },
      environment: {
        serviceDescriptorPath: descriptorPath,
        readResourceSnapshot: async () => ({
          resources: [
            {
              owner_id: "private-session",
              process_id: 123,
              state: "needs_reconcile",
              runtime: "codex",
              owner_scope: "session",
              resource_kind: "codex_thread_resources",
            },
          ],
        }),
      },
    });

    const written = await readFile(output, "utf8");
    expect(JSON.parse(written)).toEqual({
      schema: "trowel-e2e-failure-v1",
      console_counts: { warning: 1 },
      network: {
        response_status_counts: { "2xx": 1 },
        route_counts: { agent: 1 },
        request_failure_counts: {},
      },
      business_state: {
        dom: {
          available: true,
          turn_status_counts: { interrupted: 1 },
          dialog_count: 1,
          alert_count: 0,
          disabled_textbox_count: 1,
        },
        sessions: {
          available: true,
          session_count: 1,
          turn_state_counts: { completed: 1 },
          resource_state_counts: { needs_reconcile: 1 },
        },
        resources: {
          available: true,
          resource_count: 1,
          state_counts: { needs_reconcile: 1 },
          runtime_counts: { codex: 1 },
          owner_scope_counts: { session: 1 },
          resource_kind_counts: { codex_thread_resources: 1 },
        },
        desktop_transport: {
          available: true,
          endpoint_matches: true,
          credential_matches: true,
          instance_matches: true,
        },
      },
    });
    expect(written).not.toContain("private-session");
    expect(written).not.toContain("123");
  });
});
