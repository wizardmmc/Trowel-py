import type { Runtime } from "../../api/agent";

interface SessionPreferencesProps {
  readonly runtime: Runtime;
  readonly memory: boolean;
  readonly profile: boolean;
  readonly creating: boolean;
  readonly onToggleMemory: () => void;
  readonly onToggleProfile: () => void;
}

export function SessionPreferences({
  runtime,
  memory,
  profile,
  creating,
  onToggleMemory,
  onToggleProfile,
}: SessionPreferencesProps) {
  return (
    <>
      <SwitchRow
        name="Memory"
        desc={
          runtime === "codex"
            ? "注入 trowel 记忆并挂 memory MCP；Codex native memories 保持关闭。"
            : "给模型读你存的记忆：铁律、dictionary 笔记、近期日记，并挂 memory MCP。关掉做无记忆基线。"
        }
        on={memory}
        onToggle={onToggleMemory}
        disabled={creating}
      />
      <SwitchRow
        name="Profile"
        desc={'把"你是谁"画像段注入提示词。关掉只消融显式画像。'}
        on={profile}
        onToggle={onToggleProfile}
        disabled={creating}
      />
      <p className="cc-dialog__note">
        {runtime === "codex"
          ? "选择 Codex 只决定这个 session 的 runtime，不会改变已运行的 GLM 会话，也不会修改 cc-switch 配置。"
          : "选择 Claude Code 继续使用 CCHost；它与已运行的 Codex session 互不切换、互不 resume。"}
      </p>
    </>
  );
}

function SwitchRow({
  name,
  desc,
  on,
  onToggle,
  disabled,
}: {
  readonly name: string;
  readonly desc: string;
  readonly on: boolean;
  readonly onToggle: () => void;
  readonly disabled: boolean;
}) {
  return (
    <div className="cc-dialog__row">
      <div className="cc-dialog__main">
        <div className="cc-dialog__name">{name}</div>
        <div className="cc-dialog__desc">{desc}</div>
      </div>
      <button
        type="button"
        className={`cc-toggle${on ? " cc-toggle--on" : ""}`}
        onClick={onToggle}
        role="switch"
        aria-checked={on}
        aria-label={`${name} 开关`}
        disabled={disabled}
      />
    </div>
  );
}
