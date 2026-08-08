/** 为 Electron 压力烟测创建只接收输入、直到被中断的 Claude Code 替身。 */

import { chmod, mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

/**
 * 在隔离烟测目录中创建名为 claude 的可执行文件，并返回应传给子进程的 PATH。
 *
 * 首轮输出来自仓内真实录制所确认的 init/result 最小 shape，使 20 个 CCHost 进入
 * connected/idle；后续轮次只保持 stdin 和进程存活，用于实测 renderer HTTP
 * 连接预算与 Trowel 自己的 20 connected / 5 running 容量裁决。
 */
export async function createHoldingClaudePath(root, fallbackPath) {
  const binDirectory = path.join(root, "fake-runtime-bin");
  const executable = path.join(binDirectory, "claude");
  await mkdir(binDirectory, { recursive: true });
  await writeFile(
    executable,
    `#!/bin/sh
trap 'exit 0' INT TERM HUP
turn=0
while IFS= read -r _line; do
  turn=$((turn + 1))
  if [ "$turn" -eq 1 ]; then
    printf '{"type":"system","subtype":"init","session_id":"smoke-%s","model":"smoke-model","cwd":"/tmp","tools":[]}\n' "$$"
    printf '{"type":"result","subtype":"success","is_error":false,"total_cost_usd":0,"usage":{"input_tokens":0,"output_tokens":0},"num_turns":1,"duration_ms":1}\n'
  else
    while :; do sleep 1; done
  fi
done
`,
    "utf8",
  );
  await chmod(executable, 0o755);
  return `${binDirectory}${path.delimiter}${fallbackPath}`;
}
