import type { PerSessionState } from "../../stores/ccStore";
import { RateLimitBanner } from "./RateLimitBanner";

export function SessionBanners({
  active,
  activeSid,
}: {
  readonly active: PerSessionState | null;
  readonly activeSid: string | null;
}) {
  return (
    <>
      {active?.meta.hostDegraded && (
        <div className="cc-degraded-banner" role="alert">
          <b>Codex host 已断开</b>
          <span>
            运行中的 turn 已按出错收口；idle thread 可在重连后
            resume，不会自动重放写操作。
          </span>
        </div>
      )}
      <RateLimitBanner snapshot={active?.meta.rateLimit ?? null} />
      {active?.runtime === "claude_code" &&
        !active.revertEnabled &&
        activeSid && (
          <div className="cc-nogit-banner">
            <span aria-hidden>⚠</span>
            此目录不是 git 仓库，不支持回滚（聊天、历史等其他功能正常）。
          </div>
        )}
    </>
  );
}
