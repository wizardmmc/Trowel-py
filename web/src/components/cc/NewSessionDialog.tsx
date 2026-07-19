import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import type { AgentRuntimeInfo, Runtime } from "../../api/agent";
import type { ModelOption } from "../../api/cc";

/**
 * slice-072 — the "new Agent session" dialog.
 *
 * Flow (mockup-confirmed): pick Runtime → the runtime's model / effort /
 * permission options appear → Memory/Profile switches → create. Runtime is
 * frozen at create (spec C-1).
 *
 * State coverage (spec pass criteria + review P1):
 *  - **runtimesState** is a discrete loading/ready/error tri-state so a fetch
 *    failure is surfaced as "Agent API 不可用" with a retry, never disguised
 *    as "both runtimes unavailable" (review P1-1).
 *  - **creating** disables actions + shows "创建中…" (review P1-2).
 *  - **error** renders inline as an alert (review P1-2).
 *  - a runtime the catalog reports disconnected is shown but not selectable;
 *    its card dims only the status copy, not the whole card (review P1-3).
 *
 * Keyboard: the runtime row is a radiogroup with roving tabindex — Tab enters
 * at the selected card, arrow keys move selection AND focus, Enter/Space
 * confirms, Escape cancels.
 */

/** What the dialog hands back on create. Runtime is always set; the
 * permission-shaped field depends on runtime (CC: permission_mode, Codex:
 * sandbox). Empty model/effort/permission strings mean "follow host default". */
export interface NewSessionConfig {
  readonly runtime: Runtime;
  readonly memory_enabled: boolean;
  readonly profile_enabled: boolean;
  readonly model: string;
  readonly effort: string;
  readonly permission_mode: string;
  readonly sandbox: string;
}

/** Runtime catalog load state — three discrete states (review P1-1). */
export type RuntimesState =
  | { readonly status: "loading" }
  | { readonly status: "ready"; readonly runtimes: readonly AgentRuntimeInfo[] }
  | { readonly status: "error"; readonly error: string };

interface NewSessionDialogProps {
  readonly workdir: string;
  readonly onCreate: (config: NewSessionConfig) => void;
  readonly onCancel: () => void;
  /** Runtime catalog tri-state. Omit = ready + both connected (test default). */
  readonly runtimesState?: RuntimesState;
  /** Retry the runtime catalog fetch (shown on error). */
  readonly onRetryRuntimes?: () => void;
  /** Creating state — disables actions + shows "创建中…". */
  readonly creating?: boolean;
  /** Inline creation error (shown as an alert). */
  readonly error?: string | null;
  /** CC model aliases from /api/cc/models (opus/sonnet/haiku). Omit = only the
   * "跟随 settings" fallback is shown for CC. */
  readonly ccModels?: readonly ModelOption[];
}

interface RuntimeOption {
  readonly value: Runtime;
  readonly label: string;
  readonly native: string;
  readonly desc: string;
  readonly efforts: ReadonlyArray<{ readonly value: string; readonly label: string }>;
  readonly permissions: ReadonlyArray<{ readonly value: string; readonly label: string }>;
}

const RUNTIME_OPTIONS: readonly RuntimeOption[] = [
  {
    value: "claude_code",
    label: "Claude Code",
    native: "原生 claude -p",
    desc: "继续使用现有 CCHost 配置；保留 Workflow、hook 与 CC checkpoint。",
    // CC effort 档位（cc --effort 的完整档）。
    efforts: [
      { value: "", label: "跟随" },
      { value: "low", label: "low" },
      { value: "medium", label: "medium" },
      { value: "high", label: "high" },
      { value: "xhigh", label: "xhigh" },
      { value: "max", label: "max" },
      { value: "ultracode", label: "ultracode" },
    ],
    permissions: [
      { value: "bypassPermissions", label: "跟随 CC（bypass）" },
      { value: "default", label: "default" },
      { value: "acceptEdits", label: "acceptEdits" },
    ],
  },
  {
    value: "codex",
    label: "Codex",
    native: "原生 app-server",
    desc: "使用本机 Codex 订阅、sandbox、审批与 usage；不经过 Claude Code。",
    efforts: [
      { value: "", label: "跟随" },
      { value: "low", label: "low" },
      { value: "medium", label: "medium" },
      { value: "high", label: "high" },
    ],
    permissions: [
      { value: "", label: "跟随 Codex" },
      { value: "read-only", label: "read-only" },
      { value: "workspace-write", label: "workspace-write" },
    ],
  },
];

/** Codex models are not enumerated by trowel (Codex owns its model catalog in
 * thread/start). Surface the documented subscription default + the spike's
 * gpt-5.6-sol as a concrete pick; "跟随默认" defers to the host. */
const CODEX_MODELS: ReadonlyArray<{ readonly value: string; readonly label: string }> = [
  { value: "", label: "跟随 Codex 默认" },
  { value: "gpt-5.6-sol", label: "gpt-5.6-sol" },
];

