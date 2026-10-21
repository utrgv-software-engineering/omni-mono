"""Dataset conversion utilities for GRPO agent training.

This module converts omniagents EvalSuite/TestCase structures to HuggingFace
Datasets suitable for use with TRL's GRPOTrainer.

For agent training with tools, the dataset uses conversational format:
- "prompt" column contains a list of messages (system + user)
- Additional columns for expect values (used by reward functions)
"""

from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from datasets import Dataset
    from omniagents.notebook.evaluation import EvalSuite, TestCase


def eval_suite_to_hf_dataset(
    suite: "EvalSuite",
    *,
    system_prompt: Optional[str] = None,
) -> "Dataset":
    """Convert an EvalSuite to a HuggingFace Dataset for GRPO agent training.

    This function creates a dataset with conversational format suitable for
    TRL's GRPOTrainer with tools:
    - "prompt": List of messages [{"role": "system", ...}, {"role": "user", ...}]
    - One column for each key in the expect dict

    Args:
        suite: The EvalSuite to convert
        system_prompt: Optional system prompt (typically from agent.instructions)

    Returns:
        HuggingFace Dataset with prompt and expect columns

    Example:
        from omniagents.notebook import EvalSuite
        from omniagents.training import eval_suite_to_hf_dataset

        suite = EvalSuite.from_records(
            math_data[:100],
            input_fn=lambda p: f"Question: {p['problem']}",
            expect_fn=lambda p: {'expected_answer': p['answer']},
        )

        # Convert to HF dataset with system prompt
        dataset = eval_suite_to_hf_dataset(
            suite,
            system_prompt="You are a math assistant. Use the calculator tool.",
        )
        # Dataset has columns: ["prompt", "expected_answer"]
        # Where "prompt" is a list of message dicts
    """
    try:
        from datasets import Dataset
    except ImportError:
        raise ImportError(
            "datasets library is required. Install with: pip install datasets"
        )

    return eval_cases_to_hf_dataset(
        suite.cases,
        system_prompt=system_prompt,
    )


def eval_cases_to_hf_dataset(
    cases: List["TestCase"],
    *,
    system_prompt: Optional[str] = None,
) -> "Dataset":
    """Convert a list of TestCases to a HuggingFace Dataset.

    Creates conversational format suitable for TRL agent training.

    Args:
        cases: List of TestCase objects
        system_prompt: Optional system prompt to prepend

    Returns:
        HuggingFace Dataset with prompt and expect columns
    """
    try:
        from datasets import Dataset
    except ImportError:
        raise ImportError(
            "datasets library is required. Install with: pip install datasets"
        )

    # Collect all unique expect keys across all cases
    all_expect_keys: set[str] = set()
    for case in cases:
        if case.expect:
            all_expect_keys.update(case.expect.keys())

    # Build data dict
    data: Dict[str, List[Any]] = {"prompt": []}
    for key in all_expect_keys:
        data[key] = []

    # Process each case
    for case in cases:
        # Skip multi-turn cases (prompts/goal) - they don't have single input
        if case.input is None:
            continue

        # Build conversational prompt
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": case.input})

        data["prompt"].append(messages)

        # Add expect values
        expect = case.expect or {}
        for key in all_expect_keys:
            data[key].append(expect.get(key))

    return Dataset.from_dict(data)


def records_to_hf_dataset(
    records: List[Dict[str, Any]],
    *,
    input_fn: Callable[[Dict[str, Any]], str],
    expect_fn: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
    system_prompt: Optional[str] = None,
) -> "Dataset":
    """Convert raw records directly to a HuggingFace Dataset.

    This is a convenience function that skips the EvalSuite intermediate
    representation when you just need a dataset for training.

    Args:
        records: List of raw data dicts
        input_fn: Function to extract prompt from each record
        expect_fn: Function to extract expect dict from each record
        system_prompt: Optional system prompt to prepend

    Returns:
        HuggingFace Dataset with prompt and expect columns

    Example:
        # Direct conversion without EvalSuite
        dataset = records_to_hf_dataset(
            math_data[:100],
            input_fn=lambda p: f"Question: {p['problem']}",
            expect_fn=lambda p: {'expected_answer': p['answer']},
            system_prompt="You are a math assistant.",
        )
    """
    try:
        from datasets import Dataset
    except ImportError:
        raise ImportError(
            "datasets library is required. Install with: pip install datasets"
        )

    # First pass: collect all expect keys
    all_expect_keys: set[str] = set()
    if expect_fn:
        for record in records:
            expect = expect_fn(record)
            if expect:
                all_expect_keys.update(expect.keys())

    # Build data dict
    data: Dict[str, List[Any]] = {"prompt": []}
    for key in all_expect_keys:
        data[key] = []

    # Process each record
    for record in records:
        # Extract user message
        user_content = input_fn(record)

        # Build conversational prompt
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_content})

        data["prompt"].append(messages)

        # Add expect values
        if expect_fn:
            expect = expect_fn(record) or {}
            for key in all_expect_keys:
                data[key].append(expect.get(key))

    return Dataset.from_dict(data)
