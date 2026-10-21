"""
Example demonstrating the read_image tool for loading images for AI analysis.

This example shows how to use the read_image tool to load images and make them
available to the AI model for vision-based analysis.
"""

import asyncio
from agents import Agent, AgentBuilder
from omniagents.builtin.tools import read_image


async def main():
    """Run the image reading example."""

    # Create an agent with the read_image tool
    agent = AgentBuilder(
        name="Image Analyzer",
        instructions=(
            "You are an AI assistant that can analyze images. "
            "When users ask you to look at an image, use the read_image tool to load it. "
            "Then describe what you see in the image."
        ),
        tools=[read_image],
        model="gpt-4o",  # Vision-capable model
    ).build()

    # Example 1: Read and analyze a screenshot
    print("Example 1: Analyzing an image")
    print("-" * 50)

    # In a real scenario, you would have an actual image file
    # For this example, we'll show the tool invocation pattern
    print("User: Can you analyze the image at ./screenshot.png?")
    print()
    print("AI will use read_image('./screenshot.png') and then describe what it sees")
    print()

    # Example 2: Read with high detail
    print("\nExample 2: Reading with high detail")
    print("-" * 50)
    print("User: Please read diagram.png with high detail and explain it")
    print()
    print("AI will use read_image('./diagram.png', detail='high') for better analysis")
    print()

    # The tool returns both text description and the actual image:
    # [
    #   ToolOutputText(text="Image loaded from screenshot.png..."),
    #   ToolOutputImage(image_url="data:image/png;base64,...", detail="auto")
    # ]
    #
    # The LLM sees both the text and the image, allowing it to:
    # - Understand the image was loaded successfully
    # - Analyze the actual visual content
    # - Respond with detailed descriptions


if __name__ == "__main__":
    print("Read Image Tool Example")
    print("=" * 50)
    print()
    asyncio.run(main())
