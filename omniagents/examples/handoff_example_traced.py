from __future__ import annotations

from dotenv import load_dotenv
from agents import Agent, handoff, set_default_openai_api
from omniagents import Runner, function_tool
import agentio  # Import AgentIO for tracing

# Load environment variables from .env file
load_dotenv()

# Set the default OpenAI API to use chat completions
set_default_openai_api("chat_completions")

# Initialize AgentIO tracing
# This will create a local database and launch the dashboard
agentio.init(
    project="handoff_demo",  # Group traces under this project
    name="multi_agent_handoff",  # Name for this trace
    resume="never"  # Always create a new trace
)


@function_tool
def get_current_time() -> str:
    """Return the current time as a string."""
    from datetime import datetime
    return datetime.now().strftime("%H:%M:%S")


@function_tool
def calculate(expression: str) -> str:
    """Evaluate a mathematical expression."""
    try:
        result = eval(expression)
        return f"Result: {result}"
    except:
        return "Invalid expression"


# Define specialized agents - just regular Agents
# When safe=True is set in Runner, these will automatically
# be wrapped in SafeAgent when handed off to
math_agent = Agent(
    name="Math Expert",
    instructions="You are a math expert. Help users with calculations and math problems. Be concise.",
    tools=[calculate],
    model="gpt-4o",
)

time_agent = Agent(
    name="Time Keeper",
    instructions="You provide time information. Be very brief.",
    tools=[get_current_time],
    model="gpt-4o",
)

# Main agent that hands off to specialists
def build_agent() -> Agent:
    return Agent(
        name="Main Assistant",
        instructions="""You are a helpful assistant. 
        - For math problems or calculations, hand off to the Math Expert
        - For time-related questions, hand off to the Time Keeper
        - For other questions, answer them yourself""",
        handoffs=[
            handoff(math_agent),
            handoff(time_agent),
        ],
        model="gpt-4o",
    )


def main() -> None:
    runner = Runner.from_agent(
        build_agent(),
        welcome_text="Hi! I can help with general questions, math, or time. What do you need?",
        safe=True  # Enable SafeAgent mode - automatically wraps ALL agents including handoffs
    )
    runner.main(default_mode="cli")


if __name__ == "__main__":
    main()
