# QuickSand local VM sandbox

QuickSand is an experimental alternative to Docker for agent execution. Every factory agent can use it. Docker remains the default. Workplace also has an optional QuickSand workflow runner; production deployment remains unchanged.

Install the optional dependency:

```sh
pip install 'omniagents[sandbox-quicksand]'
```

This installs QuickSand, its QEMU runtime, and the Ubuntu image package. The image package may download its platform-specific image on first boot. Local inference and offline dependency installation are separate concerns.

## Profile

Use a separate factory instance/home and profile when trying this backend. Do not point it at an existing Docker state record: switching providers must never silently replace an existing environment. The persistent factory integration recognizes `client.type: quicksand`; other hosts can use the normal sandbox client factory directly.

```yaml
version: 1
client:
  type: quicksand
  state_dir: /absolute/path/to/factory-vms
options:
  image: ubuntu
  memory: 2G
  cpus: 2
  disk_size: 10G
  network_mode: full
  setup:
    - apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git
  exposed_ports: [8000]
  port_forwards:
    8000: 18000
  host_services:
    7300: 7395
manifest:
  root: /workspace
terminal:
  command: bash -i
```

`setup` runs once on a fresh VM before repository materialization. `startup` runs on every boot, including resume; the factory uses it to start the guest Docker daemon. Use it for initial Git installation if repositories will be seeded. Agents can install further packages during normal work; the persistent working disk preserves them. Docker's `build:` profile section is not supported. Existing machine options are retained on resume; changing RAM, ports, or setup in the profile does not migrate a saved VM automatically.

`port_forwards` maps guest ports to explicit **host loopback** ports. The example exposes a guest app at `http://127.0.0.1:18000`. `host_services` maps guest ports at `10.0.2.101` to host loopback ports: the VM reaches Workplace at `http://10.0.2.101:7300` when Workplace listens on host port 7395. Configure the factory's Git stand-in URL accordingly, such as `http://10.0.2.101:7300/github`. Docker service names and `host.docker.internal` do not automatically resolve in this VM.

Network defaults to `mounts_only`, which restricts general internet access. Explicit host-service forwarding still counts as network access in the approval policy. Use `full` for online package installation, or `none` for no network device. Runtime network toggling is not implemented for this backend.

## Interactive terminals

The adapter implements the same PTY interface as the Docker and Unix-local backends, including input, output streaming, resize, Ctrl-C, and termination. A Python helper **inside the guest** owns the PTY and its process group. Control messages travel over QuickSand's guest command transport to a private guest Unix socket. No agent commands run through the host shell.

Ubuntu includes the Python interpreter needed by the bridge. Other images must provide Python 3, a POSIX PTY implementation, and the normal tools required by the SDK. Pipe-based background jobs use QuickSand's streaming command API. Terminal output buffers are bounded; long unattended output can be truncated.

## Persistence and failure behavior

Each VM has a stable identity and a private directory. Its standalone
`working/disk.qcow2` lives on persistent storage, outside the SDK's temporary
socket/log directory. Restarts reuse that same disk: no snapshot, copy,
consolidation, or extra overlay layer is required. Legacy SDK saves are imported
once into a standalone working disk; the original save remains intact.

Graceful shutdown flushes the guest filesystem and QEMU block device, terminates
managed commands, and stops the VM. Installed packages, Git worktrees and other
disk changes survive. Processes and terminal sessions restart. The legacy
`save_interval_seconds` option is accepted but no longer starts periodic snapshots.

Explicit backups use QEMU's full-disk backup job without pivoting the running VM
onto another overlay. A completed backup is published atomically; errors leave
the prior backup intact. Backups and the working disk are separate. A file lock
prevents two owners from opening one VM. An unclean `RUNNING` marker still requires
inspection and explicit recovery; the working disk is retained, not silently
replaced with an older checkpoint. Missing or mismatched state fails closed.

The existing maintenance CLI supports QuickSand. Set `STATE` to the factory's `integrations/<product>.personal-sandbox.json` under its `OMNIAGENTS_HOME`:

```sh
python -m omniagents.integrations.personal_workspace status --state "$STATE"
python -m omniagents.integrations.personal_workspace backup --state "$STATE" --to ./backups
# Stop the factory before reset or restore. Reset makes a mandatory backup first.
python -m omniagents.integrations.personal_workspace reset --state "$STATE" --to ./backups --confirm-reset
python -m omniagents.integrations.personal_workspace restore --state "$STATE" --source ./backups/personal-TIMESTAMP-ID
```

A live backup requests a fresh point-in-time disk backup from the running owner
and waits for completion; it never silently copies a stale checkpoint. A stopped
VM is copied under its owner lock. `backup.json` records the timestamp. Bind mounts
are backed up separately. A failed backup prevents reset; an active or unclean VM
cannot be reset. Restore assigns a new VM identity and resumes on the next factory
start. Use `--vm-root` to relocate storage, matching `client.state_dir`.

Protect backups as credentials: VM disks can contain tokens. Host mounts are **not** included in VM backups. The Library remains in Workplace's data directory and is covered by its backup. On another machine, configure `LIBRARY_FILES_HOST_PATH` to the restored Workplace Library directory before starting.

## Factory launcher and Library

In the factory template, `bin/dev-quicksand` installs the optional dependency group, selects `sandbox_profile_quicksand.yml`, builds a clean toolchain image once, and runs `Procfile.quicksand.dev`. Ordinary workflow jobs get disposable VMs with their own Docker daemon. Explicit evaluation/benchmark/staging runtime actions keep agent processes on the host and route tools into QuickSand. See the [Workplace runner guide](../../../omniagents-workplace/docs/quicksand-runner.md). Stop another local factory/Workplace using the same ports before launching it. The launcher uses a separate `.workplace/quicksand-runtime` home, keeping existing Docker sandbox state intact. Every agent uses the same configured backend.

