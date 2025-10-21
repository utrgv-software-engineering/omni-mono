#!/usr/bin/env python3
"""
Demonstration of how output_type works in OpenAI Agents SDK.

The output_type parameter controls what type of data the agent returns:
- None or str: Returns plain text responses
- dict, list, etc: Returns structured JSON responses
- Custom classes: Can use dataclasses, Pydantic models, TypedDict for structured outputs
"""

import asyncio
from typing import List, Dict
from dataclasses import dataclass
from agents import Agent, run


# Example 1: Default string output (output_type=None or str)
async def example_string_output():
    """Agent returns plain text strings."""
    agent = Agent(
        name="String Output Agent",
        instructions="You are a helpful assistant that provides brief answers.",
        model="gpt-4o-mini",
        output_type=str,  # or None for default
    )

    result = await run(agent, "What is the capital of France?")
    print(f"String output: {result.output}")
    print(f"Output type: {type(result.output)}")
    return result.output


# Example 2: Dictionary output (output_type=dict)
async def example_dict_output():
    """Agent returns structured JSON as a dictionary."""
    agent = Agent(
        name="Dict Output Agent",
        instructions="You are a helpful assistant that returns structured information about cities.",
        model="gpt-4o-mini",
        output_type=dict,
    )

    result = await run(
        agent, "Tell me about Paris - include country, population, and a fun fact"
    )
    print(f"\nDict output: {result.output}")
    print(f"Output type: {type(result.output)}")
    return result.output


# Example 3: List output (output_type=list)
async def example_list_output():
    """Agent returns a JSON array as a list."""
    agent = Agent(
        name="List Output Agent",
        instructions="You are a helpful assistant that returns information as lists.",
        model="gpt-4o-mini",
        output_type=list,
    )

    result = await run(agent, "List the top 3 tourist attractions in Paris")
    print(f"\nList output: {result.output}")
    print(f"Output type: {type(result.output)}")
    return result.output


# Example 4: Custom dataclass output
@dataclass
class CityInfo:
    name: str
    country: str
    population: int
    famous_landmarks: List[str]
    fun_fact: str


async def example_dataclass_output():
    """Agent returns structured data matching a dataclass schema."""
    agent = Agent(
        name="Dataclass Output Agent",
        instructions="You are a helpful assistant that returns detailed city information.",
        model="gpt-4o-mini",
        output_type=CityInfo,  # Agent will output JSON matching this schema
    )

    result = await run(agent, "Give me information about Tokyo")
    print(f"\nDataclass output: {result.output}")
    print(f"Output type: {type(result.output)}")
    print(f"City name: {result.output.name}")
    print(f"Famous landmarks: {result.output.famous_landmarks}")
    return result.output


# Example 5: How OmniAgents YAML config maps to output_type
def explain_yaml_mapping():
    """Explain how YAML configuration maps to Python types."""
    print("\n" + "=" * 60)
    print("HOW OUTPUT TYPES WORK IN OMNIAGENTS YAML:")
    print("=" * 60)

    yaml_to_python = {
        "str / string": "Python str - Plain text responses",
        "dict / dictionary": "Python dict - JSON object responses",
        "list / array": "Python list - JSON array responses",
        "int / integer": "Python int - Numeric integer responses",
        "float / number": "Python float - Numeric decimal responses",
        "bool / boolean": "Python bool - True/False responses",
        "any / None": "No type constraint - Agent decides format",
    }

    print("\nYAML Config → Python Type Mapping:")
    for yaml_type, description in yaml_to_python.items():
        print(f"  {yaml_type:20} → {description}")

    print("\nExample YAML configurations:")
    print(
        """
  # String output (default)
  output_type: str
  
  # Dictionary/JSON object output
  output_type: dict
  
  # List/array output
  output_type: list
  
  # No type constraint
  output_type: any
    """
    )

    print("\nLimitations in YAML:")
    print("  - Cannot define custom classes (dataclasses, Pydantic models)")
    print("  - Cannot specify complex nested types")
    print("  - For advanced schemas, use Python code instead of YAML")


async def main():
    """Run all examples."""
    print("=" * 60)
    print("OUTPUT TYPE DEMONSTRATIONS")
    print("=" * 60)

    # Run examples
    try:
        # Example 1: String
        print("\n1. STRING OUTPUT:")
        await example_string_output()

        # Example 2: Dictionary
        print("\n2. DICTIONARY OUTPUT:")
        await example_dict_output()

        # Example 3: List
        print("\n3. LIST OUTPUT:")
        await example_list_output()

        # Example 4: Dataclass (advanced - not available in YAML)
        print("\n4. DATACLASS OUTPUT (Python only):")
        await example_dataclass_output()

    except Exception as e:
        print(f"Note: Examples require OpenAI API key. Error: {e}")

    # Explain YAML mapping
    explain_yaml_mapping()


if __name__ == "__main__":
    asyncio.run(main())
