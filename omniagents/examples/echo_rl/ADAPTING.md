# Adapting this template to your own domain

The RL machinery here is domain-agnostic. To train an agent for a *different*
task family (SQL, web automation, data cleaning, notebook editing, …), you only
touch three things — the agent, the tool(s), and the task corpus. Everything
else (sandbox lifecycle, ECHO loss, verifier reward, training loop, plots) is
reused unchanged.

## The 3 things you change

1. **The tool(s)** — `tools/your_tools.py`. Write each tool against the
   `omniagents.workspace` seam so it runs in the sandbox in both eval and
   training:
   ```python
   @function_tool
   async def run_sql(query: str) -> str:
       """Execute a SQL query and return the result.

       Args:
           query: The SQL to run.
       """
       r = await ws.exec(f"psql -c {shlex.quote(query)}")
       return r.stdout
   ```
   Keep a `run_*` helper so the env method can reuse the body.

2. **The environment method(s)** — `environments/your_env.py`. Subclass
   `SandboxTrainingEnvironment` and add one public method per tool (TRL turns
   each public method into a tool). Each method binds the workspace and calls
   the shared tool body:
   ```python
   class SqlSandboxEnvironment(SandboxTrainingEnvironment):
       def run_sql(self, query: str) -> str:
           """Execute a SQL query and return the result.

           Args:
               query: The SQL to run.
           """
           return self._run(self._arun(query))
       async def _arun(self, query):
           self._bind_workspace()
           return await run_sql_body(query)
   ```
   Public method names + Google-style docstrings (with per-arg descriptions)
   are required — TRL builds the tool schema from them.

3. **The task corpus** — `tasks/builtin/*.yaml`. Each task is a `TaskSpec`:
   a Docker `image` (or a `dockerfile`), `setup` commands (fixtures), a `prompt`,
   and a `verifier` shell command (exit code 0 = success). Bring real data via
   `tasks/loaders.py` (HF dataset or parquet) using the same schema. Two extra
   fields control data organization:
   - `split: train|val` — held out at load time (`load_builtin(split="val")`).
   - `world_model_only: true` — verifier-free; feeds ECHO only, no policy
     gradient. Great for OOD tasks you can't score. (No-op under the baseline.)

Then update `agents/<name>/agent.yml` (model + instructions + tool names) and
`training/config.yaml` (`default_image`, dataset source). Run
`experiments/run.py`.

## Design rules that make it generalize

- **Tools use `omniagents.workspace.*`, never raw Docker.** That's the seam that
  lets the identical tool run under the SDK Runner (eval) and inside the training
  sandbox.
- **Reward is a verifier in the container** (a shell command), so success is
  defined the same way for training and eval. Prefer objective, exit-code checks.
- **Keep domain code in the example, reusable code in the framework.** If you
  find yourself editing `omniagents/core/training/`, ask whether the change is
  truly domain-agnostic (per-rollout lifecycle, masks, loss) before adding it.
- **ECHO is just `world_model_coeff > 0`.** It works for any tool whose outputs
  enter the context — the env-output tokens become the world-model target.

## Optional: held-out agent eval (the eval twin)

For Runner-driven eval on held-out tasks, add an `EvalEnvironment` that mirrors
`TerminalSandboxEnvironment`: in `setup` start the sandbox + run task `setup`; in
`prepare_spec` bind the session so the agent's tools exec in the container; in
`teardown` run the `verifier` and record `verifier_passed` into the context.
Then reference `evaluations/measures.py::terminal_task_solved` from an
`evaluation.yml`. This reuses the same tasks/tools/verifier as training.
