"""Notebook-friendly APIs for omniagents.

This module provides a simplified, progressive API for working with agents
in Jupyter notebooks. It wraps the core evaluation primitives with a
DataFrame-native, visually rich interface.

Example usage:
    from omniagents.notebook import evaluate, EvalSuite, measure

    # Single-turn evaluation
    result = await evaluate(agent, "What's the weather in Paris?")

    # With expectations
    result = await evaluate(agent, "Weather in Paris?",
                            expect={"tool_called": "get_weather"})

    # Multi-turn scripted (sequential prompts with history)
    result = await evaluate(agent,
        prompts=["Book a room", "Tomorrow 2pm", "Confirm"],
        expect={"tool_called": "create_booking"})

    # Multi-turn synthetic (LLM role-plays as user)
    result = await evaluate(agent,
        goal="Book a meeting room for tomorrow at 2pm",
        persona={"name": "Alice", "email": "alice@example.com"})

    # For multiple test cases, use EvalSuite
    suite = EvalSuite("Weather Tests")
    suite.add("basic", "Weather in Paris?", expect={"tool_called": "get_weather"})
    suite.add("booking", prompts=["Book a room", ...], expect={...})
    results = await suite.run(agent)
"""

from .evaluation import (
    # Main function
    evaluate,
    # Result types
    EvalResult,
    Turn,
    # Test suites
    EvalSuite,
    EvalSuiteResults,
    TestCase,
    # Measures
    MeasureResult,
    ExpectationResult,
    measure,
    check_expectations,
    check_expectations_async,
    # Judges
    JudgeResult,
    Judge,
    get_judge,
    run_judges,
    # Multi-turn
    SyntheticUser,
    # Environment support
    EvalEnvironment,
    EnvironmentContext,
    NoOpEnvironment,
)

__all__ = [
    # Main function
    "evaluate",
    # Result types
    "EvalResult",
    "Turn",
    # Test suites
    "EvalSuite",
    "EvalSuiteResults",
    "TestCase",
    # Measures
    "MeasureResult",
    "ExpectationResult",
    "measure",
    "check_expectations",
    "check_expectations_async",
    # Judges
    "JudgeResult",
    "Judge",
    "get_judge",
    "run_judges",
    # Multi-turn
    "SyntheticUser",
    # Environment support
    "EvalEnvironment",
    "EnvironmentContext",
    "NoOpEnvironment",
]
