from typing import Any, Optional

from omniagents.core.agents.specs import AgentSpec, SafeAgentOptions


def apply_cli_overrides(spec: Optional[AgentSpec], args: Any) -> None:
    if spec is None or args is None:
        return
    if spec.safe_agent_options is None:
        spec.safe_agent_options = SafeAgentOptions()
    options = spec.safe_agent_options
    approvals = getattr(args, "approvals", None)
    if approvals == "skip":
        spec.use_safe_agent = False
    elif approvals == "auto":
        spec.use_safe_agent = True
        options.skip_approvals = True
    elif approvals == "require":
        spec.use_safe_agent = True
        options.skip_approvals = False
    on_reject = getattr(args, "on_reject", None)
    if on_reject == "halt":
        options.halt_on_rejection = True
    elif on_reject == "continue":
        options.halt_on_rejection = False
    spec.safe_agent_options = options
