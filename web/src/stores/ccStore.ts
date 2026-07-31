/** @deprecated L05 完成且调用方清零后删除；改从 agent/application 导入。 */
export * from "../agent/application/store";
export * from "../agent/domain";
export type { AgentHistoryRow, AgentSession, Runtime } from "../agent/transport/api";
export {
  createAgentStore as createCcStore,
  useAgentStore as useCcStore,
} from "../agent/application/store";
