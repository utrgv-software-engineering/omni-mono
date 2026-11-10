#!/usr/bin/env bash
# The staging check (.github/workflows/staging.yml): this checkout's factory on
# a copy of production's work, graded beside production.
#
# 1. Restore production's latest backup into a throwaway workplace, and point
#    its apps' webhooks at the factory below. The copy never sends anything
#    to production.
# 2. Start that workplace, and this checkout's factory against it.
# 3. Replay production's latest events into it. Every replayed run is graded.
# 4. Wait for the runs, then compare. The check fails when this factory did
#    worse: a judge's lower pass rate, or a run that failed where
#    production's completed.
#
# It runs on the self-hosted runner, which gives it production's backups and
# event store, read-only (PRODUCTION_BACKUPS, PRODUCTION_HOME). Anywhere
# else, such as your laptop, there is nothing to compare with, and it says
# so and passes.
set -euo pipefail
# The trusted runner sets cwd to its independent checkout of the commit.

if [ -z "${PRODUCTION_BACKUPS:-}" ] || [ ! -d "${PRODUCTION_BACKUPS}" ]; then
  echo "staging-check: no production backups here (PRODUCTION_BACKUPS); nothing to compare with"
  exit 0
fi
: "${PRODUCTION_HOME:?PRODUCTION_HOME is production factory's home (its event store)}"
: "${STAGING_REPLAY_LIMIT:?STAGING_REPLAY_LIMIT is how many of production's latest events to replay}"
: "${STAGING_REPLAY_SINCE:?STAGING_REPLAY_SINCE is how far back to replay from (24h)}"

slug=$(python3 - <<'PY'
from omniagents.core.project.runtime import load_project
print(load_project("project.yml").product.slug)
PY
)
events="$PRODUCTION_HOME/integrations/$slug.db"
if [ ! -f "$events" ]; then
  echo "staging-check: production's factory has no event store yet ($events); nothing to compare with"
  exit 0
fi

work=$(mktemp -d)
host="${OMNIAGENTS_WORKPLACE_HOST:-127.0.0.1}"
workplace_port=7310 sandbox_port=7311 factory_port=8775
token=$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')
pids=()
cleanup() {
  for pid in "${pids[@]}"; do kill "$pid" 2>/dev/null || true; done
  wait 2>/dev/null || true
  rm -rf -- "$work"
}
trap cleanup EXIT

wait_for() {  # port, what: until it answers (two minutes at most)
  if ! python3 - "$1" <<'PY'
import socket, sys, time
deadline = time.time() + 120
while time.time() < deadline:
    try:
        socket.create_connection(("127.0.0.1", int(sys.argv[1])), timeout=1).close()
        sys.exit(0)
    except OSError:
        time.sleep(1)
sys.exit(1)
PY
  then
    echo "staging-check: $2 did not start" >&2
    exit 1
  fi
}

sandbox_url="http://$host:$sandbox_port"
listen_host="0.0.0.0"
if [ -n "${OMNIAGENTS_SANDBOX_PROFILE:-}" ] && python3 -c 'import os,yaml; from pathlib import Path; raise SystemExit((yaml.safe_load(Path(os.environ["OMNIAGENTS_SANDBOX_PROFILE"]).read_text()).get("client") or {}).get("type") != "quicksand")'; then
# Tool VMs reach only the copied staging Workplace through a loopback forward.
python3 - "$OMNIAGENTS_SANDBOX_PROFILE" "$work/sandbox.yml" "$sandbox_port" <<'PYCODE'
import sys, yaml
from pathlib import Path
profile = yaml.safe_load(Path(sys.argv[1]).read_text())
profile.setdefault("options", {})["host_services"] = {7300: int(sys.argv[3])}
profile["options"]["exposed_ports"] = []
profile["options"]["port_forwards"] = {}
Path(sys.argv[2]).write_text(yaml.safe_dump(profile))
PYCODE
export OMNIAGENTS_SANDBOX_PROFILE="$work/sandbox.yml"
sandbox_url="http://10.0.2.101:7300"
listen_host="127.0.0.1"
fi
echo "::group::A copy of production's workplace"
cp "$events" "$work/production.db"
omniagents-workplace restore --from "$PRODUCTION_BACKUPS" --data "$work/data"
omniagents-workplace webhooks --data "$work/data" --to "http://127.0.0.1:$factory_port"
omniagents-workplace serve --data "$work/data" --host "$listen_host" --port "$workplace_port" \
  --public-url "http://127.0.0.1:$workplace_port" \
  --sandbox-port "$sandbox_port" --sandbox-url "$sandbox_url" \
  --workflows off > "$work/workplace.log" 2>&1 &
pids+=($!)
wait_for "$workplace_port" "the workplace"
omniagents-workplace env --data "$work/data" > "$work/factory.env"
echo "::endgroup::"

echo "::group::This factory, against it"
# Its sessions, traces and event store: kept on a server (EVALUATIONS_ARCHIVE).
if [ -n "${EVALUATIONS_ARCHIVE:-}" ]; then
  export OMNIAGENTS_HOME="$EVALUATIONS_ARCHIVE/staging/$(date -u +%Y%m%dT%H%M%SZ)-${GITHUB_SHA:0:7}"
else
  export OMNIAGENTS_HOME="$work/home"
fi
echo "Its sessions and traces: $OMNIAGENTS_HOME"
set -a
. "$work/factory.env"
set +a
omniagents run --project project.yml --mode server --host 127.0.0.1 --port "$factory_port" \
  --auth-token "$token" > "$work/factory.log" 2>&1 &
pids+=($!)
wait_for "$factory_port" "the factory"
echo "::endgroup::"

echo "::group::Production's latest work, again"
omniagents integrations replay --events "$work/production.db" --since "$STAGING_REPLAY_SINCE" \
  --limit "$STAGING_REPLAY_LIMIT" --to "http://127.0.0.1:$factory_port" --token "$token"
candidate="$OMNIAGENTS_HOME/integrations/$slug.db"
omniagents integrations wait --events "$candidate" --timeout 75m
echo "::endgroup::"

status=0
omniagents integrations compare --baseline "$work/production.db" --candidate "$candidate" \
  --since 2h --fail-on-regression || status=$?
if [ "$status" -ne 0 ]; then
  echo "::group::The factory's log"
  tail -200 "$work/factory.log"
  echo "::endgroup::"
fi
exit "$status"
