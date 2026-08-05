/** 展示当前构建的真实版本信息与 Trowel 的本地数据原则。 */

import type { PlatformPort } from "../../platform/contracts";
import { PanelHeader, StatusPill } from "./SettingsPrimitives";

interface AboutPanelProps {
  readonly version: string;
  readonly environment: PlatformPort["environment"];
}

function environmentLabel(environment: PlatformPort["environment"]): string {
  return environment === "desktop" ? "桌面应用" : "浏览器开发环境";
}

function versionLabel(version: string): string {
  if (version === "development" || version.startsWith("v")) return version;
  return `v${version}`;
}

/** 复用设置页轻量分隔行，不引入与 mockup 无关的卡片或虚构平台字段。 */
export function AboutPanel({ version, environment }: AboutPanelProps) {
  return (
    <section className="settings-panel" aria-labelledby="settings-about-title">
      <PanelHeader
        id="settings-about-title"
        title="关于 Trowel"
        description="跨会话持续存在的本地 AI 工作系统。"
      />
      <section className="settings-section">
        <div className="settings-list">
          <div className="settings-row settings-setting-row">
            <div className="settings-row__body">
              <strong>版本</strong>
              <span>当前正在运行的 Trowel 构建。</span>
            </div>
            <code className="settings-about-value">
              {versionLabel(version)} · {environmentLabel(environment)}
            </code>
          </div>
          <div className="settings-row settings-setting-row">
            <div className="settings-row__body">
              <strong>数据原则</strong>
              <span>会话正文、Profile 与凭据不进入普通日志或 telemetry。</span>
            </div>
            <StatusPill status="available" label="本地优先" />
          </div>
        </div>
      </section>
    </section>
  );
}
