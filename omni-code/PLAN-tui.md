# Omni TUI Plan

## Vision

A terminal UI that gives developers the same fleet management experience as the Omni desktop app. The user should never need to memorize CLI flags — everything is driven interactively. The CLI plumbing (`omni work`, `omni projects`) remains as machine-readable infrastructure, but the TUI is the primary interface for terminal users.

`omni projects` (no subcommand) launches the TUI. That's the only command a user needs to know.

---

## Architecture Decision: Ink (keep) vs Textual (switch)

**Keep Ink (React).** Rationale:
- Infrastructure already exists (launcher, build system, esbuild)
- Team knows React from the Electron renderer
- Ink has good primitives for the views we need (Box, Text, useInput, useApp)
- ink-text-input, ink-select-input, ink-spinner are available

**Alternative considered**: Python Textual. Would avoid the Node dependency and let the TUI call `ProjectsRuntime` directly instead of shelling out. But the build/launch infra would need rewriting, and the team doesn't use Textual.

---

## Communication: TUI <-> Runtime

**Current**: TUI shells out to `omni projects ticket add/move` via `spawnSync`. Slow, no streaming, no live state.

**Proposed**: The TUI launcher starts a lightweight JSON-RPC server (Unix socket or stdin/stdout pipe) that wraps `ProjectsRuntime`. The TUI connects to it and makes calls directly. This gives us:
- Fast calls (no process spawn per action)
- Streaming events (phase changes, task status, supervisor messages)
- Live state updates pushed to TUI

```
omni projects (CLI)
  -> tui_launcher.py starts JSON-RPC server (Python, wraps ProjectsRuntime)
  -> tui_launcher.py spawns node tui/dist/index.js --rpc-fd=3
  -> TUI connects to RPC server over fd 3 (or Unix socket)
  -> TUI renders, sends RPC calls, receives events
```

### RPC Methods (what the TUI can call)

```
# Projects
projects.list -> ProjectRecord[]
projects.add(label, workspace_dir) -> ProjectRecord
projects.remove(project_id) -> void
projects.use(project_id) -> void
projects.show(project_id) -> ProjectRecord

# Pipeline
pipeline.get(project_id) -> PipelineRecord
pipeline.update(project_id, columns) -> PipelineRecord

# Tickets
tickets.list(project_id) -> TicketRecord[]
tickets.add(project_id, title, description?, priority?) -> TicketRecord
tickets.update(ticket_id, patch) -> TicketRecord
tickets.move(ticket_id, column_id) -> TicketRecord
tickets.delete(ticket_id) -> void

# Tasks / Sandbox
tasks.list(project_id) -> TaskRecord[]
tasks.status(ticket_id) -> TaskRecord | null
sandbox.start(ticket_id) -> { container_name, urls }
sandbox.stop(ticket_id) -> void
sandbox.ssh(ticket_id) -> void  # TUI suspends, docker exec runs, TUI resumes

# Supervisor
supervisor.start(ticket_id, prompt?) -> { session_id, run_id }
supervisor.stop(ticket_id) -> void
supervisor.send(ticket_id, message) -> void
supervisor.reset(ticket_id) -> void

# Artifacts & History
artifacts.list(ticket_id, dir?) -> ArtifactEntry[]
artifacts.read(ticket_id, path) -> string
history.get(ticket_id, limit?) -> Message[]
diff.get(ticket_id) -> FileChange[]
```

### RPC Events (server -> TUI, pushed)

```
event:phase(ticket_id, phase)         # phase transition
event:task-status(ticket_id, status)  # sandbox status change
event:supervisor-message(ticket_id, message)  # agent chat message
event:token-usage(ticket_id, usage)   # token accumulation
```

---

## Views

### 1. Dashboard (default on launch)

