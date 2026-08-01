/** 在真实 SessionView 上回放大回合并记录 React 渲染性能。 */

import {
  memo,
  Profiler,
  useCallback,
  useEffect,
  useRef,
  useState,
  type ProfilerOnRenderCallback,
} from "react";

import {
  reduceAgentEvent,
  type PerSessionState,
  useAgentStore,
} from "../agent/application";
import { getExpectedRuntimePresentation } from "../agent/runtimes";
import { SessionView } from "../agent/ui";
import {
  LARGE_TURN_MANIFEST,
  buildLargeTurnReplay,
} from "./largeTurnReplay";

const LARGE_SESSION_ID = "fixture-large-turn";
const LIGHT_SESSION_ID = "fixture-light-session";
const WORKDIR = "/fixture/repo";

type ReplayStatus = "idle" | "running" | "complete" | "error";
type ReplayScenario = "clean" | "interactions";
type InteractionKind = "pageNavigation" | "sessionSwitch" | "interrupt";

interface MutableMetrics {
  scenario: ReplayScenario;
  status: ReplayStatus;
  startedAt: number | null;
  completedAt: number | null;
  appliedEvents: number;
  reducerDurations: number[];
  commitDurations: number[];
  maxCommitContext: {
    durationMs: number;
    appliedEvents: number;
    activeSid: string | null;
    page: "agent" | "placeholder";
  } | null;
  longTaskDurations: number[];
  layoutShiftScore: number;
  pageNavigation: number[];
  sessionSwitch: number[];
  interrupt: number[];
  error: string | null;
}

export interface LargeTurnBrowserResult {
  readonly scenario: ReplayScenario;
  readonly status: ReplayStatus;
  readonly replay: {
    readonly eventCount: number;
    readonly elapsedMs: number | null;
    readonly reducerTotalMs: number;
    readonly reducerP95Ms: number;
    readonly reducerMaxMs: number;
  };
  readonly react: {
    readonly commitCount: number;
    readonly totalActualDurationMs: number;
    readonly p95ActualDurationMs: number;
    readonly maxActualDurationMs: number;
    readonly maxContext: MutableMetrics["maxCommitContext"];
  };
  readonly browser: {
    readonly longTaskCount: number;
    readonly longTaskTotalMs: number;
    readonly longTaskMaxMs: number;
    readonly layoutShiftScore: number;
    readonly usedJsHeapBytes: number | null;
  };
  readonly dom: {
    readonly elements: number;
    readonly visibleTurns: number;
    readonly toolBlocks: number;
    readonly diffLines: number;
  };
  readonly interactions: Readonly<
    Record<InteractionKind, readonly number[]>
  >;
  readonly error: string | null;
}

const metrics = createEmptyMetrics();

function createEmptyMetrics(
  scenario: ReplayScenario = "clean",
): MutableMetrics {
  return {
    scenario,
    status: "idle",
    startedAt: null,
    completedAt: null,
    appliedEvents: 0,
    reducerDurations: [],
    commitDurations: [],
    maxCommitContext: null,
    longTaskDurations: [],
    layoutShiftScore: 0,
    pageNavigation: [],
    sessionSwitch: [],
    interrupt: [],
    error: null,
  };
}

function resetMetrics(scenario: ReplayScenario): void {
  Object.assign(metrics, createEmptyMetrics(scenario));
}

function startMetrics(scenario: ReplayScenario): void {
  resetMetrics(scenario);
  metrics.status = "running";
  metrics.startedAt = performance.now();
}

function completeMetrics(): void {
  metrics.status = "complete";
  metrics.completedAt = performance.now();
}

function failMetrics(error: unknown): void {
  metrics.status = "error";
  metrics.completedAt = performance.now();
  metrics.error = error instanceof Error ? error.message : String(error);
}

function percentile(values: readonly number[], fraction: number): number {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((left, right) => left - right);
  return sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * fraction))];
}

