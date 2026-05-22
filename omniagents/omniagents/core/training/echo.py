"""ECHO: an auxiliary world-modeling loss for GRPO agent training.

ECHO (Environment Cross-entropy Hybrid Objective) augments the GRPO
policy-gradient loss with an on-policy cross-entropy loss over the
*environment-observation tokens* in each rollout -- the tokens the model did
not generate but that appear in its context (here, tool-result messages).
Predicting these "for free" teaches the policy a world model of its tools.

This is the omniagents port of the technique from the ECHO paper / SkyRL
implementation. The key observation is that TRL's :class:`GRPOTrainer`, when
training with tools, already produces exactly the structure ECHO needs:

* ``_tool_call_loop`` runs the multi-turn rollout and builds a ``tool_mask``
  where ``1`` marks model-generated tokens and ``0`` marks tool-result tokens.
* ``_compute_loss`` masks the policy-gradient loss with
  ``completion_mask * tool_mask`` -- i.e. tool-result tokens are *excluded*
  from the RL loss -- after computing per-token log-probs over *all*
  completion tokens (the tool-result log-probs are computed and discarded).

So the ECHO environment mask is ``completion_mask * (1 - tool_mask)``, and the
auxiliary loss is ``-(per_token_logps * env_mask)`` normalized -- reusing the
log-probs from the policy forward pass, with no extra forward.

Setting ``world_model_coeff = 0.0`` recovers vanilla GRPO. A positive value
enables ECHO. ECHO is a no-op when the agent has no tools (no environment
tokens to predict).

Supported on ``trl>=0.28,<0.30`` with ``transformers>=5.0.0`` (tool-using GRPO
requires transformers 5; trl 0.27 is incompatible with transformers 5's
``import_utils`` change). The integration depends on a small, stable set of TRL
internals (the ``inputs`` keys ``prompt_ids``/``prompt_mask``/``completion_ids``/
``completion_mask``/``tool_mask`` and the ``_get_per_token_logps_and_entropies``
method, called grad-enabled exactly once inside ``_compute_loss``), not on the
body of ``_compute_loss``; a soft version warning fires on other versions.
"""

from __future__ import annotations

import warnings
from typing import Any, Dict, Optional, Tuple

import torch
from trl import GRPOTrainer as TRLGRPOTrainer

_SUPPORTED_TRL = {(0, 28), (0, 29)}


def _check_trl_version() -> None:
    try:
        import trl

        parts = trl.__version__.split(".")
        major_minor = (int(parts[0]), int(parts[1]))
    except Exception:  # pragma: no cover - defensive
        return
    if major_minor not in _SUPPORTED_TRL:
        supported = ", ".join(f"{a}.{b}.x" for a, b in sorted(_SUPPORTED_TRL))
        warnings.warn(
            f"EchoGRPOTrainer is verified against trl {supported} but trl {trl.__version__} "
            "is installed. The ECHO auxiliary loss relies on TRL's tool_mask / completion_mask "
            "inputs and the _get_per_token_logps_and_entropies method, and may need updating if "
            "these change.",
            RuntimeWarning,
            stacklevel=2,
        )


def compute_world_model_loss(
    per_token_logps: torch.Tensor,
    env_mask: torch.Tensor,
    *,
    coeff: float,
    normalization: str = "selected_tokens",
    max_completion_length: Optional[int] = None,
) -> Tuple[torch.Tensor, Dict[str, float]]:
    """Cross-entropy loss over environment (tool-result) tokens.

    Args:
        per_token_logps: ``(B, T)`` log-probs of each completion token under the
            current policy (teacher-forced), as computed by TRL's forward pass.
        env_mask: ``(B, T)`` mask, ``1`` on environment/tool-result tokens that
            the policy loss ignores and that ECHO predicts, ``0`` elsewhere.
        coeff: Scaling coefficient for the auxiliary loss (``world_model_coeff``).
        normalization: How to normalize the summed cross-entropy:

            * ``"selected_tokens"`` -- mean over selected env tokens (token-mean).
            * ``"sequence_mean"`` -- per-sequence token-mean, then batch-mean
              (matches GRPO ``loss_type="grpo"`` style).
            * ``"seq_mean_token_sum_norm"`` -- sum divided by
              ``batch * max_completion_length`` (matches ``dr_grpo`` style;
              requires ``max_completion_length``).
        max_completion_length: Required for ``"seq_mean_token_sum_norm"``.

    Returns:
        ``(scaled_loss, metrics)``. ``scaled_loss`` is a differentiable scalar
        already multiplied by ``coeff`` (zero-valued, gradient-safe, when no env
        tokens are selected). ``metrics`` are detached python floats for logging.
    """
    env_mask = env_mask.to(per_token_logps.dtype)
    world_ce = -per_token_logps  # (B, T): CE of predicting each env token
    selected = env_mask.sum()

    if float(selected) <= 0.0:
        # Keep a connection to the graph so backward() is well-defined.
        zero = per_token_logps.sum() * 0.0
        return coeff * zero, {
            "world_model/loss": 0.0,
            "world_model/loss_unscaled": 0.0,
            "world_model/ce_per_token": 0.0,
            "world_model/env_tokens": 0.0,
        }

    if normalization == "sequence_mean":
        per_seq_denom = env_mask.sum(dim=-1).clamp(min=1.0)
        per_seq = (world_ce * env_mask).sum(dim=-1) / per_seq_denom
        unscaled = per_seq.mean()
    elif normalization == "seq_mean_token_sum_norm":
        if max_completion_length is None:
            raise ValueError(
                "max_completion_length is required for world_loss_normalization="
                "'seq_mean_token_sum_norm'."
            )
        unscaled = (world_ce * env_mask).sum() / (per_token_logps.size(0) * max_completion_length)
    elif normalization == "selected_tokens":
        unscaled = (world_ce * env_mask).sum() / selected.clamp(min=1.0)
    else:
        raise ValueError(
            f"Unknown world_loss_normalization={normalization!r}. Expected one of "
            "'selected_tokens', 'sequence_mean', 'seq_mean_token_sum_norm'."
        )

    scaled = coeff * unscaled
    ce_per_token = (world_ce * env_mask).sum() / selected.clamp(min=1.0)
    metrics = {
        "world_model/loss": float(scaled.detach()),
        "world_model/loss_unscaled": float(unscaled.detach()),
        "world_model/ce_per_token": float(ce_per_token.detach()),
        "world_model/env_tokens": float(selected.detach()),
    }
    return scaled, metrics


