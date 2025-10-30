#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY_DIR="$ROOT_DIR/coverage/python"
WEB_DIR="$ROOT_DIR/coverage/web"
GO_DIR="$ROOT_DIR/coverage/go"

mkdir -p "$PY_DIR" "$WEB_DIR" "$GO_DIR"

cd "$ROOT_DIR"
rm -f "$PY_DIR/coverage.xml" "$ROOT_DIR/coverage.xml"
pytest
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

cd "$ROOT_DIR/omniagents/backends/bubbletea/cmd/omni-tui"
go test ./... -covermode=atomic -coverprofile="$GO_DIR/cover.out"
