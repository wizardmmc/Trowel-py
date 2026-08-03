# Agent statistics fixtures

这两份 JSONL 是 L01 真实数据的最小脱敏子集，不是手写协议样例。

- `cc-binding-2.1.197.jsonl` 取自 2026-07-14 的 CC 2.1.197 transcript，保留同一
  assistant `message.id` 随 thinking/tool 分片重复出现的顺序、时间和 usage；正文、
  身份、路径与工具参数已替换。
- `codex-turn-0.144.0.jsonl` 取自 Trowel normalized journal，保留 Codex 0.144.0
  一轮内多次 `usage_updated` 的累计水位、最后一次 `last`、首段文字和完成时序；
  正文和身份已替换。

字段关系和数值用于防止重复计数与错误使用 `usage.last`，不能作为模型性能样本。
