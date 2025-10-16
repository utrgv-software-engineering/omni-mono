# Agent Optimization Discrepancy

## Problem
The OmniAgents Studio UI cannot launch agent optimization jobs even though the equivalent `omniagents agent optimize` CLI command succeeds. We must document and resolve the parameter/flow mismatch between CLI, backend API, and UI layers.

## Resolution Summary
- Enforced a minimum optimization budget of 6 across CLI parity, API validation, and UI form checks so GEPA can execute at least one reflective iteration.
- Added per-run GEPA logging (`gepa.log`) and surfaced the log path plus last reflection error through job status for easier debugging.
- Streamed GEPA progress (total evaluations and ETA) into job status, updating Studio’s progress bar and displaying time remaining.
- Aligned manager totals/completed counters with actual metric calls to eliminate misleading `X / 1` displays.
- UI now surfaces the log link, live progress, and ETA so users can gauge optimization runtime.

## Follow-up
- Monitor GEPA log output for any recurring reflection errors.
- Consider exposing runtime metrics (rollouts/sec) if GEPA adds them to progress callbacks.

## Relevant Code
- `omniagents/cli.py` — CLI command definition and call to `optimize_agent_from_project`.
- `omniagents/studio/server/api.py` — FastAPI handler `start_agent_optimization`, job machinery, and progress streaming.
- `omniagents/studio/server/web/src/features/agents/AgentsView.tsx` — UI form logic and progress display for optimization.
- `omniagents/studio/server/web/src/lib/api.ts` — REST client helper defining the fetch call for agent optimization.
