import { useEffect, useMemo, useRef, useState } from "react";
import { ComposerToolbar, type PermissionFacts } from "./ComposerToolbar";
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
  readonly memoryEnabled?: boolean | null;
  readonly profileEnabled?: boolean | null;
}

export function Composer({
  streaming,
  disabled,
  awaitingInput,
  onSend,
  onInterrupt,
  slashItems,
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

  const acOpen =
    text.startsWith("/") &&
    (slashItems?.length ?? 0) > 0 &&
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
  const safeIndex = Math.min(acIndex, Math.max(0, acFlat.length - 1));

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
    setAcIndex((i) => Math.min(i, Math.max(0, nextLen - 1)));
  }

  function pickItem(item: SlashItem) {
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
      const last = acFlat.length - 1;
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setAcIndex((i) => Math.min(last, i + 1));
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setAcIndex((i) => Math.max(0, i - 1));
        return;
      }
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        const trimmed = text.trim();
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
          onToggleGroup={toggleGroup}
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
          memoryEnabled={memoryEnabled}
          profileEnabled={profileEnabled}
        />
      </div>
    </div>
  );
}
