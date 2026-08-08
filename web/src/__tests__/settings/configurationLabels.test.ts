/** 验证运行配置在紧凑选择器中的唯一展示规则。 */

import { expect, it } from "vitest";
import { compactConfigurationLabel } from "../../settings/domain/configurationLabels";
import type { Connection, SessionConfiguration } from "../../settings/domain/types";

const configuration = {
  id: "configuration-1",
  version: 1,
  identity_version: 1,
  name: "Sol High",
  runtime: "codex",
  connection_id: "connection-1",
  connection_identity_version: 1,
  connection_name: "OpenAI Pro",
  model: "gpt-5.6-sol",
  effort: "high",
  stable_alias: null,
  agent_callable: false,
  capability: {
    status: "verified",
    version: "test",
    source: "real_gate",
    eligible_tasks: [],
  },
  availability: "available",
  disabled_reason: null,
} satisfies SessionConfiguration;

it("shows only the stable alias when one is configured", () => {
  expect(compactConfigurationLabel(
    { ...configuration, stable_alias: "codex1" },
    [],
  )).toBe("codex1");
});

it("falls back to model connection and configuration names", () => {
  expect(compactConfigurationLabel(configuration, [] as Connection[]))
    .toBe("OpenAI Pro · Sol High");
});
