/**
 * codexReducer — minimal Codex event → ReducerState mapping (slice-072).
 *
 * Codex events arrive as runtime-tagged dicts (CodexEvent.as_dict from the
 * backend codex_host translator). slice-072 maps the core turn lifecycle
 * onto the SAME ReducerState shape ccReducer uses, so MessageList +
 * MultiSessionBar render Codex and CC sessions with one component set
 * (spec C-6: capability-driven, not runtime === ...).
 *
 * Payload field names follow the translator's real output (verified against
 * trowel_py/codex_host/translator.py): assistant/reasoning deltas ride
 * ``payload.delta``; the final assistant_message carries ``payload.text``;
 * command tool events carry ``payload.command``. Unknown / unmapped event
 * types are a no-op (returned unchanged) — the unified rich timeline is
 * slice-074+.
 *
 * host_status(host_exited) is a TURN terminal, not a row exit: spec §4 keeps
 * the binding so the next send can resume — the running turn flips to error
 * but the row stays. (Row deletion on CC session_exited is the store's job.)
 *
 * Pure + immutable like ccReducer: every case builds a new ReducerState via
 * spread. The zustand shell (ccStore) dispatches to this or ccReducer based
 * on the event's `runtime` tag.
 */
import { nextTurnId, type ReducerState, type Turn } from "./ccReducer";

/** One Codex SSE event (mirror of CodexEvent.as_dict on the backend). */
export interface CodexEvent {
  readonly type: string;
  readonly runtime?: "codex";
  readonly thread_id?: string | null;
  readonly turn_id?: string | null;
  readonly item_id?: string | null;
  readonly payload: Readonly<Record<string, unknown>>;
}

/** Read a string field off a Codex payload, or null when absent/non-string. */
function strField(
  payload: Readonly<Record<string, unknown>>,
  key: string,
): string | null {
  const v = payload[key];
  return typeof v === "string" ? v : null;
}

type TurnItem = Turn["items"][number];

/** Append one item to the last turn (no-op when there is no turn yet). */
function appendItem(prev: ReducerState, item: TurnItem): ReducerState {
  const turns = prev.turns;
  if (turns.length === 0) return prev;
  const last = turns[turns.length - 1];
  const updated: Turn = { ...last, items: [...last.items, item] };
  return { ...prev, turns: [...turns.slice(0, -1), updated] };
}

/** Immutably rewrite the last turn (no-op when there are no turns). */
function withLastTurn(
  prev: ReducerState,
  fn: (t: Turn) => Turn,
): ReducerState {
  const turns = prev.turns;
  if (turns.length === 0) return prev;
  const last = turns[turns.length - 1];
  return { ...prev, turns: [...turns.slice(0, -1), fn(last)] };
}

/**
 * Reduce one Codex event into a new ReducerState.
 *
 * Unknown types are a no-op (slice-072 intentionally does not render every
 * Codex item — those land in slice-074). Terminal types (finished /
 * interrupted / error / host_exited) flip both the turn status and the phase
 * so the UI's spinner clears exactly once.
 */
export function reduceCodexEvent(
  prev: ReducerState,
  event: CodexEvent,
): ReducerState {
  switch (event.type) {
    case "session_started":
      return {
        ...prev,
        phase: prev.phase === "awaiting_first" ? "generating" : prev.phase,
        meta: {
          ...prev.meta,
          model: strField(event.payload, "model") ?? prev.meta.model,
          ccSessionId: event.thread_id ?? prev.meta.ccSessionId,
        },
      };

    case "user": {
      const turn: Turn = {
        id: nextTurnId(),
        userText: strField(event.payload, "text") ?? "",
        items: [],
        status: "active",
        turnId: null,
        revertible: false,
      };
      return { ...prev, turns: [...prev.turns, turn] };
    }

    case "turn_started":
      return withLastTurn(prev, (t) => ({
        ...t,
        turnId: event.turn_id ?? t.turnId,
      }));

    case "assistant_delta": {
      // translator: payload.delta is the streamed fragment.
      const text = strField(event.payload, "delta") ?? "";
      const last = prev.turns[prev.turns.length - 1];
      const lastItem = last?.items[last.items.length - 1];
      if (lastItem && lastItem.kind === "text") {
        return withLastTurn({ ...prev, phase: "generating" }, (t) => ({
          ...t,
          items: [
            ...t.items.slice(0, -1),
            { kind: "text", text: lastItem.text + text },
          ],
        }));
      }
      return appendItem({ ...prev, phase: "generating" }, {
        kind: "text",
        text,
      });
    }

    case "assistant_message": {
      // The deltas already accumulated the full text; the final assistant_message
      // carries the same text and is dropped to avoid duplicating it.
      return prev;
    }

    case "reasoning_delta": {
      // translator: payload.delta is the streamed reasoning fragment.
      const text = strField(event.payload, "delta") ?? "";
      const last = prev.turns[prev.turns.length - 1];
      const lastItem = last?.items[last.items.length - 1];
      if (lastItem && lastItem.kind === "thinking") {
        return withLastTurn({ ...prev, phase: "thinking" }, (t) => ({
          ...t,
          items: [
            ...t.items.slice(0, -1),
            { kind: "thinking", text: lastItem.text + text },
          ],
        }));
      }
      return appendItem({ ...prev, phase: "thinking" }, {
        kind: "thinking",
        text,
      });
    }

    case "tool_started": {
      // translator: commandExecution items carry payload.command (the shell
      // command). Render as a tool card named "command" with the command input.
      const command = strField(event.payload, "command");
      return appendItem({ ...prev, phase: "tool" }, {
        kind: "tool",
        toolUseId: event.item_id ?? "codex-tool",
        toolName: command ? "command" : "tool",
        input: command ? { command } : {},
        status: "running",
        elapsedSeconds: null,
        result: null,
        childTools: [],
      });
    }

    case "tool_completed": {
      const id = event.item_id;
      const result =
        strField(event.payload, "output") ?? strField(event.payload, "result");
      return withLastTurn(prev, (t) => ({
        ...t,
        items: t.items.map((it) =>
          it.kind === "tool" && id !== null && it.toolUseId === id
            ? { ...it, status: "done", result: result ?? it.result }
            : it,
        ),
      }));
    }

    case "finished":
      return withLastTurn({ ...prev, phase: "done" }, (t) => ({
        ...t,
        status: "done",
      }));

    case "interrupted":
      return withLastTurn({ ...prev, phase: "interrupted" }, (t) => ({
        ...t,
        status: "interrupted",
      }));

    case "error":
      return withLastTurn({ ...prev, phase: "error" }, (t) => ({
        ...t,
        status: "error",
      }));

    case "host_status": {
      const status = strField(event.payload, "status");
      if (status === "host_exited") {
        // spec §4: the binding survives so the next send can resume — this is
        // a TURN terminal (running turn ends in error), NOT a row exit. The
        // store keeps the row; only the active turn is closed out.
        return withLastTurn({ ...prev, phase: "error" }, (t) => ({
          ...t,
          status: "error",
        }));
      }
      return prev;
    }

    default:
      return prev;
  }
}