function total(values: readonly number[]): number {
  return values.reduce((sum, value) => sum + value, 0);
}

function round(value: number): number {
  return Math.round(value * 100) / 100;
}

function snapshotMetrics(): LargeTurnBrowserResult {
  const memory = (
    performance as Performance & {
      readonly memory?: { readonly usedJSHeapSize?: number };
    }
  ).memory;
  const elapsed =
    metrics.startedAt === null
      ? null
      : (metrics.completedAt ?? performance.now()) - metrics.startedAt;
  return {
    scenario: metrics.scenario,
    status: metrics.status,
    replay: {
      eventCount:
        metrics.appliedEvents,
      elapsedMs: elapsed === null ? null : round(elapsed),
      reducerTotalMs: round(total(metrics.reducerDurations)),
      reducerP95Ms: round(percentile(metrics.reducerDurations, 0.95)),
      reducerMaxMs: round(Math.max(0, ...metrics.reducerDurations)),
    },
    react: {
      commitCount: metrics.commitDurations.length,
      totalActualDurationMs: round(total(metrics.commitDurations)),
      p95ActualDurationMs: round(percentile(metrics.commitDurations, 0.95)),
      maxActualDurationMs: round(Math.max(0, ...metrics.commitDurations)),
      maxContext: metrics.maxCommitContext,
    },
    browser: {
      longTaskCount: metrics.longTaskDurations.length,
      longTaskTotalMs: round(total(metrics.longTaskDurations)),
      longTaskMaxMs: round(Math.max(0, ...metrics.longTaskDurations)),
      layoutShiftScore: round(metrics.layoutShiftScore),
      usedJsHeapBytes:
        typeof memory?.usedJSHeapSize === "number"
          ? memory.usedJSHeapSize
          : null,
    },
    dom: {
      elements: document.querySelectorAll("*").length,
      visibleTurns: document.querySelectorAll(".cc-turn").length,
      toolBlocks: document.querySelectorAll(".cc-tool").length,
      diffLines: document.querySelectorAll(".cc-tool__diff-line").length,
    },
    interactions: {
      pageNavigation: metrics.pageNavigation.map(round),
      sessionSwitch: metrics.sessionSwitch.map(round),
      interrupt: metrics.interrupt.map(round),
    },
    error: metrics.error,
  };
}

function exposeSnapshot(): LargeTurnBrowserResult {
  const result = snapshotMetrics();
  (
    window as typeof window & {
      __TROWEL_LARGE_TURN_RESULT__?: LargeTurnBrowserResult;
    }
  ).__TROWEL_LARGE_TURN_RESULT__ = result;
  return result;
}

function nextPaint(): Promise<void> {
  return new Promise((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
  });
}

function createLightSession(base: PerSessionState): PerSessionState {
  return {
    ...base,
    name: "light-session",
    displayTitle: "Light session",
    runtime: "claude_code",
    capabilities: getExpectedRuntimePresentation("claude_code")
      .expectedCapabilities,
    nativeSessionId: "fixture-light-native",
    connected: true,
    abort: null,
    phase: "done",
    lastSeq: null,
    turns: [
      {
        id: "fixture-light-turn",
        userText: "轻量会话",
        items: [{ kind: "text", text: "用于测量会话切换。" }],
        status: "done",
        turnId: "fixture-light-native-turn",
        revertible: false,
      },
    ],
  };
}

function installFixtureState(): void {
  const replay = buildLargeTurnReplay();
  const large = {
    ...replay.initialSession,
    abort: new AbortController(),
  };
  useAgentStore.setState((state) => ({
    ...state,
    sessions: {
      [LARGE_SESSION_ID]: large,
      [LIGHT_SESSION_ID]: createLightSession(replay.initialSession),
    },
    activeSid: LARGE_SESSION_ID,
    history: [],
    historyTotal: 0,
    loadingHistory: false,
    loadingMoreHistory: false,
    historyCursor: null,
    historyHasMore: false,
    historyWorkdir: WORKDIR,
    historyError: null,
  }));
}

