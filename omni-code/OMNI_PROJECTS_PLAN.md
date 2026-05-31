# Omni Projects TUI Plan

## Summary

Build `omni projects` as a terminal-native project and ticket orchestration UI in `omni-code`, modeled on Fleet in `/home/emm/Omni/Workspace/launcher` and implemented with a React Ink frontend patterned after `/home/emm/Omni/Workspace/omniagents`. Keep all project, ticket, sandbox, and workflow concepts inside `omni-code`; treat `omniagents` only as the generic runtime and Ink reference.

## Progress Protocol

- Update this file whenever meaningful progress is made.
- Add a new dated entry under `## Progress Log` for each implementation session.
- Keep `## Current Status` accurate at all times.
- When handing off to another agent, update `## Next Steps` with the exact recommended starting point.
- If implementation changes scope, update `## Scope Decisions` before continuing.

## Important References

### Launcher Fleet

- `/home/emm/Omni/Workspace/launcher/src/shared/types.ts`
- `/home/emm/Omni/Workspace/launcher/src/renderer/features/Fleet/state.ts`
- `/home/emm/Omni/Workspace/launcher/src/main/fleet-manager.ts`
- `/home/emm/Omni/Workspace/launcher/src/main/fleet-workflow-loader.ts`
- `/home/emm/Omni/Workspace/launcher/src/main/fleet-ticket-file-sync.ts`
- `/home/emm/Omni/Workspace/launcher/src/lib/fleet-plan-file.ts`
- `/home/emm/Omni/Workspace/launcher/src/main/sandbox-manager.ts`
- `/home/emm/Omni/Workspace/launcher/src/main/ticket-machine.ts`

### OmniAgents Ink

- `/home/emm/Omni/Workspace/omniagents/omniagents/backends/ink/__init__.py`
- `/home/emm/Omni/Workspace/omniagents/omniagents/backends/ink/tui/src/components/App.tsx`
- `/home/emm/Omni/Workspace/omniagents/omniagents/backends/ink/tui/src/store/appStore.ts`
- `/home/emm/Omni/Workspace/omniagents/omniagents/backends/ink/tui/src/components/Scrollable.tsx`

## Scope Decisions

- `omniagents` remains generic and does not gain project or fleet concepts.
- `omni-code` owns project registry, tickets, pipelines, workflows, sandboxes, and supervisor orchestration.
- `omni projects` uses a dedicated Ink app in `omni_code/tui`.
- Reserved CLI words are intentional; prompt mode escape hatch is `omni -- <words...>`.
- Initial implementation focuses on a headless runtime service plus a useful read/write TUI MVP before deeper supervisor parity.
- Canonical durable store for project and ticket state is `~/.config/omni_code/config.json`, using the Launcher Fleet keys `fleetProjects`, `fleetTickets`, `fleetTasks`, and `fleetSchemaVersion`.
- Legacy Fleet data under `~/.config/Omni Code/config.json` or `~/.config/omni-code-launcher/config.json` should be imported forward into `~/.config/omni_code/config.json`, not treated as the long-term source of truth.

## Current Status

- Done: stable package-qualified `omni` entrypoint via `omni_code.cli:cli_main`
- Done: `omni work` commands exist
- Done: `omni projects` launches an `omni-code` Ink app scaffold
- Done: editable installs point at `/home/emm/Omni/Workspace/omni-code` and `/home/emm/Omni/Workspace/omniagents`
- Done: extracted initial headless projects runtime service and moved project CLI onto it
- In progress: extending the runtime service from project registry into Launcher-style tickets and pipelines
- Done: standardized runtime persistence onto the Launcher Fleet JSON schema and keys
- Done: migrated legacy Fleet JSON state into `~/.config/omni_code/config.json`
- In progress: first useful ticket and kanban board rendering in the Ink TUI
- In progress: adding ticket CLI commands so the board can be exercised without manual file edits
- In progress: runtime-generated TUI snapshots and project-specific pipeline rendering
- In progress: first interactive ticket actions in the TUI
- Not started: supervisor lifecycle integration from Fleet

## Done vs Pending

