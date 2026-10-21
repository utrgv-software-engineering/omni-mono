# Voice Overhaul Plan (OmniAgents)

## Goal
Add an alternate “voice mode” implementation based on the OpenAI Agents SDK voice pipeline (`agents.voice.VoicePipeline`) alongside the existing OmniAgents realtime voice implementation (`agents.realtime.*`). The Web UI should continue to use the same `/ws/realtime` JSON-RPC endpoint; the server chooses the underlying voice backend.

Primary outcome:
- Support **two** server-side voice backends:
  - **realtime** (current): `agents.realtime.RealtimeRunner` / OpenAI Realtime full-duplex session
  - **pipeline** (new): `agents.voice.VoicePipeline` (STT → normal streamed agent run → TTS)
- Keep **default behavior unchanged** (realtime), and enable pipeline via config.

Non-goals (initially):
- Replacing the Web UI voice UX.
- Removing/rewriting the existing realtime implementation.

## Current Architecture (Baseline)
### Web UI
- Voice feature gate/probe:
  - `omniagents/backends/web/ui/src/App.tsx` attempts `start_session()` then `stop_session()` against `ws(s)://<host>/ws/realtime` to decide `voiceEnabled`.
- Voice UI and streaming:
  - `omniagents/backends/web/ui/src/components/VoiceModal.tsx`
    - Captures mic audio (AudioWorklet fallback ScriptProcessor), resamples to 24kHz, PCM16, base64.
    - Sends JSON-RPC notifications `send_audio` to `/ws/realtime`.
    - Plays back audio from JSON-RPC notifications `realtime_event` of type `realtime_audio`.
- Realtime client implementation:
  - `omniagents/backends/web/ui/src/rpc/realtime.ts`

### Server
- FastAPI app:
  - `omniagents/backends/server/app.py`
    - Registers `/ws` (chat) via `AgentService`.
    - Registers `/ws/realtime` (voice) via `RealtimeService` if enabled.
- Current realtime JSON-RPC service:
  - `omniagents/rpc/realtime_service.py` (RPC methods: `start_session`, `send_audio`, `stop_session`, `interrupt`)
- Current realtime session bridge:
  - `omniagents/rpc/realtime_session.py` (builds `agents.realtime.RealtimeAgent` + `RealtimeRunner`, maps SDK events to OmniAgents RPC events)
- Spec support:
  - `omniagents/core/agents/specs.py` (`AgentSpec.realtime_mode`, `AgentSpec.realtime_settings`)
  - Project voice entrypoint support:
    - `omniagents/core/project/runtime.py` attaches `spec.voice_spec` based on `project.yml` `agents.voice_entrypoint`.

### Events
- OmniAgents realtime events:
  - `omniagents/rpc/realtime_events.py` defines `realtime_audio`, `realtime_response_start`, `realtime_audio_end`, etc.
- Web UI `VoiceModal` expects:
  - `realtime_response_start` (sets THINKING)
  - `realtime_audio` (audio chunks)
  - `realtime_audio_end` (release duplex hold)
  - `realtime_tool_start` / `realtime_tool_end` (overlay)

## Alternative SDK Voice Pipeline (Target)
Relevant Agents SDK modules (installed in this environment at):
- `/home/emm/.local/share/mise/installs/python/3.11.14/lib/python3.11/site-packages/agents/voice/`
  - `pipeline.py` (`VoicePipeline.run(AudioInput | StreamedAudioInput) -> StreamedAudioResult`)
  - `input.py` (`AudioInput`, `StreamedAudioInput.add_audio()`)
  - `events.py` (`VoiceStreamEventAudio`, `VoiceStreamEventLifecycle`, `VoiceStreamEventError`)
  - `result.py` (`StreamedAudioResult.stream()` yields voice events)
  - `workflow.py` (`SingleAgentVoiceWorkflow` uses `Runner.run_streamed()` under the hood)
  - `models/openai_stt.py` (streaming STT uses a hardcoded transcription WS URL)
  - `models/openai_tts.py` (TTS streams PCM bytes from Responses TTS)

Important behavior differences:
- Pipeline audio output is generated via **TTS** (not realtime model audio).
- Streaming STT uses a websocket to transcription intent; in current SDK it is hardcoded to:
  - `wss://api.openai.com/v1/realtime?intent=transcription` (`agents/voice/models/openai_stt.py`)
  - This may not honor Azure or custom base URLs without extra work.

## Design Decision
Expose a single OmniAgents `/ws/realtime` interface, but implement multiple server-side voice backends.

- Default backend: `realtime` (existing)
- Alternate backend: `pipeline` (new)

Backend selection should be driven by config (YAML / project defaults) and stored on `AgentSpec`.

---

## Phase 0 — Bootstrap & Guardrails
### 0.1 Confirm baseline behavior
- [x] Identify existing voice codepaths (UI + server).
- [ ] (Optional) Run the web backend locally and confirm voice works in current realtime mode.
  - `omniagents ... --backend web` (use existing CLI invocation)

