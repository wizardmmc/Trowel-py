/** 验证共享下拉菜单从完整触发框下方展开，不采用选中项覆盖定位。 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { PopperSelect } from "../../components/ui/PopperSelect";

it("uses a bottom-anchored Popper menu with a separate trigger", async () => {
  const onValueChange = vi.fn();
  render(
    <PopperSelect
      ariaLabel="模型"
      value="glm-5.2"
      options={[
        { value: "glm-5.2", label: "glm-5.2" },
        { value: "deepseek-v4-flash", label: "deepseek-v4-flash" },
      ]}
      onValueChange={onValueChange}
    />,
  );

  const trigger = screen.getByRole("combobox", { name: "模型" });
  await userEvent.click(trigger);

  const listbox = await screen.findByRole("listbox");
  const content = listbox.closest("[data-side]");
  expect(content).not.toBeNull();
  expect(content).toHaveAttribute("data-side", "bottom");
  expect(content).not.toContainElement(trigger);
  expect(trigger).toBeVisible();

  await userEvent.click(screen.getByRole("option", { name: "deepseek-v4-flash" }));
  expect(onValueChange).toHaveBeenCalledWith("deepseek-v4-flash");
});