### Done

- `omni` command precedence is stabilized and documented.
- Prompt escape hatch works via `omni -- <words...>`.
- Console script uses `omni_code.cli:cli_main` instead of `from main import cli_main`.
- Editable installs are pointed at the intended `omni-code` and `omniagents` workspaces.
- Canonical Fleet store is now `~/.config/omni_code/config.json`.
- Legacy Fleet data was migrated from the older Launcher-style config root.
- `ProjectsRuntime` exists and reads/writes Launcher-compatible Fleet keys.
- `omni projects` launches a dedicated `omni-code` Ink app.
- TUI renders project list, board columns, tickets, and ticket detail summary.
- TUI supports ticket creation and ticket movement through the existing CLI surface.
- `omni work start|ssh|stop|status` commands exist.
- `omni work ssh` ensures a work sandbox exists and opens a shell with `docker exec`.
- `omni work` now supports ticket-aware targeting via `--ticket`.
- `omni work` now supports URL discovery and `open` commands for sandbox/code/desktop targets.
- `omni work` now persists shared task metadata into canonical `fleetTasks` records.
- `omni work` now respects persisted ticket worktree paths when present.
- `omni work` now creates and persists ticket worktrees when Fleet ticket state requests them.
- `omni work` now supports worktree cleanup plus basic artifact and diff surfaces.
- `omni work` now exposes session history and text artifact reading for ticket contexts.
- `omni projects ticket` now exposes shared history, artifact, and diff surfaces by delegating to the runtime-backed `work` layer.
- `omni projects ticket` now has a first supervisor control surface: start, send, and stop.
- Runtime-managed `TICKET.yaml` files are now written for created/moved tickets and before supervisor starts.
- `omni projects ticket watch` now provides an inbound ticket-file sync surface for agent-written `TICKET.yaml` changes.

### Pending: Work and Runtime First

- Unify `omni work` with Fleet ticket/task state so work sandboxes and project tickets reference the same runtime objects.
- Port the remaining Launcher-style automatic worktree cleanup lifecycle into the runtime layer.
- Port Launcher-style sandbox/task lifecycle into the runtime layer.
- Add richer artifact browsing/preview and session-aware runtime reuse beyond the first CLI surfaces.
- Add richer artifact browsing/preview and session-aware runtime reuse beyond the first CLI surfaces.

### Pending: Fleet Runtime Parity

- Expand the minimal supervisor lifecycle into Launcher-style orchestration with retries, continuation, and richer state.
- Implement task tracking aligned with Launcher `fleetTasks` semantics.
- Implement `FLEET.md` loading, reload, and hook execution.
- Expand `TICKET.yaml` watching from the current CLI watch/sync surface into richer orchestration behavior.
- Implement blocked-by ordering and `get_next_ticket` parity.
- Implement gated-column and terminal-column behaviors.
- Implement retry, stall detection, and continuation behaviors where appropriate.

### Pending: Omni Projects TUI Last

- Add a proper action palette / command mode.
- Add explicit move-to-column UI beyond hotkeys.
- Add project create/remove/use inside the TUI.
- Add blocked-by editing and richer ticket metadata editing.
- Add dedicated ticket detail view with supervisor/history/artifacts/diff sections.
- Add live runtime updates instead of snapshot-only refreshes.
- Add supervisor controls once the runtime supports them.

## Implementation Plan

### Phase 1: Headless Runtime Extraction

- Create a Python runtime layer in `omni_code/projects_runtime.py` or `omni_code/projects_runtime/`
- Move project registry behavior out of `projects_cli.py`
- Introduce Fleet-compatible models for projects, tickets, tasks, and pipelines
- Keep storage in `~/.config/omni_code`, but align file/schema naming with Launcher concepts where practical

### Phase 2: Tickets and Pipeline

- Add ticket persistence and retrieval
- Implement pipeline defaults and per-project overrides
- Implement blocked-by ordering and `get_next_ticket`
- Implement safe ticket movement semantics modeled on Launcher Fleet

### Phase 3: Ink TUI MVP

