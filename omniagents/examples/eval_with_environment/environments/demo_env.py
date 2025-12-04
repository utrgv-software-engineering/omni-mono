"""Demo evaluation environment showing how to implement EvalEnvironment.

This example demonstrates:
- Loading fixtures before scenarios
- Injecting context into tools
- Setting environment variables for tools
- Verifying state after scenario completion
- Cleaning up after scenarios
"""

from __future__ import annotations

import uuid
from typing import Any, Dict

from omniagents.core.eval.environment import EvalEnvironment, EnvironmentContext


class DemoEvalEnvironment(EvalEnvironment):
    """Example evaluation environment for booking scenarios.

    This environment:
    1. Loads fixture data before each scenario
    2. Provides context values to tools (e.g., session_id, api_key)
    3. Sets environment variables for tools that read them directly
    4. Verifies expected state after scenario completion
    5. Cleans up test data after each scenario
    """

    def __init__(self, api_base_url: str = "http://localhost:3000", **kwargs):
        """Initialize the environment.

        Args:
            api_base_url: Base URL for the API (from config in evaluation.yml)
            **kwargs: Additional config options
        """
        self.api_base_url = api_base_url
        # In a real implementation, you might initialize an API client here
        # self.client = ApiClient(api_base_url)

    async def setup(self, scenario: Dict[str, Any]) -> EnvironmentContext:
        """Initialize environment before a scenario runs.

        This is called before each scenario. Use it to:
        - Load fixtures into a database or API
        - Create test sessions
        - Set up any required state

        Args:
            scenario: The scenario configuration dict, including:
                - name: Scenario name
                - fixture: Fixture file/identifier to load
                - user_persona: User persona config (for multi-turn)
                - measures: List of measures to run

        Returns:
            EnvironmentContext with session_id and any data needed by
            tools, measures, or teardown.
        """
        # Generate a unique session ID for this scenario run
        session_id = str(uuid.uuid4())

        # Get fixture info from scenario
        fixture = scenario.get("fixture")

        # In a real implementation, you would load the fixture
        # result = await self.client.load_fixture(fixture)

        # For demo, we simulate fixture loading
        context_data = {
            "session_id": session_id,
            "fixture_loaded": fixture,
            # Add any data that tools or measures need
            # These will be available in spec.context during agent runs
            "api_key": f"test_key_{session_id[:8]}",
            "resource_id": 12345,
        }

        return EnvironmentContext(
            session_id=session_id,
            data=context_data,
        )

    async def teardown(self, context: EnvironmentContext) -> None:
        """Clean up environment after a scenario completes.

        This is always called after a scenario, even if it failed.
        Use it to:
        - Delete test data
        - Clean up sessions
        - Reset state

        Args:
            context: The context returned by setup()
        """
        # In a real implementation, you would clean up
        # await self.client.cleanup_session(context.session_id)
        pass

    async def verify(
        self, context: EnvironmentContext, expected: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Verify expected state against actual state.

        This is called by measures that need to check database/API state.
        Use ctx.verify(expected) in your measure functions.

        Args:
            context: The context returned by setup()
            expected: Expected state specification from the measure

        Returns:
            Dict with verification results:
            {"success": bool, "results": {...details...}}

        Example expected formats:
            {"booking": {"exists": True, "status": "confirmed"}}
            {"user_info": {"email_collected": True}}
        """
        # In a real implementation, you would query your API/database
        # actual = await self.client.get_state(context.session_id)

        # For demo, we simulate verification
        results = {}
        success = True

        for key, expectations in expected.items():
            # Check each expected condition
            # results[key] = await self._verify_entity(context, key, expectations)
            results[key] = {"matched": True, "details": "Demo verification"}

        return {"success": success, "results": results}

    def get_env_vars(self, context: EnvironmentContext) -> Dict[str, str]:
        """Get environment variables to set during scenario execution.

        Some tools may read configuration from environment variables
        rather than from the runtime context. Override this method to
        specify which env vars to set.

        These are set before the scenario runs. If you need to clean them
        up, do so in teardown().

        Args:
            context: The context returned by setup()

        Returns:
            Dict mapping env var names to values
        """
        return {
            # Example: Set API credentials for tools that read from env
            "DEMO_API_KEY": context.data.get("api_key", ""),
            "DEMO_API_BASE_URL": self.api_base_url,
            "DEMO_SESSION_ID": context.session_id,
        }

    def get_tool_context(self, context: EnvironmentContext) -> Dict[str, Any]:
        """Get context values to inject into tool calls.

        These values are merged into spec.context and available to tools
        via the RunContextWrapper.

        Args:
            context: The context returned by setup()

        Returns:
            Dict of values to merge into tool call context
        """
        # By default, returns all context data
        # Override if you need to filter or transform values
        return dict(context.data)
