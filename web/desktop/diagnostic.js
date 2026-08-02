/** 驱动 sidecar 启动失败时的重试、日志和退出操作。 */

const bridge = window.trowelDesktop;
const message = document.querySelector("#message");
const category = document.querySelector("#category");
const exitCode = document.querySelector("#exit-code");
const actionError = document.querySelector("#action-error");
const retry = document.querySelector("#retry");
let sidecarReady = false;

async function refreshDiagnostic() {
  const state = await bridge.getDiagnostics();
  sidecarReady = state.status === "ready";
  message.textContent = sidecarReady
    ? "后台服务仍在运行。界面异常退出时，可以直接重新打开 Trowel。"
    : (state.message ?? "后台服务暂时不可用。");
  category.textContent = state.category ?? "-";
  exitCode.textContent = state.exitCode === null ? "-" : String(state.exitCode);
  retry.textContent = sidecarReady ? "重新打开 Trowel" : "重试";
}

async function runAction(action) {
  actionError.textContent = "";
  try {
    await action();
  } catch {
    actionError.textContent = "操作失败，请查看日志后重试。";
  }
}

retry.addEventListener("click", () => {
  retry.disabled = true;
  const action = sidecarReady ? bridge.openTrowel : bridge.retrySidecar;
  void runAction(() => action()).finally(() => {
    retry.disabled = false;
  });
});
document.querySelector("#open-logs").addEventListener("click", () => {
  void runAction(() => bridge.openLogs());
});
document.querySelector("#quit").addEventListener("click", () => {
  void runAction(() => bridge.requestQuit());
});

void runAction(refreshDiagnostic);
