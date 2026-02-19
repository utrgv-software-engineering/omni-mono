#!/usr/bin/env bash
set -euo pipefail

uid="${LOCAL_UID:-1000}"
gid="${LOCAL_GID:-1000}"

existing_user_name=""
if getent passwd "${uid}" >/dev/null 2>&1; then
  existing_user_name="$(getent passwd "${uid}" | cut -d: -f1)"
fi

existing_group_name=""
if getent group "${gid}" >/dev/null 2>&1; then
  existing_group_name="$(getent group "${gid}" | cut -d: -f1)"
fi

group_name="${existing_group_name:-hostgroup}"

if ! getent group "${group_name}" >/dev/null 2>&1; then
  groupadd -g "${gid}" "${group_name}"
fi

if [[ -z "${existing_user_name}" ]]; then
  if id -u user >/dev/null 2>&1; then
    usermod -u "${uid}" -g "${gid}" user >/dev/null 2>&1 || true
  else
    useradd -m -u "${uid}" -g "${gid}" -s /bin/bash user
  fi
fi

export HOME=/home/user
mkdir -p "${HOME}"
chown "${uid}:${gid}" "${HOME}" >/dev/null 2>&1 || true

code_server_enabled="${OMNI_CODE_CODE_SERVER:-0}"
if [[ "${code_server_enabled}" == "1" || "${code_server_enabled}" == "true" ]]; then
  code_server_port="${CODE_SERVER_PORT:-8080}"
  code_server_auth="${CODE_SERVER_AUTH:-password}"
  code_server_workspace="${CODE_SERVER_WORKSPACE:-/home/user/workspace}"
  code_server_log_dir="${CODE_SERVER_LOG_DIR:-/home/user/.local/share/code-server}"
  mkdir -p "${code_server_log_dir}" >/dev/null 2>&1 || true
  chown -R "${uid}:${gid}" "${code_server_log_dir}" >/dev/null 2>&1 || true
  mkdir -p "${HOME}/.config" >/dev/null 2>&1 || true
  chown -R "${uid}:${gid}" "${HOME}/.config" >/dev/null 2>&1 || true

  gosu "${uid}:${gid}" bash -lc "nohup code-server --bind-addr 0.0.0.0:${code_server_port} --auth ${code_server_auth} ${code_server_workspace} > '${code_server_log_dir}/omni-code.log' 2>&1 &" || true
fi

exec gosu "${uid}:${gid}" "$@"
