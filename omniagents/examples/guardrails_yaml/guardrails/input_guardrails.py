"""Example input guardrails for the agent."""

from agents.guardrail import input_guardrail, GuardrailFunctionOutput
from agents.run_context import RunContextWrapper
from typing import Any, Union, List
from agents.items import TResponseInputItem
import time


# Simple rate limiting using a basic counter
_request_times = []
RATE_LIMIT = 5  # Max 5 requests per minute


@input_guardrail(name="check_offensive_content")
def check_offensive_content(
    context: RunContextWrapper[Any],
    agent: Any,
    input: Union[str, List[TResponseInputItem]]
) -> GuardrailFunctionOutput:
    """Check if the input contains offensive content."""
    
    # Simple check for offensive words (in production, use a proper content filter)
    offensive_words = ["hate", "violence", "harm"]
    
    input_text = str(input).lower() if isinstance(input, str) else str(input).lower()
    
    for word in offensive_words:
        if word in input_text:
            return GuardrailFunctionOutput(
                output_info=f"Input contains potentially offensive word: {word}",
                tripwire_triggered=True
            )
    
    return GuardrailFunctionOutput(
        output_info="Content check passed",
        tripwire_triggered=False
    )


@input_guardrail(name="check_rate_limit")
def check_rate_limit(
    context: RunContextWrapper[Any],
    agent: Any,
    input: Union[str, List[TResponseInputItem]]
) -> GuardrailFunctionOutput:
    """Check if the user is within rate limits."""
    
    global _request_times
    current_time = time.time()
    
    # Remove requests older than 1 minute
    _request_times = [t for t in _request_times if current_time - t < 60]
    
    if len(_request_times) >= RATE_LIMIT:
        return GuardrailFunctionOutput(
            output_info=f"Rate limit exceeded: {RATE_LIMIT} requests per minute",
            tripwire_triggered=True
        )
    
    _request_times.append(current_time)
    
    return GuardrailFunctionOutput(
        output_info=f"Rate limit check passed ({len(_request_times)}/{RATE_LIMIT})",
        tripwire_triggered=False
    )