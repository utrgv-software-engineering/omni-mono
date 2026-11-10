# Workflow execution with act

Workplace uses pinned **nektos/act 0.2.92** to execute the workflow file at the
run's commit. The stand-in discovers workflows, matches events, and records logs,
steps, job checks and the run result. It does not interpret workflow expressions,
expand matrices, emulate setup actions, or execute workflow shell steps itself.

`act` handles actions (including JavaScript and composite actions), runtime setup,
job containers, services, matrices, dependencies, conditions and step outputs.
A missing action, unsupported platform, nonzero act exit, or missing job result
fails the check. An explicit `continue-on-error` is honored by act. Matrix results
are grouped under the workflow's job check; any failed matrix entry fails it.

## Execution backends

- **Docker:** a disposable privileged Docker-in-Docker controller, without the
  host Docker socket, credentials or host directory mounts. Each controller has
  its own outer Docker network so inner service ports are not shared across factories. act creates job and
  service containers inside it. Closing the run removes the controller and its
  containers. As with the existing Docker deployment backend, privileged Docker
  is not a hostile-code security boundary equivalent to a VM.
- **QuickSand:** act and Docker execute inside a disposable VM. No Docker or root
  access is required on the desktop host. The existing clean runner image can be
  used; the adapter installs the pinned act binary inside it. New images include
  act already. Jobs within a run have separate containers; they share the run's
  private daemon. The VM is removed when the run ends.

The default Linux job image is based on `ghcr.io/catthehacker/ubuntu:act-22.04`,
with Yarn Classic and refreshed apt package indexes, matching these common hosted-runner assumptions.
It is built inside the private daemon; no registry publishing is needed.
`--workflow-image` can select a registry image for `ubuntu-latest`/`ubuntu-22.04`.
Older `omni-workflow-runner:*` settings migrate to that upstream image instead of
using the former minimal Node/Python image. Ubuntu 20.04/24.04 labels select the
corresponding upstream images. Windows/macOS workflows are not silently run as Linux.
The first run needs internet access to download act, actions and container images.

## Factory runtime actions

Repository administrators can configure encrypted workflow secrets, variables,
and approved credential-bearing workflows in repository Settings. See
[releases](releases.md) for the provider-independent deployment controller and
the conditions under which a workflow receives deployment credentials.

Admin-allowed repositories can use self-hosted jobs containing checkout and the
named evaluate, benchmark or staging actions. act evaluates their inputs and
invokes a narrow request/response bridge. Workplace validates those inputs and
runs the installed runtime entry point in an independent checkout outside the
ordinary workflow VM/controller. Model credentials stay there, and agent tools
continue to use the configured sandbox. Workflow environment variables and guest
files do not configure that host runtime. Arbitrary shell steps in these privileged
jobs fail; ordinary scripts belong in an act container job.

## Compatibility and verification

This is act compatibility, not a claim of identical GitHub-hosted behavior. GitHub
API integrations still need a compatible endpoint and appropriate credentials.
act's cache/artifact services support exchanges within a run; Workplace does not
currently provide a persistent Actions artifact download UI after run cleanup.
A six-hour outer deadline bounds stalled runs. act enforces step timeouts; do not
assume GitHub's job timeout behavior is identical.

```sh
pytest tests/test_actions.py
OMNI_TEST_ACT=1 pytest tests/test_actions.py -k real_act --timeout=900
OMNI_TEST_QUICKSAND_RUNNER_IMAGE=/path/to/runner-image \
  pytest tests/test_quicksand_runner.py -k real_workflow --timeout=900
```

The Docker smoke workflow executes real Ruby and Node setup actions, verifies the
requested versions, uses PostgreSQL/Redis services, installs libvips with sudo,
and checks step outputs. The QuickSand proof builds/runs Docker and Compose inside
the VM through act, verifies exact-SHA checkout and fresh job containers.
