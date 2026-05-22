"""Load a policy for eval/probe, transparently handling LoRA adapters.

A run trained with LoRA saves a PEFT *adapter* (adapter_config.json), not a full
model. This loads the base + adapter in that case, or a full model otherwise, and
sets the Qwen2.5 tool-parsing response schema.
"""

from __future__ import annotations

import json
from pathlib import Path


def load_policy(path: str, *, dtype: str = "bfloat16"):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from omniagents.core.training.grpo import QWEN25_RESPONSE_SCHEMA

    dt = getattr(torch, dtype)
    adapter_cfg = Path(path) / "adapter_config.json"
    if adapter_cfg.exists():
        from peft import PeftModel

        base = json.loads(adapter_cfg.read_text())["base_model_name_or_path"]
        tok = AutoTokenizer.from_pretrained(base)
        model = AutoModelForCausalLM.from_pretrained(base, torch_dtype=dt, device_map="auto")
        model = PeftModel.from_pretrained(model, str(path))
        schema_name = base
    else:
        tok = AutoTokenizer.from_pretrained(path)
        model = AutoModelForCausalLM.from_pretrained(path, torch_dtype=dt, device_map="auto")
        schema_name = path

    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    if not getattr(tok, "response_schema", None) and "qwen2" in str(schema_name).lower():
        tok.response_schema = QWEN25_RESPONSE_SCHEMA
    model.eval()
    return model, tok
