import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { AssistantText } from "../components/cc/AssistantText";

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
    expect(root.querySelector("code")).toBeTruthy(); // inline code
    expect(root.querySelector("strong")).toBeTruthy(); // **bold**
    const link = root.querySelector("a");
    expect(link?.getAttribute("href")).toBe("https://x.io");
    expect(root.querySelectorAll("li")).toHaveLength(2); // list
    expect(root.querySelector("pre")).toBeTruthy(); // fenced code block
  });

  it("surfaces the fenced code block's language as a corner badge", () => {
    const md = "```python\nprint(1)\n```";
    const { container } = render(<AssistantText text={md} />);
    expect(container.querySelector(".cc-md-codeblock__lang")?.textContent).toBe("python");
    // the code element itself keeps react-markdown's language class
    expect(container.querySelector("pre code")?.className).toContain("language-python");
  });

  it("escapes raw HTML — no script / onerror / onload XSS vectors", () => {
    const md = "<script>alert(1)</script>\n\n<img src=x onerror=alert(2)>";
    const { container } = render(<AssistantText text={md} />);
    // react-markdown default (no rehype-raw) never materializes raw HTML
    // into real elements/attributes.
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
    // `$50` has a space before it → not a math close → no .katex span.
    const md = "价格 $100 优惠，恢复 $50。";
    const { container } = render(<AssistantText text={md} />);
    expect(container.querySelector(".katex")).toBeNull();
  });
});
