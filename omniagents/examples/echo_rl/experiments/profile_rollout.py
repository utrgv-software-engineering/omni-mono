#!/usr/bin/env python
"""Profile where training time goes, with and without vLLM.

Times each phase by monkeypatching the sync boundaries (no framework edits):
  * env_reset   — container create + task setup        (env.reset)
  * env_exec    — bash tool execution in the container  (TerminalSandboxEnvironment.bash)
  * env_verify  — verifier run                          (_run_verifier)
  * rollout     — TRL's whole generate+score phase      (_generate_and_score_completions)
  * backward    — forward/backward/optim                (EchoGRPOTrainer._compute_loss)
Generation time is derived: generation = rollout - (env_reset + env_exec + env_verify).

Usage:
    python experiments/profile_rollout.py            # HF generation  -> results/profile/hf.json
    VLLM_PLUGINS="" python experiments/profile_rollout.py --use-vllm   # -> results/profile/vllm.json
    python experiments/profile_rollout.py --compare  # print the table
"""

from __future__ import annotations

import argparse
import functools
import json
import sys
import time
from pathlib import Path

EXAMPLE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EXAMPLE_ROOT))

RESULTS = EXAMPLE_ROOT / "results" / "profile"
TIMES: dict[str, float] = {}


def _acc(name: str):
    def deco(fn):
        @functools.wraps(fn)
        def wrap(*a, **k):
            t = time.perf_counter()
            try:
                return fn(*a, **k)
            finally:
                TIMES[name] = TIMES.get(name, 0.0) + (time.perf_counter() - t)
                TIMES[name + "_n"] = TIMES.get(name + "_n", 0) + 1
        return wrap
    return deco


def _install_timers():
    from omniagents.core.training.sandbox_env import SandboxTrainingEnvironment
    from omniagents.core.training.echo import EchoGRPOTrainer
    from environments.terminal_env import TerminalSandboxEnvironment
    from trl import GRPOTrainer as TRLGRPOTrainer

    SandboxTrainingEnvironment.reset = _acc("env_reset")(SandboxTrainingEnvironment.reset)
    SandboxTrainingEnvironment._run_verifier = _acc("env_verify")(SandboxTrainingEnvironment._run_verifier)
    TerminalSandboxEnvironment.bash = _acc("env_exec")(TerminalSandboxEnvironment.bash)
    TRLGRPOTrainer._generate_and_score_completions = _acc("rollout")(
        TRLGRPOTrainer._generate_and_score_completions
    )
    EchoGRPOTrainer._compute_loss = _acc("backward")(EchoGRPOTrainer._compute_loss)


def run(use_vllm: bool, limit: int) -> None:
    _install_timers()
    from omniagents import Agent
    from omniagents.training import GRPOTrainingConfig, make_environment_factory, sandbox_verifier_reward, train_grpo
    from environments.terminal_env import TerminalSandboxEnvironment
    from tasks.loaders import load_builtin, to_eval_suite
    import yaml

    instr = yaml.safe_load((EXAMPLE_ROOT / "agents/terminal_agent/agent.yml").read_text())["instructions"]
    agent = Agent(name="Terminal Agent", model="Qwen/Qwen2.5-0.5B-Instruct", instructions=instr)
    tasks = load_builtin(split="train", limit=limit)
    suite = to_eval_suite(tasks)

    config = GRPOTrainingConfig(
        num_train_epochs=1, per_device_batch_size=2, gradient_accumulation_steps=1,
        num_generations=2, max_completion_length=256, temperature=0.8,
        max_tool_calling_iterations=4, learning_rate=1e-5,
        world_model_coeff=0.05, use_lora=True, seed=0,
        use_vllm=use_vllm, vllm_mode="colocate",
        vllm_gpu_memory_utilization=0.25, vllm_max_model_len=2048,
        output_dir=str(EXAMPLE_ROOT / "results" / "profile_run"), report_to="none", logging_steps=1,
    )
    factory = make_environment_factory(env_cls=TerminalSandboxEnvironment, image="python:3.11-slim",
                                       exec_timeout=20.0, verifier_timeout=30.0)
    t0 = time.perf_counter()
    result = train_grpo(agent, suite, reward_measures=[], config=config,
                        environment_factory=factory, extra_reward_funcs=[sandbox_verifier_reward])
    wall = time.perf_counter() - t0

    train_runtime = None
    for e in reversed(result.metrics or []):
        if "train_runtime" in e:
            train_runtime = float(e["train_runtime"]); break

    env_total = TIMES.get("env_reset", 0) + TIMES.get("env_exec", 0) + TIMES.get("env_verify", 0)
    rollout = TIMES.get("rollout", 0.0)
    out = {
        "mode": "vllm" if use_vllm else "hf",
        "wall_s": wall,
        "train_runtime_s": train_runtime,
        "rollout_s": rollout,
        "backward_s": TIMES.get("backward", 0.0),
        "env_reset_s": TIMES.get("env_reset", 0.0),
        "env_exec_s": TIMES.get("env_exec", 0.0),
        "env_verify_s": TIMES.get("env_verify", 0.0),
        "generation_s": max(rollout - env_total, 0.0),  # derived
        "counts": {k: TIMES[k] for k in TIMES if k.endswith("_n")},
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / f"{out['mode']}.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))


def compare() -> None:
    rows = []
    for mode in ("hf", "vllm"):
        p = RESULTS / f"{mode}.json"
        if p.exists():
            rows.append(json.loads(p.read_text()))
    if not rows:
        print("No profile results yet. Run with and without --use-vllm first.")
        return
    phases = [("generation_s", "LLM generation"), ("env_reset_s", "env: container+setup"),
              ("env_exec_s", "env: bash exec"), ("env_verify_s", "env: verifier"),
              ("backward_s", "backward/optim")]
    hdr = f"{'phase':24s}" + "".join(f"{r['mode']:>12s}" for r in rows)
    print(hdr); print("-" * len(hdr))
    for key, label in phases:
        line = f"{label:24s}" + "".join(f"{r.get(key,0):>11.1f}s" for r in rows)
        print(line)
    print("-" * len(hdr))
    print(f"{'rollout (gen+env)':24s}" + "".join(f"{r.get('rollout_s',0):>11.1f}s" for r in rows))
    print(f"{'train_runtime':24s}" + "".join(f"{(r.get('train_runtime_s') or 0):>11.1f}s" for r in rows))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--use-vllm", action="store_true")
    ap.add_argument("--limit", type=int, default=4)
    ap.add_argument("--compare", action="store_true")
    args = ap.parse_args()
    if args.compare:
        compare()
    else:
        run(args.use_vllm, args.limit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
