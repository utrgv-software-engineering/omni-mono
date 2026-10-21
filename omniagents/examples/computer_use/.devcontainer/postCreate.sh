#!/usr/bin/env bash
set -euo pipefail

export PIP_EXTRA_INDEX_URL="${PIP_EXTRA_INDEX_URL:-https://pypi.fury.io/ericmichael/}"

echo "[postCreate] Upgrading pip..."
python -m pip install --upgrade pip

echo "[postCreate] Installing pipx..."
sudo apt-get update
sudo apt-get install -y pipx python3-venv
pipx ensurepath || true

echo "[postCreate] Installing test tooling (pytest)..."
pip install pytest

echo "[postCreate] Installing project (editable) with dependencies..."
pip install -e .

echo "[postCreate] Done. You can run tests with: pytest"
