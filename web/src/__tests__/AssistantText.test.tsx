import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AssistantText } from "../components/cc/AssistantText";

const writeText = vi.fn<(text: string) => Promise<void>>();

beforeEach(() => {
  vi.clearAllMocks();
  writeText.mockResolvedValue();
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText },
  });
});

describe("AssistantText", () => {
  it("renders inline code, bold, link, list, and code block", () => {
    const md = [
      "见 `inline` 与 **bold**，链接 [home](https://x.io)。",
      "",
      "- 一",
      "- 二",
      "",
      "```ts",
      "const x: number = 1",
      "```",
    ].join("\n");
    const { container } = render(<AssistantText text={md} />);
    const root = container.querySelector(".cc-md") as HTMLElement;
    expect(root).toBeTruthy();
    expect(root.querySelector("code")).toBeTruthy();
    expect(root.querySelector("strong")).toBeTruthy();
    const link = root.querySelector("a");
    expect(link?.getAttribute("href")).toBe("https://x.io");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(root.querySelectorAll("li")).toHaveLength(2);
    expect(root.querySelector("pre")).toBeTruthy();
  });

  it("opens a workdir-local absolute path through the session file endpoint", () => {
    const path = "/workspace/project/docs/local%20example.html";
    render(
      <AssistantText
        text={`[原型](${path})`}
        sessionId="session-1"
        workdir="/workspace/project"
      />,
    );

    const link = screen.getByRole("link", { name: "原型" });
    expect(link).toHaveAttribute(
      "href",
      "/api/agent/sessions/session-1/files?path=docs%2Flocal%20example.html",
    );
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("does not make an absolute path outside the session workdir clickable", () => {
    render(
      <AssistantText
        text="[私有文件](/workspace/private/secret.html)"
        sessionId="session-1"
        workdir="/workspace/project"
      />,
    );

    expect(screen.queryByRole("link", { name: "私有文件" })).toBeNull();
    expect(screen.getByText("私有文件")).toHaveAttribute(
      "title",
      "本地路径不在当前会话的工作目录内",
    );
  });

  it("surfaces the fenced code block's language as a corner badge", () => {
    const md = "```python\nprint(1)\n```";
    const { container } = render(<AssistantText text={md} />);
    const codeBlock = container.querySelector(".cc-md-codeblock");
    expect(codeBlock?.querySelector(".cc-md-codeblock__meta")).toBeTruthy();
    expect(codeBlock?.querySelector(".cc-md-codeblock__header")).toBeNull();
    expect(container.querySelector(".cc-md-codeblock__lang")?.textContent).toBe("python");
    expect(container.querySelector("pre code")?.className).toContain("language-python");
  });

  it("copies each fenced output independently without surrounding prose or fences", async () => {
    const title = "fix(agent-mcp): 保持长时委派的父子会话控制";
    const body = [
      "## 关联 Issue",
      "",
      "待创建。创建后填写 `Refs #<issue>`；PR 合入 `milestone-8` 后手动关闭 Issue。",
    ].join("\n");
    const md = [
      "**PR 标题**",
      "",
      "```text",
      title,
      "```",
      "",
      "**PR 正文草稿**",
      "",
      "```markdown",
      body,
      "```",
    ].join("\n");

    render(<AssistantText text={md} />);

    const copyButtons = screen.getAllByRole("button", { name: "复制代码块" });
    expect(copyButtons).toHaveLength(2);

    fireEvent.click(copyButtons[0]);
    await waitFor(() => expect(writeText).toHaveBeenLastCalledWith(title));

    fireEvent.click(copyButtons[1]);
    await waitFor(() => expect(writeText).toHaveBeenLastCalledWith(body));
  });

  it("escapes raw HTML — no script / onerror / onload XSS vectors", () => {
    const md = "<script>alert(1)</script>\n\n<img src=x onerror=alert(2)>";
    const { container } = render(<AssistantText text={md} />);
    expect(container.querySelectorAll("script, [onerror], [onload]")).toHaveLength(0);
  });

  it("renders block math $$...$$ as KaTeX", () => {
    const md = "$$E=mc^2$$";
    const { container } = render(<AssistantText text={md} />);
    expect(container.querySelector(".katex")).toBeTruthy();
  });

  it("renders inline math $...$ as KaTeX", () => {
    const md = "展开 $x$ 是输出";
    const { container } = render(<AssistantText text={md} />);
    expect(container.querySelector(".katex")).toBeTruthy();
  });

  it("does not swallow currency $...$ into math (GitHub closing rule)", () => {
    const md = "价格 $100 优惠，恢复 $50。";
    const { container } = render(<AssistantText text={md} />);
    expect(container.querySelector(".katex")).toBeNull();
  });
});
