"""Simulated user for multi-turn evaluation scenarios.

This module provides the SimulatedUser class that role-plays a user
interacting with an agent to achieve a goal. It uses the agents library
just like other evaluation agents (judges, optimizers, etc.).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from agents import Agent, Runner

from omniagents.core.utils.jinja import render_template

__all__ = ["SimulatedUser", "UserPersona", "COMPLETION_SIGNALS"]

# Sentinel to detect when user signals completion
COMPLETION_SIGNALS = ["[DONE]", "[GOAL_ACHIEVED]", "[GIVE_UP]"]


@dataclass
class UserPersona:
    """Configuration for a simulated user."""

    goal: str
    """What the user is trying to accomplish."""

    info: Dict[str, Any] = field(default_factory=dict)
    """User info (name, email, behavior, etc.) - all passed to system prompt."""

    initial_message: Optional[str] = None
    """Optional specific first message. If None, generated from goal."""

    instructions: Optional[str] = None
    """Custom instructions. If provided, replaces default system prompt."""

    @classmethod
    def from_scenario(
        cls, scenario: Dict[str, Any], base_dir: Optional[Path] = None
    ) -> "UserPersona":
        """Create UserPersona from scenario config.

        Expects:
            scenario.goal: str (required)
            scenario.persona: dict (optional) - all fields become info
            scenario.initial_message: str (optional)
            scenario.instructions: str (optional) - custom system prompt
            scenario.instructions_file: str (optional) - path to instructions file
        """
        instructions = scenario.get("instructions")
        if not instructions and scenario.get("instructions_file") and base_dir:
            instructions_path = base_dir / scenario["instructions_file"]
            if instructions_path.is_file():
                instructions = instructions_path.read_text(encoding="utf-8")

        return cls(
            goal=scenario["goal"],
            info=dict(scenario.get("persona", {})),
            initial_message=scenario.get("initial_message"),
            instructions=instructions,
        )


def build_user_system_prompt(persona: UserPersona, agent_description: str) -> str:
    """Build the system prompt for the simulated user LLM.

    Args:
        persona: The user persona configuration.
        agent_description: Description of the agent (from spec.description or spec.name).
    """
    info_lines = []
    for key, value in persona.info.items():
        label = key.replace("_", " ").title()
        info_lines.append(f"- {label}: {value}")
    info_block = (
        "\n".join(info_lines) if info_lines else "- No specific information provided"
    )

    if persona.instructions:
        return render_template(
            persona.instructions,
            goal=persona.goal,
            info=persona.info,
            info_block=info_block,
            agent_description=agent_description,
        )

    return f"""You are a simulated user interacting with {agent_description}.

## Your Goal
{persona.goal}

## Your Information
{info_block}

## Instructions
1. Respond naturally to the assistant's questions and messages
2. Provide your information when asked (based on what you know above)
3. Stay in character throughout the conversation
4. If the assistant asks for confirmation of correct details, confirm them
5. If the assistant provides incorrect information, politely correct them

## Completion
- When your goal has been achieved (e.g., task confirmed), respond with "[DONE]" at the end of your message
- If you've been trying for several turns and the assistant cannot help, respond with "[GIVE_UP]" at the end
- Do NOT say [DONE] until you have received confirmation that your goal was accomplished

## Important
- You are the USER, not the assistant
- Keep responses concise and natural
- Don't explain what you're doing, just do it
"""


class SimulatedUser:
    """A simulated user that interacts with an agent to achieve a goal.

    Uses an agents.Agent to generate realistic user responses based on a persona.
    """

    def __init__(
        self,
        persona: UserPersona,
        agent_description: str,
        model: str = "gpt-4.1",
    ):
        self.persona = persona
        self.agent_description = agent_description
        self.model = model
        self._agent: Optional[Agent] = None
        self._completed = False
        self._completion_reason: Optional[str] = None

    def _get_agent(self) -> Agent:
        """Get or create the underlying Agent."""
        if self._agent is None:
            self._agent = Agent(
                name="SimulatedUser",
                instructions=build_user_system_prompt(
                    self.persona, self.agent_description
                ),
                model=self.model,
            )
        return self._agent

    @property
    def completed(self) -> bool:
        """Whether the user has signaled completion."""
        return self._completed

    @property
    def completion_reason(self) -> Optional[str]:
        """Why the user completed: 'done', 'give_up', or None."""
        return self._completion_reason

    async def get_initial_message(self) -> str:
        """Get the user's first message to start the conversation.

        Returns either the configured initial_message or generates one from the goal.
        """
        if self.persona.initial_message:
            return self.persona.initial_message

        # Generate an initial message based on the goal
        prompt = f"""Based on your goal, write a natural opening message to start the conversation.
Your goal: {self.persona.goal}

Write only the message, nothing else. Be natural and conversational."""

        result = await Runner.run(self._get_agent(), prompt, max_turns=1)
        return str(result.final_output or "")

    async def respond(self, assistant_message: str) -> str:
        """Generate the user's response to the assistant's message.

        Args:
            assistant_message: The assistant's latest message.

        Returns:
            The user's response text.
        """
        if self._completed:
            return "[DONE] (already completed)"

        # The prompt is just the assistant's message - the agent has instructions
        result = await Runner.run(self._get_agent(), assistant_message, max_turns=1)
        response = str(result.final_output or "")

        # Check for completion signals
        response_upper = response.upper()
        for signal in COMPLETION_SIGNALS:
            if signal in response_upper:
                self._completed = True
                if "DONE" in signal or "ACHIEVED" in signal:
                    self._completion_reason = "done"
                else:
                    self._completion_reason = "give_up"
                break

        return response
