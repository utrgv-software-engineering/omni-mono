"""Reward function adapters for GRPO agent training.

This module converts omniagents @measure functions into reward functions
compatible with HuggingFace TRL's GRPOTrainer.

The key insight is that omniagents measures already return pass/fail signals,
which map directly to binary rewards (1.0 for pass, 0.0 for fail).

For agent training with tools, completions may be:
- Strings (simple text completions)
- Lists of messages (multi-turn with tool calls and results)
"""

from typing import Any, Callable, Dict, List, Optional, Union
from dataclasses import dataclass, field

from omniagents.core.evaluation.context import EvalContext, build_eval_context, Message
from omniagents.core.evaluation.registry import get_measure, _MEASURES


def _normalize_completion_to_history(
    completion: Union[str, List[Dict], Dict],
) -> List[Dict]:
    """Normalize a completion to a list of message dicts.

    TRL's GRPOTrainer with tools may provide completions as:
    - String: Simple text completion
    - List[Dict]: Multi-turn messages with tool calls/results
    - Dict: Single message dict

    TRL uses a nested format for tool calls:
        {'role': 'assistant', 'tool_calls': [{'type': 'function', 'function': {'name': 'x', 'arguments': {...}}}]}

    But omniagents expects flattened format:
        {'type': 'function_call', 'name': 'x', 'arguments': {...}}

    This function flattens tool_calls so parse_tool_calls can find them.

    Returns a list of message dicts suitable for EvalContext.
    """
    if isinstance(completion, str):
        # Simple string completion - wrap as assistant message
        return [{"role": "assistant", "content": completion, "type": "message"}]

    if isinstance(completion, dict):
        # Single message dict - check for nested tool_calls
        return _flatten_message(completion)

    if isinstance(completion, list):
        # Already a list of messages - flatten each one
        normalized = []
        for msg in completion:
            if isinstance(msg, str):
                normalized.append(
                    {"role": "assistant", "content": msg, "type": "message"}
                )
            elif isinstance(msg, dict):
                normalized.extend(_flatten_message(msg))
            else:
                normalized.append(
                    {"role": "assistant", "content": str(msg), "type": "message"}
                )
        return normalized

    # Unknown format - try to convert to string
    return [{"role": "assistant", "content": str(completion), "type": "message"}]


def _flatten_message(msg: Dict) -> List[Dict]:
    """Flatten a single message, converting TRL format to omniagents format.

    TRL tool call format:
        {'role': 'assistant', 'content': '', 'tool_calls': [
            {'type': 'function', 'function': {'name': 'geocode', 'arguments': {'city': 'London'}}}
        ]}

    TRL tool output format:
        {'role': 'tool', 'name': 'geocode', 'content': 'London, UK (lat=51.51, lon=-0.13)'}

    Converted to omniagents format:
        Tool calls:  {'type': 'function_call', 'name': 'geocode', 'arguments': {...}, 'call_id': ...}
        Tool outputs: {'type': 'function_call_output', 'call_id': ..., 'output': ...}
    """
    result = []
    role = msg.get("role")

    # Handle tool outputs (TRL uses role="tool")
    if role == "tool":
        # Convert TRL tool output to omniagents format
        flattened = {
            "type": "function_call_output",
            "call_id": msg.get("tool_call_id")
            or msg.get("name"),  # TRL may use tool_call_id or name
            "output": msg.get("content"),
        }
        result.append(flattened)
        return result

    # Extract tool_calls if present
    tool_calls = msg.get("tool_calls")

    # Add the message itself (without tool_calls to avoid confusion)
    msg_copy = {k: v for k, v in msg.items() if k != "tool_calls"}
    if msg_copy.get("role") == "assistant" and "type" not in msg_copy:
        msg_copy["type"] = "message"
    result.append(msg_copy)

    # Flatten tool_calls into separate items
    if tool_calls and isinstance(tool_calls, list):
        for i, call in enumerate(tool_calls):
            if not isinstance(call, dict):
                continue

            # Handle TRL format: {'type': 'function', 'function': {'name': ..., 'arguments': ...}}
            if call.get("type") == "function" and "function" in call:
                func = call["function"]
                flattened = {
                    "type": "function_call",
                    "name": func.get("name"),
                    "arguments": func.get("arguments"),
                    "call_id": call.get("id") or f"call_{i}",
                }
                result.append(flattened)
            # Handle already-flat format (just in case)
            elif "name" in call:
                flattened = {
                    "type": "function_call",
                    "name": call.get("name"),
                    "arguments": call.get("arguments"),
                    "call_id": call.get("call_id") or call.get("id") or f"call_{i}",
                }
                result.append(flattened)

    return result


