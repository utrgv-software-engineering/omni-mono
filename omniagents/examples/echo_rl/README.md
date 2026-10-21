# echo_rl — train a terminal agent with GRPO + ECHO (a reusable RL template)

A complete, runnable reference for training tool-using agents with reinforcement
learning in **omniagents**, ported from [ECHO: Terminal Agents Learn World
Models for Free](https://github.com/microsoft/echo-rl). A terminal agent drives
a `bash` tool inside a **per-rollout Docker sandbox**; reward comes from an
**in-container verifier**; and the **ECHO** auxiliary loss trains the policy to
predict the terminal output its own actions cause — turning every rollout's
environment feedback into supervision, for free.

It's built to be a **template for similar experiments in any domain** (SQL, web,
data-cleaning, …): only the agent, the tool(s), and the task corpus are
domain-specific — see [`ADAPTING.md`](ADAPTING.md).

## Why this is a good reference

- **The full experiment loop**: tasks → training (baseline vs ECHO) → eval →
  reproduction plots.
- **One mechanism, two uses**: the same `bash` tool and sandbox power both
  training (TRL-driven) and eval (Runner-driven), via the `omniagents.workspace`
  seam.
- **Three dataset sources** behind one schema: a bundled laptop corpus, the
  public HF dataset (echo-rl's lineage), and an adapter for echo-rl's parquets.
- **Native omniagents project layout** (`project.yml`, `agents/`, `tools/`,
  `environments/`, `evaluations/`).

## Layout

```
project.yml                 omniagents project (entrypoint agent + paths)
agents/terminal_agent/      declarative agent (model, instructions, tools)   ← domain
tools/terminal_tools.py     the bash tool (omniagents.workspace.exec)         ← domain
tasks/                      THE DATASET LAYER                                 ← domain
  schema.py                   TaskSpec (image, setup, prompt, verifier, tags, split, world_model_only)
  builtin/*.yaml              bundled corpus (file-ops / data / dev / system / verifier-free)
  loaders.py                  load_builtin(split=...) | load_openthoughts_rl | load_echo_parquet
  images.py                   build per-task images from Dockerfiles
environments/terminal_env.py  TerminalSandboxEnvironment (subclass of the framework env)
training/                   config.yaml + train.py (GRPO + ECHO toggle)
evaluations/                verifier measure + how-to-eval
experiments/                run.py (baseline vs ECHO) + evaluate.py (held-out val) + analyze.ipynb
results/                    runs land here (gitignored)
ADAPTING.md                 port this template to your own domain
```

The reusable machinery lives in the **framework**, not here:
`omniagents.core.training.sandbox_env.SandboxTrainingEnvironment` (per-rollout
sandbox + workspace binding + verifier) and `EchoGRPOTrainer` (the ECHO loss).

## Requirements

```bash
# Training without vLLM (HF generation; LoRA/QLoRA ok):
pip install -e ".[training]"     # transformers>=5, trl 0.28-0.29, torch, peft, ...

# Reproducible, vLLM-capable stack (exact pins; tightly coupled — see comments):
pip install "vllm==0.21.0"
pip install -r examples/echo_rl/requirements.txt
```
A running **Docker** daemon (rollouts run in containers; the bundled corpus uses
`python:3.11-slim`). The `[training]` extra's ranges are deliberately loose; the
**`requirements.txt`** pins the exact `torch/transformers/trl/vllm` combo
that's verified to run together (the vLLM path needs that precise set).

## Quickstart (laptop, bundled tasks)

Run from the repo root (paths resolve against the example regardless of CWD):

```bash
# 1. Train with ECHO on the bundled corpus (downloads Qwen2.5-0.5B, uses Docker):
python examples/echo_rl/training/train.py                        # ECHO (world_model_coeff=0.05)
python examples/echo_rl/training/train.py --world-model-coeff 0  # GRPO baseline

# 2. Run the headline experiment (both variants) and plot:
python examples/echo_rl/experiments/run.py
python examples/echo_rl/experiments/analyze.py    # writes results/plots/echo_vs_baseline.png
# or open experiments/analyze.ipynb

# 3. Evaluate on the held-out val split (verifier pass-rate):
python examples/echo_rl/experiments/evaluate.py --model examples/echo_rl/results/echo/model --label echo
python examples/echo_rl/experiments/evaluate.py --model Qwen/Qwen2.5-0.5B-Instruct --label base   # baseline
```

Tip: pass `--limit 3` to `train.py`/`run.py` for a fast first run.

In the logs you'll see `rewards/sandbox_verifier/mean` (the pass-rate) and
`world_model/*` (the ECHO loss; non-zero once the model emits tool calls).

## Splits & verifier-free tasks (mirrors echo-rl)

Each `TaskSpec` carries two fields that reproduce how the paper organized data:

- **`split`** (`train` / `val`) — training uses the `train` split (`dataset.split`
  in `config.yaml`); the `val` split is held out (`load_builtin(split="val")`),
  echoing echo-rl's train8770 / val100 in-distribution holdout.
- **`world_model_only`** (bool) — *verifier-free* tasks. They have no verifier and
  contribute **no policy gradient** (their reward is constant → zero advantage),
  but ECHO still trains on their terminal-output tokens. This is echo-rl's
  "verifier-free adaptation": fold in (often OOD) tasks you can't score and learn
  from the environment stream alone. They are no-ops under the GRPO baseline.

See `tasks/builtin/world_model_only.yaml` for examples and `evaluations/README.md`
for using the held-out split.

## Scale up to real data

```bash
# echo-rl's actual task lineage (~720 tasks, builds per-task Docker images):
python experiments/run.py --source openthoughts --limit 50
```
or point at echo-rl's own parquet files via `dataset.source: echo_parquet` +
`data_files:` in `training/config.yaml`. Both go through the same `task_binary`
extractor in `tasks/loaders.py`.

> Scale honestly: the bundled corpus + tiny model + few steps is for *mechanics*,
> not paper numbers. The clean ECHO-vs-baseline separation needs the larger task
> set, a bigger model, and more GRPO steps (the paper uses Qwen3-8B/14B, 8,770
> tasks, n=16 rollouts, 500 steps).

## Laptop training with LoRA / QLoRA

ECHO composes with **LoRA** (train small adapters, not the full model), which
slashes memory so a capable model fits on a laptop/consumer GPU:

```bash
python examples/echo_rl/training/train.py --config training/config.laptop_lora.yaml
```

`config.laptop_lora.yaml` enables `lora.use_lora` (and optionally
`lora.load_in_4bit` for **QLoRA** — 4-bit base, CUDA + bitsandbytes only). By
hardware: a CUDA laptop (8–16GB) can run **Qwen3-8B with `load_in_4bit: true`**;
a smaller CUDA GPU or Mac (MPS) should use a ~1.5–3B model with plain LoRA.
Trained runs save a LoRA *adapter*; `evaluate.py`/`probe_env_ce.py` load base +
adapter automatically.

**Honest caveat:** LoRA fixes *memory*, not *throughput*. GRPO still runs many
multi-turn Docker rollouts per step, so a laptop is for **development and
small-scale experiments** — a full publishable run (hundreds of steps × hundreds
of tasks × 8 rollouts) is still server-GPU territory. On CPU/MPS, generation
speed is the wall regardless of LoRA.

## Faster training with vLLM (the throughput lever)

GRPO is rollout-bound; **vLLM** batches generation across rollouts and is the
main speed lever at scale. Enable TRL's colocate vLLM (no code, reuses the same
sandbox env + ECHO):

