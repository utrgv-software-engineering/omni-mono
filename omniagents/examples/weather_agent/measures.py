from __future__ import annotations

import json
from typing import Dict, Any, List, Optional

from omniagents.core.evaluation import (
    evaluation_measure,
    parse_tool_calls,
    assert_tool_called,
    assert_no_tool_called,
    assert_tool_output_satisfies,
    assert_tool_call_sequence,
    assert_coords_propagated,
    assert_assistant_contains,
    final_assistant_text,
)
from typing_extensions import TypedDict


@evaluation_measure
def tool_hallucination(ctx) -> Dict[str, Any]:
    history: List[Dict[str, Any]] = getattr(ctx, "history", []) or []
    calls = parse_tool_calls(history)
    allowed = set(getattr(ctx, "available_tools", set()) or set())
    unknown = []
    for c in calls:
        n = c.get("name")
        if n and n not in allowed:
            unknown.append({
                "index": c.get("index"),
                "name": n,
                "arguments": c.get("args"),
                "call_id": c.get("call_id"),
            })
    total = len(calls)
    bad = len(unknown)
    return {
        "name": "tool_hallucination",
        "passed": bad == 0,
        "counts": {"total_tool_calls": total, "unknown_tool_calls": bad},
        "unknown_tool_calls": unknown,
    }


def _parse_json_maybe(value) -> Optional[dict]:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return None
    return None


@evaluation_measure
def weather_happy_path(ctx) -> Dict[str, Any]:
    history: List[Dict[str, Any]] = getattr(ctx, "history", []) or []

    ok_seq, _ = assert_tool_call_sequence(history, ["geocode", "get_weather"], ordered=True)
    if not ok_seq:
        return {"name": "weather_happy_path", "passed": False, "reason": "wrong_tool_sequence"}

    ok_gc, info_gc = assert_tool_called(history, "geocode")
    if not ok_gc or not info_gc.get("matches"):
        return {"name": "weather_happy_path", "passed": False, "reason": "no_geocode_call"}
    g = info_gc["matches"][0]
    args_g = g.get("args") or {}
    city_name = str(args_g.get("city_name", ""))
    if "berlin" not in city_name.lower():
        return {"name": "weather_happy_path", "passed": False, "reason": "geocode_city_not_berlin", "city_name": city_name}

    ok_coords, info_coords = assert_coords_propagated(history, "geocode", "get_weather", tol=1e-2)
    if not ok_coords:
        reason = info_coords.get("reason", "coords_mismatch")
        return {"name": "weather_happy_path", "passed": False, "reason": reason}

    ok_w, info_w = assert_tool_called(history, "get_weather", after=g.get("index"))
    if not ok_w or not info_w.get("matches"):
        return {"name": "weather_happy_path", "passed": False, "reason": "no_get_weather_call"}
    w = info_w["matches"][0]
    args_w = w.get("args") or {}
    temp_unit = args_w.get("temperature_unit")
    if temp_unit is not None and str(temp_unit).lower() != "celsius":
        return {"name": "weather_happy_path", "passed": False, "reason": "wrong_temperature_unit", "temperature_unit": temp_unit}

    ok_text, _ = assert_assistant_contains(history, must_include=["°C"])
    if not ok_text:
        return {"name": "weather_happy_path", "passed": False, "reason": "no_celsius_unit_in_output"}

    return {"name": "weather_happy_path", "passed": True}