- Expand the current TUI into explicit views:
  - project list
  - project board
  - ticket detail
- Add a Zustand store for projects/tickets/UI state
- Support keyboard-first navigation and basic mutations

### Phase 4: Supervisor and Sandbox Integration

- Recreate Fleet supervisor lifecycle behavior in a headless runtime service
- Reuse `omni work` and `sandbox_cli.py` where possible
- Add session history, artifacts, and diff access for ticket detail

### Phase 5: Workflow and Ticket-File Parity

- Add `FLEET.md` config and hook support
- Add `TICKET.yaml` sync and escalation semantics
- Converge behavior with Launcher Fleet for real project use

## Next Steps

1. Improve `omni work` integration with shared Fleet runtime state, especially for ticket-aware SSH targeting.
2. Port Launcher-style worktree, sandbox, and task lifecycle behavior into the runtime.
3. Add supervisor/session/artifact/diff surfaces to the runtime.
4. Leave richer `omni projects` TUI polish until after the runtime and `work` flows are solid.

## Progress Log

### 2026-03-07

- Mapped Fleet architecture in `/home/emm/Omni/Workspace/launcher`.
- Confirmed `omniagents` Ink architecture should be reused as a pattern, not as a project-domain implementation.
- Added `omni work`, `omni projects`, prompt-mode escape hatch, and a dedicated `omni-code` Ink app scaffold.
- Fixed ambiguous entrypoint behavior by switching the console script to `omni_code.cli:cli_main`.
- Corrected local environment to use the intended editable `omni-code` and `omniagents` checkouts.
- Started Phase 1 by committing to a runtime extraction plan.
- Added `omni_code/projects_runtime.py` with initial project, ticket, and pipeline runtime models.
- Refactored `projects_cli.py` to use the runtime service for project operations.
- Added tests covering runtime-backed project operations and initial ticket persistence.
- Expanded the `omni projects` Ink app from a project list scaffold into a basic board view showing tickets by pipeline column.
- Updated this plan so future agents have concrete runtime and TUI next steps.
- Added ticket CLI commands for add/list/move and runtime helpers to support them.
- Added runtime snapshot export so the Ink TUI can render project-specific pipelines and ticket data without owning persistence logic.
- Expanded the TUI to show project-specific columns and a selected ticket detail pane.
- Added the first interactive TUI action: moving the selected ticket to the next column and reloading the board.
- Standardized `ProjectsRuntime` onto the Launcher Fleet store so pre-existing desktop projects and tickets can become the shared source of truth.
- Migrated the existing legacy Fleet state into the new canonical `~/.config/omni_code/config.json` store.
- Re-prioritized the plan so `omni work` and shared runtime parity land before deeper `omni projects` TUI polish.
- Added ticket-aware `omni work` targeting so `start|ssh|stop|status` can resolve a workspace from Fleet ticket state.
- Added `omni work open` and richer `status` URL reporting using Docker port inspection.
- Added initial `fleetTasks` persistence for `omni work` so runtime state is no longer Docker-only.
- Added worktree-aware ticket workspace resolution for `omni work` using persisted Fleet ticket state.
- Added minimal worktree creation and persistence for tickets with `useWorktree` and `branch` set.
- Added `omni work stop --cleanup-worktree`, `omni work artifacts`, and `omni work diff` as the first shared runtime surfaces beyond raw sandbox control.
- Added `omni work history` and `omni work artifact-read` so ticket-linked work contexts can inspect session and artifact output from the terminal.
- Added `omni projects ticket history|artifacts|artifact-read|diff` as shared ticket-level surfaces for future TUI and CLI workflows.
- Added initial ticket supervisor CLI commands (`supervisor-start`, `supervisor-send`, `supervisor-stop`) using the existing sandbox WebSocket RPC path.
- Added runtime-owned `TICKET.yaml` generation so ticket movement and supervisor starts now update the agent-facing control file.
- Added `omni projects ticket sync|watch` so inbound `TICKET.yaml` column and escalation changes can be applied from the terminal.
- Added in-TUI ticket creation and direct move-to-column hotkeys using the runtime-backed CLI surface.
