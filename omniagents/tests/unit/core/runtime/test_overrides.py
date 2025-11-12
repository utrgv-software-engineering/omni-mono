import types

from omniagents.core.agents.specs import AgentSpec, SafeAgentOptions
from omniagents.core.runtime.overrides import apply_cli_overrides


def make_spec():
    return AgentSpec(
        name="Agent",
        welcome_text="Welcome",
        get_agent_instructions=lambda: "",
        build_agent=None,
    )


def test_skip_disables_safe_agent():
    spec = make_spec()
    spec.use_safe_agent = True
    spec.safe_agent_options = SafeAgentOptions()
    args = types.SimpleNamespace(approvals="skip", on_reject=None)
    apply_cli_overrides(spec, args)
    assert spec.use_safe_agent is False


def test_auto_enables_safe_agent_and_skips_approvals():
    spec = make_spec()
    spec.safe_agent_options = SafeAgentOptions(skip_approvals=False)
    args = types.SimpleNamespace(approvals="auto", on_reject=None)
    apply_cli_overrides(spec, args)
    assert spec.use_safe_agent is True
    assert spec.safe_agent_options.skip_approvals is True


def test_require_enables_safe_agent_and_requires_approvals():
    spec = make_spec()
    spec.safe_agent_options = SafeAgentOptions(skip_approvals=True)
    args = types.SimpleNamespace(approvals="require", on_reject=None)
    apply_cli_overrides(spec, args)
    assert spec.use_safe_agent is True
    assert spec.safe_agent_options.skip_approvals is False


def test_on_reject_continue_disables_halt():
    spec = make_spec()
    spec.safe_agent_options = SafeAgentOptions(halt_on_rejection=True)
    args = types.SimpleNamespace(approvals=None, on_reject="continue")
    apply_cli_overrides(spec, args)
    assert spec.safe_agent_options.halt_on_rejection is False


def test_on_reject_halt_enables_halt():
    spec = make_spec()
    spec.safe_agent_options = SafeAgentOptions(halt_on_rejection=False)
    args = types.SimpleNamespace(approvals=None, on_reject="halt")
    apply_cli_overrides(spec, args)
    assert spec.safe_agent_options.halt_on_rejection is True


def test_no_args_does_not_modify_spec():
    spec = make_spec()
    spec.use_safe_agent = True
    spec.safe_agent_options = SafeAgentOptions(skip_approvals=True)
    apply_cli_overrides(spec, None)
    assert spec.use_safe_agent is True
    assert spec.safe_agent_options.skip_approvals is True
