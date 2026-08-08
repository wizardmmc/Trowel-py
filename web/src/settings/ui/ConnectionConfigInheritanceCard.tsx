/** 展示 Agent 连接私有配置家的状态、复制范围和覆盖操作。 */

import { StatusPill } from "./SettingsPrimitives";

type AgentRuntime = "claude_code" | "codex";

interface ConnectionConfigInheritanceCardProps {
  readonly runtime: AgentRuntime;
  readonly inherited: boolean;
  readonly saved: boolean;
  readonly dirty: boolean;
  readonly inheriting: boolean;
  readonly onInherit: () => void;
}

const RUNTIME_COPY = {
  claude_code: {
    title: "Claude Code 配置家",
    source: "~/.claude",
    included: ["skills", "commands", "agents", "rules", "输出样式", "CLAUDE.md", "settings"],
    excluded: ["settings.env", "登录态", "会话记录", "日志"],
    detail: "复制后与全局目录断开；这项连接之后独立修改、独立生效。",
    liveNotice: "再次复制会覆盖受支持内容，同连接的在跑会话可能热加载变化。",
  },
  codex: {
    title: "Codex 配置家",
    source: "~/.codex + ~/.agents/skills",
    included: ["config.toml", "AGENTS.md", "rules", "~/.codex/skills", "~/.agents/skills"],
    excluded: ["auth.json", "会话记录", "SQLite", "日志", "插件缓存"],
    detail: "复制后与全局目录断开；不同 Codex 连接不会继续共享用户技能或配置。",
    liveNotice: "再次复制会覆盖受支持内容；如有正在使用这项连接的会话，需要先关闭再复制。",
  },
} as const;

/** 统一呈现 Claude 与 Codex 对称的连接级配置继承语义。 */
export function ConnectionConfigInheritanceCard({
  runtime,
  inherited,
  saved,
  dirty,
  inheriting,
  onInherit,
}: ConnectionConfigInheritanceCardProps) {
  const copy = RUNTIME_COPY[runtime];
  return (
    <section className="settings-form-block settings-config-home">
      <div className="settings-config-home__heading">
        <div>
          <span className="settings-config-home__eyebrow">连接隔离</span>
          <h3>{copy.title}</h3>
          <p>{copy.detail}</p>
        </div>
        <StatusPill
          status={inherited ? "available" : "unknown"}
          label={inherited ? "已复制" : "未复制"}
        />
      </div>

      <div className="settings-config-home__body">
        <div className="settings-config-home__source">
          <span>复制来源</span>
          <code>{copy.source}</code>
          <strong>{inherited ? "已完成一次独立副本" : "尚未从全局配置复制"}</strong>
          <small>
            {saved
              ? copy.liveNotice
              : "先保存连接，Trowel 才会创建稳定的连接配置家。"}
          </small>
        </div>
        <div className="settings-config-home__scope">
          <div>
            <span>会复制</span>
            <ul>{copy.included.map((item) => <li key={item}>{item}</li>)}</ul>
          </div>
          <div className="is-excluded">
            <span>不会复制</span>
            <ul>{copy.excluded.map((item) => <li key={item}>{item}</li>)}</ul>
          </div>
        </div>
      </div>

      <div className="settings-config-home__actions">
        <p>
          {!inherited && saved
            ? "不复制也能使用系统技能、管理员技能和项目技能；这里只代表没有用户配置副本。"
            : "复制是一次性快照，不会建立跨连接的实时同步。"}
        </p>
        <button
          type="button"
          className="settings-button"
          disabled={!saved || dirty || inheriting}
          onClick={onInherit}
        >
          {inheriting ? "复制中…" : inherited ? "重新复制" : "复制全局配置"}
        </button>
      </div>
      {dirty && saved && (
        <p className="settings-save-note">保存当前修改后才能复制，避免连接版本变化时误覆盖。</p>
      )}
    </section>
  );
}
