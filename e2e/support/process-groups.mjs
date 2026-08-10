/** 按资源快照身份安全终止行为 E2E 自己登记的独立进程组。 */

import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

const DEFAULT_DEPENDENCIES = Object.freeze({
  hostPid: globalThis.process.pid,
  inspectProcess,
  processGroupAlive,
  signalGroup: (processGroup, signal) => globalThis.process.kill(-processGroup, signal),
  delay: (milliseconds) =>
    new Promise((resolve) => setTimeout(resolve, milliseconds)),
});

/**
 * 只终止 PID、进程组和启动身份仍与快照一致的资源。
 *
 * @param {object} snapshot 当前隔离数据根发布的资源快照。
 * @param {object} options 信号策略和测试可替换依赖。
 * @param {string} options.expectedAppInstanceIdentity descriptor 对应的去敏实例身份。
 * @param {string} options.expectedDataRootIdentity 隔离数据根的去敏身份。
 * @returns {Promise<number>} 实际核验通过的独立进程组数量。
 */
export async function terminateVerifiedProcessGroups(
  snapshot,
  {
    expectedAppInstanceIdentity,
    expectedDataRootIdentity,
    initialSignal = "SIGTERM",
    graceMs = 3_000,
    finalMs = 3_000,
    dependencies = DEFAULT_DEPENDENCIES,
  } = {},
) {
  if (
    snapshot?.version !== 2 ||
    !Array.isArray(snapshot?.resources) ||
    typeof expectedAppInstanceIdentity !== "string" ||
    !expectedAppInstanceIdentity ||
    typeof expectedDataRootIdentity !== "string" ||
    !expectedDataRootIdentity ||
    snapshot.app_instance_id !== expectedAppInstanceIdentity ||
    snapshot.data_root_identity !== expectedDataRootIdentity
  ) {
    throw new Error("process-group termination rejected an untrusted resource snapshot");
  }
  const hostIdentity = await dependencies.inspectProcess(dependencies.hostPid);
  if (!hostIdentity) {
    throw new Error("process-group termination could not verify the runner identity");
  }
  const groups = new Set();
  for (const resource of snapshot.resources) {
    if (
      resource?.state === "closed" ||
      !Number.isInteger(resource?.pid) ||
      !Number.isInteger(resource?.process_group) ||
      typeof resource?.process_start_identity !== "string"
    ) {
      continue;
    }
    const current = await dependencies.inspectProcess(resource.pid);
    if (
      !current ||
      current.processGroup !== resource.process_group ||
      current.startIdentity !== resource.process_start_identity ||
      current.processGroup <= 1 ||
      current.processGroup === hostIdentity.processGroup
    ) {
      continue;
    }
    groups.add(current.processGroup);
  }

  signalGroups(groups, initialSignal, dependencies.signalGroup);
  await waitForGroups(groups, graceMs, dependencies);
  const liveAfterGrace = new Set(
    [...groups].filter(dependencies.processGroupAlive),
  );
  if (initialSignal !== "SIGKILL" && liveAfterGrace.size > 0) {
    signalGroups(liveAfterGrace, "SIGKILL", dependencies.signalGroup);
    await waitForGroups(liveAfterGrace, finalMs, dependencies);
  }
  if ([...groups].some(dependencies.processGroupAlive)) {
    throw new Error("verified process-group termination did not converge");
  }
  return groups.size;
}

/** 对一组已核验的进程组发送信号，目标已退出时视为完成。 */
function signalGroups(groups, signal, signalGroup) {
  for (const processGroup of groups) {
    try {
      signalGroup(processGroup, signal);
    } catch (error) {
      if (error?.code !== "ESRCH") throw error;
    }
  }
}

/** 在有界时间内等待所有目标进程组退出。 */
async function waitForGroups(groups, timeoutMs, dependencies) {
  const deadline = Date.now() + timeoutMs;
  while (
    Date.now() < deadline &&
    [...groups].some(dependencies.processGroupAlive)
  ) {
    await dependencies.delay(25);
  }
}

/** 读取与生产 Host 相同的进程组和启动身份事实。 */
async function inspectProcess(pid) {
  if (!Number.isInteger(pid) || pid <= 0) return null;
  try {
    const { stdout } = await execFileAsync("ps", [
      "-o",
      "pgid=",
      "-o",
      "lstart=",
      "-o",
      "comm=",
      "-p",
      String(pid),
    ]);
    const fields = stdout.trim().split(/\s+/);
    if (fields.length < 7) return null;
    const processGroup = Number(fields.shift());
    if (!Number.isInteger(processGroup)) return null;
    return {
      processGroup,
      startIdentity: createHash("sha256").update(fields.join(" ")).digest("hex"),
    };
  } catch {
    return null;
  }
}

/** 用信号 0 探测进程组是否仍存在。 */
function processGroupAlive(processGroup) {
  try {
    globalThis.process.kill(-processGroup, 0);
    return true;
  } catch (error) {
    return error?.code === "EPERM";
  }
}
