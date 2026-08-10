/** 对 turn、session 与最终 App 三层资源终态执行独立断言。 */

import { createHash } from "node:crypto";

function liveResourcesFor(records, predicate) {
  return records.filter((record) => record?.state !== "closed" && predicate(record));
}

/** @param {object[]} records resource snapshot 记录。 @param {string} turnId 原生 turn ID。 */
export function assertTurnResourcesClosed(records, turnId) {
  const count = liveTurnResourceCount(records, turnId);
  if (count > 0) throw new Error(`turn still owns ${count} resource(s)`);
}

/** 返回指定 turn 尚未关闭的资源数，供终态后的异步收敛轮询使用。 */
export function liveTurnResourceCount(records, turnId) {
  const ownerId = redactIdentity(turnId);
  return liveResourcesFor(
    records,
    (record) => record.owner_scope === "turn" && record.owner_id === ownerId,
  ).length;
}

/** @param {object[]} records resource snapshot 记录。 @param {string} sessionId Agent session ID。 */
export function assertSessionResourcesClosed(records, sessionId) {
  const ownerId = redactIdentity(sessionId);
  const live = liveResourcesFor(
    records,
    (record) => record.owner_scope === "session" && record.owner_id === ownerId,
  );
  if (live.length > 0) {
    throw new Error(`session still owns ${live.length} resource(s)`);
  }
}

/** @param {{status?: string, remaining_resource_count?: number}} marker Host 退出 marker。 */
export function assertApplicationResourcesClosed(marker) {
  const remaining = Number(marker?.remaining_resource_count ?? -1);
  if (marker?.status !== "closed" || remaining !== 0) {
    throw new Error(`application still owns ${remaining} resource(s)`);
  }
}

/** 从原子快照提取资源记录，拒绝把缺失/畸形快照误判成归零。 */
export function snapshotRecords(snapshot) {
  if (!snapshot || !Array.isArray(snapshot.resources)) {
    throw new Error("resource snapshot does not contain records");
  }
  return snapshot.resources;
}

/** 复现生产快照的不可逆 owner 标识，不让明文会话或 turn ID 进入证据。 */
export function redactIdentity(value) {
  return createHash("sha256").update(value, "utf8").digest("hex").slice(0, 20);
}
