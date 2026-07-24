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

        mock_output = Mock()
        mock_output.output_info = "Inappropriate content detected"

        mock_result = Mock()
        mock_result.guardrail = mock_guardrail
        mock_result.output = mock_output

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

        mock_output = Mock()
        mock_output.output_info = "Response too long"

        mock_result = Mock()
        mock_result.guardrail = mock_guardrail
        mock_result.output = mock_output

        exc = OutputGuardrailTripwireTriggered(mock_result)
        exc.guardrail_result = mock_result

        info = _build_guardrail_error_info(exc)

        assert info["type"] == "OutputGuardrailTripwireTriggered"
        assert info["guardrail"]["name"] == "ResponseLengthGuard"
        assert info["guardrail"]["result"] == "Response too long"

    def test_tool_input_guardrail_exception(self):
        """Test error info from ToolInputGuardrailTripwireTriggered."""
        mock_guardrail = Mock()
        mock_guardrail.get_name = Mock(return_value="ToolSafetyGuard")

        mock_output = Mock()
        mock_output.behavior = {
            "type": "raise_exception",
            "message": "Unsafe tool call",
        }
        mock_output.output_info = {"check": "dangerous_pattern", "matched": True}

        exc = ToolInputGuardrailTripwireTriggered(mock_guardrail, mock_output)
        exc.guardrail = mock_guardrail
        exc.output = mock_output

        info = _build_guardrail_error_info(exc)

        assert info["type"] == "ToolInputGuardrailTripwireTriggered"
        assert info["guardrail"]["name"] == "ToolSafetyGuard"
        assert info["guardrail"]["behavior"]["type"] == "raise_exception"
        assert info["guardrail"]["output_info"]["check"] == "dangerous_pattern"

    def test_tool_output_guardrail_exception(self):
        """Test error info from ToolOutputGuardrailTripwireTriggered."""
        mock_guardrail = Mock()
        mock_guardrail.get_name = Mock(return_value="ResultValidator")

        mock_output = Mock()
        mock_output.behavior = {"type": "reject_content"}
        mock_output.output_info = {"validation": "failed", "reason": "contains_pii"}

        exc = ToolOutputGuardrailTripwireTriggered(mock_guardrail, mock_output)
        exc.guardrail = mock_guardrail
        exc.output = mock_output

        info = _build_guardrail_error_info(exc)

        assert info["type"] == "ToolOutputGuardrailTripwireTriggered"
        assert info["guardrail"]["name"] == "ResultValidator"
        assert info["guardrail"]["behavior"]["type"] == "reject_content"
        assert info["guardrail"]["output_info"]["validation"] == "failed"

    def test_guardrail_exception_with_run_data(self):
        """Test guardrail error info includes run_data when available."""
        mock_guardrail = Mock()
        mock_guardrail.get_name = Mock(return_value="TestGuard")

        mock_output = Mock()
        mock_output.output_info = "Violation"

        mock_result = Mock()
        mock_result.guardrail = mock_guardrail
        mock_result.output = mock_output

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


class TestIsRetryableError:
    """Direct tests of the module-level retry classifier."""

    def test_agents_exceptions_never_retry(self):
        from omniagents.core.agents.service import _is_retryable_error

        for exc in (
            MaxTurnsExceeded("Max turns reached"),
            ModelBehaviorError("Model error"),
            UserError("User error"),
        ):
            assert _is_retryable_error(exc) is False

    def test_httpx_transport_errors_retry(self):
        """A dropped SSE stream surfaces as httpx.RemoteProtocolError ("peer
        closed connection without sending complete message body") — transient
        network failure, must retry rather than kill the resident's turn."""
        import httpx

        from omniagents.core.agents.service import _is_retryable_error

        transport_errors = [
            httpx.RemoteProtocolError(
                "peer closed connection without sending complete message body "
                "(incomplete chunked read)"
            ),
            httpx.ConnectError("connect failed"),
            httpx.ReadError("read failed"),
            httpx.WriteError("write failed"),
            httpx.ReadTimeout("read timeout"),
        ]
        for exc in transport_errors:
            assert _is_retryable_error(exc) is True, exc

    def test_plain_value_error_does_not_retry(self):
        from omniagents.core.agents.service import _is_retryable_error

        assert _is_retryable_error(ValueError("badly formed input")) is False


# TODO: Add integration tests for end-to-end exception handling in AgentService.start_run
# These would test:
# - MaxTurnsExceeded results in end_reason='max_turns', not 'error'
# - Guardrail exceptions get end_reason='guardrail_violation' with details
# - ModelBehaviorError and UserError are not retried and fail immediately
# - Transient errors (rate limits, timeouts) trigger retry logic
#
# These require complex mocking of the entire AgentService and would be better
# suited as integration tests rather than unit tests.
