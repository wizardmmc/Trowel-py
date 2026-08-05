/** 展示后端 resolver 返回的真实路径及平台操作状态。 */

import type { PathStatus } from "../domain/types";
import { EmptyState, PanelHeader } from "./SettingsPrimitives";

interface PathsPanelProps {
  readonly paths: PathStatus | null;
  readonly error: string | null;
  readonly browserMode: boolean;
  readonly onCopy: (path: string) => void;
  readonly onReveal: (path: string) => void;
  readonly onRefresh: () => void;
}

const PATH_LABELS: Readonly<Record<string, { label: string; detail: string }>> = {
  data_root: { label: "数据根", detail: "Memory 与本地数据库" },
  memory: { label: "Memory", detail: "长期记忆与 Daily" },
  profile: { label: "Profile", detail: "确认后的用户画像" },
  trowel_config: { label: "Trowel 配置", detail: "应用自己的运行配置" },
  connection_registry: { label: "连接注册表", detail: "连接、会话配置与任务绑定事实" },
  claude_settings: { label: "Claude 设置", detail: "外部工具拥有，Trowel 不覆盖" },
  codex_config: { label: "Codex 配置", detail: "外部工具拥有，Trowel 不覆盖" },
};

const PATH_GROUPS = [
  {
    title: "Trowel 数据",
    description: "可在文件管理器中打开，也可复制完整路径。",
    keys: ["data_root", "memory", "profile", "trowel_config"],
  },
  {
    title: "连接与原生配置",
    description: "连接注册表由 Trowel 管理；Claude 与 Codex 原生文件仍由各自工具拥有。",
    keys: ["connection_registry", "claude_settings", "codex_config"],
  },
] as const;

/** 渲染路径列表；不存在或 browser 模式时只保留复制能力。 */
export function PathsPanel({ paths, error, browserMode, onCopy, onReveal, onRefresh }: PathsPanelProps) {
  return (
    <section className="settings-panel" aria-labelledby="settings-paths-title">
      <PanelHeader
        id="settings-paths-title"
        title="存储与路径"
        description="显示当前进程实际解析到的位置。正式、日常开发和隔离开发的数据不会混用。"
        aside={paths ? dataModeLabel(paths.data_mode) : "等待读取"}
      />
      {!paths ? (
        <div>
          <EmptyState title="路径尚未读到" detail={error ?? "可以稍后重新读取。"} />
          <button type="button" className="settings-button" onClick={onRefresh}>重新读取路径</button>
        </div>
      ) : (
        <>
          <div className="settings-environment-strip">
            <strong>当前环境：{dataModeLabel(paths.data_mode)}</strong>
            <span>数据根来自 Trowel Host 启动参数</span>
          </div>
          {PATH_GROUPS.map((group) => (
            <section className="settings-section settings-path-section" key={group.title}>
              <div className="settings-section__head">
                <div><h3>{group.title}</h3><p>{group.description}</p></div>
              </div>
              <div className="settings-list settings-path-list">
                {group.keys.map((name) => {
                  const entry = paths.paths[name];
                  return entry ? (
                    <PathRow
                      key={name}
                      name={name}
                      entry={entry}
                      browserMode={browserMode}
                      onCopy={onCopy}
                      onReveal={onReveal}
                    />
                  ) : null;
                })}
              </div>
            </section>
          ))}
        </>
      )}
      {browserMode && (
        <p className="settings-inline-note">浏览器模式不能调用系统文件管理器，路径仍可复制。</p>
      )}
    </section>
  );
}

interface PathRowProps {
  readonly name: string;
  readonly entry: PathStatus["paths"][string];
  readonly browserMode: boolean;
  readonly onCopy: (path: string) => void;
  readonly onReveal: (path: string) => void;
}

/** 单行只消费后端给出的路径与存在状态，不在前端拼接位置。 */
function PathRow({ name, entry, browserMode, onCopy, onReveal }: PathRowProps) {
  const meta = PATH_LABELS[name] ?? { label: name, detail: entry.kind };
  const revealDisabled = browserMode || !entry.exists;
  const revealTitle = browserMode
    ? "普通浏览器不能打开本机位置"
    : entry.exists
      ? "在文件管理器中显示"
      : "路径尚不存在";

  return (
    <div className="settings-row settings-path-row">
      <div className="settings-path-row__name">
        <strong>{meta.label}</strong>
        <span>{meta.detail}</span>
      </div>
      <code className="settings-path-row__value">{entry.path}</code>
      <div className="settings-row__actions">
        <button
          type="button"
          className="settings-icon-button"
          aria-label={`复制${meta.label}路径`}
          title="复制路径"
          onClick={() => onCopy(entry.path)}
        >
          <CopyIcon />
        </button>
        <button
          type="button"
          className="settings-icon-button"
          disabled={revealDisabled}
          aria-label={`打开${meta.label}位置`}
          title={revealTitle}
          onClick={() => onReveal(entry.path)}
        >
          <FolderIcon />
        </button>
      </div>
    </div>
  );
}

function CopyIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="9" width="12" height="12" rx="2" /><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" /></svg>;
}

function FolderIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 5h6l2 2h10v12H3z" /></svg>;
}

/** 翻译后端稳定数据模式。 */
function dataModeLabel(mode: string): string {
  const labels: Readonly<Record<string, string>> = {
    packaged: "打包应用",
    "canonical-dev": "标准开发",
    "isolated-dev": "隔离开发",
    browser: "浏览器",
  };
  return labels[mode] ?? mode;
}