### 0.2 Add this plan to repo
- [x] Add `VOICE_OVERHAUL_PLAN.md` (this file).

---

## Phase 1 — Add Config Surface for Voice Backend Selection
### 1.1 Extend `AgentSpec`
- [x] Add a new field to `omniagents/core/agents/specs.py`:
  - `voice_backend: str = "realtime"` (allowed: `"realtime"`, `"pipeline"`)

### 1.2 Parse from agent YAML
- [x] Update `omniagents/core/config/loader.py` to read `voice_backend` from YAML.
- [x] Validate values; fallback to `"realtime"`.

### 1.3 Ensure project voice spec inherits/controls voice backend
- [x] Confirm `omniagents/core/project/runtime.py` sets `spec.voice_spec` (already does).
- [x] Decide precedence:
  - If `spec.voice_spec` exists, use `spec.voice_spec.voice_backend` for `/ws/realtime`.
  - Else, use `spec.voice_backend`.

Acceptance criteria:
- A YAML agent can specify `voice_backend: pipeline`.

---

## Phase 2 — Implement Pipeline Voice Backend (Server)
### 2.1 Add a new JSON-RPC service
- [x] Create `omniagents/rpc/voice_pipeline_service.py`.
- [x] Provide the same JSON-RPC methods the UI already uses:
  - `start_session(session_id?: str) -> { session_id, run_id }`
  - `send_audio(session_id: str, audio_base64: str, commit: bool = False) -> bool`
  - `stop_session(session_id: str) -> bool`
  - `interrupt(session_id: str) -> bool`

### 2.2 Session object: `VoicePipelineSession`
- [x] Add a helper class in the new module (or a sibling module) to manage per-session state:
  - Build a single SDK `Agent` from the OmniAgents `AgentSpec` (use existing builder logic):
    - `omniagents/core/agents/builder.py::_default_build_agent`
  - Construct the SDK pipeline:
    - `from agents.voice import VoicePipeline`
    - `from agents.voice import StreamedAudioInput`
    - `from agents.voice.workflow import SingleAgentVoiceWorkflow`
  - Maintain a persistent workflow instance across turns so that:
    - handoffs are preserved
    - conversation history persists (`SingleAgentVoiceWorkflow` keeps `_input_history`)

#### 2.2.1 Respect OmniAgents model config
- [x] Call `spec.resolve_default_model_config()` (if present) during `start_session`.
- [x] Pass `model_config` into `omniagents/core/agents/builder.py::_default_build_agent(..., session=...)` so the agent model/settings follow `model_config`.
- [x] Create a `RunConfig(model_provider=...)` for `Runner.run_streamed()` similarly to `omniagents/core/agents/service.py`.

### 2.3 Audio handling contract
- [x] Define what the UI sends vs what pipeline expects:
  - UI sends base64 PCM16 bytes at 24kHz, mono (from `VoiceModal.tsx`).
  - Pipeline’s `StreamedAudioInput.add_audio()` expects `numpy` arrays of `np.int16` or `np.float32`.
- [x] Convert base64 PCM16 → `np.frombuffer(decoded, dtype=np.int16)`.

### 2.4 Driving the pipeline
- [x] On `start_session`:
  - Create `StreamedAudioInput()`.
  - `pipeline.run(streamed_input)` returns `StreamedAudioResult`.
  - Spawn a background task to iterate `async for ev in result.stream(): ...`.

### 2.5 Event bridging to existing Web UI
Goal: do not modify the UI initially.

- [x] Map pipeline events to OmniAgents realtime notifications (via `self.channel.async_call("realtime_event", ...)` the same way `RealtimeService` does):
  - `VoiceStreamEventLifecycle(event="turn_started")` → emit `{ type: "realtime_response_start", ... }`
  - `VoiceStreamEventAudio(data=np.int16 array)` → emit `{ type: "realtime_audio", audio_base64: <base64 PCM16> }`
  - `VoiceStreamEventLifecycle(event="turn_ended")` → emit `{ type: "realtime_audio_end", ... }`
  - `VoiceStreamEventLifecycle(event="session_ended")` → optionally emit a terminal event (or just stop)
  - `VoiceStreamEventError` → emit `{ type: "realtime_error", ... }`

Note:
- This mapping is implemented in OmniAgents today for realtime sessions via:
  - `omniagents/rpc/realtime_service.py::_stream_session_events`
  - `omniagents/rpc/realtime_events.py`

Implementation detail:
- For compatibility with existing UI, re-use existing event dict shape from `omniagents/rpc/realtime_events.py`.
  - For the pipeline backend, it is acceptable to synthesize only the subset the UI uses.

### 2.6 Commit / turn semantics
- [ ] Decide how to interpret the UI’s `commit` flag.
  - Current UI never sets `commit=true` (as of `VoiceModal.tsx`).
  - Pipeline STT turn detection will segment automatically.
