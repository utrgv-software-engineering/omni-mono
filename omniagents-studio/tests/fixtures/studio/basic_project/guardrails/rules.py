from agents.guardrail import InputGuardrail, OutputGuardrail


async def _allow_input(context, agent, value):
    return {"allowed": True}


async def _allow_output(context, agent, value):
    return {"allowed": True}


input_policy = InputGuardrail(_allow_input, name="input_policy")
output_policy = OutputGuardrail(_allow_output, name="output_policy")
