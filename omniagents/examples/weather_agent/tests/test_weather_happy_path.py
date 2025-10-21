from omniagents.core.evaluation import build_eval_context, run_measures
from examples.weather_agent import measures as _measures  # register local measures


def test_weather_happy_path_passes_for_berlin_celsius():
    history = [
        {"role": "user", "content": "What is the weather in Berlin, Germany?"},
        {
            "type": "function_call",
            "name": "geocode",
            "arguments": '{"city_name": "Berlin, Germany"}',
            "call_id": "1",
        },
        {
            "type": "function_call_output",
            "call_id": "1",
            "output": '{"results": [{"name": "Berlin", "country": "DE", "latitude": 52.52, "longitude": 13.405}]}',
        },
        {
            "type": "function_call",
            "name": "get_weather",
            "arguments": '{"latitude": 52.52, "longitude": 13.405}',
            "call_id": "2",
        },
        {
            "type": "function_call_output",
            "call_id": "2",
            "output": '{"current": {"temperature_2m": 18}}',
        },
        {"role": "assistant", "content": "Currently 18 °C with light clouds."},
    ]

    ctx = build_eval_context(history=history, tools=[])
    res = run_measures(ctx, measures=["weather_happy_path"])[0]
    assert res["name"] == "weather_happy_path"
    assert res["applicable"] is True
    assert res["passed"] is True


def test_weather_happy_path_fails_without_celsius_unit():
    history = [
        {"role": "user", "content": "What is the weather in Berlin, Germany?"},
        {
            "type": "function_call",
            "name": "geocode",
            "arguments": '{"city_name": "Berlin, Germany"}',
            "call_id": "1",
        },
        {
            "type": "function_call_output",
            "call_id": "1",
            "output": '{"results": [{"name": "Berlin", "country": "DE", "latitude": 52.52, "longitude": 13.405}]}',
        },
        {
            "type": "function_call",
            "name": "get_weather",
            "arguments": '{"latitude": 52.52, "longitude": 13.405}',
            "call_id": "2",
        },
        {
            "type": "function_call_output",
            "call_id": "2",
            "output": '{"current": {"temperature_2m": 18}}',
        },
        {"role": "assistant", "content": "Currently 18 degrees with light clouds."},
    ]

    ctx = build_eval_context(history=history, tools=[])
    res = run_measures(ctx, measures=["weather_happy_path"])[0]
    assert res["name"] == "weather_happy_path"
    assert res["applicable"] is True
    assert res["passed"] is False
    assert res["reason"] == "no_celsius_unit_in_output"