- [ ] Still accept `commit=true` and treat it as “end of stream for current turn” if desired (optional).

### 2.7 Dependencies
- [x] Add an optional dependency group, e.g. `omniagents[voice]`, that installs:
  - `openai-agents[voice]==0.4.2` (or `openai-agents[realtime,voice]==0.4.2`)
  - Ensure `numpy` and `websockets` are available.
- [x] Ensure runtime error is clean if `agents.voice` is unavailable.

Acceptance criteria:
- With `voice_backend: pipeline`, `/ws/realtime` accepts a session and streams back `realtime_audio` events.

---

## Phase 3 — Wire Backend Selection Into FastAPI
### 3.1 Modify server app routing
- [x] Update `omniagents/backends/server/app.py`:
  - When enabling `/ws/realtime`, choose between:
    - `omniagents/rpc/realtime_service.py::RealtimeService`
    - `omniagents/rpc/voice_pipeline_service.py::VoicePipelineService` (new)

### 3.2 Ensure spec gating is correct
- [x] Current logic uses `spec.realtime_mode` or `spec.voice_spec`.
- [x] Update logic so pipeline backend does not require `realtime_mode=True`.

Acceptance criteria:
- UI voice probe in `App.tsx` works for both backends.

---

## Phase 4 — Remove/Adjust Realtime Defaults For Non-Realtime Backend
### 4.1 Avoid forcing realtime defaults when pipeline is selected
- [x] In `omniagents/backends/web/__init__.py` (and `omniagents/core/runtime/runner.py` notebook path), the code currently forces:
  - `realtime_mode=True`, `realtime_settings`, etc.
- [x] Change this so it only runs when the selected `voice_backend == "realtime"`.

Acceptance criteria:
- Pipeline backend can run without `realtime_mode=True`.

---

## Phase 5 — Tests & Debugging Hooks
### 5.1 Unit tests (Python)
- [x] Add minimal tests to validate routing + service selection without needing actual OpenAI calls.
  - Suggested approach:
    - Mock `agents.voice.VoicePipeline` / `StreamedAudioResult.stream()` to yield deterministic events.
    - Assert `VoicePipelineService` emits correct `realtime_event` payloads.
- [ ] Candidate test locations:
  - `tests/` (search existing patterns first)

### 5.2 Manual test checklist
- [ ] Start server with `voice_backend: pipeline` and confirm:
  - App voice probe succeeds.
  - Voice modal receives `realtime_audio` and plays audio.
  - Multiple turns work (history preserved).

---

## Phase 6 — Documentation & Examples
### 6.1 Add docs
- [ ] Document configuration in README or docs:
  - `voice_backend: pipeline`
  - install instructions for pipeline dependencies

### 6.2 Example agent
- [ ] Add an example agent YAML showcasing:
  - handoffs + tools
  - `voice_backend: pipeline`

---

## Phase 7 (Deferred) — Azure / Extra Endpoint Support for Pipeline STT
Pipeline STT now respects `model_config.base_url` for openai/openai-compatible providers via a patched transcription session.

Remaining work (only if needed):
- [ ] Azure-specific header/URL rules (api-key header, deployments, api-version).
- [ ] Support additional per-provider auth schemes.

---

## Resume Protocol (for a new agent)
1) Repo sanity:
- Run `git status -sb` and confirm tree state.

2) Read this plan:
- Open `VOICE_OVERHAUL_PLAN.md`.

3) Rebuild context quickly by re-reading these files:
- Web UI:
  - `omniagents/backends/web/ui/src/App.tsx`
  - `omniagents/backends/web/ui/src/components/VoiceModal.tsx`
  - `omniagents/backends/web/ui/src/rpc/realtime.ts`
- Server routing:
  - `omniagents/backends/server/app.py`
- Existing realtime implementation:
  - `omniagents/rpc/realtime_service.py`
  - `omniagents/rpc/realtime_session.py`
  - `omniagents/rpc/realtime_events.py`
- Spec/config:
  - `omniagents/core/agents/specs.py`
  - `omniagents/core/config/loader.py`
  - `omniagents/core/project/runtime.py`

4) Agents SDK references (for pipeline backend):
- `agents/voice/pipeline.py`
- `agents/voice/input.py`
- `agents/voice/events.py`
- `agents/voice/result.py`
- `agents/voice/workflow.py`
- `agents/voice/models/openai_stt.py`

5) Start implementing from Phase 1.

---

## Progress Log
- 2026-02-04: Baseline investigation complete; plan written.
- 2026-02-04: Phase 1.1–1.2 implemented (`voice_backend` in spec + YAML loader).
- 2026-02-04: Phase 2 + Phase 3 implemented (pipeline service + routing).
- 2026-02-04: Phase 4 implemented (skip realtime defaults for pipeline).
- 2026-02-04: Pipeline STT uses model_config base_url (openai-compatible).
