"""Execution-environment domain primitives.

The conversation, durable workspace, and live tool-runtime lifecycles are
deliberately independent.  This package contains the value objects that make
those identities explicit; product composition must not use a process or path
as a substitute for an environment identity.
"""

from .errors import (
    CapabilityUnavailableError,
    EnvironmentChangedError,
    EnvironmentNotFoundError,
    EnvironmentRequiredError,
    EnvironmentUnavailableError,
    ExecutionResolutionError,
    WorkspaceEnvironmentMismatchError,
    WorkspaceNotFoundError,
)
from .host import AgentHostResources, register_host_environment
from .lifecycle import EnvironmentLifecycleController

from .models import (
    AutomaticEnvironment,
    EnvironmentCapability,
    EnvironmentKind,
    EnvironmentLease,
    EnvironmentState,
    ExistingEnvironment,
    ExecutionEnvironmentDescriptor,
    NoEnvironment,
    SandboxExecutionRuntime,
    TerminalExecutionDefaults,
    ThreadRuntimeBinding,
    WorkspaceResource,
)
from .registry import (
    ExecutionEnvironmentManager,
    ThreadRuntimeBindingRegistry,
    WorkspaceRegistry,
)
from .routing import (
    OperationExecutionDescriptor,
    OperationRouter,
    RequestContext,
    bind_request_context,
    current_request_context,
    require_environment_lease,
    require_request_context,
    reset_request_context,
)

__all__ = [
    "AgentHostResources",
    "AutomaticEnvironment",
    "CapabilityUnavailableError",
    "EnvironmentChangedError",
    "EnvironmentCapability",
    "EnvironmentKind",
    "EnvironmentLease",
    "EnvironmentLifecycleController",
    "EnvironmentNotFoundError",
    "EnvironmentRequiredError",
    "EnvironmentState",
    "EnvironmentUnavailableError",
    "ExistingEnvironment",
    "ExecutionEnvironmentDescriptor",
    "ExecutionEnvironmentManager",
    "ExecutionResolutionError",
    "NoEnvironment",
    "OperationExecutionDescriptor",
    "OperationRouter",
    "RequestContext",
    "SandboxExecutionRuntime",
    "TerminalExecutionDefaults",
    "ThreadRuntimeBinding",
    "ThreadRuntimeBindingRegistry",
    "WorkspaceEnvironmentMismatchError",
    "WorkspaceNotFoundError",
    "WorkspaceRegistry",
    "WorkspaceResource",
    "bind_request_context",
    "current_request_context",
    "require_environment_lease",
    "require_request_context",
    "reset_request_context",
    "register_host_environment",
]
