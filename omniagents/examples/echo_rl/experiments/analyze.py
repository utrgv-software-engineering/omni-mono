"""Aggregate and analyze baseline-vs-ECHO runs (used by analyze.ipynb / CLI).

    python examples/echo_rl/experiments/analyze.py            # plots -> results/plots/
    python examples/echo_rl/experiments/analyze.py --sig echo base   # significance

Handles both single runs (results/<variant>/metrics.json) and seeded sweeps
(results/<variant>/seed<k>/metrics.json). Produces, over GRPO steps and averaged
across seeds (mean ± std):
  * verifier pass-rate            (rewards/sandbox_verifier/mean) — paper Fig 1/2
  * terminal-output cross-entropy (world_model/ce_per_token)      — paper Fig 3

`--sig A B` runs a paired Wilcoxon signed-rank test on held-out per-task
pass-rates from results/eval/A.json vs results/eval/B.json (the publishability
significance check).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

EXAMPLE_ROOT = Path(__file__).resolve().parents[1]
RESULTS = EXAMPLE_ROOT / "results"


def _run_files(variant: str) -> list[Path]:
    """metrics.json files for a variant: seeded (seed*/) or single."""
    seeded = sorted((RESULTS / variant).glob("seed*/metrics.json"))
    if seeded:
        return seeded
    single = RESULTS / variant / "metrics.json"
    return [single] if single.exists() else []


def _series(run: dict, key: str) -> dict[int, float]:
    out = {}
    for i, entry in enumerate(run.get("log_history", [])):
        if isinstance(entry.get(key), (int, float)):
            out[entry.get("step", i)] = float(entry[key])
    return out


def aggregate(variant: str, key: str):
    """Return (steps, mean, std) across seeds for a metric key."""
    import numpy as np

    runs = [json.loads(p.read_text()) for p in _run_files(variant)]
    series = [_series(r, key) for r in runs]
    steps = sorted(set().union(*[s.keys() for s in series])) if series else []
    mean, std = [], []
    for st in steps:
        vals = [s[st] for s in series if st in s]
        mean.append(float(np.mean(vals)))
        std.append(float(np.std(vals)))
    return steps, mean, std


def plot(variants=("grpo_baseline", "echo")) -> None:
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 2, figsize=(12, 4))
    colors = {"grpo_baseline": "#1b9e77", "echo": "#d6336c"}
    panels = [
        ("rewards/sandbox_verifier/mean", axes[0], "Verifier pass-rate", "pass-rate"),
        (
            "world_model/ce_per_token",
            axes[1],
            "Terminal-output cross-entropy",
            "nats / env-token",
        ),
    ]
    for key, ax, title, ylabel in panels:
        for v in variants:
            steps, mean, std = aggregate(v, key)
            if not steps:
                continue
            import numpy as np

            mean, std = np.array(mean), np.array(std)
            ax.plot(steps, mean, label=v, color=colors.get(v), marker="o", ms=3)
            if (std > 0).any():
                ax.fill_between(
                    steps, mean - std, mean + std, color=colors.get(v), alpha=0.2
                )
        ax.set_title(title)
        ax.set_xlabel("GRPO step")
        ax.set_ylabel(ylabel)
        ax.legend()
    fig.tight_layout()
    out = RESULTS / "plots"
    out.mkdir(parents=True, exist_ok=True)
    fig.savefig(out / "echo_vs_baseline.png", dpi=120)
    print(f"wrote {out / 'echo_vs_baseline.png'}")


def significance(label_a: str, label_b: str) -> None:
    """Paired Wilcoxon on held-out per-task pass-rates (eval/<label>.json)."""
    a = json.loads((RESULTS / "eval" / f"{label_a}.json").read_text())
    b = json.loads((RESULTS / "eval" / f"{label_b}.json").read_text())
    tasks = sorted(set(a["per_task"]) & set(b["per_task"]))
    pr = lambda d, t: sum(s >= 1.0 for s in d["per_task"][t]) / len(d["per_task"][t])
    xa = [pr(a, t) for t in tasks]
    xb = [pr(b, t) for t in tasks]
    da = sum(xa) / len(xa)
    db = sum(xb) / len(xb)
    print(
        f"{label_a}: pass-rate={da:.3f}  |  {label_b}: pass-rate={db:.3f}  (n={len(tasks)} tasks)"
    )
    try:
        from scipy.stats import wilcoxon

        diffs = [x - y for x, y in zip(xa, xb)]
        if any(d != 0 for d in diffs):
            stat, p = wilcoxon(xa, xb)
            print(f"paired Wilcoxon: W={stat:.3f}  p={p:.4f}")
        else:
            print("paired Wilcoxon: no differences (identical pass-rates)")
    except ImportError:
        print("(install scipy for the Wilcoxon test)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--sig", nargs=2, metavar=("A", "B"), help="significance: eval labels A vs B"
    )
    args = ap.parse_args()
    if args.sig:
        significance(*args.sig)
    else:
        plot()
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
