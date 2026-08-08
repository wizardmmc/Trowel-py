/** 接收给 Agent 的指令、工作区和会话条件，把研讨现场作为系统背景提交后端确定性交接。 */

import { useState } from "react";
import { createPortal } from "react-dom";
import {
  ConnectionSessionEditor,
  defaultConnectionSessionConfig,
  type ConnectionSessionConfig,
} from "../../agent";
import type { AgentConnectionOption } from "../../agent/transport";
import type { Discussion, HandoffAgentInput } from "../domain";

interface HandoffDialogProps {
  readonly discussion: Discussion;
  readonly connections: readonly AgentConnectionOption[];
  readonly pending: boolean;
  readonly onChooseWorkdir: (current: string, apply: (path: string) => void) => void;
  readonly onSubmit: (agent: HandoffAgentInput, instruction: string) => void;
  readonly onCancel: () => void;
}

/** 交接工作区和会话条件与新建研讨草稿彼此独立。 */
export function HandoffDialog({
  discussion,
  connections,
  pending,
  onChooseWorkdir,
  onSubmit,
  onCancel,
}: HandoffDialogProps) {
  const [workdir, setWorkdir] = useState(discussion.workdir);
  const [instruction, setInstruction] = useState("");
  const [session, setSession] = useState<ConnectionSessionConfig>(() =>
    defaultConnectionSessionConfig(connections, undefined, "agent"),
  );
  const markedCount = discussion.rounds.reduce(
    (total, round) => total + round.participants.filter((item) => item.marked).length,
    0,
  );
  const finalRound = discussion.rounds.findLast(
    (round) => round.status === "published",
  );

  return createPortal(
    <div className="cc-dialog__backdrop" onMouseDown={onCancel} role="presentation">
      <section
        className="cc-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="在 Agent 中继续"
        onMouseDown={(event) => event.stopPropagation()}
        onKeyDown={(event) => {
          if (event.key === "Escape") onCancel();
        }}
      >
        <div className="cc-dialog__head">
          <p className="cc-dialog__title">在 Agent 中继续</p>
          <span className="cc-dialog__workdir" title={discussion.topic}>
            {discussion.topic}
          </span>
        </div>
        <div className="cc-dialog__body">
          <div className="discussion-handoff-preview">
            <strong>交接现场</strong>
            <ul>
              <li>原始问题和 {Math.max(0, discussion.messages.length - 1)} 条用户补充</li>
              <li>
                {discussion.participants.length} 位参与者的第 {finalRound?.number ?? 0} 轮公开立场
              </li>
              <li>{markedCount} 条用户标记</li>
              <li>完整记录由后端附加应用只读链接</li>
            </ul>
          </div>
          <label className="discussion-field">
            <span>给 Agent 的指令</span>
            <textarea
              value={instruction}
              disabled={pending}
              placeholder="说明接下来要做什么；交接现场会作为系统背景一并提供"
              autoFocus
              onChange={(event) => setInstruction(event.target.value)}
            />
          </label>
          <div className="discussion-field">
            <span>工作区</span>
            <button
              type="button"
              className="discussion-workspace-field"
              onClick={() => onChooseWorkdir(workdir, setWorkdir)}
            >
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z" />
              </svg>
              <span className="discussion-workspace-field__main">
                <strong>{basename(workdir)}</strong>
                <small>{workdir}</small>
              </span>
              <b>更换</b>
            </button>
          </div>
          <ConnectionSessionEditor
            value={session}
            connections={connections}
            disabled={pending}
            onChange={setSession}
          />
        </div>
        <div className="cc-dialog__foot">
          <button type="button" className="cc-dialog__btn" onClick={onCancel}>
            取消
          </button>
          <button
            type="button"
            className="cc-dialog__btn cc-dialog__btn--primary"
            disabled={
              pending ||
              !instruction.trim() ||
              !session.connection_id ||
              !session.model
            }
            onClick={() => onSubmit({ ...session, workdir }, instruction.trim())}
          >
            {pending ? "正在创建…" : "创建并发送指令"}
          </button>
        </div>
      </section>
    </div>,
    document.body,
  );
}

function basename(path: string): string {
  return path.split("/").filter(Boolean).at(-1) ?? path;
}
