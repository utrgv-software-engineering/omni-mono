"""Core runner for executing agents in different modes."""

from typing import Optional, List, Any, Dict
from omniagents.core.specs import AgentSpec, SafeAgentOptions
from omniagents.core.config_loader import load_agent_spec_from_yaml, DEFAULT_SETTINGS_FIELDS
from omniagents.core.backend_loader import load_backend


class Runner:
    """Run agents in different modes."""
    
    def __init__(self, spec: Optional[AgentSpec] = None, config_path: Optional[str] = None):
        if spec:
            self.spec = spec
        elif config_path:
            self.spec = load_agent_spec_from_yaml(config_path)
        else:
            raise ValueError("Either spec or config_path must be provided")
    
    @classmethod
    def from_agent(
        cls,
        agent: Any,  # AgentLike
        *,
        welcome_text: Optional[str] = None,
        safe: bool = False,
        skip_approvals: bool = False,
        halt_on_rejection: bool = True,
        safe_tool_names: Optional[List[str]] = None,
        safe_tool_patterns: Optional[List[str]] = None,
        **kwargs
    ) -> "Runner":
        """
        Create a Runner from an existing agents.Agent instance.
        
        This is a convenience method that creates an AgentSpec from the agent
        and returns a Runner configured to use it.
        """
        name = getattr(agent, "name", "agent")
        instr = agent.instructions() if callable(agent.instructions) else agent.instructions
        
        # Create spec from agent
        spec = AgentSpec(
            name=name,
            header_title=name,
            page_title=name,
            welcome_text=welcome_text or instr,
            get_agent_instructions=lambda: instr,
            available_tools=getattr(agent, "tools", []),
            model_name=getattr(agent, "model", None),
            use_safe_agent=safe,
            safe_agent_options=SafeAgentOptions(
                skip_approvals=skip_approvals,
                halt_on_rejection=halt_on_rejection,
                safe_tool_names=safe_tool_names or [],
                safe_tool_patterns=safe_tool_patterns or [],
            ),
            settings_fields=list(DEFAULT_SETTINGS_FIELDS),
            **kwargs  # Allow additional spec parameters
        )
        
        return cls(spec=spec)
    
    def run(self, mode: str = "gui", **kwargs) -> None:
        """Run agent in specified mode.
        
        Args:
            mode: Execution mode - 'gui', 'cli', or 'server'
            **kwargs: Additional arguments passed to the backend
        """
        backend_class = load_backend(mode)
        if not backend_class:
            raise ValueError(f"Unknown backend: {mode}")
        
        backend = backend_class()
        backend.run(spec=self.spec, **kwargs)