`OMNIAGENTS_SANDBOX_PROFILE` explicitly selects the profile, bypassing Docker-specific user profile layers. The bundled profile maps guest Workplace access to `10.0.2.101:7300`; the Procfile sets Workplace's sandbox URL to match. The launcher uses the existing Workplace data.

`LIBRARY_FILES_HOST_PATH` is mounted at `/workspace/Library` through QuickSand's host-directory sharing. Normal Library tools create metadata, and normal agent file tools edit the shared files. Generic profile mounts use `client.binds: [{source: /absolute/host/directory, target: /guest/directory, read_only: false}]`.

## Validation

Fast regression tests:

```sh
pytest tests/unit/core/sandbox/test_quicksand.py
```

Opt-in VM proof, with the sibling `omniagents-workplace` package installed:

```sh
OMNI_TEST_QUICKSAND=1 pytest tests/integration/test_quicksand_vm.py -s --timeout=900
```

The proof starts a disposable Workplace, boots Ubuntu, installs packages, creates a Git worktree and uncommitted file, checks binary file transfer and credential refresh, uses an interactive terminal, exposes an HTTP preview, and verifies repeated saves/restarts, Library sharing, and backup/reset/restore after deleting the original VM. It makes no model calls and does not modify a production instance.

Hardware acceleration is important for practical performance. Check the logged accelerator: KVM/HVF/WHPX are hardware paths; TCG is software emulation. The initial Linux development host lacked `/dev/kvm`, so its proof uses TCG. This establishes functionality, not hardware-accelerated performance or readiness on DigitalOcean, macOS, or Windows.

A separate opt-in live factory test runs one actual generalist task through the factory run starter, normal approvals, repository tools, and Library tools against disposable Workplace data:

```sh
OMNI_TEST_QUICKSAND_FACTORY=1 pytest tests/integration/test_quicksand_factory.py -s --timeout=1200 --basetemp=.pytest_cache/quicksand
```

It requires the sibling factory checkout (or `OMNI_FACTORY_ROOT`) and existing model sign-in. Set `OMNIAGENTS_CODEX_HOME` to that sign-in directory when needed. The test validates token refresh before provisioning. Use a disk-backed `--basetemp`: full-toolchain disk generations can exceed a memory-backed `/tmp` quota. It does not commit or push.

Verified on Linux with TCG software emulation: 69 focused regression tests, the real VM lifecycle proof, and one actual factory generalist task. The task created a Git worktree, left its edit uncommitted, passed all nine fixture repository tests, and created/edited/showed a Library document through normal tools and approvals. Independent checks confirmed the repository file and shared Library contents. The full factory profile was provisioned with Node 22 and Chromium, then resumed from a relocated backup for the live task.

## Current boundaries

- No automatic migration from a Docker workspace.
- No live-process reattachment across factory restarts.
- The general environment provisioner's Docker-specific reconnect/maintenance features are not extended by this prototype. The persistent factory path is the validated target.

Upstream: [QuickSand](https://github.com/microsoft/quicksand), [save lifecycle](https://github.com/microsoft/quicksand/blob/main/docs/user-guide/05-save-and-rollback.md), [performance](https://github.com/microsoft/quicksand/blob/main/docs/user-guide/08-performance.md).

## VM desktop

Select `options.image: ubuntu-desktop` and `options.enable_display: true`. The
factory QuickSand profile enables this and includes the builtin `desktop`
capability: screenshots, pixel-coordinate mouse clicks/movement, scrolling,
typing, and key chords. Agent processes remain on the host; all input targets
the assigned VM. Native keyboard typing currently accepts ASCII text.

Workplace shows **Desktop** when the factory has a running display. Its noVNC
viewer uses an authenticated WebSocket bridge to QuickSand's loopback VNC
server. It reconnects after VM restarts; keyboard/mouse control is available
inside the viewer. Access requires a Workplace administrator and the factory
workspace owner's upstream identity. The browser never receives that token.

For the preconfigured browser environment, use `options.image: quicksand-cua`,
`enable_display: false`, `desktop_vnc_port: 5901`, and a loopback mapping such as
`port_forwards: {5901: 15901}`. CUA supplies Xvfb, x11vnc, Chromium and Playwright;
its systemd services start on boot. The same desktop tools and authenticated
Workplace viewer target this guest display. Input and screenshots use the
standard `asyncvnc` client; no custom VNC protocol or second desktop is started.
Native QEMU display and guest VNC are mutually exclusive. Keep the forwarded
port private; do not publish it on a container or public host interface.

Workflow jobs remain separate, disposable VMs with display disabled. They can
use the same prepared image without sharing the resident desktop's state.

The Linux x86_64 `quicksand-qemu` 0.5.12 wheel omits the graphics modules and
VGA firmware used by its desktop mode. A narrowly version-matched compatibility
helper downloads checksum-pinned official Ubuntu packages and extracts only
the missing files into `OMNIAGENTS_HOME/runtimes`. It requires neither root nor
system package tools, and does not modify the SDK installation. Other runtime
builds use their packaged graphics support. Review/remove this helper when
upstream packaging includes these files.

Native VM proof (no model calls):

```sh
OMNI_TEST_QUICKSAND=1 pytest tests/integration/test_quicksand_desktop.py -s --timeout=600
```

When hosting QuickSand inside Docker with its default seccomp policy, set
`OMNIAGENTS_QUICKSAND_DISK_AIO=threads` on the runtime and image-builder
containers. This uses QEMU's standard threaded disk I/O because Docker blocks
`io_uring`; it does not require a privileged container or disabling seccomp.
