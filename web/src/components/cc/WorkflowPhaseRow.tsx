import { useState } from "react";
import type { WorkflowAgentInfo, WorkflowPhaseInfo } from "../../api/ccTypes";
import {
  briefWorkflowText,
  formatWorkflowTokens,
  WorkflowCaret,
} from "./WorkflowSummary";

function AgentNode({
  agent,
  defaultOpen,
}: {
  readonly agent: WorkflowAgentInfo;
  readonly defaultOpen: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const hasPreview = Boolean(agent.prompt_preview || agent.result_preview);
  const tokens = agent.tokens ?? 0;
  const toolCalls = agent.tool_calls ?? 0;
  return (
    <div className="cc-agent" data-state={agent.state}>
      <div
        className="cc-agent__head"
        role={hasPreview ? "button" : undefined}
        tabIndex={hasPreview ? 0 : undefined}
        aria-expanded={hasPreview ? open : undefined}
        onClick={hasPreview ? () => setOpen((current) => !current) : undefined}
        onKeyDown={
          hasPreview
            ? (event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  setOpen((current) => !current);
                }
              }
            : undefined
        }
      >
        {hasPreview && <WorkflowCaret open={open} />}
        <span className="cc-agent__dot" aria-hidden="true" />
        <span className="cc-agent__label">{agent.label}</span>
        {agent.model && <span className="cc-agent__model">{agent.model}</span>}
        {agent.last_tool_name && (
          <span className="cc-agent__last">last: {agent.last_tool_name}</span>
        )}
        {toolCalls > 0 && <span className="cc-agent__tools">{toolCalls} tools</span>}
        {tokens > 0 && (
          <span className="cc-agent__tok">{formatWorkflowTokens(tokens)} tok</span>
        )}
      </div>
      {open && hasPreview && (
        <div className="cc-agent__body">
          {agent.prompt_preview && (
            <div className="cc-agent__preview">
              <b>prompt</b>
              {briefWorkflowText(agent.prompt_preview, 200)}
            </div>
          )}
          {agent.result_preview && (
            <div className="cc-agent__preview">
              <b>result</b>
              {briefWorkflowText(agent.result_preview, 200)}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function WorkflowPhaseRow({
  phase,
  agents,
}: {
  readonly phase: WorkflowPhaseInfo;
  readonly agents: readonly WorkflowAgentInfo[];
}) {
  const [open, setOpen] = useState(true);
  const done = agents.filter((agent) => agent.state === "done").length;
  const total = agents.length;
  const running = agents.some((agent) => agent.state === "running");
  return (
    <div className="cc-phase" aria-expanded={open}>
      <div
        className="cc-phase__head"
        role="button"
        tabIndex={0}
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            setOpen((current) => !current);
          }
        }}
      >
        <WorkflowCaret open={open} />
        <span className="cc-phase__title">{phase.title}</span>
        {phase.detail && <span className="cc-phase__detail">{phase.detail}</span>}
        <span className="cc-phase__count">
          {total > 0
            ? running
              ? `${done}/${total} running`
              : `${done}/${total} done`
            : "pending"}
        </span>
      </div>
      {open && total > 0 && (
        <div className="cc-phase__agents">
          {agents.map((agent, index) => (
            <AgentNode
              key={agent.agent_id || index}
              agent={agent}
              defaultOpen={false}
            />
          ))}
        </div>
      )}
    </div>
  );
}
