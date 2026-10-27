# QuickJS tool orchestration

## Decision

Run JavaScript in a fresh, restricted QuickJS process alongside the agent service.
Keep Python as the authoritative tool executor. The worker is independent of the
project sandbox (host, Docker, WASM, or remote). No Node installation or executable
on the user's PATH is required. Use the pinned QuickJS-NG Python wheel in an
isolated Python subprocess; never expose Python callbacks, std/os, imports,
filesystem, network, environment, or credentials to JavaScript.

Process separation provides crash isolation, not an OS security boundary against
native engine vulnerabilities. Do not describe it as equivalent to the workspace
sandbox. Ship platform wheels, not a compiler requirement. Upstream currently
covers Linux x64/ARM64, macOS ARM64 and Windows x64; Intel macOS uses our bundled native
engine wheel built from the same pinned sources. Test platform installation in CI.

## Contract

- `exec_javascript(code, yield_time_ms=1000)` starts a fresh runtime with top-level
  await. A running response contains a cell ID; `wait_javascript` retrieves emitted
  output and completion, and `cancel_javascript` stops pending work.
- `tools.list()` discovers allowed function tool names, descriptions and schemas.
- `tools.call(name, arguments)` returns the original result before model-preview
  truncation, with a stable result ID, status and completeness metadata.
- `session.results.list/get/read` finds and retrieves automatically saved results.
  Retention is automatic for all tool results, including small results: no explicit
  store step. IDs and access are bound to the current product/agent/session.
- `text(value)` explicitly emits bounded model-visible output. Intermediate values
  do not enter the model context. Media must retain its SDK-native representation.
- Transcript and descendant inspection must use canonical conversation records
  and their authorization checks; never trust session IDs supplied by JavaScript.
- Calls use the same approval predicates, guardian/client review, durable dispatch
  claims, workspace context and cancellation ownership as direct calls. No nested
  orchestration recursion. Only locally dispatched FunctionTools are callable;
  provider-executed tools remain outside this contract.
- A denied call performs no side effect. A completed call is retained even if later
  JavaScript fails. Never automatically replay an unfinished side effect after a
  crash. Unawaited calls are accounted for, not silently discarded.

## Limits and lifecycle

Version IPC frames and cap source, frames, heap, stack, emitted output, call count
and concurrency. Count JavaScript CPU separately from approval/tool wait time.
Use an external watchdog and clean up worker processes on cancellation. Large
results remain durable and can be paged without forcing them through a bounded
IPC frame. Report capture/transport limits explicitly; 'full' means the value
returned by the tool, not bytes already discarded by the underlying provider.

Fresh runtimes avoid pretending that JS stacks survive process restarts. Completed
results survive restart; interrupted computation does not. Existing `.txt` saved
output references and the desktop reader stay supported. Session deletion purges
retained results. Do not attempt to recover previously discarded output.

## Implementation sequence and acceptance

1. Prove the pinned worker, async promise bridge, limits and platform packaging.
2. Retain all original results with metadata and paginated discovery, separately
   from direct-tool and emitted-output budgets.
3. Wire nested dispatch through approval and durable execution boundaries; preserve
   tool UI events. Test allow, reject, cancellation and ownership loss.
4. Enable the capability for coding agents; keep other tools available. Document
   the API in the capability so an agent can discover schemas and filter results.
5. Verify large/minified output filtering, retrieval after restart, parallel calls,
   failure after completed calls, no host capabilities, and resource exhaustion.
6. Implement canonical subagent transcript inspection and yield/wait with durable
   identities and UI lifecycle tests. Do not substitute transient UI events for a
   durable transcript or claim replay of arbitrary JavaScript.

Launcher reuses the saved-output reader and canonical tool activity presentation.
Any new nested presentation requires permanent Playwright coverage. No new model
or provider settings. Release order: omniagents, omni-code, launcher. Do not enable
on a platform until wheel installation and worker smoke tests pass there.

## Verification record

Implemented in this checkout:

- Pinned QuickJS-NG 0.17.0.1 in an isolated worker with no Python callbacks or host
  APIs. Each invocation gets a fresh runtime and a minimal process environment.
- 100 KB source, 64 MiB JS heap, 512 KiB stack, 5 seconds cumulative worker CPU,
  2 MB IPC frames, 256 requests, 8 concurrent dispatches and 40 KB emitted text.
  Approval/tool waits do not consume the JS compute watchdog. Oversized returned
  values are retained but transported by reference; text reads page at 8,000 chars.
- All local FunctionTool results are automatically captured before previews, in
  the owning conversation's private result directory. A SQLite index preserves
  JSON structure and discovery metadata; compatible text artifacts serve paging,
  search and the existing desktop reader. SQLite and text files are session-scoped
  and purged with session derivatives. Failed retention is explicit in JS envelopes.
