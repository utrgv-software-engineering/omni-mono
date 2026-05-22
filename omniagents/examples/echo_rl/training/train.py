#!/usr/bin/env python
"""Train the terminal agent with GRPO (+ optional ECHO) on sandboxed tasks.

    python examples/echo_rl/training/train.py                         # use config.yaml
    python examples/echo_rl/training/train.py --world-model-coeff 0   # GRPO baseline
    python examples/echo_rl/training/train.py --source openthoughts --limit 50

Loads tasks (bundled / HF / echo parquet) -> EvalSuite -> per-rollout Docker
sandbox (TerminalSandboxEnvironment) -> train_grpo. Reward is the per-task
in-container verifier; ECHO trains on the terminal-output tokens.

Saves the run's metrics (TRL log_history) to <output_dir>/metrics.json so
experiments/analyze.ipynb can plot baseline vs ECHO.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

EXAMPLE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EXAMPLE_ROOT))

from environments.terminal_env import TerminalSandboxEnvironment  # noqa: E402
from tasks.loaders import (  # noqa: E402
    load_builtin,
    load_echo_parquet,
    load_openthoughts_rl,
    to_eval_suite,
)


def load_tasks(cfg: dict):
    ds = cfg.get("dataset", {})
    source = ds.get("source", "builtin")
    limit = ds.get("limit")
    if source == "builtin":
        return load_builtin(ds.get("categories"), split=ds.get("split", "train"), limit=limit)
    if source == "openthoughts":
        return load_openthoughts_rl(limit=limit)
    if source == "echo_parquet":
        return load_echo_parquet(ds["data_files"], limit=limit)
    raise ValueError(f"Unknown dataset.source: {source!r}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(Path(__file__).parent / "config.yaml"))
    ap.add_argument("--source", help="override dataset.source")
    ap.add_argument("--limit", type=int, help="override dataset.limit")
    ap.add_argument("--world-model-coeff", type=float, help="override echo.world_model_coeff")
    ap.add_argument("--seed", type=int, help="override seed")
    ap.add_argument("--model", help="override model")
    ap.add_argument("--use-vllm", action="store_true", help="enable vLLM colocate generation")
    ap.add_argument("--output-dir", help="override output_dir")
    args = ap.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text())
    if args.model:
        cfg["model"] = args.model
    if args.source:
        cfg.setdefault("dataset", {})["source"] = args.source
    if args.limit is not None:
        cfg.setdefault("dataset", {})["limit"] = args.limit
    if args.world_model_coeff is not None:
        cfg.setdefault("echo", {})["world_model_coeff"] = args.world_model_coeff
    if args.seed is not None:
        cfg["seed"] = args.seed
    if args.use_vllm:
        cfg.setdefault("vllm", {})["use_vllm"] = True
    # Resolve a relative output_dir against the example root (not the CWD) so
    # results land in examples/echo_rl/results/ regardless of where you run from.
    output_dir = args.output_dir or cfg["output_dir"]
    if not Path(output_dir).is_absolute():
        output_dir = str(EXAMPLE_ROOT / output_dir)

    from omniagents import Agent
    from omniagents.training import (
        GRPOTrainingConfig,
        make_environment_factory,
        sandbox_verifier_reward,
        train_grpo,
        EchoGRPOTrainer,
    )

    # Agent (matches agents/terminal_agent/agent.yml).
    agent_yml = yaml.safe_load((EXAMPLE_ROOT / "agents/terminal_agent/agent.yml").read_text())
    agent = Agent(name=agent_yml["name"], model=cfg["model"], instructions=agent_yml["instructions"])

    tasks = load_tasks(cfg)
    n_wmo = sum(1 for t in tasks if t.world_model_only)
    src = cfg.get("dataset", {}).get("source")
    print(f"Loaded {len(tasks)} train tasks (source={src}, split={cfg.get('dataset', {}).get('split', 'train')}, "
          f"world_model_only={n_wmo})")
    if src == "builtin":
        n_val = len(load_builtin(split="val"))
        print(f"Held-out val tasks available: {n_val} (see evaluations/README.md)")
    suite = to_eval_suite(tasks)

    g, e, rt = cfg["grpo"], cfg["echo"], cfg.get("runtime", {})
    config = GRPOTrainingConfig(
        num_train_epochs=g["num_train_epochs"],
        per_device_batch_size=g["per_device_batch_size"],
        gradient_accumulation_steps=g["gradient_accumulation_steps"],
        num_generations=g["num_generations"],
        max_completion_length=g["max_completion_length"],
        temperature=g["temperature"],
        learning_rate=g["learning_rate"],
        max_grad_norm=g.get("max_grad_norm", 1.0),
        max_tool_calling_iterations=g["max_tool_calling_iterations"],
        world_model_coeff=e["world_model_coeff"],
        world_loss_normalization=e["world_loss_normalization"],
        seed=cfg.get("seed", 0),
        gradient_checkpointing=cfg.get("lora", {}).get("gradient_checkpointing", False),
        use_lora=cfg.get("lora", {}).get("use_lora", False),
        lora_r=cfg.get("lora", {}).get("r", 16),
        lora_alpha=cfg.get("lora", {}).get("alpha", 32),
        lora_dropout=cfg.get("lora", {}).get("dropout", 0.05),
        load_in_4bit=cfg.get("lora", {}).get("load_in_4bit", False),
        use_vllm=cfg.get("vllm", {}).get("use_vllm", False),
        vllm_mode=cfg.get("vllm", {}).get("mode", "colocate"),
        vllm_gpu_memory_utilization=cfg.get("vllm", {}).get("gpu_memory_utilization", 0.3),
        vllm_max_model_len=cfg.get("vllm", {}).get("max_model_len", None),
        output_dir=output_dir,
        report_to=cfg.get("report_to", "none"),
        logging_steps=cfg.get("logging_steps", 1),
    )

    factory = make_environment_factory(
        env_cls=TerminalSandboxEnvironment,
        image=cfg["default_image"],
        exec_timeout=rt.get("exec_timeout", 30.0),
        verifier_timeout=rt.get("verifier_timeout", 60.0),
    )

    result = train_grpo(
        agent=agent,
        suite=suite,
        reward_measures=[],
        config=config,
        environment_factory=factory,
        extra_reward_funcs=[sandbox_verifier_reward],
    )

    variant = "echo" if e["world_model_coeff"] > 0 else "grpo_baseline"
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    if cfg.get("save_model", True):
        result.save_model(str(out / "model"))   # so experiments/evaluate.py can load it
        print(f"Saved model to {out / 'model'}")
    (out / "metrics.json").write_text(json.dumps({
        "variant": variant,
        "world_model_coeff": e["world_model_coeff"],
        "seed": cfg.get("seed", 0),
        "num_tasks": len(tasks),
        "final_loss": result.final_loss,
        "log_history": result.metrics,
    }, indent=2))
    print(f"Saved metrics to {out / 'metrics.json'} "
          f"(EchoGRPOTrainer={isinstance(result.trainer, EchoGRPOTrainer)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
