"""Example output guardrails for the agent."""

from agents.guardrail import output_guardrail, GuardrailFunctionOutput
from agents.run_context import RunContextWrapper
from typing import Any
import re


@output_guardrail(name="validate_response_length")
def validate_response_length(
    context: RunContextWrapper[Any], agent: Any, agent_output: Any
) -> GuardrailFunctionOutput:
    """Ensure the response is not too long."""

    MAX_LENGTH = 2000  # Maximum characters

    output_str = str(agent_output)

    if len(output_str) > MAX_LENGTH:
        return GuardrailFunctionOutput(
            output_info=f"Response too long: {len(output_str)} characters (max: {MAX_LENGTH})",
            tripwire_triggered=True,
        )

    return GuardrailFunctionOutput(
        output_info=f"Response length OK: {len(output_str)} characters",
        tripwire_triggered=False,
    )


@output_guardrail(name="check_sensitive_info")
def check_sensitive_info(
    context: RunContextWrapper[Any], agent: Any, agent_output: Any
) -> GuardrailFunctionOutput:
    """Check if the output contains sensitive information."""

    output_str = str(agent_output)

    # Check for common patterns of sensitive data
    patterns = {
        "SSN": r"\b\d{3}-\d{2}-\d{4}\b",
        "Credit Card": r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b",
        "API Key": r"\b[A-Za-z0-9]{32,}\b",  # Simple check for long alphanumeric strings
        "Email": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
    }

    for data_type, pattern in patterns.items():
        if re.search(pattern, output_str):
            return GuardrailFunctionOutput(
                output_info=f"Output may contain sensitive information: {data_type}",
                tripwire_triggered=True,
            )

    return GuardrailFunctionOutput(
        output_info="No sensitive information detected", tripwire_triggered=False
    )
