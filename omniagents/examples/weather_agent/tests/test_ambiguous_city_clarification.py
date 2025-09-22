from omniagents.core.evaluation import build_eval_context, run_measures
from examples.weather_agent import measures as _measures  # register local measures


def test_ambiguous_city_clarification_passes_when_asks_question():
    history = [
        {"role": "user", "content": "Tell me the weather in Springfield."},
        {"type": "function_call", "name": "geocode", "arguments": '{"city_name": "Springfield"}', "call_id": "1"},
        {
            "type": "function_call_output",
            "call_id": "1",
            "output": '{"results": [
                {"name": "Springfield", "country": "US", "latitude": 39.8, "longitude": -89.6},
                {"name": "Springfield", "country": "US", "latitude": 44.0, "longitude": -123.0}
            ]}',
        },
        {
            "role": "assistant",
            "content": "There are multiple cities named Springfield. Could you please specify the country?",
        },
    ]

    ctx = build_eval_context(history=history, tools=[])
    res = run_measures(ctx, measures=["ambiguous_city_clarification"]) [0]
    assert res["name"] == "ambiguous_city_clarification"
    assert res["passed"] is True


def test_ambiguous_city_clarification_fails_when_calls_get_weather():
    history = [
        {"role": "user", "content": "Tell me the weather in Kansas City."},
        {"type": "function_call", "name": "geocode", "arguments": '{"city_name": "Kansas City"}', "call_id": "1"},
        {
            "type": "function_call_output",
            "call_id": "1",
            "output": '{"results": [
                {"name": "Kansas City", "country": "US", "latitude": 39.1, "longitude": -94.6},
                {"name": "Kansas City", "country": "US", "latitude": 39.1, "longitude": -94.6}
            ]}',
        },
        {"type": "function_call", "name": "get_weather", "arguments": '{"latitude": 39.1, "longitude": -94.6}', "call_id": "2"},
        {"type": "function_call_output", "call_id": "2", "output": '{"current": {"temperature_2m": 20}}'},
        {"role": "assistant", "content": "It is 20 °C."},
    ]

    ctx = build_eval_context(history=history, tools=[])
    res = run_measures(ctx, measures=["ambiguous_city_clarification"]) [0]
    assert res["name"] == "ambiguous_city_clarification"
    assert res["passed"] is False
    assert res["reason"] == "called_get_weather_without_clarification"
