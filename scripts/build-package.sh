#!/usr/bin/env bash
# Build the frontend and stage it under trowel_py/static/ so the wheel picks
# it up via [tool.setuptools.package-data]. Run before `pip install .` (the
# non-editable release install). `pip install -e .` (dev) doesn't need this —
# app.py falls back to web/dist/ directly.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "→ building frontend (web/dist)…"
# Fresh clones have no node_modules — bun install on first run.
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