def _extract_final_text(history: List[Dict]) -> str:
    """Extract the final assistant text from a message history.

    For agent training, the completion may include tool calls and results.
    We want the final text response from the assistant.
    """
    for msg in reversed(history):
        role = msg.get("role", "")
        if role == "assistant":
            content = msg.get("content")
            if content and isinstance(content, str):
                return content
    return ""


@dataclass
class MeasureRewardAdapter:
    """Adapts an omniagents measure to a GRPO reward function.

    This class wraps a measure function and provides the interface expected
    by TRL's GRPOTrainer. The measure is run on each completion, and the
    binary pass/fail result is converted to a 1.0/0.0 reward.

    Handles both simple string completions and multi-turn agent completions
    with tool calls.

    Attributes:
        measure_fn: The measure function to adapt
        measure_name: Name of the measure (for logging)
        pass_reward: Reward value for passing (default: 1.0)
        fail_reward: Reward value for failing (default: 0.0)
        partial_credit: If True, use score from measure extra if available

    Example:
        @measure
        def correct_answer(ctx: EvalContext):
            # ... check answer ...
            return pass_reason("Correct") or fail_reason("Wrong")

        adapter = MeasureRewardAdapter(correct_answer)
        rewards = adapter(completions, expected_answer=["42", "43", "44"])
    """

    measure_fn: Callable[[EvalContext], Dict[str, Any]]
    measure_name: str = ""
    pass_reward: float = 1.0
    fail_reward: float = 0.0
    partial_credit: bool = False

    def __post_init__(self):
        if not self.measure_name:
            self.measure_name = getattr(self.measure_fn, "_measure_name", None)
            if not self.measure_name:
                self.measure_name = getattr(self.measure_fn, "__name__", "unknown")

    @property
    def __name__(self) -> str:
        """Return measure name for TRL compatibility."""
        return self.measure_name

    def __call__(
        self,
        completions: List[Union[str, List[Dict], Dict]],
        **dataset_columns: List[Any],
    ) -> List[float]:
        """Compute rewards for a batch of completions.

        Args:
            completions: List of completions. Each can be:
                - String: Simple text completion
                - List[Dict]: Multi-turn messages with tool calls
                - Dict: Single message
            **dataset_columns: Additional columns from the dataset that will
                be passed to the measure via ctx.expect. Each value should be
                a list with the same length as completions.

        Returns:
            List of float rewards (same length as completions)
        """
        rewards = []

        for i, completion in enumerate(completions):
            # Build expect dict from dataset columns for this sample
            expect = {}
            for key, values in dataset_columns.items():
                if isinstance(values, list) and i < len(values):
                    expect[key] = values[i]
                elif not isinstance(values, list):
                    expect[key] = values

            # Build a minimal EvalContext from the completion
            ctx = self._build_context(completion, expect)

            # Run the measure
            try:
                result = self.measure_fn(ctx)
                reward = self._result_to_reward(result)
            except Exception:
                # If measure fails, give fail_reward
                reward = self.fail_reward

            rewards.append(reward)

        return rewards

    def _build_context(
        self,
        completion: Union[str, List[Dict], Dict],
        expect: Dict[str, Any],
    ) -> EvalContext:
        """Build an EvalContext from a completion.

        Handles both string completions and multi-turn message lists.
        """
        # Normalize completion to history
        history = _normalize_completion_to_history(completion)

        # Build context with expect in metadata
        ctx = build_eval_context(
            history=history,
            metadata={"scenario": {"expect": expect}},
        )

        return ctx

    def _result_to_reward(self, result: Dict[str, Any]) -> float:
        """Convert a measure result to a reward value."""
        passed = result.get("passed", False)

        if self.partial_credit:
            # Check for a score in the result
            score = result.get("score")
            if score is not None:
                try:
                    return float(score)
                except (TypeError, ValueError):
                    pass

        return self.pass_reward if passed else self.fail_reward


