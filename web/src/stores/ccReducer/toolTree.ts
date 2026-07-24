import type { ReducerState, ToolItem, Turn, TurnItem } from "./model";

/** 递归更新 turn item 树中匹配 toolUseId 的工具。 */
export function updateToolInTree(
  items: readonly TurnItem[],
  toolUseId: string,
  update: (tool: ToolItem) => ToolItem,
): readonly TurnItem[] | null {
  let found = false;

  const updateTool = (tool: ToolItem): ToolItem => {
    if (tool.toolUseId === toolUseId) {
      found = true;
      return update(tool);
    }
    if (tool.childTools.length === 0) {
      return tool;
    }
    return { ...tool, childTools: tool.childTools.map(updateTool) };
  };

  const result = items.map((item) =>
    item.kind === "tool" ? updateTool(item) : item,
  );
  return found ? result : null;
}

/** 将子工具挂到当前 turn 中匹配的父工具；找不到父工具时返回 null。 */
export function attachChildToParent(
  prev: ReducerState,
  parentToolUseId: string,
  child: ToolItem,
): ReducerState | null {
  const turns = prev.turns;
  if (turns.length === 0) return null;

  const last = turns[turns.length - 1];
  const items = updateToolInTree(last.items, parentToolUseId, (parent) => ({
    ...parent,
    childTools: [...parent.childTools, child],
  }));
  if (items === null) return null;

  const updated: Turn = { ...last, items };
  return { ...prev, turns: [...turns.slice(0, -1), updated] };
}

/** 更新当前 turn 中的工具；找不到 toolUseId 时保持原状态。 */
export function updateToolInCurrentTurn(
  prev: ReducerState,
  toolUseId: string,
  update: (tool: ToolItem) => ToolItem,
): ReducerState {
  const turns = prev.turns;
  if (turns.length === 0) return prev;

  const last = turns[turns.length - 1];
  const items = updateToolInTree(last.items, toolUseId, update);
  if (items === null) return prev;

  const updated: Turn = { ...last, items };
  return { ...prev, turns: [...turns.slice(0, -1), updated] };
}
