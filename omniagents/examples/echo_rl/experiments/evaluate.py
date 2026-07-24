#!/usr/bin/env python
"""Evaluate a terminal agent on the held-out val split (verifier pass-rate).

    # base model (works out of the box):
    python examples/echo_rl/experiments/evaluate.py --model Qwen/Qwen2.5-0.5B-Instruct --label base
    # a trained checkpoint (train.py saves to <output_dir>/model):
    python examples/echo_rl/experiments/evaluate.py --model examples/echo_rl/results/echo/model --label echo

Runs N multi-turn rollouts per task in the Docker sandbox — reusing
TerminalSandboxEnvironment (its `bash` tool + verifier) and TRL's response
parser — then scores each rollout with the in-container verifier. Reports
pass-rate (mean over rollouts) and pass@1 (fraction of tasks solved at least
once). Mirrors echo-rl's val100 protocol (held-out, multiple rollouts, temp 0.6).

Saves results/eval/<label>.json.
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
from tasks.loaders import load_builtin  # noqa: E402
from tasks.schema import task_expect  # noqa: E402


def _parse_args_dict(arguments):
    if isinstance(arguments, str):
        try:
            return json.loads(arguments)
        except Exception:
            return {}
    return dict(arguments or {})


def rollout(
    model, tok, env, task, instructions, *, max_turns, max_new_tokens, temperature
):
    """One multi-turn episode; returns 1.0 if the verifier passes, else 0.0."""
    import torch
    from trl.chat_template_utils import parse_response

    env.reset(
        prompt=None,
        **{k: v for k, v in task_expect(task.to_record()).items() if k != "id"},
    )
    messages = [
        {"role": "system", "content": instructions},
        {"role": "user", "content": task.prompt},
    ]

    for _ in range(max_turns):
        enc = tok.apply_chat_template(
            messages,
            tools=[env.bash],
            add_generation_prompt=True,
            tokenize=True,
            return_dict=True,
            return_tensors="pt",
        )
        enc = {k: v.to(model.device) for k, v in enc.items()}
        with torch.no_grad():
            out = model.generate(
                **enc,
                max_new_tokens=max_new_tokens,
                do_sample=temperature > 0,
                temperature=temperature or None,
                pad_token_id=tok.pad_token_id,
            )
        new_ids = out[0][enc["input_ids"].shape[-1] :].tolist()
        parsed = parse_response(tok, new_ids)
        tool_calls = parsed.get("tool_calls") or []

        assistant = {"role": "assistant", "content": parsed.get("content", "") or ""}
        if tool_calls:
            assistant["tool_calls"] = tool_calls
        messages.append(assistant)

        if not tool_calls:
            break  # final answer, no tool call -> episode ends
        for call in tool_calls:
            fn = call.get("function", {})
            args = _parse_args_dict(fn.get("arguments"))
            if fn.get("name") == "bash":
                # bash is async (TRL gathers it during training); drive it
                # synchronously here via the pool loop for this eval rollout.
                result = env._run(
                    env._abash(
                        str(args.get("command", "")), int(args.get("timeout", 30) or 30)
                    )
                )
            else:
                result = {"error": f"unknown tool {fn.get('name')!r}"}
            messages.append(
                {"role": "tool", "name": fn.get("name"), "content": str(result)}
            )

    return env._run_verifier()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, help="HF id or saved checkpoint dir")
    ap.add_argument("--label", default=None, help="name for the results file")
    ap.add_argument("--split", default="val")
    ap.add_argument("--rollouts", type=int, default=4)
    ap.add_argument("--temperature", type=float, default=0.6)
    ap.add_argument("--max-turns", type=int, default=6)
    ap.add_argument("--max-new-tokens", type=int, default=256)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--image", default="python:3.11-slim")
    args = ap.parse_args()

    from modelio import load_policy

    label = args.label or Path(args.model).name
    instructions = yaml.safe_load(
        (EXAMPLE_ROOT / "agents/terminal_agent/agent.yml").read_text()
    )["instructions"]

    # Skip verifier-free tasks: there is nothing to score.
    tasks = [
        t
        for t in load_builtin(split=args.split, limit=args.limit)
        if not t.world_model_only
    ]
    print(
        f"Evaluating '{label}' on {len(tasks)} {args.split} tasks "
        f"({args.rollouts} rollouts/task, temp={args.temperature})"
    )

    model, tok = load_policy(args.model)  # handles LoRA adapters + full models

    env = TerminalSandboxEnvironment(
        image=args.image, exec_timeout=15.0, verifier_timeout=30.0
    )
    per_task = {}

    def one_rollout(t):
        try:
            return rollout(
                model,
                tok,
                env,
                t,
                instructions,
                max_turns=args.max_turns,
                max_new_tokens=args.max_new_tokens,
                temperature=args.temperature,
            )
        except Exception as exc:  # never let one rollout abort the eval
            print(f"    (rollout error on {t.id}: {type(exc).__name__}: {exc})")
            return 0.0

    try:
        for t in tasks:
            scores = [one_rollout(t) for _ in range(args.rollouts)]
            per_task[t.id] = scores
            pr = sum(s >= 1.0 for s in scores) / len(scores)
            print(
                f"  {t.id:24s} pass-rate={pr:.2f}  ({sum(s>=1.0 for s in scores)}/{len(scores)})"
            )
    finally:
        env._close()

    flat = [s for v in per_task.values() for s in v]
    pass_rate = sum(s >= 1.0 for s in flat) / max(len(flat), 1)
    pass_at_1 = sum(any(s >= 1.0 for s in v) for v in per_task.values()) / max(
        len(per_task), 1
    )
    summary = {
        "label": label,
        "split": args.split,
        "rollouts": args.rollouts,
        "pass_rate": pass_rate,
        "pass_at_1": pass_at_1,
        "per_task": per_task,
    }

    out = EXAMPLE_ROOT / "results" / "eval"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{label}.json").write_text(json.dumps(summary, indent=2))
    print(
        f"\n{label}: pass-rate={pass_rate:.3f}  pass@1={pass_at_1:.3f}  -> {out / (label + '.json')}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
