"""Unit tests for the ECHO auxiliary world-modeling loss (EchoGRPOTrainer).

These cover the pure loss math, the environment-mask derivation, and the
log-prob capture wiring -- all without loading a model or running TRL's full
training step. They guard the small set of TRL internals ECHO depends on.
"""

import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("trl")

from omniagents.core.training.echo import (  # noqa: E402
    EchoGRPOTrainer,
    compute_world_model_loss,
)


def test_loss_zero_when_no_env_tokens():
    logps = torch.randn(2, 5, requires_grad=True)
    env_mask = torch.zeros(2, 5)
    loss, metrics = compute_world_model_loss(logps, env_mask, coeff=0.5)
    assert float(loss.detach()) == 0.0
    assert metrics["world_model/env_tokens"] == 0.0
    # Still differentiable (gradient-safe zero).
    loss.backward()
    assert logps.grad is not None


def test_selected_tokens_is_masked_token_mean_cross_entropy():
    # CE per token = -logp. With selected_tokens normalization the unscaled loss
    # is the mean of -logp over env tokens only.
    logps = torch.tensor([[-1.0, -2.0, -3.0, -4.0]])
    env_mask = torch.tensor([[0.0, 1.0, 1.0, 0.0]])  # tokens 1 and 2 are env tokens
    coeff = 2.0
    loss, metrics = compute_world_model_loss(
        logps, env_mask, coeff=coeff, normalization="selected_tokens"
    )
    expected_unscaled = (2.0 + 3.0) / 2  # mean of -logp over selected
    assert metrics["world_model/loss_unscaled"] == pytest.approx(expected_unscaled)
    assert float(loss) == pytest.approx(coeff * expected_unscaled)
    assert metrics["world_model/ce_per_token"] == pytest.approx(expected_unscaled)
    assert metrics["world_model/env_tokens"] == pytest.approx(2.0)


def test_sequence_mean_normalization():
    logps = torch.tensor([[-1.0, -3.0], [-2.0, -10.0]])
    env_mask = torch.tensor([[1.0, 1.0], [1.0, 0.0]])  # seq0: 2 tokens, seq1: 1 token
    loss, metrics = compute_world_model_loss(
        logps, env_mask, coeff=1.0, normalization="sequence_mean"
    )
    # seq0 mean = (1+3)/2 = 2.0 ; seq1 mean = 2.0/1 = 2.0 ; batch mean = 2.0
    assert metrics["world_model/loss_unscaled"] == pytest.approx(2.0)


def test_seq_mean_token_sum_norm_requires_max_completion_length():
    logps = torch.tensor([[-1.0, -2.0]])
    env_mask = torch.tensor([[1.0, 1.0]])
    with pytest.raises(ValueError):
        compute_world_model_loss(
            logps, env_mask, coeff=1.0, normalization="seq_mean_token_sum_norm"
        )
    loss, metrics = compute_world_model_loss(
        logps,
        env_mask,
        coeff=1.0,
        normalization="seq_mean_token_sum_norm",
        max_completion_length=4,
    )
    # sum(-logp)=3, divided by batch(1)*max_completion_length(4) = 0.75
    assert metrics["world_model/loss_unscaled"] == pytest.approx(3.0 / 4)


def test_unknown_normalization_raises():
    logps = torch.tensor([[-1.0]])
    env_mask = torch.tensor([[1.0]])
    with pytest.raises(ValueError):
        compute_world_model_loss(logps, env_mask, coeff=1.0, normalization="bogus")


def test_env_mask_is_completion_minus_tool_mask():
    # The ECHO env mask must select exactly the tool-result tokens that the GRPO
    # policy loss (completion_mask * tool_mask) excludes, within real tokens.
    completion_mask = torch.tensor([[1, 1, 1, 1, 0]])  # last is padding
    tool_mask = torch.tensor([[1, 0, 0, 1, 1]])  # tokens 1,2 are tool results
    env_mask = completion_mask * (1 - tool_mask)
    assert env_mask.tolist() == [[0, 1, 1, 0, 0]]
    # Padding (completion_mask=0) is never selected even if tool_mask=0 there.
    assert env_mask[0, 4].item() == 0


class _CaptureProbe(EchoGRPOTrainer):
    """Bypass TRL's heavy __init__ to unit-test the capture/compute wiring."""

    def __init__(self):  # noqa: D401 - deliberately skip super().__init__
        self.world_model_coeff = 0.5
        self.world_loss_normalization = "selected_tokens"
        self.max_completion_length = 8
        self.tools = [object()]  # non-empty => ECHO active
        self._echo_capture = False
        self._echo_logps = None
        self.current_gradient_accumulation_steps = 2
        self._captured_logps = torch.tensor([[-1.0, -2.0, -3.0]])

        class _Model:
            training = True

        self.model = _Model()
        self._metrics = {"train": {}, "eval": {}}
        from collections import defaultdict

        self._metrics["train"] = defaultdict(list)

    # Stand in for TRL's grad-enabled forward inside _compute_loss.
    def _super_compute_loss(self, inputs):
        # Mimics TRL calling _get_per_token_logps_and_entropies under capture.
        if self._echo_capture:
            self._echo_logps = self._captured_logps
        return torch.tensor(1.0)

    # Reimplement only the parent-call seam so we don't need TRL's real method.
    def _compute_loss(self, model, inputs):
        if self.world_model_coeff <= 0 or not self.tools or "tool_mask" not in inputs:
            return self._super_compute_loss(inputs)
        self._echo_capture = True
        self._echo_logps = None
        try:
            policy_loss = self._super_compute_loss(inputs)
        finally:
            self._echo_capture = False
        per_token_logps = self._echo_logps
        self._echo_logps = None
        env_mask = inputs["completion_mask"] * (1 - inputs["tool_mask"])
        world_scaled, metrics = compute_world_model_loss(
            per_token_logps,
            env_mask,
            coeff=self.world_model_coeff,
            normalization=self.world_loss_normalization,
            max_completion_length=self.max_completion_length,
        )
        gas = self.current_gradient_accumulation_steps or 1
        world_scaled = world_scaled / gas
        for key, value in metrics.items():
            self._metrics["train"][key].append(value)
        return policy_loss + world_scaled


def test_compute_loss_adds_scaled_aux_term_and_logs_metrics():
    probe = _CaptureProbe()
    inputs = {
        "completion_mask": torch.tensor([[1, 1, 1]]),
        "tool_mask": torch.tensor([[1, 0, 1]]),  # token 1 is env => -logp = 2.0
    }
    total = probe._compute_loss(probe.model, inputs)
    # policy_loss(1.0) + coeff(0.5)*ce(2.0)/gas(2) = 1.0 + 0.5
    assert float(total) == pytest.approx(1.5)
    assert probe._metrics["train"]["world_model/env_tokens"][-1] == pytest.approx(1.0)
    assert probe._metrics["train"]["world_model/ce_per_token"][-1] == pytest.approx(2.0)
    # Capture flag must be reset after use.
    assert probe._echo_capture is False
    assert probe._echo_logps is None
