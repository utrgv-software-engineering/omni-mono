#!/usr/bin/env python
"""Run the matched experiment: GRPO baseline vs GRPO+ECHO, across seeds.

    # laptop sanity (bundled tasks, 1 seed):
    python examples/echo_rl/experiments/run.py
    # publishable matrix (2 arms x 3 seeds, ~8B, OpenThoughts):
    python examples/echo_rl/experiments/run.py \
        --config training/config.publishable.yaml --seeds 0,1,2

For each (variant, seed) it trains into results/<variant>/seed<k>/ with a
metrics.json. The only difference between arms is world_model_coeff (0.0 vs
0.05); seeds give the variance you need for a paired significance test. Then
evaluate each saved model with experiments/evaluate.py and aggregate with
experiments/analyze.py.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

EXAMPLE_ROOT = Path(__file__).resolve().parents[1]
TRAIN = EXAMPLE_ROOT / "training" / "train.py"


def run_arm(
    variant: str, coeff: float, seed: int, config: str | None, extra: list[str]
) -> None:
    out = EXAMPLE_ROOT / "results" / variant / f"seed{seed}"
    cmd = [
        sys.executable,
        str(TRAIN),
        "--world-model-coeff",
        str(coeff),
        "--seed",
        str(seed),
        "--output-dir",
        str(out),
    ]
    if config:
        cmd += ["--config", config]
    cmd += extra
    print(f"\n=== {variant} | seed {seed} | world_model_coeff={coeff} ===")
    subprocess.run(cmd, check=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", help="training config (default: training/config.yaml)")
    ap.add_argument("--seeds", default="0", help="comma-separated seeds, e.g. 0,1,2")
    ap.add_argument("--source", help="dataset.source override")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--only", choices=["baseline", "echo"], help="run a single arm")
    args = ap.parse_args()

    seeds = [int(s) for s in args.seeds.split(",") if s.strip() != ""]
    extra: list[str] = []
    if args.source:
        extra += ["--source", args.source]
    if args.limit is not None:
        extra += ["--limit", str(args.limit)]

    for seed in seeds:
        if args.only != "echo":
            run_arm("grpo_baseline", 0.0, seed, args.config, extra)
        if args.only != "baseline":
            run_arm("echo", 0.05, seed, args.config, extra)

    print(
        "\nDone. Evaluate: experiments/evaluate.py  |  Aggregate/plots: experiments/analyze.py"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
