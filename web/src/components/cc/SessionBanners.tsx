/** 汇总当前会话的额度、连接异常和能力缺失提示。 */

import type { PerSessionState } from "../../agent/application";
import { getRuntimePresentation } from "../../agent/runtimes";
import { RateLimitBanner } from "./RateLimitBanner";

export function SessionBanners({
  active,
  activeSid,
}: {
  readonly active: PerSessionState | null;
  readonly activeSid: string | null;
}) {
  const presentation = active
    ? getRuntimePresentation(active.runtime, active.capabilities)
    : null;
  return (
    <>
      {active?.meta.hostDegraded &&
        presentation?.headerStatus.degradedHostLabel && (
        <div className="cc-degraded-banner" role="alert">
          <b>{presentation.headerStatus.degradedHostLabel}</b>
          <span>
            运行中的 turn 已按出错收口；idle thread 可在重连后
            resume，不会自动重放写操作。
          </span>
        </div>
      )}
      <RateLimitBanner snapshot={active?.meta.rateLimit ?? null} />
      {active &&
        presentation?.supports("checkpoint") &&
        active.checkpointAvailable === false &&
        activeSid && (
          <div className="cc-nogit-banner">
            <span aria-hidden>⚠</span>
            当前无法创建新的回滚点；已有回滚点仍可使用。
          </div>
        )}
      {active &&
        presentation?.supports("checkpoint") &&
        active.checkpointAvailable === null &&
        activeSid && (
          <div className="cc-nogit-banner" role="status">
            <span aria-hidden>⚠</span>
            回滚可用性尚未确认；已有会话仍按每轮记录决定是否显示回滚入口。
          </div>
        )}
      {active && presentation && presentation.missingCapabilities.length > 0 && (
        <div className="cc-degraded-banner" role="status">
          <b>Runtime capability 信息不完整</b>
          <span>
            部分设置或专属展示已隐藏：
            {presentation.missingCapabilities.join("、")}
          </span>
        </div>
      )}
    </>
  );
}
