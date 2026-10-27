# Persistent memory

Declare `memory` in `project.yml` for every agent, or in an `agent.yml` to
replace those defaults. `memory: false` disables it for an agent.

```yaml
memory:
  url: ${MEMORY_MCP_URL}
  token_env: MEMORY_MCP_TOKEN
  stores:
    team:
      access: read
      instructions: Team conventions and repository behavior.
    agent:
      access: write
      instructions: Lessons for the next run of this role.
  extraction:
    model: default
    triggers: [run_end]
```

The workplace's app environment supplies the URL, token and agent-store
prefix. The `agent` alias resolves to `${MEMORY_AGENT_STORE}/<agent-name>`.
Explicit store IDs are also accepted. Agent declarations restrict its
memory tool; the workplace independently enforces permissions for the app
or person whose token is used. Set team write access only for roles that
should maintain shared conventions.

A store with `access: read, forget: true` permits reading and retiring
memories without allowing new entries, edits, or supersession. The factory
improver uses this for other roles' stores. This narrows the agent's tool
permissions; the workplace token must still have write access to the store.
It does not bypass the improver's instruction to verify the promoting PR
merged before forgetting the lesson.

Core memories are loaded when an agent is built for a session run, appended
after instruction templates are rendered, and ordered consistently. Their
prompt text excludes version counters and timestamps. Each turn searches
the incoming text and appends relevant results under `## Memory`. An
unavailable memory service is logged without preventing the run.

The `memory` tool supports `search`, `get`, `core`, `remember`, `update`,
`supersede`, and `forget`. Pass the operation and a JSON object in
`arguments_json`. The optional `memory` capability supplies additional
usage guidance; declaring `memory:` enables the runtime integration.

Omit `extraction` to use hand-written memories without background model
calls. When enabled, extraction uses its own model call and is scheduled
outside the foreground run. `model: default` resolves through the project's
model catalog and provider, including the ChatGPT subscription provider;
an explicit model can select a smaller extractor. Failures are logged.
Extraction is background work, not a durable job queue: process shutdown
can cancel unfinished extraction.

Factory `run_end` extraction runs after grading, including failed runs and
child-agent transcripts. Each write carries source run/session/automation
metadata and grade evidence. Duplicate active lessons are skipped and
contradictions can supersede older memories. Transcript input excludes
inline file/image data and is bounded to 100,000 characters, with individual
strings capped at 8000 characters.

For a personal agent that sleeps and wakes, configure:

```yaml
memory:
  url: ${MEMORY_MCP_URL}
  token_env: MEMORY_MCP_TOKEN
  stores:
    personal: {access: write}
    team: {access: read}
  extraction:
    model: default
    triggers: [idle, compaction, daily]
    idle_seconds: 60
```

Set `MEMORY_PERSONAL_STORE` to the personal store ID shown in the Memory tab
and use that person's Memory token. Idle extraction is cancelled when a new
run starts before the idle delay. Native and checkpoint compaction schedule
extraction of the context being compacted. Daily extraction runs at UTC day
boundaries while the session's process remains alive, writes logs and
reviews core preferences. Note expiry is enforced during retrieval even
when no agent is running.

The factory improver searches across role stores and days. Repeated lessons
can become code changes through its normal PR workflow. It keeps the memory
until it verifies the PR merged, then records the PR URL as the reason for
forgetting it. This policy lives in the improver's instructions; opening a
PR does not automatically delete memories.
