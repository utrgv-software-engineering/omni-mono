# Running a publishable ECHO-vs-GRPO experiment

This is the **smallest setup that, run to completion, yields a defensible
result**: a single-model ablation (ECHO vs a matched GRPO baseline) on a real
task set, with seeds, held-out + OOD evaluation, and the off-policy world-model
probe. It is *not* a laptop run — see compute below. Everything here is the
configuration/scaffolding; you supply the GPUs and wall-clock.

## The claim
> Adding ECHO's environment-prediction loss to GRPO improves held-out terminal
> task success — the only changed variable is `world_model_coeff` (0.0 → 0.05).

## Minimum viable configuration (`training/config.publishable.yaml`)
| Knob | Value | Why this is the floor |
|---|---|---|
| Model | ~8B instruct (Qwen3-8B) | <7B emits too few successful tool calls for GRPO signal; capacity competes with world-modeling at small scale |
| Train tasks | OpenThoughts ~720 (`source: openthoughts`) | enough diversity for held-out generalization to mean something |
| Rollouts/prompt | 8 | GRPO advantage needs intra-group reward contrast |
| Steps | ~400–500 | where ECHO's separation from baseline is clear |
| Seeds | 2–3 per arm | a single run is not a result; needed for significance |
| Eval | `val` (in-dist) **+ `test` (OOD)** | OOD is what makes the claim publishable |
| Compute | 1 × 80GB GPU per run; ~1–3 GPU-days for the 2×3 matrix | one 8B model fits at this batch size |

## Steps

```bash
cd examples/echo_rl

# 1. Train the matrix: 2 arms (baseline / ECHO) × 3 seeds.
python experiments/run.py --config training/config.publishable.yaml --seeds 0,1,2
#   -> results/grpo_baseline/seed{0,1,2}/{metrics.json,model}
#   -> results/echo/seed{0,1,2}/{metrics.json,model}

# 2. Held-out + OOD eval of each saved model (verifier pass-rate).
for arm in grpo_baseline echo; do for s in 0 1 2; do
  python experiments/evaluate.py --model results/$arm/seed$s/model \
      --split test --rollouts 8 --label ${arm}_s${s}
done; done

# 3. Training curves with seed variance (mean ± std).
python experiments/analyze.py            # -> results/plots/echo_vs_baseline.png

# 4. Significance on held-out pass-rate (paired Wilcoxon over tasks).
python experiments/analyze.py --sig echo_s0 grpo_baseline_s0

# 5. Off-policy world-model probe (paper Fig 3): does ECHO predict a STRONGER
#    model's terminal output better than the baseline?
python experiments/probe_env_ce.py build --teacher Qwen/Qwen2.5-7B-Instruct \
    --split test --rollouts 2 --out results/probe/pool.json
python experiments/probe_env_ce.py score --model results/echo/seed0/model         --pool results/probe/pool.json --label echo
python experiments/probe_env_ce.py score --model results/grpo_baseline/seed0/model --pool results/probe/pool.json --label baseline
```

## What to report
- **Held-out + OOD pass-rate**, ECHO vs baseline, mean ± std over seeds, with the
  paired Wilcoxon p-value (step 4).
- **Sample-efficiency**: steps-to-reach-baseline-peak from the curves (step 3).
- **Env-token CE** on the teacher pool, ECHO < baseline (step 5) — evidence the
  gains come from a better terminal world model, not memorization.

## Honest scope
This is the *minimum* defensible unit (one model, the public task slice). The
paper's full strength comes from 3 model families + the public TerminalBench-2.0
benchmark and the larger 8,770-task corpus. To strengthen toward that: add more
models, swap `test` for an external benchmark suite, and grow the task set
(`load_openthoughts_rl` is full-size; generate more via an Endless-Terminals-style
pipeline). The 0.5B + bundled-corpus default in `config.yaml` is for *mechanics
only* and will not show the effect.