def measure_to_reward(
    measure: Union[str, Callable[[EvalContext], Dict[str, Any]]],
    *,
    pass_reward: float = 1.0,
    fail_reward: float = 0.0,
    partial_credit: bool = False,
) -> MeasureRewardAdapter:
    """Convert an omniagents measure to a GRPO reward function.

    This is the primary way to use omniagents evaluation measures as
    reward signals for GRPO training.

    Args:
        measure: Either a measure function or the name of a registered measure
        pass_reward: Reward for passing the measure (default: 1.0)
        fail_reward: Reward for failing the measure (default: 0.0)
        partial_credit: If True, use 'score' from measure extra if available

    Returns:
        A MeasureRewardAdapter that can be used as a reward function

    Example:
        from omniagents.notebook import measure, EvalContext, pass_reason, fail_reason
        from omniagents.training import measure_to_reward

        @measure
        def correct_math_answer(ctx: EvalContext):
            response = ctx.final_assistant_message.text if ctx.final_assistant_message else ""
            expected = ctx.expect.get('expected_answer')
            # ... grading logic ...
            if is_correct:
                return pass_reason("Correct")
            return fail_reason("Incorrect")

        # Convert to reward function
        reward_fn = measure_to_reward(correct_math_answer)

        # Use with train_grpo
        result = train_grpo(
            agent=agent,
            suite=suite,
            reward_measures=[reward_fn],  # or just ["correct_math_answer"]
        )
    """
    # Look up measure by name if string provided
    if isinstance(measure, str):
        measure_fn = get_measure(measure)
        measure_name = measure
    else:
        measure_fn = measure
        measure_name = getattr(measure, "_measure_name", None) or getattr(
            measure, "__name__", ""
        )

    return MeasureRewardAdapter(
        measure_fn=measure_fn,
        measure_name=measure_name,
        pass_reward=pass_reward,
        fail_reward=fail_reward,
        partial_credit=partial_credit,
    )


def combine_rewards(
    *rewards: Union[MeasureRewardAdapter, Callable],
    weights: Optional[List[float]] = None,
) -> Callable[[List[Union[str, List[Dict]]]], List[float]]:
    """Combine multiple reward functions into one.

    This is useful when you want to train with multiple objectives,
    such as correctness AND format compliance.

    Args:
        *rewards: Reward functions to combine
        weights: Optional weights for each reward (default: equal weights)

    Returns:
        A combined reward function

    Example:
        correctness = measure_to_reward(correct_answer)
        format_check = measure_to_reward(uses_boxed_format)

        combined = combine_rewards(correctness, format_check, weights=[0.8, 0.2])

        result = train_grpo(agent, suite, reward_measures=[combined])
    """
    if weights is None:
        weights = [1.0 / len(rewards)] * len(rewards)

    if len(weights) != len(rewards):
        raise ValueError(
            f"weights length ({len(weights)}) must match rewards length ({len(rewards)})"
        )

    def combined_reward(
        completions: List[Union[str, List[Dict]]], **kwargs
    ) -> List[float]:
        # Compute rewards from each function
        all_rewards = []
        for reward_fn in rewards:
            r = reward_fn(completions, **kwargs)
            all_rewards.append(r)

        # Combine with weights
        combined = []
        for i in range(len(completions)):
            total = sum(w * all_rewards[j][i] for j, w in enumerate(weights))
            combined.append(total)

        return combined

    return combined_reward
