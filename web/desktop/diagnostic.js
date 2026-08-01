/** 驱动 sidecar 启动失败时的重试、日志和退出操作。 */

const bridge = window.trowelDesktop;
const message = document.querySelector("#message");
const category = document.querySelector("#category");
const exitCode = document.querySelector("#exit-code");
const actionError = document.querySelector("#action-error");
const retry = document.querySelector("#retry");

async function refreshDiagnostic() {
  const state = await bridge.getDiagnostics();
  message.textContent = state.message ?? "后台服务暂时不可用。";
  category.textContent = state.category ?? "-";
  exitCode.textContent = state.exitCode === null ? "-" : String(state.exitCode);
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
  void runAction(() => bridge.retrySidecar()).finally(() => {
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
