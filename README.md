# Omni

One repository for the Omni agent stack and related projects.

| Directory | What it is | Published as |
| --- | --- | --- |
| `omniagents/` | Agent framework: runtime, RPC protocol, web and Ink UIs | `omniagents` on PyPI |
| `omni-code/` | The Omni Code product built on omniagents | `omni-code` on PyPI |
| `omni-desktop/` | Electron + browser app that installs and runs Omni Code | GitHub releases |

Each directory keeps its own build, tests and `CLAUDE.md`; see the README in
each one. Tags are prefixed by package, e.g. `omniagents-v0.7.40`,
`omni-code-v0.6.34`, `omni-desktop-v1.8.6`.

History from the former `omniagents`, `omni-code` and `omni-desktop` (launcher)
repositories is preserved under each directory, so `git log -- <path>` and
`git blame` work across the move. The retired Rust `omni-sandbox` CLI is in
history under `omni-sandbox/`.
