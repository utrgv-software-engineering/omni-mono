from omniagents import output_guardrail, GuardrailFunctionOutput

@output_guardrail()
def {sanitized}(context, agent, agent_output):
    return GuardrailFunctionOutput(output_info={}, tripwire_triggered=False)
