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

exec gosu "${uid}:${gid}" "$@"