class EchoGRPOTrainer(TRLGRPOTrainer):
    """TRL ``GRPOTrainer`` plus the ECHO auxiliary world-modeling loss.

    The auxiliary loss is added to the policy-gradient loss in
    :meth:`_compute_loss`, reusing the per-token log-probs that TRL already
    computes (and otherwise discards over tool-result tokens). No extra forward
    pass is performed.

    Extra constructor kwargs (everything else is forwarded to TRL):
        world_model_coeff: Coefficient for the auxiliary loss. ``0.0`` disables
            ECHO (the trainer then behaves exactly like TRL's ``GRPOTrainer``).
        world_loss_normalization: See :func:`compute_world_model_loss`.
    """

    def __init__(
        self,
        *args: Any,
        world_model_coeff: float = 0.0,
        world_loss_normalization: str = "selected_tokens",
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.world_model_coeff = float(world_model_coeff)
        self.world_loss_normalization = world_loss_normalization
        # Capture state for grabbing the loss-path log-probs (see below).
        self._echo_capture = False
        self._echo_logps: Optional[torch.Tensor] = None

        if self.world_model_coeff > 0:
            _check_trl_version()
            if getattr(self, "use_liger_kernel", False):
                raise ValueError(
                    "ECHO world-model loss (world_model_coeff > 0) is not supported with "
                    "use_liger_kernel=True, which bypasses _compute_loss. Disable the Liger "
                    "kernel or set world_model_coeff=0.0."
                )

    def _get_per_token_logps_and_entropies(self, *args: Any, **kwargs: Any):
        # TRL calls this both during generation (under torch.no_grad, for old/ref
        # log-probs) and once inside _compute_loss (grad-enabled, current policy).
        # We only want the latter, which we mark with self._echo_capture.
        logps, entropies = super()._get_per_token_logps_and_entropies(*args, **kwargs)
        if self._echo_capture:
            self._echo_logps = logps
        return logps, entropies

    def _compute_loss(self, model, inputs):
        # Fall back to plain GRPO when ECHO is off, the agent has no tools, or
        # TRL did not provide a tool_mask (nothing to predict).
        if self.world_model_coeff <= 0 or not self.tools or "tool_mask" not in inputs:
            return super()._compute_loss(model, inputs)

        self._echo_capture = True
        self._echo_logps = None
        try:
            policy_loss = super()._compute_loss(model, inputs)
        finally:
            self._echo_capture = False

        per_token_logps = self._echo_logps
        self._echo_logps = None
        if per_token_logps is None:  # pragma: no cover - defensive
            return policy_loss

        # Environment tokens = completion tokens that are tool results.
        env_mask = inputs["completion_mask"] * (1 - inputs["tool_mask"])

        world_scaled, metrics = compute_world_model_loss(
            per_token_logps,
            env_mask,
            coeff=self.world_model_coeff,
            normalization=self.world_loss_normalization,
            max_completion_length=self.max_completion_length,
        )
        # Match the gradient-accumulation scaling TRL applies to the policy loss
        # (grpo/bnpo/dr_grpo divide by current_gradient_accumulation_steps).
        gas = getattr(self, "current_gradient_accumulation_steps", 1) or 1
        world_scaled = world_scaled / gas

        mode = "train" if self.model.training else "eval"
        for key, value in metrics.items():
            self._metrics[mode][key].append(value)

        return policy_loss + world_scaled
