from omniagents.core.agents.specs import SafeAgentOptions
from omniagents.rpc.agents.workers import _default_worker_safety


class _Spec:
    def __init__(self, use_safe_agent, safe_agent_options=None):
        self.use_safe_agent = use_safe_agent
        self.safe_agent_options = safe_agent_options


def test_safe_agent_defaults_to_skip_approvals():
    spec = _Spec(use_safe_agent=True, safe_agent_options=SafeAgentOptions())
    _default_worker_safety(spec)
    assert spec.safe_agent_options.skip_approvals is True


def test_missing_options_get_created():
    spec = _Spec(use_safe_agent=True, safe_agent_options=None)
    _default_worker_safety(spec)
    assert spec.safe_agent_options is not None
    assert spec.safe_agent_options.skip_approvals is True


def test_safe_agent_disabled_untouched():
    spec = _Spec(use_safe_agent=False, safe_agent_options=None)
    _default_worker_safety(spec)
    assert spec.safe_agent_options is None


def test_explicit_skip_approvals_true_preserved():
    opts = SafeAgentOptions(skip_approvals=True, halt_on_rejection=False)
    spec = _Spec(use_safe_agent=True, safe_agent_options=opts)
    _default_worker_safety(spec)
    assert spec.safe_agent_options is opts
    assert spec.safe_agent_options.halt_on_rejection is False
