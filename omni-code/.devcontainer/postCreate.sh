#!/usr/bin/env bash
set -euo pipefail

echo "[postCreate] Upgrading pip..."
python -m pip install --upgrade pip

echo "[postCreate] Installing project in editable mode with dev deps..."
if [ -n "${FURY_AUTH:-}" ]; then
  echo "Using Gemfury extra index URL"
  pip install -e .[dev] --extra-index-url "https://${FURY_AUTH}:@pypi.fury.io/ericmichael/"
else
  pip install -e .[dev]
fi

echo "[postCreate] Done. You can run tests with: pytest"

