/** 定义研讨公开快照、创建草稿和确定性交接的前端领域类型。 */

import type { ConnectionSessionConfig, Runtime } from "../../agent";

export type DiscussionStatus =
  | "draft"
  | "running"
  | "waiting_user"
  | "completed"
  | "stopped"
  | "needs_reconcile";

export interface DiscussionParticipant {
  readonly id: string;
  readonly position: number;
  readonly name: string;
  readonly runtime: Runtime;
  readonly connection_name: string | null;
  readonly model: string;
  readonly effective_model: string;
  readonly effort: string | null;
  readonly permission_mode: string | null;
  readonly permission_preset:
    | "follow"
    | "read-only"
    | "workspace-write"
    | "danger-full-access"
    | null;
  readonly memory_enabled: boolean;
  readonly profile_enabled: boolean;
  readonly self_enabled: boolean;
  readonly status: string;
}

export interface DiscussionRoundParticipant {
  readonly participant_id: string;
  readonly current_attempt_id: string | null;
  readonly position: number;
  readonly name: string;
  readonly status: string;
  readonly content: string | null;
  readonly error_code: string | null;
  readonly error_message: string | null;
  readonly usage: Readonly<Record<string, unknown>> | null;
  readonly activity: {
    readonly tool_call_count: number;
    readonly tool_names: Readonly<Record<string, number>>;
    readonly subagent_count: number;
  } | null;
  readonly marked: boolean;
  readonly started_at: string | null;
  readonly completed_at: string | null;
}

export interface DiscussionRound {
  readonly id: string;
  readonly number: number;
  readonly kind: "regular" | "final";
  readonly status: "sealed" | "running" | "published" | "stopped";
  readonly total_participants: number;
  readonly terminal_participants: number;
  readonly started_at: string;
  readonly published_at: string | null;
  readonly stop_reason: string | null;
  readonly participants: readonly DiscussionRoundParticipant[];
}

export interface DiscussionMessage {
  readonly id: string;
  readonly sequence: number;
  readonly after_round_number: number;
  readonly target_scope: "all" | "participant";
  readonly target_participant_id: string | null;
  readonly body: string;
  readonly created_at: string;
}

export interface DiscussionHandoff {
  readonly status: "started";
  readonly version: number;
  readonly discussion_id: string;
  readonly agent_session_id: string;
  readonly turn_id: string;
  readonly created_at: string;
}

export interface Discussion {
  readonly id: string;
  readonly topic: string;
  readonly workdir: string;
  readonly progression_mode: "automatic" | "user_guided";
  readonly max_rounds: number | null;
  readonly status: DiscussionStatus;
  readonly version: number;
  readonly active_round_number: number | null;
  readonly created_at: string;
  readonly updated_at: string;
  readonly completed_at: string | null;
  readonly stopped_at: string | null;
  readonly participants: readonly DiscussionParticipant[];
  readonly messages: readonly DiscussionMessage[];
  readonly rounds: readonly DiscussionRound[];
  readonly handoffs: readonly DiscussionHandoff[];
}

export interface DiscussionSessionConfiguration {
  readonly id: string;
  readonly name: string;
  readonly runtime: Runtime;
  readonly connection_id: string;
  readonly model: string;
  readonly effort: string | null;
  readonly availability: string;
  readonly disabled_reason: string | null;
}

export interface ParticipantDraft {
  readonly localId: string;
  readonly name: string;
  readonly usesDefaultName: boolean;
  readonly session: ConnectionSessionConfig;
}

export interface CreateDiscussionInput {
  readonly request_id: string;
  readonly topic: string;
  readonly workdir: string;
  readonly progression_mode: "automatic" | "user_guided";
  readonly max_rounds: number | null;
  readonly participants: readonly {
    readonly name: string;
    readonly session_configuration_id?: string;
    readonly connection_id?: string;
    readonly model?: string;
    readonly effort?: string | null;
    readonly permission_mode: string | null;
    readonly permission_preset:
      | "follow"
      | "read-only"
      | "workspace-write"
      | "danger-full-access"
      | null;
    readonly memory_enabled: boolean;
    readonly profile_enabled: boolean;
    readonly self_enabled: boolean;
  }[];
}

export interface HandoffAgentInput extends ConnectionSessionConfig {
  readonly workdir: string;
}

export interface HandoffResult {
  readonly status: "started";
  readonly version: number;
  readonly discussion_id: string;
  readonly agent_session_id: string;
  readonly turn_id: string;
}

export interface DiscussionEvent {
  readonly sequence: number;
  readonly discussion_id: string;
  readonly type: string;
  readonly version: number;
  readonly round_number: number | null;
}

export interface DiscussionAttemptEvent {
  readonly type: "attempt_event";
  readonly discussion_id: string;
  readonly round_number: number;
  readonly participant_id: string;
  readonly attempt_id: string;
  readonly attempt_sequence: number;
  readonly event: import("../../agent/transport/agentEvent").AgentEvent;
}

export interface DiscussionAttemptGap {
  readonly type: "attempt_gap";
  readonly discussion_id: string;
  readonly round_number: number;
  readonly participant_id: string;
  readonly attempt_id: string;
}

export type DiscussionStreamEvent =
  | DiscussionEvent
  | DiscussionAttemptEvent
  | DiscussionAttemptGap;
