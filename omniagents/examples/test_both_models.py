#!/usr/bin/env python3
"""
Test AgentIO with both OpenAIChatCompletionsModel and OpenAIResponsesModel
to ensure all trace data is properly captured.
"""

import asyncio
import os
from pathlib import Path
from dotenv import load_dotenv
from openai import AsyncOpenAI
from agents import Agent
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from agents.models.openai_responses import OpenAIResponsesModel
import agentio

# Load .env file
load_dotenv()

# Initialize AgentIO
agentio.init(project="model_test", name="both_models_test")


async def test_chat_completions_model():
    """Test with OpenAIChatCompletionsModel"""
    print("\n=== Testing OpenAIChatCompletionsModel ===")

    client = AsyncOpenAI()
    model = OpenAIChatCompletionsModel(model="gpt-4o-mini", openai_client=client)

    agent = Agent(
        model=model,
        name="chat_completions_agent",
        instructions="You are a helpful assistant using the Chat Completions API.",
    )

    # Test basic response
    response = await agent.run("What is 2+2? Answer in one word.")
    print(f"Response: {response.content}")

    # Test with tools
    def add_numbers(a: int, b: int) -> int:
        """Add two numbers"""
        return a + b

    agent_with_tools = Agent(
        model=model,
        name="chat_completions_with_tools",
        instructions="You are a math assistant. Use the add_numbers tool when asked to add.",
        tools=[add_numbers],
    )

    response = await agent_with_tools.run("What is 15 plus 27?")
    print(f"Response with tools: {response.content}")


async def test_responses_model():
    """Test with OpenAIResponsesModel"""
    print("\n=== Testing OpenAIResponsesModel ===")

    client = AsyncOpenAI()
    model = OpenAIResponsesModel(model="gpt-4o-mini", openai_client=client)

    agent = Agent(
        model=model,
        name="responses_agent",
        instructions="You are a helpful assistant using the Responses API.",
    )

    # Test basic response
    response = await agent.run("What is the capital of France? Answer in one word.")
    print(f"Response: {response.content}")

    # Test with tools
    def multiply_numbers(a: int, b: int) -> int:
        """Multiply two numbers"""
        return a * b

    agent_with_tools = Agent(
        model=model,
        name="responses_with_tools",
        instructions="You are a math assistant. Use the multiply_numbers tool when asked to multiply.",
        tools=[multiply_numbers],
    )

    response = await agent_with_tools.run("What is 8 times 7?")
    print(f"Response with tools: {response.content}")

    # Test streaming (Responses API specific feature)
    print("\nTesting streaming with Responses API:")
    async for chunk in agent.stream("Count from 1 to 5 slowly."):
        if chunk.content:
            print(chunk.content, end="", flush=True)
    print()


async def test_complex_scenario():
    """Test a more complex scenario with nested agents"""
    print("\n=== Testing Complex Scenario ===")

    client = AsyncOpenAI()

    # Create agents with different models
    planner = Agent(
        model=OpenAIChatCompletionsModel("gpt-4o-mini", client),
        name="planner_agent",
        instructions="You plan tasks and delegate to other agents.",
    )

    executor = Agent(
        model=OpenAIResponsesModel("gpt-4o-mini", client),
        name="executor_agent",
        instructions="You execute the planned tasks.",
    )

    # Simulate a workflow
    plan = await planner.run("Plan how to write a hello world program in Python")
    print(f"Plan: {plan.content[:100]}...")

    execution = await executor.run(f"Execute this plan: {plan.content}")
    print(f"Execution: {execution.content[:100]}...")


async def main():
    """Run all tests"""
    print("🚀 Starting AgentIO trace capture tests for both models")
    print("=" * 60)

    try:
        # Test Chat Completions Model
        await test_chat_completions_model()

        # Test Responses Model
        await test_responses_model()

        # Test complex scenario
        await test_complex_scenario()

        print("\n" + "=" * 60)
        print("✅ All tests completed successfully!")
        print("\n📊 Check the traces:")
        print("  - SQLite DB: ~/.omniagents/traces/model_test.db")
        print("  - View in UI: http://localhost:7860 (if server is running)")
        print("\n🔍 The traces should contain:")
        print("  - Generation spans with model config and usage")
        print("  - Function spans for tool calls")
        print("  - Agent spans with proper hierarchy")
        print("  - Input/output data for both model types")
        print("  - Response IDs (for Responses API)")

    except Exception as e:
        print(f"\n❌ Error during testing: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    # Check for API key
    if not os.environ.get("OPENAI_API_KEY"):
        print("⚠️  Warning: OPENAI_API_KEY not set. Tests will fail.")
        print("Set it with: export OPENAI_API_KEY=your-key-here")
        exit(1)

    asyncio.run(main())
