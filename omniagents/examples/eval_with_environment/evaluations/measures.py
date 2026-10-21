"""Example measures for the eval_with_environment demo.

Measures are functions that evaluate agent behavior based on the
conversation history and optional environment verification.
"""

from typing import Any, Dict

from omniagents.core.evaluation import evaluation_measure
from omniagents.core.evaluation.context import EvalContext, pass_reason, fail_reason


@evaluation_measure
def booking_found(ctx: EvalContext) -> Dict[str, Any]:
    """Check if the agent found and reported booking information."""
    # Check if agent made a get_booking tool call
    booking_calls = ctx.tool_calls("get_booking")
    if not booking_calls:
        return fail_reason("Agent did not call get_booking tool")

    # Check if the final response mentions booking details
    final_text = ctx.final_assistant_message.text if ctx.final_assistant_message else ""
    if not final_text:
        return fail_reason("No final assistant response")

    # Simple heuristic: check for booking-related keywords
    keywords = ["booking", "reservation", "confirmed", "status"]
    if any(kw in final_text.lower() for kw in keywords):
        return pass_reason("Agent found and reported booking information")

    return fail_reason("Agent response did not include booking details")


@evaluation_measure
def correct_booking_id(ctx: EvalContext) -> Dict[str, Any]:
    """Check if the agent used the correct booking ID."""
    booking_calls = ctx.tool_calls("get_booking")
    if not booking_calls:
        return fail_reason("No get_booking calls made")

    # Check the arguments of the tool call
    for call in booking_calls:
        args = call.args or {}
        if isinstance(args, dict) and args.get("booking_id") == "ABC123":
            return pass_reason("Correct booking ID used")

    return fail_reason("Booking ID ABC123 not found in tool calls")


@evaluation_measure
def booking_created(ctx: EvalContext) -> Dict[str, Any]:
    """Check if a booking was successfully created."""
    create_calls = ctx.tool_calls("create_booking")
    if not create_calls:
        return fail_reason("Agent did not call create_booking tool")

    # Check the tool output for success
    for call in create_calls:
        if call.output and call.output.output_json:
            result = call.output.output_json
            if result.get("success") or result.get("booking_id"):
                return pass_reason("Booking created successfully")

    return fail_reason("Booking creation did not succeed")


@evaluation_measure
def correct_time_slot(ctx: EvalContext) -> Dict[str, Any]:
    """Check if the booking was made for the correct time slot."""
    create_calls = ctx.tool_calls("create_booking")
    if not create_calls:
        return fail_reason("No create_booking calls made")

    for call in create_calls:
        args = call.args or {}
        if isinstance(args, dict):
            # Check for 2pm booking (scenario requirement)
            time = args.get("time") or args.get("start_time") or ""
            if (
                "14:00" in str(time)
                or "2pm" in str(time).lower()
                or "2:00" in str(time)
            ):
                return pass_reason("Correct time slot (2pm) used")

    return fail_reason("Time slot 2pm not found in booking")


@evaluation_measure
def user_info_collected(ctx: EvalContext) -> Dict[str, Any]:
    """Check if the agent collected required user information."""
    create_calls = ctx.tool_calls("create_booking")
    if not create_calls:
        return fail_reason("No booking attempt made")

    for call in create_calls:
        args = call.args or {}
        if isinstance(args, dict):
            has_name = bool(args.get("name") or args.get("user_name"))
            has_email = bool(args.get("email") or args.get("user_email"))

            if has_name and has_email:
                return pass_reason("User name and email collected")
            elif has_name:
                return fail_reason("Email not collected")
            elif has_email:
                return fail_reason("Name not collected")

    return fail_reason("User information not collected")


