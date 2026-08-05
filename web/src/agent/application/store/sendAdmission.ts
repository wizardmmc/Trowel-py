/** 原子执行会话发送准入，并在通过时写入乐观 turn。 */

import type { Turn } from "../../domain/reducer";
import {
  CLEARED_TRANSPORT_ISSUE,
  isRootTurnInFlight,
  transportIssueFromMessage,
  type PerSessionState,
} from "./sessionState";

export const MAX_RUNNING = 5;
export const MAX_CONNECTIONS = 20;

interface SendAdmission {
  readonly accepted: boolean;
  readonly sessions: Readonly<Record<string, PerSessionState>>;
}

/**
 * 原子检查发送上限并写入乐观 turn。
 * 调用方必须在同一个 Zustand set 回调中提交返回的 sessions。
 */
export function admitSessionSend(
  sessions: Readonly<Record<string, PerSessionState>>,
  sid: string,
  turn: Turn,
  abort: AbortController,
): SendAdmission {
  const session = sessions[sid];
  if (!session) {
    return { accepted: false, sessions };
  }
  if (session.resourceState !== "connected") {
    return rejectWithError(
      sessions,
      sid,
      session,
      session.resourceState === "needs_reconcile"
        ? "会话资源仍待对账，请先重试关闭或复制诊断"
        : "会话资源正在关闭，不能启动新 turn",
    );
  }
  if (isRootTurnInFlight(session)) return { accepted: false, sessions };
  if (session.commandPending) {
    return rejectWithError(
      sessions,
      sid,
      session,
      `/${session.commandPending} 正在启动，请等待当前操作完成`,
    );
  }

  const running = Object.values(sessions).filter(
    (candidate) =>
      candidate.sessionKind !== "delegate" && isRootTurnInFlight(candidate),
  ).length;
  if (running >= MAX_RUNNING) {
    return rejectWithError(
      sessions,
      sid,
      session,
      `同时 in-turn 的 session 已达上限（${MAX_RUNNING}），等一个完成或中断`,
    );
  }

  if (!session.connected) {
    const connectedCount = Object.values(sessions).filter(
      (candidate) =>
        candidate.sessionKind !== "delegate" &&
        candidate.connected &&
        !candidate.meta.exited,
    ).length;
    if (connectedCount >= MAX_CONNECTIONS) {
      return rejectWithError(
        sessions,
        sid,
        session,
        `连接数已达上限（${MAX_CONNECTIONS}），请先关闭一些 session`,
      );
    }
  }

  return {
    accepted: true,
    sessions: {
      ...sessions,
      [sid]: {
        ...session,
        turns: [...session.turns, turn],
        phase: "awaiting_first",
        meta: { ...session.meta, lastTurnTokens: null },
        ...CLEARED_TRANSPORT_ISSUE,
        abort,
        connected: true,
        resourceState: "connected",
        turnState: "starting",
        currentTurnId: null,
      },
    },
  };
}

function rejectWithError(
  sessions: Readonly<Record<string, PerSessionState>>,
  sid: string,
  session: PerSessionState,
  transportError: string,
): SendAdmission {
  return {
    accepted: false,
    sessions: {
      ...sessions,
      [sid]: { ...session, ...transportIssueFromMessage(transportError) },
    },
  };
}
