/** 创建 Electron Host 的本地生命周期日志，并容忍日志目录暂时不可写。 */

import { appendFileSync, mkdirSync } from "node:fs";
import path from "node:path";

export type LifecycleLogger = (
  event: string,
  category?: string | null,
) => void;

/** 返回不会遮蔽应用启动结果的日志函数，并尽早创建诊断页要打开的目录。 */
export function createLifecycleLogger(logDirectory: string): LifecycleLogger {
  const logPath = path.join(logDirectory, "desktop-host.log");
  try {
    mkdirSync(logDirectory, { recursive: true });
  } catch {
    // 日志目录失败仍交给 sidecar 启动分类，不能让记录动作抢先终止应用。
  }
  return (event, category = null) => {
    try {
      mkdirSync(logDirectory, { recursive: true });
      appendFileSync(
        logPath,
        `${JSON.stringify({ at: new Date().toISOString(), event, category })}\n`,
        "utf8",
      );
    } catch {
      // 日志不可写不能覆盖真正的启动结果，诊断页仍可报告目录权限问题。
    }
  };
}
