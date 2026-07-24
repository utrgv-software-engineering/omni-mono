#!/usr/bin/env python
"""Smoke test for the ECHO world-model loss (EchoGRPOTrainer).

Checks end-to-end that the new trainer actually runs:

  1. Runs a tiny GRPO training step on ``Qwen/Qwen2.5-0.5B`` (the model used by
     notebook 210) with a tool and ``world_model_coeff > 0``, confirming
     ``train_grpo`` selects ``EchoGRPOTrainer`` and completes without error.
  2. Deterministically exercises the ECHO auxiliary-loss path by calling the
     real trainer's ``_compute_loss`` with a synthetic ``tool_mask`` that has
     environment tokens -- this guarantees the loss math + log-prob capture run
     on a real model forward, regardless of whether the tiny model emitted tool
     calls during rollout.

Usage:
    python scripts/smoke_test_echo_grpo.py

Exits 0 on success, 1 on failure. This is intentionally NOT a pytest test: it
loads a 0.5B model and runs real training, which is too slow for the unit suite
(see tests/unit/core/training/test_echo_grpo.py for the fast unit tests).
"""

from __future__ import annotations

import sys
import tempfile

MODEL = "Qwen/Qwen2.5-0.5B"  # same model as notebook 210_reasoning_with_grpo_omniagents_trainer


