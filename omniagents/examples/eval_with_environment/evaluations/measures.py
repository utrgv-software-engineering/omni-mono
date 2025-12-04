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
