"""Cross-provider quota read model (slice-093-pre).

A unified, read-only view of provider quota (GLM Coding Plan 5h/weekly via a
5-minute poll; Codex/GPT ``usedPercent`` via the existing slice-077 push),
consumed by the WorkBroker (093) and the frontend. Fetching lives here so
slice-093 ("不实现付费账单抓取") only consumes — never fetches — this model.
"""
