# omni-sandbox

Cross-platform process sandbox CLI with fail-closed network semantics.

`omni-sandbox exec` runs a command inside a platform-native process sandbox:

| Platform | Isolation |
|----------|-----------|
| Linux    | bubblewrap (user namespaces) + seccomp |
| macOS    | `sandbox-exec` (seatbelt profiles) |
| Windows  | AppContainer (restricted tokens + ACLs) |

## Usage

```sh
omni-sandbox exec --workspace /path/to/workspace \
  [--ro-bind DIR]... [--rw-bind DIR]... \
  [--net] \
  [--protect SUBPATH]... [--no-protected-subpaths] \
  [--cwd DIR] [--pids-limit N] \
  -- COMMAND [ARGS...]
```

- `--workspace` is mounted read-write; everything else defaults to a
  read-only view of the host with network blocked.
- `--net` grants unrestricted network access. Network is all-or-nothing:
  `--net-allow` (a host allowlist) is a **hard error by design** — the
  process sandbox cannot enforce host-level egress filtering, so it fails
  closed rather than silently granting full network access. Host
  allowlisting requires a container backend with network-level enforcement.
- `--protect` marks subpath names within writable roots read-only
  (defaults: `.git`, `.env`, `.codex`; `--no-protected-subpaths` clears).
- `--pids-limit` caps the process count via `setrlimit(RLIMIT_NPROC)` on
  Linux/macOS; unsupported on Windows (hard error, fail closed).

Exit code is the sandboxed command's; `125` signals a sandbox setup error.

## Build

```sh
cargo build --release   # binary at target/release/omni-sandbox
cargo test              # unit tests, no sandbox privileges required
```

## Consumers

- **omni-code governance** (`omni_code/governance.py`): re-execs the CLI
  inside `omni-sandbox exec` when the management-plane policy requires
  process isolation. Binary discovery: `OMNI_SANDBOX_BIN` env var →
  packaged `omni_code/assets/bin/` → sibling checkout
  `../omni-sandbox/target/{release,debug}/` → `PATH`.
- **omniagents `process` sandbox backend**
  (`omniagents/core/sandbox/process_extended.py`): a unix_local-style
  sandbox client that wraps every exec in `omni-sandbox exec`.

## History

This crate was extracted from the omni-code repository (`sandbox-cli/`)
with its full commit history via `git subtree split`.

## Maintainer note: no GitHub remote yet

This repository is local-only. Creating the GitHub remote is a manual
maintainer step:

1. Create the `omni-sandbox` repository on GitHub and `git remote add
   origin <url> && git push -u origin main`.
2. CI (`.github/workflows/ci.yml`) builds and tests the four-target
   matrix and uploads release assets; the release-upload step uses the
   default `GITHUB_TOKEN`, so no extra secrets are required unless you
   add publishing steps.

## License

MIT — see [LICENSE](LICENSE).