/** Model options for a runtime. CC pulls its aliases from /api/cc/models
 * (opus/sonnet/haiku — whatever cc settings.json declares); Codex uses the
 * fixed CODEX_MODELS list above. */
function modelsFor(
  runtime: Runtime,
  ccModels: readonly ModelOption[],
): ReadonlyArray<{ readonly value: string; readonly label: string }> {
  if (runtime === "codex") return CODEX_MODELS;
  return [
    { value: "", label: "跟随 settings" },
    ...ccModels.map((m) => ({ value: m.value, label: m.label })),
  ];
}

const RUNTIME_LABEL: Record<Runtime, string> = {
  claude_code: "Claude Code",
  codex: "Codex",
};

function optionIndex(value: Runtime): number {
  return RUNTIME_OPTIONS.findIndex((o) => o.value === value);
}

export function NewSessionDialog({
  workdir,
  onCreate,
  onCancel,
  runtimesState,
  onRetryRuntimes,
  creating = false,
  error = null,
  ccModels = [],
}: NewSessionDialogProps) {
  const [runtime, setRuntime] = useState<Runtime>("claude_code");
  const [model, setModel] = useState<string>("");
  const [effort, setEffort] = useState<string>("");
  const [permission, setPermission] = useState<string>("");
  const [memory, setMemory] = useState(true);
  const [profile, setProfile] = useState(true);

  const radioRefs = useRef<Array<HTMLButtonElement | null>>([]);

  // resolve the catalog into a per-runtime connected map (undefined = assume
  // connected — tests / no catalog).
  const readyRuntimes =
    runtimesState?.status === "ready" ? runtimesState.runtimes : null;
  const isConnected = (rt: Runtime): boolean => {
    if (!readyRuntimes) return true;
    return readyRuntimes.some((r) => r.runtime === rt && r.connected);
  };

  const catalogLoading = runtimesState?.status === "loading";
  const catalogError =
    runtimesState?.status === "error" ? runtimesState.error : null;
  // The create button is blocked while the catalog is loading/erroring, while
  // creating, or when the selected runtime is not connected (review P1-1).
  const selectedConnected = isConnected(runtime);
  const createBlocked =
    creating || catalogLoading || catalogError !== null || !selectedConnected;

  // slice-072: when the active runtime option changes, reset its model/effort/
  // permission selections so a stale CC choice never leaks into a Codex session.
  function selectRuntime(next: Runtime): void {
    if (next === runtime) return;
    setRuntime(next);
    setModel("");
    setEffort("");
    setPermission("");
  }

  /** Arrow-key handler with roving tabindex: move selection to the next
   * connected runtime and focus it (review P1 + Info: focus must follow
   * aria-checked, and skip disconnected cards). */
  function onRuntimeKeyDown(
    event: React.KeyboardEvent<HTMLButtonElement>,
    index: number,
  ): void {
    if (
      event.key !== "ArrowRight" &&
      event.key !== "ArrowDown" &&
      event.key !== "ArrowLeft" &&
      event.key !== "ArrowUp"
    ) {
      return;
    }
    event.preventDefault();
    const n = RUNTIME_OPTIONS.length;
    const dir = event.key === "ArrowRight" || event.key === "ArrowDown" ? 1 : -1;
    for (let step = 1; step <= n; step++) {
      const nextIdx = ((index + dir * step) % n + n) % n;
      const next = RUNTIME_OPTIONS[nextIdx];
      if (isConnected(next.value)) {
        selectRuntime(next.value);
        return;
      }
    }
  }

  // Roving tabindex: focus follows the selected runtime (arrow keys + initial
  // autofocus land on the selected card, not stuck on the first).
  useEffect(() => {
    const idx = optionIndex(runtime);
    radioRefs.current[idx]?.focus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runtime]);

  const activeOption = RUNTIME_OPTIONS[optionIndex(runtime)];

  function submit(): void {
    const config: NewSessionConfig = {
      runtime,
      memory_enabled: memory,
      profile_enabled: profile,
      model,
      effort,
      permission_mode: runtime === "claude_code" ? permission : "",
      sandbox: runtime === "codex" ? permission : "",
    };
    onCreate(config);
  }

  return createPortal(
    <div className="cc-dialog__backdrop" onClick={onCancel} role="presentation">
      <div
        className="cc-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="新建 Agent 会话"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === "Escape") onCancel();
        }}
      >
        <div className="cc-dialog__head">
          <p className="cc-dialog__title">新建 Agent 会话</p>
          <span className="cc-dialog__workdir" title={workdir}>
            {workdir}
          </span>
        </div>
        <div className="cc-dialog__body">
          <div className="cc-dialog__section-label">Runtime（创建后不可切换）</div>
          <div
            className="cc-dialog__runtime-grid"
            role="radiogroup"
            aria-label="Runtime"
          >
            {RUNTIME_OPTIONS.map((opt, idx) => {
              const connected = isConnected(opt.value);
              const selected = runtime === opt.value;
              return (
                <button
                  key={opt.value}
                  ref={(el) => {
                    radioRefs.current[idx] = el;
                  }}
                  type="button"
                  role="radio"
                  aria-checked={selected}
                  tabIndex={selected ? 0 : -1}
                  disabled={creating}
                  aria-disabled={!connected || undefined}
                  className={
                    "cc-dialog__runtime-card" +
                    (selected ? " cc-dialog__runtime-card--selected" : "") +
                    (!connected ? " cc-dialog__runtime-card--disabled" : "")
                  }
                  onClick={() => {
                    if (connected) selectRuntime(opt.value);
                  }}
                  onKeyDown={(e) => onRuntimeKeyDown(e, idx)}
                >
                  <span className="cc-dialog__runtime-name">{opt.label}</span>
                  <span className="cc-dialog__runtime-native">{opt.native}</span>
                  <span className="cc-dialog__runtime-desc">{opt.desc}</span>
                  {!connected && (
                    <span className="cc-dialog__runtime-unavailable">
                      未连接
                    </span>
                  )}
                </button>
              );
            })}
          </div>

          {catalogLoading && (
            <div className="cc-dialog__diag">正在检查 runtime 连接…</div>
          )}
          {catalogError !== null && (
            <div className="cc-dialog__diag" role="alert">
              Agent API 不可用：{catalogError}
              {onRetryRuntimes && (
                <button
                  type="button"
                  className="cc-dialog__btn"
                  onClick={onRetryRuntimes}
                  disabled={creating}
                  style={{ marginLeft: 8 }}
                >
                  重试
                </button>
              )}
            </div>
          )}

          {/* runtime-specific model / effort / permission (mockup-confirmed).
              Empty value = follow host default. */}
          <div className="cc-dialog__section-label">Model</div>
          <div className="cc-dialog__option-row">
            {modelsFor(runtime, ccModels).map((m) => (
              <button
                key={m.label}
                type="button"
                className={
                  "cc-dialog__option" +
                  (model === m.value ? " cc-dialog__option--selected" : "")
                }
                disabled={creating}
                onClick={() => setModel(m.value)}
              >
                {m.label}
              </button>
            ))}
          </div>
          <div className="cc-dialog__section-label">Effort</div>
          <div className="cc-dialog__option-row">
            {activeOption.efforts.map((e) => (
              <button
                key={e.label}
                type="button"
                className={
                  "cc-dialog__option" +
                  (effort === e.value ? " cc-dialog__option--selected" : "")
                }
                disabled={creating}
                onClick={() => setEffort(e.value)}
              >
                {e.label}
              </button>
            ))}
          </div>
          <div className="cc-dialog__section-label">Permission</div>
          <div className="cc-dialog__option-row">
            {activeOption.permissions.map((p) => (
              <button
                key={p.label}
                type="button"
                className={
                  "cc-dialog__option" +
                  (permission === p.value ? " cc-dialog__option--selected" : "")
                }
                disabled={creating}
                onClick={() => setPermission(p.value)}
              >
                {p.label}
              </button>
            ))}
          </div>

          <SwitchRow
            name="Memory"
            desc={
              runtime === "codex"
                ? "注入 trowel 记忆并挂 memory MCP；Codex native memories 保持关闭。"
                : "给模型读你存的记忆：铁律、dictionary 笔记、近期日记，并挂 memory MCP。关掉做无记忆基线。"
            }
            on={memory}
            onToggle={() => setMemory((v) => !v)}
            disabled={creating}
          />
          <SwitchRow
            name="Profile"
            desc={'把"你是谁"画像段注入提示词。关掉只消融显式画像。'}
            on={profile}
            onToggle={() => setProfile((v) => !v)}
            disabled={creating}
          />
          <p className="cc-dialog__note">
            {runtime === "codex"
              ? "选择 Codex 只决定这个 session 的 runtime，不会改变已运行的 GLM 会话，也不会修改 cc-switch 配置。"
              : "选择 Claude Code 继续使用 CCHost；它与已运行的 Codex session 互不切换、互不 resume。"}
          </p>
          {error && (
            <p className="cc-dialog__error" role="alert">
              {error}
            </p>
          )}
        </div>
        <div className="cc-dialog__foot">
          <button
            type="button"
            className="cc-dialog__btn"
            onClick={onCancel}
            disabled={creating}
          >
            取消
          </button>
          <button
            type="button"
            className="cc-dialog__btn cc-dialog__btn--primary"
            onClick={submit}
            disabled={createBlocked}
            title={
              !selectedConnected
                ? "选中的 runtime 未连接"
                : catalogLoading
                  ? "正在检查 runtime 连接"
                  : undefined
            }
          >
            {creating ? "创建中…" : `创建 ${RUNTIME_LABEL[runtime]} 会话`}
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
  disabled,
}: {
  readonly name: string;
  readonly desc: string;
  readonly on: boolean;
  readonly onToggle: () => void;
  readonly disabled?: boolean;
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
        disabled={disabled}
      />
    </div>
  );
}
