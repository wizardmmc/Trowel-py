/**
 * Agent 会话的纯事件 reducer，也是 TrowelEvent 改变领域状态的唯一入口。
 * live SSE 与 history replay 共用 reduceEvent；Zustand 和连接编排留在 application。
 */
import type {
  RetryingEvent,
  TrowelEvent,
} from "../transport/events";
import {
  applyTextEvent,
  applyThinkingEvent,
  applyThinkingProgress,
} from "./reducer/content";
import {
  applyHostStatus,
  applyRateLimitUpdated,
  applyUsageUpdated,
} from "./reducer/codexMeta";
import {
  type ReducerState,
  type RetryingItem,
  type Turn,
  type TurnItem,
  type TurnStatus,
} from "./reducer/model";
import {
  applyApprovalRequest,
  applyElicitationRequest,
} from "./reducer/requests";
import { applySubagentProgress } from "./reducer/subagents";
import {
  applyErrorEvent,
  applyFinishedEvent,
  applyInterruptedEvent,
} from "./reducer/terminal";
import {
  applyToolCall,
  applyToolProgress,
  applyToolResult,
} from "./reducer/tools";
import {
  applyTurnStart,
  applyUserEvent,
} from "./reducer/turns";
import { applyWorkflowTree } from "./reducer/workflows";

export * from "./reducer/model";
export {
  endActiveTurnOnStreamClose,
  finalizeHistoryForView,
} from "./reducer/lifecycle";
export {
  _resetTurnIdCounterForTests,
  nextTurnId,
} from "./reducer/turns";

function appendToCurrentTurn(
  prev: ReducerState,
  item: TurnItem,
  status?: TurnStatus,
): ReducerState {
  const turns = prev.turns;
  if (turns.length === 0) {
    return prev;
  }
  const last = turns[turns.length - 1];
  const updatedLast: Turn = {
    ...last,
    items: [...last.items, item],
    status: status ?? last.status,
  };
  return { ...prev, turns: [...turns.slice(0, -1), updatedLast] };
}

export function reduceEvent(prev: ReducerState, event: TrowelEvent): ReducerState {
  if (event.type !== "stalled_warning" && prev.meta.stallWarning !== null) {
    prev = { ...prev, meta: { ...prev.meta, stallWarning: null } };
  }
  switch (event.type) {
    case "session_started":
      return {
        ...prev,
        phase: prev.phase === "awaiting_first" ? "generating" : prev.phase,
        meta: {
          ...prev.meta,
          model: event.model,
          ccSessionId: event.cc_session_id,
        },
      };

    case "turn_start":
      return applyTurnStart(prev, event);

    case "user":
      return applyUserEvent(prev, event);

    case "text": {
      return applyTextEvent(prev, event);
    }

    case "thinking_progress": {
      return applyThinkingProgress(prev, event);
    }

    case "thinking": {
      return applyThinkingEvent(prev, event);
    }

    case "tool_call": {
      return applyToolCall(prev, event);
    }

    case "tool_progress":
      return applyToolProgress(prev, event);

    case "tool_result": {
      return applyToolResult(prev, event);
    }

    case "elicit_request":
      return applyElicitationRequest(prev, event);

    case "approval_request":
      return applyApprovalRequest(prev, event);

    case "subagent_progress":
      return applySubagentProgress(prev, event);

    case "retrying":
      return appendToCurrentTurn(
        { ...prev, phase: "retrying" },
        retryingItemFrom(event),
      );

    case "hook":
      return { ...prev, meta: { ...prev.meta, hookFired: event.hook_name } };

    case "status":
      if (event.stage === "compacting") {
        return { ...prev, phase: "compacting" };
      }
      if (event.stage === "background_waiting") {
        return { ...prev, phase: "background_waiting" };
      }
      return prev;

    case "compact_boundary":
      return applyCompletedCompaction(prev);

    case "compaction":
      return event.phase === "completed"
        ? applyCompletedCompaction(prev)
        : prev;

    case "local_command":
      return appendToCurrentTurn(prev, {
        kind: "local_command",
        content: event.content,
      });

    case "finished": {
      return applyFinishedEvent(prev, event);
    }

    case "error":
      return applyErrorEvent(prev, event);

    case "interrupted":
      return applyInterruptedEvent(prev);

    case "stalled_warning":
      return {
        ...prev,
        meta: {
          ...prev.meta,
          stallWarning: {
            severity: event.severity,
            elapsed_s: event.elapsed_s,
          },
        },
      };

    case "model_changed": {
      const nextModel = event.model ?? prev.meta.model;
      if (nextModel === prev.meta.model) return prev;
      return { ...prev, meta: { ...prev.meta, model: nextModel } };
    }

    case "session_exited":
      return {
        ...prev,
        meta: {
          ...prev.meta,
          exited: true,
          exitReturncode: event.returncode,
        },
      };

    case "workflow_tree":
      return applyWorkflowTree(prev, event);

    case "usage_updated":
      return applyUsageUpdated(prev, event);

    case "host_status":
      return applyHostStatus(prev, event);

    case "rate_limit_updated":
      return applyRateLimitUpdated(prev, event);

    case "goal_updated":
      return {
        ...prev,
        goal: {
          objective: event.objective,
          status: event.status,
          tokenBudget: event.token_budget,
          tokensUsed: event.tokens_used,
          timeUsedSeconds: event.time_used_seconds,
          createdAt: event.created_at,
          updatedAt: event.updated_at,
        },
      };

    case "goal_cleared":
      return { ...prev, goal: null };

    case "plan_updated":
      return {
        ...prev,
        plan: {
          explanation: event.explanation,
          steps: event.steps.map((step) => ({ ...step })),
        },
      };

    case "turn_diff_updated":
      return {
        ...prev,
        turnDiff: { turnId: event.turn_id, diff: event.diff },
      };

    default:
      return prev;
  }
}

function applyCompletedCompaction(prev: ReducerState): ReducerState {
  return appendToCurrentTurn(
    {
      ...prev,
      meta: {
        ...prev.meta,
        compactionCount: prev.meta.compactionCount + 1,
      },
    },
    { kind: "compact_boundary" },
  );
}

function retryingItemFrom(event: RetryingEvent): RetryingItem {
  return {
    kind: "retrying",
    attempt: event.attempt,
    maxRetries: event.max_retries,
    errorStatus: event.error_status,
    error: event.error,
    retryDelayMs: event.retry_delay_ms,
  };
}
