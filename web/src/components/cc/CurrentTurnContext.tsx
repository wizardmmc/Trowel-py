/** 在长消息流顶部显示当前 turn 的用户消息和跳转入口。 */

interface CurrentTurnContextProps {
  readonly text: string;
  readonly visible: boolean;
  readonly onJump: () => void;
}

export function CurrentTurnContext({
  text,
  visible,
  onJump,
}: CurrentTurnContextProps) {
  if (!text) return null;
  return (
    <div
      className={`cc-turn-context${visible ? " cc-turn-context--visible" : ""}`}
      aria-hidden={!visible}
    >
      <button
        type="button"
        className="cc-turn-context__button"
        tabIndex={visible ? 0 : -1}
        title={text}
        aria-label={`回到${text}`}
        onClick={onJump}
      >
        <span className="cc-turn-context__inner">
          <span className="cc-msg__tag">你</span>
          <span className="cc-turn-context__text">{text}</span>
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M12 19V5M6 11l6-6 6 6" />
          </svg>
        </span>
      </button>
    </div>
  );
}
