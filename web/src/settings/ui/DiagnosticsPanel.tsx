/** 展示每条连接彼此独立的网络、runtime 和 Trowel 反代诊断。 */

import type { Diagnostics } from "../domain/types";
import { EmptyState, PanelHeader, StatusPill } from "./SettingsPrimitives";

interface DiagnosticsPanelProps {
  readonly diagnostics: Diagnostics | null;
  readonly error: string | null;
  readonly fetchedAt: string | null;
  readonly onRefresh: () => void;
}

const LAYERS = [
  ["network", "连接网络", "模型服务与模型目录的最近状态"],
  ["runtime_launch", "Runtime 启动", "本机执行引擎能否启动"],
  ["trowel_proxy", "Trowel 兼容反代", "Trowel 当前是否能为该连接提供代理"],
] as const;

/** unknown、unsupported 与 unavailable 分开显示，不折算成绿色正常态。 */
export function DiagnosticsPanel({ diagnostics, error, fetchedAt, onRefresh }: DiagnosticsPanelProps) {
  return (
    <section className="settings-panel" aria-labelledby="settings-diagnostics-title">
      <PanelHeader
        id="settings-diagnostics-title"
        title="连接诊断"
        description="诊断按具体连接拆成网络、Runtime 启动与兼容反代三层，故障不会统一显示成“连接失败”。"
        aside={<button type="button" className="settings-button" onClick={onRefresh}><ActivityIcon />重新诊断</button>}
      />
      {error && diagnostics?.connections.length ? (
        <div className="settings-error-box" role="alert">
          <span>最新诊断读取失败：{error}</span>
          <button type="button" onClick={onRefresh}>重试</button>
        </div>
      ) : null}
      {!diagnostics?.connections.length ? (
        <EmptyState
          title={error ? "诊断读取失败" : "没有可诊断的连接"}
          detail={error ?? "先创建并保存一条 Trowel 连接。"}
        />
      ) : (
        <div className="settings-diagnostics">
          {diagnostics.connections.map((connection) => (
            <section className="settings-section settings-diagnostic" key={connection.connection_id}>
              <div className="settings-section__head settings-diagnostic__head">
                <div><h3>{connection.connection_name}</h3><p>每一层只报告自己的最近事实。</p></div>
                <span>{fetchedAt ? `读取于 ${formatTime(fetchedAt)}` : "尚未读取"}</span>
              </div>
              <div className="settings-diagnostic-list">
              {LAYERS.map(([key, label, detail], index) => {
                const layer = connection[key];
                return (
                  <div className="settings-diagnostic__row" key={key}>
                    <span className="settings-diagnostic__index">{index + 1}</span>
                    <div className="settings-diagnostic__name">
                      <strong>{label}</strong>
                      <span>{connection.connection_name} · {detail}</span>
                    </div>
                    <code className="settings-diagnostic__endpoint">{layer.code ?? "暂无脱敏代码"}</code>
                    <StatusPill status={layer.status} label={diagnosticStatusLabel(layer.status)} />
                  </div>
                );
              })}
              </div>
            </section>
          ))}
          <div className="settings-inline-notice">连接网络可达不等于 Runtime 能完整支持工具、恢复和 usage；能力资格仍由各 runtime 的真实 Gate 单独维护。</div>
        </div>
      )}
    </section>
  );
}

function ActivityIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 12h4l2-7 4 14 2-7h6" /></svg>;
}

/** 翻译诊断有限状态。 */
function diagnosticStatusLabel(status: string): string {
  const labels: Readonly<Record<string, string>> = {
    available: "可用",
    unavailable: "不可用",
    unknown: "未知",
    unsupported: "暂不支持",
    not_applicable: "不适用",
  };
  return labels[status] ?? status;
}

/** 使用本机短时间展示本次诊断读取时刻。 */
function formatTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleTimeString("zh-CN", {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false,
      });
}
