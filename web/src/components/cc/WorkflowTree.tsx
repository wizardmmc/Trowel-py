import { useState } from "react";
import type { WorkflowAgentInfo } from "../../api/ccTypes";
import type { WorkflowItem } from "../../stores/ccReducer";
import { WorkflowPhaseRow } from "./WorkflowPhaseRow";
import {
  briefWorkflowText,
  WorkflowCaret,
  WorkflowSummary,
} from "./WorkflowSummary";

interface WorkflowTreeProps {
  readonly workflow: WorkflowItem;
  readonly workdir?: string;
}

export function WorkflowTree({ workflow }: WorkflowTreeProps) {
  const [open, setOpen] = useState(true);
  const grouped = new Map<string, WorkflowAgentInfo[]>();
  for (const phase of workflow.phases) grouped.set(phase.title, []);
  const other: WorkflowAgentInfo[] = [];
  for (const agent of workflow.agents) {
    const bucket =
      agent.phase_title && grouped.has(agent.phase_title)
        ? grouped.get(agent.phase_title)
        : null;
    if (bucket) bucket.push(agent);
    else other.push(agent);
  }

  return (
    <div className="cc-workflow" data-status={workflow.status} aria-expanded={open}>
      <div
        className="cc-workflow__header"
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
        <WorkflowSummary workflow={workflow} />
      </div>
      {open && (
        <div className="cc-workflow__body">
          {workflow.error && (
            <div className="cc-wf-error">
              <div className="cc-wf-error__label">
                {workflow.status === "killed" ? "Workflow aborted" : "Workflow failed"}
              </div>
              {briefWorkflowText(workflow.error, 240)}
            </div>
          )}
          {workflow.phases.map((phase) => (
            <WorkflowPhaseRow
              key={phase.title}
              phase={phase}
              agents={grouped.get(phase.title) ?? []}
            />
          ))}
          {other.length > 0 && (
            <WorkflowPhaseRow
              phase={{ title: "(other)", detail: null }}
              agents={other}
            />
          )}
        </div>
      )}
    </div>
  );
}
