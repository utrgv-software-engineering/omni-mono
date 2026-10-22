# OmniAgents Studio

A local web UI for OmniAgents projects: browse the traces OmniAgents records,
review and annotate conversations, try agents in a Playground, and run
evaluation workflows (dimensions, scenario sets, measures, judges, suites,
optimization).

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
cd web && npm ci && npm run lint && npx vitest run && npm run build
make assets                      # rebuild omniagents_studio/static from web/
```

Layout: `omniagents_studio/api.py` (FastAPI app), `server.py` (serve/export),
`cli.py`, `static/` (built UI, committed), `web/` (React sources), `docs/`
(design notes).
