from omniagents.core.evaluation import build_eval_context, run_measures
from examples.weather_agent import measures  # register local measures
from examples.weather_agent.tools.weather_tools import geocode, get_weather


def test_tool_hallucination_detects_unknown_tool():
    history = [
        {"role": "user", "content": "Weather in Paris"},
        {"type": "function_call", "name": "geocode", "arguments": '{"city_name": "Paris"}', "call_id": "1"},
        {"type": "function_call_output", "call_id": "1", "output": '{"results": [{"latitude": 48.8566, "longitude": 2.3522}]}'},
        {"type": "function_call", "name": "get_weather", "arguments": '{"latitude":48.8566,"longitude":2.3522}', "call_id": "2"},
        {"type": "function_call_output", "call_id": "2", "output": '{"current": {"temperature_2m": 20}}'},
        {"type": "function_call", "name": "nonexistent_tool", "arguments": "{}", "call_id": "3"},
    ]

    ctx = build_eval_context(history=history, tools=[geocode, get_weather])
    results = run_measures(ctx, measures=["tool_hallucination"])
    assert results and results[0]["name"] == "tool_hallucination"
    assert results[0]["counts"]["total_tool_calls"] == 3
    assert results[0]["counts"]["unknown_tool_calls"] == 1
    assert results[0]["unknown_tool_calls"][0]["name"] == "nonexistent_tool"
