import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

vi.mock("../api/cc", () => ({
  listDir: vi.fn(async () => []),
}));
import { listDir } from "../api/cc";
import { WorkdirPicker } from "../components/cc/WorkdirPicker";

describe("WorkdirPicker", () => {
  it("renders recents as chips", () => {
    render(
      <WorkdirPicker
        recents={["/works/trowel-py", "/works/my-side-project"]}
        favorites={["/Users/alice/my-app"]}
        onSelect={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(screen.getByText("/works/trowel-py")).toBeInTheDocument();
    expect(screen.getByText("/works/my-side-project")).toBeInTheDocument();
    expect(screen.getByText("/Users/alice/my-app")).toBeInTheDocument();
  });

  it("clicking a recent fills the input", () => {
    render(
      <WorkdirPicker
        recents={["/works/my-side-project"]}
        onSelect={() => {}}
        onCancel={() => {}}
      />,
    );
    const input = screen.getByLabelText("工作目录") as HTMLInputElement;
    fireEvent.click(screen.getByText("/works/my-side-project"));
    expect(input.value).toBe("/works/my-side-project");
  });

  it("manual path input + 确定 calls onSelect with the path", () => {
    const onSelect = vi.fn();
    render(
      <WorkdirPicker recents={[]} onSelect={onSelect} onCancel={() => {}} />,
    );
    const input = screen.getByLabelText("工作目录");
    fireEvent.change(input, { target: { value: "/Users/alice/my-app" } });
    fireEvent.click(screen.getByRole("button", { name: "确定" }));
    expect(onSelect).toHaveBeenCalledWith("/Users/alice/my-app");
  });

  it("Enter submits the input", () => {
    const onSelect = vi.fn();
    render(
      <WorkdirPicker recents={[]} onSelect={onSelect} onCancel={() => {}} />,
    );
    const input = screen.getByLabelText("工作目录");
    fireEvent.change(input, { target: { value: "/wd" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onSelect).toHaveBeenCalledWith("/wd");
  });

  it("确定 button is disabled when input is empty", () => {
    render(
      <WorkdirPicker recents={[]} onSelect={() => {}} onCancel={() => {}} />,
    );
    const input = screen.getByLabelText("工作目录");
    fireEvent.change(input, { target: { value: "" } });
    expect(screen.getByRole("button", { name: "确定" })).toBeDisabled();
  });

  it("trims whitespace before submitting", () => {
    const onSelect = vi.fn();
    render(
      <WorkdirPicker recents={[]} onSelect={onSelect} onCancel={() => {}} />,
    );
    const input = screen.getByLabelText("工作目录");
    fireEvent.change(input, { target: { value: "  /wd  " } });
    fireEvent.click(screen.getByRole("button", { name: "确定" }));
    expect(onSelect).toHaveBeenCalledWith("/wd");
  });

  it("cancel button calls onCancel", () => {
    const onCancel = vi.fn();
    render(
      <WorkdirPicker recents={[]} onSelect={() => {}} onCancel={onCancel} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(onCancel).toHaveBeenCalled();
  });

  it("Escape in the input calls onCancel (a11y)", () => {
    const onCancel = vi.fn();
    render(
      <WorkdirPicker recents={[]} onSelect={() => {}} onCancel={onCancel} />,
    );
    fireEvent.keyDown(screen.getByLabelText("工作目录"), { key: "Escape" });
    expect(onCancel).toHaveBeenCalled();
  });
});

describe("WorkdirPicker tree + Tab completion", () => {
  beforeEach(() => {
    vi.mocked(listDir).mockReset();
  });

  it("lists subdirectories of the initial path", async () => {
    vi.mocked(listDir).mockResolvedValue([
      { name: "works", path: "/x/works" },
      { name: "studiolo", path: "/x/studiolo" },
    ]);
    render(
      <WorkdirPicker initialPath="/x" recents={[]} onSelect={() => {}} onCancel={() => {}} />,
    );
    await waitFor(() => {
      expect(screen.getByText("📁 works")).toBeInTheDocument();
      expect(screen.getByText("📁 studiolo")).toBeInTheDocument();
    });
  });

  it("clicking a subdir descends into it (input updates)", async () => {
    vi.mocked(listDir).mockResolvedValue([
      { name: "works", path: "/x/works" },
    ]);
    render(
      <WorkdirPicker initialPath="/x" recents={[]} onSelect={() => {}} onCancel={() => {}} />,
    );
    await waitFor(() => screen.getByText("📁 works"));
    fireEvent.click(screen.getByText("📁 works"));
    expect((screen.getByLabelText("工作目录") as HTMLInputElement).value).toBe(
      "/x/works/",
    );
  });

  it("Tab completes a unique sibling prefix", async () => {
    vi.mocked(listDir).mockResolvedValue([
      { name: "works", path: "/x/works" },
      { name: "other", path: "/x/other" },
    ]);
    render(
      <WorkdirPicker initialPath="/x/wo" recents={[]} onSelect={() => {}} onCancel={() => {}} />,
    );
    await waitFor(() => expect(vi.mocked(listDir)).toHaveBeenCalledWith("/x"));
    const input = screen.getByLabelText("工作目录") as HTMLInputElement;
    fireEvent.keyDown(input, { key: "Tab" });
    await waitFor(() => expect(input.value).toBe("/x/works/"));
  });

  it("Tab completes to common prefix when multiple siblings match", async () => {
    vi.mocked(listDir).mockResolvedValue([
      { name: "work-a", path: "/x/work-a" },
      { name: "work-b", path: "/x/work-b" },
    ]);
    render(
      <WorkdirPicker initialPath="/x/work" recents={[]} onSelect={() => {}} onCancel={() => {}} />,
    );
    await waitFor(() => expect(vi.mocked(listDir)).toHaveBeenCalledWith("/x"));
    const input = screen.getByLabelText("工作目录") as HTMLInputElement;
    fireEvent.keyDown(input, { key: "Tab" });
    await waitFor(() => expect(input.value).toBe("/x/work-"));
  });

  it("double-click a subdir selects it immediately", async () => {
    const onSelect = vi.fn();
    vi.mocked(listDir).mockResolvedValue([
      { name: "works", path: "/x/works" },
    ]);
    render(
      <WorkdirPicker initialPath="/x" recents={[]} onSelect={onSelect} onCancel={() => {}} />,
    );
    await waitFor(() => screen.getByText("📁 works"));
    fireEvent.dblClick(screen.getByText("📁 works"));
    expect(onSelect).toHaveBeenCalledWith("/x/works");
  });

  it('".." ascends to the parent', async () => {
    vi.mocked(listDir).mockResolvedValue([]);
    render(
      <WorkdirPicker initialPath="/x/y" recents={[]} onSelect={() => {}} onCancel={() => {}} />,
    );
    fireEvent.click(screen.getByText("📁 .."));
    expect((screen.getByLabelText("工作目录") as HTMLInputElement).value).toBe("/x");
  });

  it("Tab on ambiguous completes common prefix and opens dropdown", async () => {
    vi.mocked(listDir).mockResolvedValue([
      { name: "work-a", path: "/x/work-a" },
      { name: "work-b", path: "/x/work-b" },
    ]);
    render(
      <WorkdirPicker initialPath="/x/work" recents={[]} onSelect={() => {}} onCancel={() => {}} />,
    );
    await waitFor(() => expect(vi.mocked(listDir)).toHaveBeenCalledWith("/x"));
    const input = screen.getByLabelText("工作目录") as HTMLInputElement;
    fireEvent.keyDown(input, { key: "Tab" });
    await waitFor(() => expect(input.value).toBe("/x/work-"));
    expect(screen.getAllByRole("option")).toHaveLength(2);
  });

  it("ArrowDown + Enter in dropdown selects the highlighted candidate", async () => {
    vi.mocked(listDir).mockResolvedValue([
      { name: "work-a", path: "/x/work-a" },
      { name: "work-b", path: "/x/work-b" },
    ]);
    render(
      <WorkdirPicker initialPath="/x/work" recents={[]} onSelect={() => {}} onCancel={() => {}} />,
    );
    await waitFor(() => expect(vi.mocked(listDir)).toHaveBeenCalledWith("/x"));
    const input = screen.getByLabelText("工作目录") as HTMLInputElement;
    fireEvent.keyDown(input, { key: "Tab" });
    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(input.value).toBe("/x/work-b/");
  });

  it("ArrowRight accepts the ghost suggestion (fish-style)", async () => {
    vi.mocked(listDir).mockResolvedValue([
      { name: "works", path: "/x/works" },
      { name: "other", path: "/x/other" },
    ]);
    render(
      <WorkdirPicker initialPath="/x/wo" recents={[]} onSelect={() => {}} onCancel={() => {}} />,
    );
    await waitFor(() => expect(vi.mocked(listDir)).toHaveBeenCalledWith("/x"));
    const input = screen.getByLabelText("工作目录") as HTMLInputElement;
    fireEvent.keyDown(input, { key: "ArrowRight" });
    expect(input.value).toBe("/x/works/");
  });

  it("Escape closes the dropdown without cancelling", async () => {
    vi.mocked(listDir).mockResolvedValue([
      { name: "work-a", path: "/x/work-a" },
      { name: "work-b", path: "/x/work-b" },
    ]);
    const onCancel = vi.fn();
    render(
      <WorkdirPicker initialPath="/x/work" recents={[]} onSelect={() => {}} onCancel={onCancel} />,
    );
    await waitFor(() => expect(vi.mocked(listDir)).toHaveBeenCalledWith("/x"));
    const input = screen.getByLabelText("工作目录");
    fireEvent.keyDown(input, { key: "Tab" });
    expect(screen.getAllByRole("option")).toHaveLength(2);
    fireEvent.keyDown(input, { key: "Escape" });
    expect(screen.queryAllByRole("option")).toHaveLength(0);
    expect(onCancel).not.toHaveBeenCalled();
  });

  it("expand ~ to home is NOT done client-side (passed through as-is)", () => {
    const onSelect = vi.fn();
    render(
      <WorkdirPicker recents={[]} onSelect={onSelect} onCancel={() => {}} />,
    );
    const input = screen.getByLabelText("工作目录");
    fireEvent.change(input, { target: { value: "~/my-app" } });
    fireEvent.click(screen.getByRole("button", { name: "确定" }));
    expect(onSelect).toHaveBeenCalledWith("~/my-app");
  });
});