@evaluation_measure
def clarification_requested(ctx: EvalContext) -> Dict[str, Any]:
    """Check if the agent asked for clarification from a vague user."""
    # Look for question marks in assistant messages (simple heuristic)
    questions_asked = 0
    for msg in ctx.assistant_messages:
        if "?" in msg.text:
            questions_asked += 1

    if questions_asked >= 2:
        return pass_reason(f"Agent asked {questions_asked} clarifying questions")
    elif questions_asked == 1:
        return pass_reason("Agent asked 1 clarifying question", marginal=True)
    else:
        return fail_reason("Agent did not ask clarifying questions")


@evaluation_measure
async def booking_verified_in_db(ctx: EvalContext) -> Dict[str, Any]:
    """Example measure using environment verification.

    This demonstrates how to use ctx.verify() to check state in an
    external system (database, API, etc.) via the EvalEnvironment.
    """
    # This only works when running with environment support
    try:
        result = await ctx.verify(
            {
                "booking": {
                    "exists": True,
                    "status": "confirmed",
                }
            }
        )

        if result.get("success"):
            return pass_reason("Booking verified in database")
        else:
            details = result.get("results", {})
            return fail_reason(f"Booking verification failed: {details}")

    except RuntimeError as e:
        # No environment configured
        return fail_reason(f"Cannot verify: {e}")


# ---------------------------------------------------------------------------
# Custom evaluation environment
#
# Environments whose behavior isn't expressible as configuration (this
# demo talks to a booking API) register a factory under a type name.
# measures.py is imported by the pipeline before the environment is
# built, so registering here is enough; evaluation.yml then declares
#
#     environment:
# type: booking_demo
#       api_base_url: http://localhost:3000
# ---------------------------------------------------------------------------

import uuid
from typing import Optional

from omniagents.core.eval.environment import EnvironmentContext, EvalEnvironment
from omniagents.core.eval.environment_config import register_environment_type


class DemoEvalEnvironment(EvalEnvironment):
    """Example evaluation environment for booking scenarios.

    This environment:
    1. Loads fixture data before each scenario
    2. Provides context values to tools (e.g., session_id, api_key)
    3. Sets environment variables for tools that read them directly
    4. Verifies expected state after scenario completion
    5. Cleans up test data after each scenario
    """

    def __init__(self, api_base_url: str = "http://localhost:3000", **kwargs: Any):
        self.api_base_url = api_base_url
        # In a real implementation, you might initialize an API client here
        # self.client = ApiClient(api_base_url)

    async def setup(self, scenario: Dict[str, Any]) -> EnvironmentContext:
        """Load fixtures / create test sessions before a scenario runs."""
        session_id = str(uuid.uuid4())
        fixture = scenario.get("fixture")
        # In a real implementation, you would load the fixture:
        # await self.client.load_fixture(fixture)
        return EnvironmentContext(
            session_id=session_id,
            data={
                "session_id": session_id,
                "fixture_loaded": fixture,
                # Available in spec.context during agent runs:
                "api_key": f"test_key_{session_id[:8]}",
                "resource_id": 12345,
            },
        )

    async def teardown(self, context: EnvironmentContext) -> None:
        """Clean up test data (always called, even on failure)."""
        # await self.client.cleanup_session(context.session_id)

    async def verify(
        self, context: EnvironmentContext, expected: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Verify expected state; measures call this via ctx.verify()."""
        # In a real implementation, query your API/database here.
        results = {
            key: {"matched": True, "details": "Demo verification"}
            for key in (expected or {})
        }
        return {"success": True, "results": results}

    def get_env_vars(self, context: EnvironmentContext) -> Dict[str, str]:
        """Env vars set around scenario execution for tools that read them."""
        return {
            "DEMO_API_KEY": context.data.get("api_key", ""),
            "DEMO_API_BASE_URL": self.api_base_url,
            "DEMO_SESSION_ID": context.session_id,
        }


def _build_demo_environment(
    config: Dict[str, Any], *, project_root: Optional[Any], eval_dir: Optional[Any]
) -> DemoEvalEnvironment:
    return DemoEvalEnvironment(**config)


register_environment_type("booking_demo", _build_demo_environment)
