import { useState } from "react";
import { createPortal } from "react-dom";

/**
 * slice-060 — the "new session" setup dialog.
 *
 * Replaces the old "click + → instant startSession" flow. The user first picks
 * the Memory / Profile A/B condition (both default ON = the prior production
 * behavior, zero-regression), THEN creates. Cancelling fires onCancel and
 * creates NO backend session (no temp row to clean up).
 *
 * The two switches are independent (off/on is a first-class combo). They are
 * frozen at create: this dialog is the only place to choose them — once a
 * session exists, the only way to change its condition is to make a new one.
 *
 * Rendered through a portal to document.body so it escapes .cc-view's
 * overflow clip (same reason ModelEffortChip's picker uses a portal).
 */
interface NewSessionDialogProps {
  readonly workdir: string;
  /** Fired with the chosen switches on "创建会话". The caller creates the session. */
  readonly onCreate: (memoryEnabled: boolean, profileEnabled: boolean) => void;
  /** Fired on backdrop click / 取消 — no session is created. */
  readonly onCancel: () => void;
}

export function NewSessionDialog({
  workdir,
  onCreate,
  onCancel,
}: NewSessionDialogProps) {
  const [memory, setMemory] = useState(true);
  const [profile, setProfile] = useState(true);

  return createPortal(
    <div
      className="cc-dialog__backdrop"
      onClick={onCancel}
      role="presentation"
    >
      <div
        className="cc-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="新会话设置"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === "Escape") onCancel();
        }}
      >
        <div className="cc-dialog__head">
          <p className="cc-dialog__title">新会话</p>
          <span className="cc-dialog__workdir" title={workdir}>
            {workdir}
          </span>
        </div>
        <div className="cc-dialog__body">
          <SwitchRow
            name="Memory"
            desc="给模型读你存的记忆：铁律、dictionary 笔记、近期日记，并挂 memory MCP 让它能搜索。关掉做无记忆基线。"
            on={memory}
            onToggle={() => setMemory((v) => !v)}
          />
          <SwitchRow
            name="Profile"
            desc={'把"你是谁"画像段注入提示词。关掉只消融显式画像，记忆里的日记仍可能带个人信息。'}
            on={profile}
            onToggle={() => setProfile((v) => !v)}
          />
        </div>
        <p className="cc-dialog__note">
          做对照实验请用全新会话，不要 resume——带历史上下文的会话不是干净样本。
        </p>
        <div className="cc-dialog__foot">
          <button type="button" className="cc-dialog__btn" onClick={onCancel}>
            取消
          </button>
          <button
            type="button"
            className="cc-dialog__btn cc-dialog__btn--primary"
            onClick={() => onCreate(memory, profile)}
          >
            创建会话
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

function SwitchRow({
  name,
  desc,
  on,
  onToggle,
}: {
  readonly name: string;
  readonly desc: string;
  readonly on: boolean;
  readonly onToggle: () => void;
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
      />
    </div>
  );
}