function applyReplayEvent(eventIndex: number): void {
  const event = buildLargeTurnReplay().events[eventIndex];
  const started = performance.now();
  useAgentStore.setState((state) => {
    const current = state.sessions[LARGE_SESSION_ID];
    if (!current) throw new Error("large-turn session is missing");
    const reduced = reduceAgentEvent(current, event);
    if (reduced.kind !== "updated") {
      throw new Error(`event ${event.seq} reduced as ${reduced.kind}`);
    }
    return {
      ...state,
      sessions: {
        ...state.sessions,
        [LARGE_SESSION_ID]: reduced.session,
      },
    };
  });
  metrics.reducerDurations.push(performance.now() - started);
  metrics.appliedEvents += 1;
}

const ProfiledSession = memo(function ProfiledSession({
  onRender,
}: {
  readonly onRender: ProfilerOnRenderCallback;
}) {
  return (
    <Profiler id="large-turn-session" onRender={onRender}>
      <SessionView workdir={WORKDIR} />
    </Profiler>
  );
});

export function LargeTurnHarness() {
  const [showAgent, setShowAgent] = useState(true);
  const [version, setVersion] = useState(0);
  const runningRef = useRef(false);

  const publish = useCallback(() => {
    exposeSnapshot();
    setVersion((value) => value + 1);
  }, []);

  const onRender = useCallback<ProfilerOnRenderCallback>(
    (_id, _phase, actualDuration) => {
      if (metrics.status === "running" || metrics.status === "complete") {
        metrics.commitDurations.push(actualDuration);
        if (
          metrics.maxCommitContext === null ||
          actualDuration > metrics.maxCommitContext.durationMs
        ) {
          metrics.maxCommitContext = {
            durationMs: round(actualDuration),
            appliedEvents: metrics.appliedEvents,
            activeSid: useAgentStore.getState().activeSid,
            page: document.querySelector(".large-turn-harness__placeholder")
              ? "placeholder"
              : "agent",
          };
        }
        exposeSnapshot();
      }
    },
    [],
  );

  useEffect(() => {
    installFixtureState();
    exposeSnapshot();
    const observers: PerformanceObserver[] = [];
    if (PerformanceObserver.supportedEntryTypes.includes("longtask")) {
      const observer = new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) {
          if (metrics.status === "running") {
            metrics.longTaskDurations.push(entry.duration);
          }
        }
      });
      observer.observe({ type: "longtask", buffered: true });
      observers.push(observer);
    }
    if (PerformanceObserver.supportedEntryTypes.includes("layout-shift")) {
      const observer = new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) {
          const shift = entry as PerformanceEntry & {
            readonly value?: number;
            readonly hadRecentInput?: boolean;
          };
          if (
            metrics.status === "running" &&
            !shift.hadRecentInput &&
            typeof shift.value === "number"
          ) {
            metrics.layoutShiftScore += shift.value;
          }
        }
      });
      observer.observe({ type: "layout-shift", buffered: true });
      observers.push(observer);
    }
    const recordInterrupt = (event: MouseEvent) => {
      if (!(event.target instanceof Element)) return;
      if (!event.target.closest(".cc-status__interrupt")) return;
      const started = performance.now();
      void nextPaint().then(() => {
        metrics.interrupt.push(performance.now() - started);
        publish();
      });
    };
    document.addEventListener("click", recordInterrupt, true);
    return () => {
      observers.forEach((observer) => observer.disconnect());
      document.removeEventListener("click", recordInterrupt, true);
    };
  }, [publish]);

  async function runReplay(scenario: ReplayScenario) {
    if (runningRef.current) return;
    runningRef.current = true;
    startMetrics(scenario);
    installFixtureState();
    setShowAgent(true);
    publish();

    try {
      const eventCount = buildLargeTurnReplay().events.length;
      const batchSize = 8;
      const triggeredCheckpoints = new Set<string>();
      for (let index = 0; index < eventCount; index += batchSize) {
        const end = Math.min(index + batchSize, eventCount);
        for (let eventIndex = index; eventIndex < end; eventIndex += 1) {
          applyReplayEvent(eventIndex);
        }
        if (index % 160 === 0) publish();
        await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
        if (scenario === "interactions") {
          runInteractionCheckpoint(end, triggeredCheckpoints);
        }
      }
      await nextPaint();
      completeMetrics();
    } catch (error) {
      failMetrics(error);
    } finally {
      runningRef.current = false;
      publish();
    }
  }

  function togglePage() {
    const started = performance.now();
    setShowAgent((visible) => !visible);
    void nextPaint().then(() => {
      metrics.pageNavigation.push(performance.now() - started);
      publish();
    });
  }

  function toggleSession() {
    const state = useAgentStore.getState();
    const target =
      state.activeSid === LARGE_SESSION_ID
        ? LIGHT_SESSION_ID
        : LARGE_SESSION_ID;
    const started = performance.now();
    void state.activateSession(target).then(async () => {
      await nextPaint();
      metrics.sessionSwitch.push(performance.now() - started);
      publish();
    });
  }

  function runInteractionCheckpoint(
    eventCount: number,
    triggered: Set<string>,
  ) {
    const checkpoint = (
      id: string,
      selector: string,
      threshold: number,
    ) => {
      if (!triggered.has(id) && eventCount >= threshold) {
        triggered.add(id);
        document.querySelector<HTMLElement>(selector)?.click();
      }
    };
    checkpoint(
      "interrupt",
      ".cc-status__interrupt",
      400,
    );
    checkpoint(
      "page-away",
      '[data-testid="page-toggle"]',
      800,
    );
    checkpoint(
      "page-return",
      '[data-testid="page-toggle"]',
      960,
    );
    checkpoint(
      "session-away",
      '[data-testid="session-toggle"]',
      1_400,
    );
    checkpoint(
      "session-return",
      '[data-testid="session-toggle"]',
      1_600,
    );
  }

  const result = snapshotMetrics();
  return (
    <main className="large-turn-harness">
      <header className="large-turn-harness__controls">
        <div>
          <strong>Slice 434 · large-turn replay</strong>
          <span>
            {LARGE_TURN_MANIFEST.events.total} events ·{" "}
            {LARGE_TURN_MANIFEST.expectedDom.toolBlocks} tools
          </span>
        </div>
        <div className="large-turn-harness__actions">
          <button
            type="button"
            data-testid="run-replay"
            disabled={result.status === "running"}
            onClick={() => void runReplay("clean")}
          >
            {result.status === "running" ? "回放中…" : "开始回放"}
          </button>
          <button
            type="button"
            data-testid="run-interactions"
            disabled={result.status === "running"}
            onClick={() => void runReplay("interactions")}
          >
            交互回放
          </button>
          <button
            type="button"
            data-testid="page-toggle"
            onClick={togglePage}
          >
            {showAgent ? "切到提取页" : "回到 Agent"}
          </button>
          <button
            type="button"
            data-testid="session-toggle"
            onClick={toggleSession}
          >
            切换会话
          </button>
        </div>
        <pre data-testid="large-turn-metrics">
          {JSON.stringify(result, null, 2)}
        </pre>
      </header>
      <section className="large-turn-harness__stage">
        {showAgent ? (
          <ProfiledSession onRender={onRender} />
        ) : (
          <div className="large-turn-harness__placeholder">
            <h1>提取页占位</h1>
            <p>这里用于测量离开 Agent 页面时的响应。</p>
          </div>
        )}
      </section>
      <output className="large-turn-harness__version" aria-hidden="true">
        {version}
      </output>
    </main>
  );
}
