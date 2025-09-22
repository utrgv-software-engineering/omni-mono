from __future__ import annotations

from agents import Agent
from omniagents import Runner
from examples.weather_agent.tools.weather_tools import geocode, get_weather


INSTRUCTIONS = (
    "You are a helpful weather assistant."
    " When asked for weather in a city, first geocode the city name,"
    " then fetch current weather for the selected coordinates using get_weather."
    " If the city name is ambiguous, ask the user to choose by country."
    " Keep answers concise and include units."
)


def build_agent() -> Agent:
    return Agent(
        name="Weather Agent",
        instructions=INSTRUCTIONS,
        tools=[geocode, get_weather],
        model="gpt-4o",
    )


def main() -> None:
    runner = Runner.from_agent(
        build_agent(),
        welcome_text="Ask for weather, e.g., 'Weather in Paris'",
        safe=False,
    )
    runner.main(default_mode="cli")


if __name__ == "__main__":
    main()
