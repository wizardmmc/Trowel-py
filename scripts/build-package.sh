#!/usr/bin/env bash
# 发布安装前将前端产物放入 trowel_py/static，供 wheel 的 package-data 收集。
# editable install 会由 app.py 直接读取 web/dist，无需执行此脚本。
set -euo pipefail
cd "$(dirname "$0")/.."

echo "→ building frontend (web/dist)…"
# 首次构建缺少 node_modules 时补装依赖。
if [ ! -d web/node_modules ]; then
  echo "  (no web/node_modules — running bun install first)"
  ( cd web && bun install )
fi
( cd web && bun run build )

echo "→ copying web/dist → trowel_py/static (for package-data)…"
rm -rf trowel_py/static
cp -r web/dist trowel_py/static

cat <<'EOF'
✓ built. Install:
    uv pip install .             # release-style (copies into site-packages)
    uv pip install -e .          # editable dev (links source; uses web/dist)
Then:
    trowel-py                    # starts server (:8000) + opens browser
EOF