- Nested tools use the existing approval predicate and reviewer, durable claims,
  run and agent lifecycle hooks, ownership checks, audit events and UI events.
  Completed originals survive later JavaScript failure. Unknown/disabled tools and
  recursive orchestration are rejected. Pending claims are never replayed.
- `session.transcript.list({limit, cursor})` reads the current canonical conversation
  excluding reasoning blocks; it accepts no caller-supplied session identity.
- `javascript` capability enabled in omni-code's omni, worker and explorer agents.
  The explorer can only call its existing read-only tools.
- Launcher keeps its existing activity and saved-output UI. Permanent browser and
  Electron tests cover JavaScript selection and durable retrieval.

Completed lifecycle and transcript implementation:

- Live cells yield while tools or approvals are pending. Four cells per session may
  run concurrently. `wait_javascript` accepts a character offset for incremental
  text. `session.cells.list({after, limit})` discovers durable outcomes after restart.
  Run teardown cancels outstanding cells before finalizing the workspace diff.
- Cells belong to their originating run: another run cannot adopt a live cell.
  Completed output survives restart; interrupted JS stacks are never replayed.
  Pending side effects retain their normal durable execution claims.
- Agent-as-tool invocations receive canonical child conversation IDs before they
  dispatch, with durable nested tool claims and persisted messages/tool activities.
  `session.subagents.list({limit, cursor, parent_id})` traverses descendants and
  `session.subagents.transcript({id, limit, cursor})` reads canonical records.
  Background workers use durable references to their existing conversation shard.
  Reads verify ancestry and ownership, and exclude model reasoning.
- SDK input/output guardrails apply to nested calls. Output transformations run
  before retention so rejected/redacted raw values are not exposed by the result
  store. Tripwires abort the runtime and fence subsequent run operations; JavaScript
  cannot suppress them with try/catch.
- `image(imageUrlOrBlock)` and `file(fileBlock)` emit validated SDK-native blocks.
  Media is bounded separately (eight blocks, at most 1 MB each, subject to IPC frame
  limits) and preserved in durable cell outcomes.
- Intel macOS release wheels bundle the pinned engine with its licenses; installed
  users do not need a compiler. Both publishing workflows build that wheel before
  publishing alongside the ordinary wheel. CI tests Windows, Linux, Apple Silicon
  and Intel macOS installation/runtime smoke checks.

Stable boundaries:

- This is a capability-limited orchestration process, not a replacement for the
  workspace sandbox. Tool execution retains its existing host/Docker/WASM/remote
  routing; the JS process itself does not gain those environments' host APIs.
- No persistent JS variables or stack replay. All durable retrieval is through
  authorized result and conversation APIs, not a filesystem transcript mirror.
- Provider-executed tools remain outside local FunctionTool capture. Voice realtime
  keeps its existing direct path because it lacks this durable run lifecycle.
- Cross-platform CI is configured, but macOS and Windows jobs cannot be executed on
  this Linux workstation. No release or publication is part of this implementation.

Local validation:

- 70 focused OmniAgents tests pass for QuickJS, retention, approvals, durable
  dispatch, cancellation, guardrails, media, child transcripts and workspace context
  routing. All 16 worker tests and 16 Omni Code explorer/model contract tests pass.
- Targeted Python type checks, Black, launcher TypeScript and targeted ESLint pass.
- Server/browser production build passes. Built OmniAgents wheels include worker,
  supervisor, cells, dispatcher, subagent support, result store, tools and capability
  YAML. A forced native-engine Linux wheel also installs and runs its worker in a
  clean Python environment without the upstream QuickJS package.
- Four visual proofs pass across browser/server and Electron. The JavaScript
  story deletes the original source file, restarts the entire app, discovers the
  retained result without a supplied ID, and reads both that result and the
  canonical transcript. It also invokes a subagent with a real nested tool call,
  checks its durable completed execution claim, restarts again and retrieves that
  child transcript. The existing paged/searchable saved-output UI also passes
  after restart.
- Visual proof command from launcher:

  ```sh
  OMNI_CLI_PATH=/home/emm/Omni/Workspace/omni-code/.venv/bin/omni \
    VISUAL_PROOF=1 E2E_CHROMIUM_EXECUTABLE=/usr/bin/chromium \
    E2E_ELECTRON_X11=1 DISPLAY=:0 npx playwright test \
    tests/e2e/specs/javascript-output.spec.ts \
    tests/e2e/specs/retained-tool-output.spec.ts
  ```

  Report: `launcher/artifacts/playwright-proof-report/index.html`.
  Traces, video and screenshots: `launcher/artifacts/playwright-proof-results/`.
