from omniagents import input_guardrail, GuardrailFunctionOutput

@input_guardrail()
def {sanitized}(context, agent, input):
    return GuardrailFunctionOutput(output_info={}, tripwire_triggered=False)
