# OmniAgents Studio

Studio is a local web UI over an OmniAgents project. It owns no evaluation or
agent logic of its own: every screen goes through what `omniagents` itself
provides, so what Studio shows and writes is what the CLI, the notebook API and
a run see.

| Screen | Built on |
|---|---|
| Agents: what an agent runs with | each agent's real runtime (`AgentService.describe_agent`) |
| Agents: editing | `omniagents.core.project.agent_files` |
| Playground | each agent's real runtime, through the shared chat UI |
| Logs | the canonical conversation store, through each agent's runtime |
| Evaluation | `omniagents.core.eval.workspace.EvalWorkspace` |

## Server

- `omniagents_studio/api.py`: the app, authentication, projects, traces and
  reviews (judgments, notes and the failure categories a reviewer saw, kept
  with the traces in `~/.omniagents/traces/<project>.db`).
- `agent_runtimes.py`: for every project agent, the server `omniagents run`
  starts (`build_app`), mounted at `/api/agents/<key>/runtime` with the project
  directory as its workspace. Editing an agent reloads the project's agents; an
  agent that does not load is listed with the reason so it can be fixed.
- `agents.py`: `/api/agents`: list, describe, the editor's options (the
  project's capabilities, tools, guardrails and agents), read the files, edit
  settings and instructions, replace `agent.yml`, create, delete.
- `evaluation.py`: `/api/evaluation`: one endpoint per workspace call.
  Refusals come back as 422 (or 404) with the reason.
- `jobs.py`: long work (running scenarios, generating, annotating, optimizing a
  judge or an agent) is a job with progress and a log tail, followed over
  `/api/evaluation/jobs/<id>/ws` or by asking for it.

The web app (`web/`) is an npm workspace of the monorepo using
`@omniagents/ui` and `@omniagents/chat-ui`. `omniagents-studio serve --dev` runs
the Vite dev server beside the API; otherwise Studio serves a checkout's
`web/dist`, or the bundle a wheel ships in `omniagents_studio/static`
(`make assets`, never committed).

## Agents

**What it runs with** is the built agent: the model after the resolver, the
tools a run carries (capability tools included) with how each is approved,
guardrails, the structured output schema, subagents, capabilities, MCP servers,
skills, commands, and the instructions rendered for the workspace.

**Edit** changes the agent's files. The form covers label, description, model,
turn limit, instructions (saved where they live: the instructions file, or
inline), capabilities, tools, approvals, guardrails, model settings and
structured output; the `agent.yml` tab edits the whole file. Saving changes only
what was edited (comments and layout stay), and is refused, with each problem,
when the agent would then not load with everything it names: an unknown tool,
capability, guardrail or agent, a wrong `name`, a template that does not parse.

## Logs

Every conversation any client had (desktop, web UI, TUI, Playground, evaluation
runs), from the conversation store. **Review** shows a conversation with the
shared chat UI beside the review panel. A review of an evaluation answer (a
conversation of a saved run) is written to the evaluations directory's
`reviews/<run id>.jsonl` with the answer it judges, and is committed: judge
datasets are built from these. A review of any other conversation (Playground,
desktop, web) stays in this machine's trace database. **Timing** shows the
run's traces when tracing was on. The **Sample** filter lists unreviewed
evaluation answers the review assistant picked to read next (cluster
representatives and random picks; "Draw a sample" draws a new one), and the
**Suggested** filter the answers it thinks may show a failure mode, which the
review panel offers to accept or dismiss.

## Evaluation

The agent picker in the header chooses what runs are made against.

- **Scenarios**: the project's scenario files in `scenarios/` (what the suite
  runs) and the generated sets in `generated/`, edited in place. Run a set;
  promote a generated scenario into `scenarios/promoted.yml`. A scenario's
  failure categories say what it is written to probe; what reviewers saw is in
  the reviews. **Dimensions and generation** drafts combinations of dimension
  values for a new set, which you review (remove, add) before generating
  scenarios from exactly those; queries rated unrealistic are set aside in
  `filtered.yml`. It also edits the dimensions synthetic scenarios are drawn
  from.
- **Runs**: run the suite (a selection, a holdout split, unmeasured scenarios,
  a turn limit), make the train/dev/test holdout, and read saved runs: each with
  its pass rate and 95% interval, answered rate, metrics with their slices, the
  manifest it ran with, and every scenario and trial with its measures and a
  link to its conversation. Two runs are compared when they used the same
  scenarios the same number of times: what changed in the manifest, the change
  in pass rate, and the scenarios whose outcome changed. A run can be kept as
  a named baseline (`baselines/<name>/`, committed), the evidence for a number
  someone reports; runs themselves stay on this machine.
- **Categories and measures**: failure categories with the measures that
  catch each and how many scenarios and reviews use it; measures, built in or
  the project's `measures.py`, with a stub written for a new one. **Failure
  modes from review notes** groups reviewers' notes into failure modes
  (existing categories or proposed new ones) to accept or dismiss; **Find
  more** on a category reads every evaluation answer for it and suggests the
  ones that may show it.
- **Judges**: each judge's model, what it reads, the category it decides, its
  instructions and template, and whether it is validated as it is now (its
  TPR and TNR on its dataset's test split, Pass positive); judge datasets,
  built per category from every reviewed answer and balanced by label, with
  each answer's Pass/Fail label, the reply the judge reads and a warning when
  a label is too scarce; choosing a judge's few-shot examples from its train
  split (critiqued by the reviewer's notes), with a warning when its model is
  an alias rather than a dated snapshot; optimizing a judge on a dataset. A run's detail shows
  each judge's raw pass rate and, for a validated judge, the rate corrected
  for its errors.
- **Optimize**: improving an agent's instructions against a judge dataset; the
  best instructions are saved into the agent's files.

All of it lives in the project's evaluations directory, laid out as
`omniagents/core/eval/layout.py` describes. Committed: what people write
(`evaluation.yml`, `measures.py`, `judges/<key>/`, `scenarios/`) and what is
expensive or impossible to make again (`reviews/`, `generated/`, `holdout/`,
`judge-datasets/<category>/`, `baselines/`). Local: `runs/` and
`optimizations/`. Reviews and generated sets include agents' answers, so
evaluation inputs must be synthetic. A project laid out the old way is refused
with what to move where.
