/** 展示新会话的 Memory 与 Profile 开关及其隔离说明。 */

interface SessionPreferencesProps {
  readonly memoryDescription: string;
  readonly isolationNote: string;
  readonly memory: boolean;
  readonly profile: boolean;
  readonly creating: boolean;
  readonly onToggleMemory: () => void;
  readonly onToggleProfile: () => void;
}

export function SessionPreferences({
  memoryDescription,
  isolationNote,
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
        desc={memoryDescription}
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
      <p className="cc-dialog__note">{isolationNote}</p>
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
