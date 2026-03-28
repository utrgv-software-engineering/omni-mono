"""Adapts a ContentScanner provider into InputGuardrail / OutputGuardrail.

When a content_scanner provider is configured, these guardrails are
auto-prepended to the agent's guardrail lists so that all input and
output passes through the scanner.
"""

from __future__ import annotations

from typing import Any, List, Union

from omniagents.core.providers.content_scanner.base import ContentScanner


def make_scanner_input_guardrail(scanner: ContentScanner) -> Any:
    """Create an InputGuardrail that delegates to *scanner*."""
    from agents.guardrail import InputGuardrail

    async def _check_input(context, agent, input_data):
        from agents.guardrail import GuardrailFunctionOutput

        # Extract text content from input
        text = _extract_text(input_data)
        if not text:
            return GuardrailFunctionOutput(
                output_info="No text to scan", tripwire_triggered=False
            )
        result = await scanner.scan(text, direction="input")
        if not result.allowed:
            findings_summary = ", ".join(
                f.pattern_name for f in result.findings
            )
            return GuardrailFunctionOutput(
                output_info=f"Content blocked: {findings_summary}",
                tripwire_triggered=True,
            )
        if result.findings:
            findings_summary = ", ".join(
                f.pattern_name for f in result.findings
            )
            return GuardrailFunctionOutput(
                output_info=f"Sensitive data detected (allowed): {findings_summary}",
                tripwire_triggered=False,
            )
        return GuardrailFunctionOutput(
            output_info="Clean", tripwire_triggered=False
        )

    return InputGuardrail(guardrail_function=_check_input, name="content_scanner_input")


def make_scanner_output_guardrail(scanner: ContentScanner) -> Any:
    """Create an OutputGuardrail that delegates to *scanner*."""
    from agents.guardrail import OutputGuardrail

    async def _check_output(context, agent, agent_output):
        from agents.guardrail import GuardrailFunctionOutput

        text = str(agent_output) if agent_output else ""
        if not text:
            return GuardrailFunctionOutput(
                output_info="No text to scan", tripwire_triggered=False
            )
        result = await scanner.scan(text, direction="output")
        if not result.allowed:
            findings_summary = ", ".join(
                f.pattern_name for f in result.findings
            )
            return GuardrailFunctionOutput(
                output_info=f"Output blocked: {findings_summary}",
                tripwire_triggered=True,
            )
        return GuardrailFunctionOutput(
            output_info="Clean", tripwire_triggered=False
        )

    return OutputGuardrail(guardrail_function=_check_output, name="content_scanner_output")


def _extract_text(input_data: Any) -> str:
    """Best-effort extraction of text from agent input."""
    if isinstance(input_data, str):
        return input_data
    if isinstance(input_data, list):
        parts = []
        for item in input_data:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                content = item.get("content", "")
                if isinstance(content, str):
                    parts.append(content)
        return " ".join(parts)
    return str(input_data) if input_data else ""