@evaluation_measure
def ambiguous_city_clarification(ctx) -> Dict[str, Any]:
    history: List[Dict[str, Any]] = getattr(ctx, "history", []) or []
    ok_gc, info_gc = assert_tool_called(history, "geocode")
    if not ok_gc or not info_gc.get("matches"):
        return {"name": "ambiguous_city_clarification", "passed": False, "reason": "no_geocode_call"}
    g = info_gc["matches"][0]
    def _ambig(out: Dict[str, Any]) -> bool:
        try:
            return len(out.get("results") or []) > 1
        except Exception:
            return False
    ok_out, _ = assert_tool_output_satisfies(history, "geocode", _ambig, for_call=g)
    if not ok_out:
        return {"name": "ambiguous_city_clarification", "passed": False, "reason": "not_ambiguous"}
    ok_now, _ = assert_no_tool_called(history, "get_weather", after=g.get("index"))
    if not ok_now:
        return {"name": "ambiguous_city_clarification", "passed": False, "reason": "called_get_weather_without_clarification"}
    assistant_text = final_assistant_text(history)
    if not assistant_text:
        return {"name": "ambiguous_city_clarification", "passed": False, "reason": "no_assistant_message"}
    text_l = assistant_text.lower()
    if ("cannot find" in text_l) or ("no results" in text_l):
        return {"name": "ambiguous_city_clarification", "passed": False, "reason": "claimed_no_results"}
    mentions_scope = any(w in text_l for w in ["country", "state", "region"])
    is_question = ("?" in assistant_text) or any(w in text_l for w in ["which", "specify", "choose"])
    if not (mentions_scope and is_question):
        return {"name": "ambiguous_city_clarification", "passed": False, "reason": "no_clarifying_question"}
    return {"name": "ambiguous_city_clarification", "passed": True}


@evaluation_measure
def fahrenheit_temperature(ctx) -> Dict[str, Any]:
    history: List[Dict[str, Any]] = getattr(ctx, "history", []) or []

    ok_seq, _ = assert_tool_call_sequence(history, ["geocode", "get_weather"], ordered=True)
    if not ok_seq:
        return {"name": "fahrenheit_temperature", "passed": False, "reason": "wrong_tool_sequence"}

    ok_gc, info_gc = assert_tool_called(history, "geocode")
    if not ok_gc or not info_gc.get("matches"):
        return {"name": "fahrenheit_temperature", "passed": False, "reason": "no_geocode_call"}
    g = info_gc["matches"][0]

    ok_coords, info_coords = assert_coords_propagated(history, "geocode", "get_weather", tol=1e-2)
    if not ok_coords:
        reason = info_coords.get("reason", "coords_mismatch")
        return {"name": "fahrenheit_temperature", "passed": False, "reason": reason}

    ok_w, info_w = assert_tool_called(history, "get_weather", after=g.get("index"))
    if not ok_w or not info_w.get("matches"):
        return {"name": "fahrenheit_temperature", "passed": False, "reason": "no_get_weather_call"}
    w = info_w["matches"][0]
    args_w = w.get("args") or {}
    temp_unit = args_w.get("temperature_unit")
    if temp_unit is None or str(temp_unit).lower() != "fahrenheit":
        return {"name": "fahrenheit_temperature", "passed": False, "reason": "wrong_temperature_unit", "temperature_unit": temp_unit}

    ok_text, info_text = assert_assistant_contains(history, must_include=["°F"], must_not_include=["°C", "celsius"])
    if not ok_text:
        reason = info_text.get("reason", "output_unit_issue")
        if reason == "missing":
            reason = "no_fahrenheit_unit_in_output"
        elif reason == "forbidden":
            reason = "celsius_present_in_output"
        return {"name": "fahrenheit_temperature", "passed": False, "reason": reason}

    return {"name": "fahrenheit_temperature", "passed": True}


class _FriendlinessDecision(TypedDict):
    is_friendly: bool
    explanation: str


@evaluation_measure
def friendliness_judge_agent(ctx) -> Dict[str, Any]:
    meta = getattr(ctx, "metadata", {}) or {}
    judge = meta.get("friendliness_judge")
    if not isinstance(judge, dict):
        return {"name": "friendliness_judge_agent", "passed": False, "reason": "no_judge_result"}
    is_friend = bool(judge.get("is_friendly", False))
    return {"name": "friendliness_judge_agent", "passed": is_friend, "judge": judge}
