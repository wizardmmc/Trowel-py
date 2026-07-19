import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ModelEffortChip } from "../components/cc/ModelEffortChip";

describe("ModelEffortChip shared runtime primitive", () => {
  it("renders only the selected Codex model's native effort values", () => {
    render(
      <ModelEffortChip
        models={[
          {
            value: "gpt-5.6-luna",
            label: "Luna",
            real_model: "gpt-5.6-luna",
            description: "fast",
            is_default: true,
          },
        ]}
        efforts={[
          { value: "low", description: "light" },
          { value: "medium", description: "balanced", isDefault: true },
        ]}
        currentModelAlias="gpt-5.6-luna"
        currentEffort="medium"
        onPickModel={vi.fn()}
        onPickEffort={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /effort: medium/ }));
    expect(screen.getByRole("option", { name: /low/ })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /medium/ })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /ultra/ })).not.toBeInTheDocument();
  });

  it("shows catalog failure and allows retry without inventing model rows", () => {
    const retry = vi.fn();
    render(
      <ModelEffortChip
        models={[]}
        efforts={[]}
        currentModelAlias="gpt-current"
        currentEffort="high"
        onPickModel={vi.fn()}
        onPickEffort={vi.fn()}
        catalogError="catalog offline"
        onRetryCatalog={retry}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /model: gpt-current/ }));
    expect(screen.getByRole("alert")).toHaveTextContent("catalog offline");
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(retry).toHaveBeenCalledTimes(1);
    expect(screen.queryAllByRole("option")).toHaveLength(0);
  });
});
