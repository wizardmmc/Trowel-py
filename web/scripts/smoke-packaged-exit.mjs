/** 判断打包 App 的进程终态是否可以继续做资源完整性验证。 */

/**
 * 接受正常退出或 macOS 退出 watchdog 的安装包进程终态。
 *
 * 本函数只判断进程终态；调用方仍需核对 smoke 标记、资源退出记录、日志和数据库。
 *
 * @param {{ code: number | null, signal: string | null }} result Electron 主进程的退出码和信号。
 * @returns {boolean} 是否可以继续验证退出后的资源事实。
 */
export function isAcceptedPackagedAppExit(result) {
  return result.code === 0
    || (result.code === null && result.signal === "SIGKILL");
}
