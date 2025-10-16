#!/usr/bin/env bash
set -euo pipefail

echo "[postCreate] Upgrading pip..."
python -m pip install --upgrade pip

echo "[postCreate] Installing pipx..."
sudo apt-get update
sudo apt-get install -y pipx python3-venv
pipx ensurepath || true

if [ -n "${FURY_AUTH:-}" ]; then
  echo "[postCreate] Configuring pip extra index from FURY_AUTH..."
  pip config set global.extra-index-url "https://${FURY_AUTH}:@pypi.fury.io/ericmichael/" || true
fi

echo "[postCreate] Installing test tooling (pytest)..."
pip install pytest

echo "[postCreate] Installing project (editable) with dependencies..."
if [ -n "${FURY_AUTH:-}" ]; then
  pip install -e . --extra-index-url "https://${FURY_AUTH}:@pypi.fury.io/ericmichael/"
else
  pip install -e .
fi

echo "[postCreate] Done. You can run tests with: pytest"
