# QuickSand workflow runner

QuickSand provides the execution VM; Docker Engine inside that VM runs containers
and Compose services. The host's Docker daemon and socket are not used.

Workplace uses **act**, not its former Python workflow interpreter. act executes
ordinary jobs in containers inside the VM and starts declared services there.
One disposable VM contains a complete workflow run; job containers are separate.
Factory evaluation/benchmark/staging actions use a restricted bridge to the agent
runtime outside the VM. See [workflow execution](actions.md) for the common adapter,
image settings, result handling and compatibility limits.

## Start

Install `omniagents-workplace[quicksand]` for workflow jobs. Factory runtime
jobs also need `omniagents[sandbox-quicksand]` installed beside Workplace.

Build a clean image once, on disk-backed storage:

```sh
python -m omniagents_workplace.github.quicksand_space --output ./runner-image
omniagents-workplace serve --data ./workplace-data \
  --workflows quicksand --workflow-quicksand-image ./runner-image \
  --workflow-agent-profile /absolute/path/to/quicksand-profile.yml
```

The builder installs Git, Python, Node 22, uv, Playwright/Chromium, Docker,
Compose and pinned act. `--profile /path/to/profile.yml` instead bakes that Ubuntu or Ubuntu Desktop profile's
`options.setup` commands on top of the Docker/Python base. A lock serializes
builds; an existing image is validated and reused, never overwritten. Use a new
output path to rebuild. Use a clean builder image, not a personal workspace backup.

The factory template's `bin/dev-quicksand` handles this: it keys the clean image
by the setup profile, generates an absolute image path, and configures both the
workflow runner and the agent tool profile. First setup happens once; job writes
are never saved into that image. Persistent personal/pipelined tool workspaces
still retain their own packages and files independently.

A tool profile using a prepared image should match its virtual disk size (the
builder uses 20G), clear setup commands already baked into the image, and start
Docker on every boot:

```yaml
client:
  type: quicksand
options:
  image: /absolute/path/to/runner-image
  disk_size: 20G
  network_mode: full
  setup: []
  startup: ["systemctl start docker"]
manifest:
  root: /workspace
```

## Explicit host runtime actions

Only repositories an admin permits to use `self-hosted` can start host runtime
jobs. In QuickSand mode, a self-hosted job containing one of these actions is a
host runtime job:

- `./.github/actions/evaluate`: `agent`, `scenario`
- `./.github/actions/benchmark`: `suite`, optional `configs`
- `./.github/actions/staging`: optional `limit` (positive integer), `since` (e.g. `24h`)

These jobs accept `actions/checkout@v4` and the runtime actions only. Shell
`run:` steps and other setup actions fail. Put ordinary build/test scripts in a
separate VM job, connected with `needs`. Nothing silently falls back to host shell
execution. `--workflow-agent-profile` must explicitly select QuickSand.

Workplace invokes an installed runtime entry point with validated arguments. It
does not execute the PR's `action.yml` or staging shell script. Workflow-provided
`env`, `BASH_ENV`, `PATH`, `GITHUB_ENV`, and scripts cannot configure that host
process. The host gets an independent checkout of the workflow commit, never
files edited by a tool VM.

Factory agent definitions are **trusted control-plane configuration**: they can
include executable Python context and evaluation modules. Allowing a repository
on the self-hosted runner grants that trust; this is not a way to execute
arbitrary untrusted agent definitions safely on the host.

The template also supplies native composite action files for real GitHub
self-hosted runners. Existing host/Docker Workplace modes invoke the same installed
entry points in their normal job environment. `EVALUATIONS_ARCHIVE` retains the
existing traces, scenario results and benchmark-report locations. Staging uses
the installed replay implementation and a copied Workplace, not production tools.

## Lifecycle and verification

Step cancellation uses QuickSand's guest command cancellation transport. Timeout
cleanup steps can still run in the VM. Job cleanup stops the whole VM, including
Docker containers and detached processes. QuickSand's parent-process reaper stops
QEMU if its owner crashes. Normal close removes temporary VM files; an abrupt
host crash may leave temporary files for the operating system to clean up.

Actions, expressions, matrices and services are executed by act. A failed action
or missing result fails the check; there is no successful-skip fallback.

Opt-in checks make no model calls:

```sh
OMNI_TEST_QUICKSAND_RUNNER_IMAGE=/absolute/path/to/runner-image \
  pytest tests/test_quicksand_runner.py -s --timeout=900
```

They cover actual Docker builds, containers and Compose, exact-SHA checkout,
step outputs/environment, fresh-job isolation, timeouts with cleanup,
cancellation, rejection of host shell steps/environment hooks, and the factory
evaluation environment's VM routing and private Library mount, and persistence
of Docker data across an agent VM restart. The factory
checkout is required beside omni-mono for the evaluation-environment check.
Production staging replays and live benchmark/model suites are separate tests.