```
Omni Projects                                    q quit  ? help
---------------------------------------------------------------
  Projects            | Active Work
                      |
> * my-app       (12) |  #auth-fix     running    impl
    api-server    (3) |  #add-tests    awaiting   review
    docs          (0) |  #refactor     idle       backlog
                      |
                      |
  [a] add project     |
  [d] delete project  |
  [enter] open board  |
---------------------------------------------------------------
 3 projects, 2 active tasks
```

Left pane: project list with ticket counts, `*` = current project.
Right pane: active tickets across all projects with phase + column.

Keys: `j/k` navigate projects, `enter` opens project board, `a` add project, `d` delete, `tab` switch panes, `1-9` jump to active ticket.

### 2. Project Board (kanban)

```
my-app                                   backlog(3) impl(2) review(1) done(5)
---------------------------------------------------------------------------
 Backlog          | Implementation     | Review           | Completed
                  |                    |                  |
 > #auth-fix      |   #add-tests      |   #refactor      |   #cleanup
   med             |   high [running]  |   med [awaiting] |   low
                  |                    |                  |
   #new-feature   |   #fix-bug        |                  |   #docs
   low            |   crit [error]    |                  |   med
                  |                    |                  |
   #perf-opt      |                    |                  |   ...3 more
   med            |                    |                  |
---------------------------------------------------------------------------
 j/k select  h/l column  enter detail  s start  x stop  m move  a add  esc back
```

Columns sized proportionally. Cards show title, priority, phase badge if active.
Visual indicators: `[running]` green, `[awaiting]` blue pulsing, `[error]` red.

Keys: `h/l` move between columns, `j/k` move within column, `enter` ticket detail, `s` start supervisor, `x` stop, `m` move to column (shows picker), `a` add ticket, `esc` back to dashboard.

### 3. Ticket Detail

```
#auth-fix - Fix authentication token refresh          [Implementation] med
---------------------------------------------------------------------------
 [C]hat  [O]verview  [P]R  [A]rtifacts  [D]iff

 Phase: running                                          tokens: 12.4k
 Session: abc123                                         run: def456

 --- Chat History (last 10) ---
 [1] user: Fix the auth token refresh logic in src/auth.ts
 [2] assistant: I'll start by reading the current implementation...
 [3] assistant: Found the issue - the refresh token is not being...
 [4] user: Also make sure to handle the case where...
 [5] assistant: Good point. I'll add error handling for expired...

 > Type message (enter send, esc back) ____________________

---------------------------------------------------------------------------
 s start  x stop  r retry  n new session  esc back
```

Tab-switchable views:
- **Chat**: supervisor message history + input field for sending messages
- **Overview**: description (editable), blocked-by list, session info, token usage
- **PR**: PR title + body from artifacts (rendered markdown → plain text)
- **Artifacts**: file tree browser with text preview
- **Diff**: git status + file list with change indicators

### 4. SSH / Shell

When user presses `S` (capital) on a ticket or types `omni work ssh`:
- TUI suspends (Ink unmounts, restores terminal)
- `docker exec -it <container> /bin/bash` runs
- User works in the container shell
- On exit, TUI resumes where they left off

This is the killer feature for terminal users — seamless transition between managing work and doing work.

---

## Implementation Phases

### Phase 1: RPC Server + Dashboard

**Goal**: Replace spawnSync with proper RPC, show live state.

1. Build JSON-RPC server in `tui_server.py` wrapping `ProjectsRuntime`
   - Communicate over stdin/stdout (parent=server, child=TUI node process)
   - Or Unix socket at `/tmp/omni-tui-{pid}.sock`
2. Build RPC client in TypeScript (`src/rpc.ts`)
3. Rewrite `App.tsx` to use RPC instead of spawnSync + file reads
4. Implement Dashboard view (project list + active work pane)

### Phase 2: Kanban Board

**Goal**: Full interactive board with live phase indicators.

1. Multi-column board layout component
2. Ticket cards with phase badges
3. Move tickets (column picker on `m`)
4. Add/delete tickets
5. Start/stop supervisor from board (`s`/`x`)
6. Subscribe to phase + task-status events for live updates

