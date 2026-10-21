#!/bin/bash
set -e

# Remove a potentially pre-existing server.pid for Rails
if [ -f /rails/tmp/pids/server.pid ]; then
  echo "Removing stale server.pid"
  rm /rails/tmp/pids/server.pid
fi

# Ensure storage directory exists for SQLite databases
mkdir -p /rails/storage

# Prepare the database (creates if needed, runs migrations)
echo "Preparing database..."
bin/rails db:prepare

# Ensure Tailwind assets exist before the server boots
echo "Building Tailwind assets..."
bin/rails tailwindcss:build

DEFAULT_DOCKER_AGENTS_VENV="/var/lib/hotel-agents-venv"
export AGENTS_VENV="${AGENTS_VENV:-${DEFAULT_DOCKER_AGENTS_VENV}}"

if [ -d "agents" ] && [ -f "agents/requirements.txt" ]; then
  echo "Setting up AI agents environment (${AGENTS_VENV})"
  if [[ "${AGENTS_VENV}" = /* ]]; then
    AGENT_VENV_PATH="${AGENTS_VENV}"
  else
    AGENT_VENV_PATH="agents/${AGENTS_VENV}"
  fi
  if [ ! -d "${AGENT_VENV_PATH}" ]; then
    mkdir -p "$(dirname "${AGENT_VENV_PATH}")"
    python3 -m venv "${AGENT_VENV_PATH}"
  fi
  "${AGENT_VENV_PATH}/bin/pip" install --upgrade pip
  "${AGENT_VENV_PATH}/bin/pip" install -r agents/requirements.txt
fi

exec "$@"
