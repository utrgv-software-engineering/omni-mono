from omniagents import output_guardrail, GuardrailFunctionOutput

@output_guardrail()
def final_validator(context, agent, agent_output):
    return GuardrailFunctionOutput(output_info={}, tripwire_triggered=False)
