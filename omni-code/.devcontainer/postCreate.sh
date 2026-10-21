#!/usr/bin/env bash
set -euo pipefail

echo "[postCreate] Upgrading pip..."
python -m pip install --upgrade pip

echo "[postCreate] Installing pipx..."
sudo apt-get update
sudo apt-get install -y pipx python3-venv
pipx ensurepath || true

EXTRA_INDEX="https://pypi.fury.io/ericmichael/"

echo "[postCreate] Installing test tooling (pytest)..."
pip install pytest

echo "[postCreate] Installing project (editable) with dependencies..."
pip install -e . --extra-index-url "$EXTRA_INDEX"

echo "[postCreate] Done. You can run tests with: pytest"
