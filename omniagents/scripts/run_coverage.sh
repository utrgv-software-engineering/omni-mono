#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY_DIR="$ROOT_DIR/coverage/python"
WEB_DIR="$ROOT_DIR/coverage/web"
INK_DIR="$ROOT_DIR/coverage/ink"

mkdir -p "$PY_DIR" "$WEB_DIR" "$INK_DIR"

cd "$ROOT_DIR"
rm -f "$PY_DIR/coverage.xml" "$ROOT_DIR/coverage.xml"
npm ci --prefix omniagents/backends/web/ui
pytest -n "${PYTEST_XDIST_WORKERS:-auto}" --dist loadfile
if [ -f "$ROOT_DIR/coverage.xml" ]; then
  mv "$ROOT_DIR/coverage.xml" "$PY_DIR/coverage.xml"
fi

cd "$ROOT_DIR/omniagents/studio/server/web"
mkdir -p "$WEB_DIR"
rm -f "$WEB_DIR"/lcov.info
npm run test:coverage
if [ -f "$ROOT_DIR/omniagents/studio/server/web/coverage/lcov.info" ]; then
  mv "$ROOT_DIR/omniagents/studio/server/web/coverage/lcov.info" "$WEB_DIR/lcov.info"
fi

cd "$ROOT_DIR/omniagents/backends/ink/tui"
mkdir -p "$INK_DIR"
rm -f "$INK_DIR"/lcov.info
npm run test -- --coverage
if [ -f "$ROOT_DIR/omniagents/backends/ink/tui/coverage/lcov.info" ]; then
  mv "$ROOT_DIR/omniagents/backends/ink/tui/coverage/lcov.info" "$INK_DIR/lcov.info"
fi
