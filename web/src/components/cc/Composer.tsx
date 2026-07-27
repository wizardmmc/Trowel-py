import { useEffect, useId, useMemo, useRef, useState } from "react";
import { ComposerToolbar, type PermissionFacts } from "./ComposerToolbar";
import type { PermissionPreset } from "./PermissionFactsChip";
import type { EffortControlOption } from "./ModelEffortChip";
import { SlashAutocomplete } from "./SlashAutocomplete";
import {
  flatVisible,
  groupSlashItems,
  type SlashSource,
} from "./slashGroups";
import type { ModelOption, SlashItem } from "../../api/cc";

interface ComposerProps {
  readonly streaming: boolean;
  readonly disabled: boolean;
  readonly awaitingInput?: boolean;
  readonly onSend: (text: string) => void;
  readonly onInterrupt: () => void;
  // 省略 slashItems 时保持原始文本直发。
  readonly slashItems?: readonly SlashItem[];
  readonly onLocalCommand?: (item: SlashItem, rawText: string) => void;
  readonly slashLoading?: boolean;
  readonly slashError?: string | null;
  readonly onRetrySlashItems?: () => void;
  readonly onRequestModelPicker?: () => void;
  readonly onRequestEffortPicker?: () => void;
  readonly models?: readonly ModelOption[];
  readonly efforts?: readonly EffortControlOption[];
  readonly currentModelAlias?: string | null;
  readonly currentEffort?: string | null;
  readonly onPickModel?: (alias: string) => void;
  readonly onPickEffort?: (value: string) => void;
  readonly modelCatalogError?: string | null;
  readonly onRetryModelCatalog?: () => void;
  readonly settingsDisabled?: boolean;
  readonly permissionFacts?: PermissionFacts | null;
  readonly onSelectPermissionPreset?: (preset: PermissionPreset) => void;
  readonly memoryEnabled?: boolean | null;
  readonly profileEnabled?: boolean | null;
}

function closestEnabledIndex(items: readonly SlashItem[], index: number): number {
  if (items.length === 0) return 0;
  const clamped = Math.min(Math.max(index, 0), items.length - 1);
  if (!items[clamped].disabled) return clamped;
  for (let distance = 1; distance < items.length; distance += 1) {
    const after = clamped + distance;
    if (after < items.length && !items[after].disabled) return after;
    const before = clamped - distance;
    if (before >= 0 && !items[before].disabled) return before;
  }
  return clamped;
}

function moveEnabledIndex(
  items: readonly SlashItem[],
  index: number,
  step: -1 | 1,
): number {
  for (let next = index + step; next >= 0 && next < items.length; next += step) {
    if (!items[next].disabled) return next;
  }
  return index;
}

