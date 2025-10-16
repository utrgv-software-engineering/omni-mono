from agents.guardrail import input_guardrail, GuardrailFunctionOutput

@input_guardrail()
def off_topic(context, agent, input):
    return GuardrailFunctionOutput(output_info={}, tripwire_triggered=False)
