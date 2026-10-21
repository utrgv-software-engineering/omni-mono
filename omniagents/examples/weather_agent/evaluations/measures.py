from omniagents.core.evaluation import evaluation_measure, fail_reason, pass_reason


@evaluation_measure
def always_pass(ctx):
    return {"passed": True}


@evaluation_measure
def ambiguous_location_assumption_judge_agent(ctx):
    judge = ctx.judge_result("ambiguous_location_assumption_judge")
    if not judge:
        return fail_reason("no_judge_result")
    return judge.to_result()


@evaluation_measure
def ambiguous_location_requires_clarification(ctx):
    ambiguous_call = None
    for call in ctx.tool_calls("geocode"):
        if call.has_multiple_results():
            ambiguous_call = call
            break
    if ambiguous_call is None:
        return pass_reason("geocode_not_ambiguous")
    for call in ctx.tool_calls("get_weather"):
        if call.after(ambiguous_call):
            return fail_reason("called_get_weather_after_ambiguous_geocode")
    final_message = ctx.final_assistant_message
    if final_message:
        text = final_message.text or ""
        low = text.lower()
        if "°" in text or any(
            term in low for term in ["fahrenheit", "celsius", "degree", "degrees"]
        ):
            return fail_reason("weather_details_after_ambiguous_geocode")
        if "?" in text:
            keywords = [
                "which",
                "specify",
                "clarify",
                "city",
                "location",
                "state",
                "country",
            ]
            if any(word in low for word in keywords):
                return pass_reason("asked_for_clarification")
    return pass_reason("no_weather_details")
