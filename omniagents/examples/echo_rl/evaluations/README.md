# Evaluation

There are two complementary ways to measure the agent here.

## 1. Verifier pass-rate during training (primary, built-in)

The per-task in-container **verifier** (exit code 0 = solved) is the GRPO reward,
so its mean is logged every step as `rewards/sandbox_verifier/mean`. That *is*
the pass-rate — `experiments/analyze.ipynb` plots it for the GRPO baseline vs
GRPO+ECHO, reproducing the paper's headline view. No extra eval run needed.

To evaluate on **held-out** tasks, train on a subset and reserve the rest:

```python
from tasks.loaders import load_builtin
train_tasks = load_builtin(["file-ops", "data-processing"])   # train split
heldout     = load_builtin(["dev-tooling", "system-ops"])     # eval split
```

## 2. Held-out pass-rate eval (`experiments/evaluate.py`)

Runs the policy on the **val split** in the sandbox and scores it with the
verifier — echo-rl's val protocol (held-out, N rollouts, temp 0.6):

```bash
# base model:
python experiments/evaluate.py --model Qwen/Qwen2.5-0.5B-Instruct --label base
# a trained checkpoint (train.py saves to <output_dir>/model):
python experiments/evaluate.py --model results/echo/model --label echo
```

It reuses `TerminalSandboxEnvironment` (the same `bash` tool + verifier as
training) and TRL's response parser to drive a multi-turn rollout, then reports
**pass-rate** (mean over rollouts) and **pass@1** (tasks solved ≥ once), saving
`results/eval/<label>.json`. Verifier-free (`world_model_only`) tasks are skipped
(nothing to score). Compare `base` vs `echo` vs `grpo_baseline` to see held-out
gains.

## 3. Agent-level eval with `terminal_task_solved` (Runner-driven, optional)

`measures.py` provides `terminal_task_solved`, a verifier-based `@measure` for
evaluating a trained agent *conversationally* via the omniagents Runner + an
`EvalEnvironment` twin. This is the YAML/CLI-native path; see `../ADAPTING.md`.
