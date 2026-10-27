# OmniAgents Studio

A local web UI for OmniAgents projects: review every conversation the
project's agents had (from the conversation store, with run timing from traces
when tracing is on), see and edit what each agent runs with, chat with agents in
a Playground (the shared OmniAgents chat UI against the agent's real runtime),
and evaluate them: scenarios and dimensions, runs with their intervals and
comparisons, failure categories, measures, judges and optimization.

Studio only reads and writes what OmniAgents already keeps on disk. Traces are
recorded by `omniagents` itself when a project turns them on
(`tracing: {provider: studio}`; tracing is off by default, evaluations record
unless the manifest names a provider) into `~/.omniagents/traces/<project>.db`, so nothing has to be installed in an
agent's environment for its traces to show up here.

## Run

From the monorepo root, `uv sync --all-packages` installs Studio into the
workspace venv. Then, from an OmniAgents project directory:

```bash
omniagents-studio serve               # uses ./project.yml; http://127.0.0.1:7860
omniagents-studio serve --project my_project      # a trace project by name
omniagents-studio serve --project path/to/project.yml
omniagents-studio export traces.json --project my_project   # json, csv, parquet
omniagents-studio show --project my_project
```

`--token <secret>` requires a bearer token on the API. `--dev` runs the Vite
dev server from `web/` alongside the API (needs Node/npm).

## Develop

```bash
cd omniagents-studio
../.venv/bin/pytest -n auto      # Python tests
(cd .. && npm ci)                # the web app is an npm workspace of the monorepo
cd web && npm run lint && npx vitest run && npm run build
make assets                      # the bundle a wheel ships, in omniagents_studio/static
```

Layout: `omniagents_studio/api.py` (the app, traces and reviews),
`agent_runtimes.py` (each agent's real runtime, mounted at
`/api/agents/<key>/runtime`), `agents.py` (describing and editing agents),
`evaluation.py` (the project's evaluation, over `EvalWorkspace`), `jobs.py`,
`server.py` (serve/export), `cli.py`, `web/` (React sources). `docs/STUDIO.md`
describes each screen and what it is built on.