### Phase 3: Ticket Detail + Chat

**Goal**: See what the agent is doing, interact with it.

1. Tab component (Chat/Overview/PR/Artifacts/Diff)
2. Chat tab: scrollable message history + input field
3. `supervisor.send` for user messages
4. Live message streaming via events
5. Overview tab: editable description, dependencies, token usage

### Phase 4: SSH Integration

**Goal**: Seamless shell access to containers.

1. `S` key suspends TUI, runs `docker exec -it`
2. On shell exit, TUI resumes
3. `sandbox.start` auto-provisions if not running
4. Show container URLs (code-server, VNC) in ticket detail

### Phase 5: Artifacts + PR + Diff

**Goal**: Inspect agent output without leaving the terminal.

1. Artifact file tree with expand/collapse
2. Text file preview (syntax highlighted if possible via cli-highlight or similar)
3. PR tab: title + body display
4. Diff tab: git status + unified diff display

---

## Key UX Principles

1. **No flags to remember.** Everything is navigable. The user launches `omni projects` and drives everything with keys.
2. **Vim-style navigation.** j/k/h/l everywhere. Enter to drill in, Esc to go back.
3. **Live state.** Phases, messages, task status update in real-time via RPC events.
4. **SSH is first-class.** One keypress to drop into a container. This is the bridge between "managing work" and "doing work."
5. **Progressive disclosure.** Dashboard -> Board -> Ticket Detail. Each level shows more.
6. **CLI still works.** Power users can still `omni work ssh --ticket auth-fix` directly. The TUI and CLI share the same runtime.

---

## Files to Create/Modify

### New files
- `omni_code/tui_server.py` — JSON-RPC server wrapping ProjectsRuntime
- `omni_code/tui/src/rpc.ts` — RPC client (connects to server)
- `omni_code/tui/src/views/Dashboard.tsx` — project list + active work
- `omni_code/tui/src/views/Board.tsx` — kanban board
- `omni_code/tui/src/views/TicketDetail.tsx` — ticket detail with tabs
- `omni_code/tui/src/views/ChatTab.tsx` — supervisor chat
- `omni_code/tui/src/views/OverviewTab.tsx` — ticket overview
- `omni_code/tui/src/views/ArtifactsTab.tsx` — file tree + preview
- `omni_code/tui/src/views/PRTab.tsx` — PR display
- `omni_code/tui/src/views/DiffTab.tsx` — git diff display
- `omni_code/tui/src/components/` — shared components (StatusBadge, KeyHint, Input, etc.)
- `omni_code/tui/src/hooks/useRpc.ts` — React hook for RPC calls + event subscriptions

### Modified files
- `omni_code/tui_launcher.py` — start RPC server, pass connection to TUI
- `omni_code/tui/src/App.tsx` — router between views, RPC context provider
- `omni_code/tui/src/index.tsx` — accept RPC connection args
- `omni_code/tui/package.json` — add ink-text-input, ink-spinner deps

### Keep as-is
- `omni_code/projects_cli.py` — CLI plumbing (TUI calls runtime directly via RPC)
- `omni_code/work_cli.py` — CLI plumbing (SSH command still useful standalone)
- `omni_code/projects_runtime.py` — core runtime (wrapped by RPC server)

---

## Open Questions

1. **RPC transport**: stdin/stdout pipe vs Unix socket? Pipe is simpler (no cleanup), socket allows multiple clients. Leaning pipe for v1.
2. **Ink vs raw ANSI**: Ink is good for structured layouts but limited for full-screen apps. If we hit walls, consider switching to blessed-contrib or building a custom renderer. But try Ink first.
3. **Syntax highlighting in artifacts**: Worth the dependency? Could use `cli-highlight` npm package. Skip for v1, add in Phase 5.
4. **Live log tailing**: Should the chat tab show a live tail of the agent's output, or just message-level updates? Message-level is simpler and matches the desktop app.