export function Composer({
  streaming,
  disabled,
  awaitingInput,
  onSend,
  onInterrupt,
  slashItems,
  onLocalCommand,
  slashLoading = false,
  slashError = null,
  onRetrySlashItems,
  onRequestModelPicker,
  onRequestEffortPicker,
  models,
  efforts,
  currentModelAlias,
  currentEffort,
  onPickModel,
  onPickEffort,
  modelCatalogError,
  onRetryModelCatalog,
  settingsDisabled = false,
  permissionFacts,
  onSelectPermissionPreset,
  memoryEnabled,
  profileEnabled,
}: ComposerProps) {
  const [text, setText] = useState("");
  const [acIndex, setAcIndex] = useState(0);
  const [dismissed, setDismissed] = useState(false);
  // 折叠状态必须与键盘索引同层维护，默认收起体量较大的 plugin 组。
  const [collapsedSources, setCollapsedSources] = useState<ReadonlySet<SlashSource>>(
    () => new Set<SlashSource>(["plugin"]),
  );
  const taRef = useRef<HTMLTextAreaElement>(null);
  const autocompleteId = useId();

  const acOpen =
    text.startsWith("/") &&
    ((slashItems?.length ?? 0) > 0 || slashLoading || slashError !== null) &&
    !dismissed;
  const query = acOpen ? text.slice(1) : "";
  const searching = query.trim() !== "";

  // 键盘索引与菜单必须共享同一筛选和排序结果。
  const acGroups = useMemo(
    () => groupSlashItems(slashItems ?? [], query),
    [slashItems, query],
  );
  const acFlat = useMemo(
    () => flatVisible(acGroups, searching, collapsedSources),
    [acGroups, searching, collapsedSources],
  );
  // 折叠或数据变化后，高亮索引不能指向隐藏行。
  const safeIndex = closestEnabledIndex(acFlat, acIndex);

  // 输入框随内容增高，但最多 200px。
  useEffect(() => {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = `${Math.min(ta.scrollHeight, 200)}px`;
  }, [text]);

  function toggleGroup(source: SlashSource) {
    const next = new Set(collapsedSources);
    if (next.has(source)) next.delete(source);
    else next.add(source);
    setCollapsedSources(next);
    const nextLen = flatVisible(acGroups, searching, next).length;
    setAcIndex((i) =>
      closestEnabledIndex(
        flatVisible(acGroups, searching, next),
        Math.min(i, Math.max(0, nextLen - 1)),
      ),
    );
  }

  function findLocalCommand(rawText: string): SlashItem | undefined {
    const match = /^\s*\/([^\s]+)(?:\s|$)/.exec(rawText);
    if (!match) return undefined;
    return slashItems?.find(
      (item) => item.source === "codex" && item.name === match[1],
    );
  }

  function pickItem(item: SlashItem, rawText = `/${item.name}`) {
    if (item.disabled) return;
    if (item.source === "codex") {
      onLocalCommand?.(item, rawText);
      setText("");
      setDismissed(false);
      return;
    }
    // model/effort 命令直接打开选择器，不回填输入框。
    if (item.name === "model" && onRequestModelPicker) {
      onRequestModelPicker();
      setText("");
      setDismissed(false);
      return;
    }
    if (item.name === "effort" && onRequestEffortPicker) {
      onRequestEffortPicker();
      setText("");
      setDismissed(false);
      return;
    }
    setText(`/${item.name} `);
    setDismissed(true);
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (acOpen) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setAcIndex(moveEnabledIndex(acFlat, safeIndex, 1));
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setAcIndex(moveEnabledIndex(acFlat, safeIndex, -1));
        return;
      }
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        const trimmed = text.trim();
        const localCommand = findLocalCommand(trimmed);
        if (localCommand) {
          pickItem(localCommand, trimmed);
          return;
        }
        if (trimmed === "/model" && onRequestModelPicker) {
          onRequestModelPicker();
          setText("");
          setDismissed(false);
          return;
        }
        if (trimmed === "/effort" && onRequestEffortPicker) {
          onRequestEffortPicker();
          setText("");
          setDismissed(false);
          return;
        }
        // 无参数内置命令一次 Enter 即发送。
        if (trimmed === "/cost" || trimmed === "/status") {
          onSend(trimmed);
          setText("");
          setDismissed(false);
          return;
        }
        const item = acFlat[safeIndex];
        if (item) {
          pickItem(item);
          return;
        }
        // 无匹配项时继续走普通提交，保留用户输入的命令参数。
      }
      if (e.key === "Escape") {
        // Esc 优先关闭补全菜单，并保留已输入文本。
        e.preventDefault();
        setDismissed(true);
        return;
      }
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
      return;
    }
    if (e.key === "Escape") {
      if (text.length > 0) {
        e.preventDefault();
        setText("");
        setDismissed(false);
        return;
      }
      if (streaming) {
        e.preventDefault();
        onInterrupt();
      }
    }
  }

  function submit() {
    const trimmed = text.trim();
    if (!trimmed || disabled) return;
    const localCommand = findLocalCommand(trimmed);
    if (localCommand) {
      pickItem(localCommand, trimmed);
      return;
    }
    onSend(trimmed);
    setText("");
    setDismissed(false);
  }

  return (
    <div className="cc-composer">
      {acOpen && (
        <SlashAutocomplete
          groups={acGroups}
          searching={searching}
          collapsed={collapsedSources}
          selectedIndex={safeIndex}
          onSelect={pickItem}
          onHighlight={setAcIndex}
          onToggleGroup={toggleGroup}
          id={autocompleteId}
          loading={slashLoading}
          error={slashError}
          onRetry={onRetrySlashItems}
        />
      )}
      <div className="cc-composer__shell">
        <textarea
          ref={taRef}
          className="cc-composer__input"
          placeholder={
            awaitingInput
              ? "等你回答上方问题（Enter 发送）"
              : "发消息给 Agent（Enter 发送，Shift+Enter 换行，Esc 中断/清空，/ 触发命令补全）"
          }
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            setDismissed(false);
            setAcIndex(0);
          }}
          onKeyDown={handleKeyDown}
          disabled={disabled}
          aria-label="CC 消息输入"
          aria-autocomplete="list"
          aria-expanded={acOpen}
          aria-controls={acOpen ? autocompleteId : undefined}
          aria-activedescendant={
            acOpen && acFlat.length > 0
              ? `${autocompleteId}-option-${safeIndex}`
              : undefined
          }
        />
        <ComposerToolbar
          streaming={streaming}
          sendDisabled={disabled || text.trim().length === 0}
          onSend={submit}
          onInterrupt={onInterrupt}
          models={models}
          efforts={efforts}
          currentModelAlias={currentModelAlias}
          currentEffort={currentEffort}
          onPickModel={onPickModel}
          onPickEffort={onPickEffort}
          modelCatalogError={modelCatalogError}
          onRetryModelCatalog={onRetryModelCatalog}
          settingsDisabled={settingsDisabled}
          permissionFacts={permissionFacts}
          onSelectPermissionPreset={onSelectPermissionPreset}
          memoryEnabled={memoryEnabled}
          profileEnabled={profileEnabled}
        />
      </div>
    </div>
  );
}
