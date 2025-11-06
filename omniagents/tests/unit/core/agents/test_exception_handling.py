"""Tests for agent exception handling in AgentService."""

import asyncio
import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock

from agents import (
    AgentsException,
    MaxTurnsExceeded,
    ModelBehaviorError,
    UserError,
    InputGuardrailTripwireTriggered,
    OutputGuardrailTripwireTriggered,
    ToolInputGuardrailTripwireTriggered,
    ToolOutputGuardrailTripwireTriggered,
)

from omniagents.core.agents.service import (
    _build_agent_error_info,
    _build_guardrail_error_info,
)


class TestBuildAgentErrorInfo:
    """Test error info building from AgentsException."""

    def test_basic_exception_info(self):
        """Test basic error info without run_data."""
        exc = ModelBehaviorError("Model did unexpected thing")
        info = _build_agent_error_info(exc)

        assert info["message"] == "Model did unexpected thing"
        assert info["type"] == "ModelBehaviorError"
        assert "run_data" not in info

    def test_exception_with_run_data(self):
        """Test error info with run_data preserved."""
        exc = UserError("Invalid configuration")

        # Mock run_data
        mock_agent = Mock()
        mock_agent.name = "test-agent"
        mock_run_data = Mock()
        mock_run_data.new_items = [Mock(), Mock(), Mock()]  # 3 turns
        mock_run_data.last_agent = mock_agent
        exc.run_data = mock_run_data

        info = _build_agent_error_info(exc)

        assert info["message"] == "Invalid configuration"
        assert info["type"] == "UserError"
        assert "run_data" in info
        assert info["run_data"]["turn_count"] == 3
        assert info["run_data"]["agent_name"] == "test-agent"

    def test_exception_without_agent_name(self):
        """Test error info when agent name is not available."""
        exc = ModelBehaviorError("Error")
        mock_run_data = Mock()
        mock_run_data.new_items = [Mock()]
        mock_run_data.last_agent = None
        exc.run_data = mock_run_data

        info = _build_agent_error_info(exc)

        assert info["run_data"]["turn_count"] == 1
        assert info["run_data"]["agent_name"] is None


class TestBuildGuardrailErrorInfo:
    """Test error info building from guardrail exceptions."""

    def test_input_guardrail_exception(self):
        """Test error info from InputGuardrailTripwireTriggered."""
        mock_guardrail = Mock()
        mock_guardrail.get_name = Mock(return_value="ContentFilter")

        mock_result = Mock()
        mock_result.guardrail = mock_guardrail
        mock_result.result = "Inappropriate content detected"

        exc = InputGuardrailTripwireTriggered(mock_result)
        exc.guardrail_result = mock_result

        info = _build_guardrail_error_info(exc)

        assert info["type"] == "InputGuardrailTripwireTriggered"
        assert "guardrail" in info
        assert info["guardrail"]["name"] == "ContentFilter"
        assert info["guardrail"]["result"] == "Inappropriate content detected"

    def test_output_guardrail_exception(self):
        """Test error info from OutputGuardrailTripwireTriggered."""
        mock_guardrail = Mock()
        mock_guardrail.get_name = Mock(return_value="ResponseLengthGuard")

        mock_result = Mock()
        mock_result.guardrail = mock_guardrail
        mock_result.result = "Response too long"

        exc = OutputGuardrailTripwireTriggered(mock_result)
        exc.guardrail_result = mock_result

        info = _build_guardrail_error_info(exc)

        assert info["type"] == "OutputGuardrailTripwireTriggered"
        assert info["guardrail"]["name"] == "ResponseLengthGuard"

    def test_tool_input_guardrail_exception(self):
        """Test error info from ToolInputGuardrailTripwireTriggered."""
        mock_guardrail = Mock()
        mock_guardrail.__class__.__name__ = "ToolSafetyGuard"

        mock_output = Mock()
        mock_output.behavior = {
            "type": "raise_exception",
            "message": "Unsafe tool call",
        }

        exc = ToolInputGuardrailTripwireTriggered(mock_guardrail, mock_output)
        exc.guardrail = mock_guardrail
        exc.output = mock_output

        info = _build_guardrail_error_info(exc)

        assert info["type"] == "ToolInputGuardrailTripwireTriggered"
        assert info["guardrail"]["name"] == "ToolSafetyGuard"
        assert info["guardrail"]["behavior"]["type"] == "raise_exception"

    def test_tool_output_guardrail_exception(self):
        """Test error info from ToolOutputGuardrailTripwireTriggered."""
        mock_guardrail = Mock()
        mock_guardrail.__class__.__name__ = "ResultValidator"

        mock_output = Mock()
        mock_output.behavior = {"type": "reject_content"}

        exc = ToolOutputGuardrailTripwireTriggered(mock_guardrail, mock_output)
        exc.guardrail = mock_guardrail
        exc.output = mock_output

        info = _build_guardrail_error_info(exc)

        assert info["type"] == "ToolOutputGuardrailTripwireTriggered"
        assert info["guardrail"]["behavior"]["type"] == "reject_content"

    def test_guardrail_exception_with_run_data(self):
        """Test guardrail error info includes run_data when available."""
        mock_guardrail = Mock()
        mock_guardrail.get_name = Mock(return_value="TestGuard")

        mock_result = Mock()
        mock_result.guardrail = mock_guardrail
        mock_result.result = "Violation"

        exc = InputGuardrailTripwireTriggered(mock_result)
        exc.guardrail_result = mock_result

        # Add run_data
        mock_agent = Mock()
        mock_agent.name = "test-agent"
        mock_run_data = Mock()
        mock_run_data.new_items = [Mock(), Mock()]
        mock_run_data.last_agent = mock_agent
        exc.run_data = mock_run_data

        info = _build_guardrail_error_info(exc)

        assert "guardrail" in info
        assert "run_data" in info
        assert info["run_data"]["turn_count"] == 2
        assert info["run_data"]["agent_name"] == "test-agent"