```bash
VLLM_PLUGINS="" python examples/echo_rl/training/train.py \
    --config training/config.laptop_lora.yaml --use-vllm --model Qwen/Qwen2.5-0.5B-Instruct
```

`config.laptop_lora.yaml` has a `vllm:` block (`gpu_memory_utilization`,
`max_model_len`). When vLLM is active you'll see `sampling/importance_sampling_*`
metrics in the logs (TRL's vLLM correction).

**Working dependency stack** (verified end-to-end, incl. on an 8GB GPU):
`torch 2.11 + transformers 5.9 + trl 0.29 + vllm 0.21`. Notes:
- TRL warns vLLM 0.21 is "unsupported" but the colocate path works at runtime.
- `VLLM_PLUGINS=""` avoids unrelated third-party vLLM plugins crashing startup.
- On a small GPU (8GB), keep `vllm.gpu_memory_utilization` low (~0.25) and
  `vllm.max_model_len` small (~2048) — otherwise vLLM reserves KV cache for the
  model's full 32k context and OOMs. Pair with LoRA.
- The benefit shows at scale (many rollouts/large batch); on 2-task runs the
  per-step time is still dominated by Docker, not generation.

For fully custom rollouts, `train_grpo(..., rollout_func=...)` is also wired
(TRL's `rollout_func` hook); the colocate path above is the recommended fast
path since TRL handles vLLM generation + weight sync for you.

## Where the time goes (profiled) & the warm-container pool

GRPO is rollout-bound: the gradient step is <0.5% of wall-clock. Profiling one
step (`experiments/profile_rollout.py`, 0.5B, this 8GB GPU) showed the two real
costs and how to attack each:

| phase | naive | + warm pool | + warm pool & vLLM |
|---|---|---|---|
| container create+setup | 67.6s | 5.5s | 5.8s |
| LLM generation | 26.0s | 26.8s | 3.7s |
| backward/optim | 0.4s | 0.4s | 0.3s |
| **train_runtime** | **95s** | **34s** | **11s** |

- **Container churn was the #1 cost** (recreating a fresh container per rollout,
  ~8s each). `SandboxTrainingEnvironment` now keeps a shared **warm pool** of
  *pre-created, never-reused* containers, refilled concurrently on worker threads
  off the critical path (`pool_size`, `max_concurrent_builds` knobs). Each rollout
  still gets a **pristine** container — we do **not** reuse dirty containers (that
  would leak filesystem/processes/installed packages across tasks and silently
  corrupt the verifier reward). The pool just hides the *create* latency.
- **Generation** is then the dominant cost — that's what **vLLM** attacks (≈7×).

Together: ~8.6× faster here. Re-run `profile_rollout.py --compare` on your stack.

**Two more wins built in:**
- **Prefix caching** — vLLM 0.21's engine enables it by default, so the shared
  prompt prefix (system + task + prior turns) and the `num_generations` sibling
  rollouts reuse KV across turns automatically. (No knob; it's on.)
- **Concurrent environment work** — the `bash` tool is async and the verifier
  reward runs all rollouts' verifiers at once, so a step's container exec /
  verifier work happens **in parallel across rollouts** (the shared pool loop
  offloads each to its thread executor) instead of serially. Negligible on the
  toy tasks (bash ~0.1s, verifier ~0.6s) but a large win for real tasks with
  slow commands or `pytest` verifiers. (Full *generation↔environment* pipelining
  — keeping the GPU busy *during* container waits — needs the custom
  `rollout_func` loop; the colocate path here overlaps env-with-env, not
  env-with-gen.)

## How it maps to the paper

| paper / echo-rl | here |
|---|---|
| Harbor container per rollout | `SandboxTrainingEnvironment` (omniagents Docker sandbox) |
| `bash` tool, multi-turn | `tools/terminal_tools.py` + TRL's tool loop |
| pytest/verifier reward | per-task `verifier` (exit 0 = pass) via `sandbox_verifier_reward` |
| ECHO env-token loss | `world_model_coeff=0.05` (`EchoGRPOTrainer`) |
| OpenThoughts-Agent-v1-RL / Endless Terminals | `load_openthoughts_rl` / bundled corpus |