def main() -> int:
    import torch

    from omniagents import Agent, function_tool
    from omniagents.notebook import (
        EvalSuite,
        measure,
        EvalContext,
        pass_reason,
        fail_reason,
    )
    from omniagents.training import train_grpo, GRPOTrainingConfig, EchoGRPOTrainer

    # --- A tool, so there are tool-result ("environment") tokens for ECHO ----
    @function_tool
    def calculator(expression: str) -> str:
        """Evaluate a simple arithmetic expression.

        Args:
            expression: A Python arithmetic expression, e.g. "2 + 3 * 4".
        """
        try:
            return str(eval(expression, {"__builtins__": {}}, {}))
        except Exception as exc:  # pragma: no cover - defensive
            return f"error: {exc}"

    agent = Agent(
        name="Calc Agent",
        model=MODEL,
        instructions=(
            "You are a calculator assistant. Use the calculator tool to compute "
            "the answer, then state it as: ANSWER: <number>."
        ),
        tools=[calculator],
    )

    @measure
    def correct_answer(ctx: EvalContext):
        response = (
            ctx.final_assistant_message.text if ctx.final_assistant_message else ""
        )
        expected = str(ctx.expect.get("answer", ""))
        if expected and expected in response:
            return pass_reason(f"Found {expected}")
        return fail_reason(f"Expected {expected}")

    problems = [
        {"q": "What is 2 + 3?", "answer": "5"},
        {"q": "What is 6 * 7?", "answer": "42"},
        {"q": "What is 10 - 4?", "answer": "6"},
        {"q": "What is 8 + 8?", "answer": "16"},
    ]
    suite = EvalSuite.from_records(
        problems,
        name="Smoke Arithmetic",
        input_fn=lambda p: p["q"],
        expect_fn=lambda p: {"answer": p["answer"]},
        measures=["correct_answer"],
    )

    tmp_out = tempfile.mkdtemp(prefix="echo_smoke_")
    config = GRPOTrainingConfig(
        num_train_epochs=1,
        per_device_batch_size=2,
        gradient_accumulation_steps=1,
        num_generations=2,
        max_completion_length=64,
        temperature=0.8,
        max_tool_calling_iterations=2,
        world_model_coeff=0.05,  # <-- enables ECHO
        world_loss_normalization="selected_tokens",
        output_dir=tmp_out,
        report_to="none",
        logging_steps=1,
    )

    # ----------------------------------------------------------------------
    print("=" * 70)
    print(f"[1/2] End-to-end GRPO+ECHO training on {MODEL} ...")
    print("=" * 70)
    result = train_grpo(
        agent=agent,
        suite=suite,
        reward_measures=["correct_answer"],
        config=config,
    )

    trainer = result.trainer
    assert isinstance(
        trainer, EchoGRPOTrainer
    ), f"expected EchoGRPOTrainer, got {type(trainer).__name__}"
    assert trainer.world_model_coeff == 0.05
    print(
        f"  OK: trainer is EchoGRPOTrainer (world_model_coeff={trainer.world_model_coeff})"
    )
    print(f"  Final loss: {result.final_loss}")

    # Did ECHO metrics show up during the real run? (only if the model emitted
    # tool calls -> tool-result tokens). Soft check: report either way.
    wm_logged = any(
        any(k.startswith("world_model/") for k in entry)
        for entry in (result.metrics or [])
    )
    if wm_logged:
        env_tokens = [
            entry.get("world_model/env_tokens")
            for entry in result.metrics
            if "world_model/env_tokens" in entry
        ]
        print(
            f"  OK: ECHO metrics logged during rollout (env_tokens seen: {env_tokens})"
        )
    else:
        print(
            "  NOTE: tiny model produced no tool calls this run -> no env tokens "
            "during rollout (ECHO loss was a graceful 0). Forcing the path below."
        )

    # ----------------------------------------------------------------------
    print("=" * 70)
    print("[2/2] Deterministic ECHO loss path via real _compute_loss ...")
    print("=" * 70)
    model = trainer.model
    device = next(model.parameters()).device
    vocab = model.config.vocab_size

    B, P, C = 2, 3, 4
    g = torch.Generator().manual_seed(0)
    inputs = {
        "prompt_ids": torch.randint(0, vocab, (B, P), generator=g).to(device),
        "prompt_mask": torch.ones(B, P, dtype=torch.long, device=device),
        "completion_ids": torch.randint(0, vocab, (B, C), generator=g).to(device),
        "completion_mask": torch.ones(B, C, dtype=torch.long, device=device),
        # tool_mask: 1 = model token, 0 = tool-result (environment) token.
        # env_mask = completion_mask * (1 - tool_mask) -> rows have env tokens.
        "tool_mask": torch.tensor([[1, 0, 0, 1], [1, 1, 0, 1]], device=device),
        "advantages": torch.tensor([1.0, -1.0], device=device),
        # Required by TRL's default loss_type normalizer (dapo/cispo).
        "num_items_in_batch": torch.tensor(float(B * C), device=device),
    }

    # mode bucket TRL logs into depends on model.training; track the right one.
    mode = "train" if model.training else "eval"
    n_before = len(trainer._metrics[mode].get("world_model/env_tokens", []))
    loss = trainer._compute_loss(model, inputs)
    assert torch.isfinite(loss), f"loss is not finite: {loss}"

    metrics = trainer._metrics[mode]
    assert "world_model/env_tokens" in metrics, "ECHO did not log world_model metrics"
    assert len(metrics["world_model/env_tokens"]) == n_before + 1
    env_tokens = metrics["world_model/env_tokens"][-1]
    ce = metrics["world_model/ce_per_token"][-1]
    # tool_mask has 3 zeros within completion -> 3 environment tokens
    assert env_tokens == 3.0, f"expected 3 env tokens, got {env_tokens}"
    assert ce > 0.0, f"expected positive cross-entropy, got {ce}"
    # Capture state must be reset.
    assert trainer._echo_capture is False
    assert trainer._echo_logps is None
    print(
        f"  OK: _compute_loss ran, loss={float(loss.detach()):.4f}, "
        f"env_tokens={env_tokens}, ce_per_token={ce:.4f}"
    )

    # Sanity: with coeff temporarily 0 the loss must drop the aux term.
    trainer.world_model_coeff = 0.0
    base_loss = trainer._compute_loss(model, inputs)
    trainer.world_model_coeff = 0.05
    assert torch.isfinite(base_loss)
    with_aux, without_aux = float(loss.detach()), float(base_loss.detach())
    assert (
        abs(with_aux - without_aux) > 1e-6
    ), "ECHO term made no difference to the loss -- aux loss not applied"
    print(
        f"  OK: ECHO term changes loss (with={with_aux:.4f}, without={without_aux:.4f})"
    )

    print("=" * 70)
    print("SMOKE TEST PASSED")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        import traceback

        traceback.print_exc()
        print("\nSMOKE TEST FAILED:", exc, file=sys.stderr)
        sys.exit(1)