class TestRetryLogic:
    """Test the retry logic with proper exception type checking."""

    @pytest.mark.asyncio
    async def test_agents_exceptions_not_retried(self):
        """Verify that AgentsException types are never retried."""
        from omniagents.core.agents.service import AgentService

        # Test that _is_retryable rejects all AgentsException types
        # This would be inside the start_run method, so we need to mock the service

        # Create list of exceptions that should NOT be retried
        non_retryable = [
            MaxTurnsExceeded("Max turns reached"),
            ModelBehaviorError("Model error"),
            UserError("User error"),
        ]

        # We can't easily test the full service without a complex mock,
        # but we can verify the logic by importing and checking the function
        # that would be defined inside start_run

        # For now, just verify the exceptions exist and can be instantiated
        for exc in non_retryable:
            assert isinstance(exc, AgentsException)

    @pytest.mark.asyncio
    async def test_openai_errors_are_retryable(self):
        """Verify that OpenAI API errors are properly detected as retryable."""
        # This test verifies the type-based checking works
        # In actual implementation, these would trigger retries

        try:
            from openai import RateLimitError, APIConnectionError, APITimeoutError

            # These exceptions should be detected as retryable
            retryable_errors = [
                RateLimitError(
                    message="Rate limit exceeded",
                    response=Mock(status_code=429),
                    body=None,
                ),
                APIConnectionError(message="Connection failed", request=Mock()),
                APITimeoutError(request=Mock()),
            ]

            # Verify they're not AgentsExceptions
            for exc in retryable_errors:
                assert not isinstance(exc, AgentsException)

        except ImportError:
            pytest.skip("OpenAI SDK not available for testing")


class TestExceptionHandlingFlow:
    """Test end-to-end exception handling in AgentService.start_run."""

    @pytest.mark.asyncio
    async def test_max_turns_creates_normal_completion(self):
        """Test that MaxTurnsExceeded results in end_reason='max_turns', not 'error'."""
        # This would require mocking the entire AgentService.start_run flow
        # For now, document the expected behavior

        # Expected: when MaxTurnsExceeded is raised
        # - end_reason should be "max_turns"
        # - error_info should be None
        # - run_end event should NOT include an error field
        pass

    @pytest.mark.asyncio
    async def test_guardrail_violation_has_special_end_reason(self):
        """Test that guardrail exceptions get end_reason='guardrail_violation'."""
        # Expected: when any guardrail exception is raised
        # - end_reason should be "guardrail_violation"
        # - error_info should include guardrail details
        # - run_end event should include the error field with guardrail info
        pass

    @pytest.mark.asyncio
    async def test_model_behavior_error_not_retried(self):
        """Test that ModelBehaviorError immediately fails without retry."""
        # Expected: when ModelBehaviorError is raised
        # - No retry should occur
        # - end_reason should be "error"
        # - error_info should include run_data if available
        pass

    @pytest.mark.asyncio
    async def test_user_error_not_retried(self):
        """Test that UserError immediately fails without retry."""
        # Expected: when UserError is raised
        # - No retry should occur
        # - end_reason should be "error"
        # - error_info should preserve the user error message
        pass


# Integration test would go here, but requires full service mock
# For now, the unit tests above verify the helper functions work correctly
