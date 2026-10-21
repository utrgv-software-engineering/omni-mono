"""SDK-native hosted tool factory for OmniAgents YAML.

Hosted tools are managed by OpenAI's API (e.g. WebSearchTool, FileSearchTool,
CodeInterpreterTool, etc.). Unlike user-defined ``@function_tool`` functions
which are discovered from a directory, hosted tools are SDK classes that can
only be referenced declaratively. This module exposes a tiny registry and a
factory that builds an SDK tool instance from a YAML mapping like::

    tools:
      - hosted: web_search
      - hosted: file_search
        vector_store_ids: ["vs_abc"]
        max_num_results: 5

The factory returns the underlying SDK object and is appended to
``AgentSpec.available_tools`` alongside ``FunctionTool`` instances. The agent
builder's ``_apply_tool_timeouts`` already guards on ``isinstance(tool,
FunctionTool)``, so hosted tools pass through unchanged.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Mapping, cast

# ---------------------------------------------------------------------------
# Builders for individual hosted tools
# ---------------------------------------------------------------------------


def _build_web_search(spec: Mapping[str, Any]) -> Any:
    from agents import WebSearchTool

    kwargs: Dict[str, Any] = {}
    if "user_location" in spec:
        kwargs["user_location"] = spec["user_location"]
    if "filters" in spec:
        kwargs["filters"] = spec["filters"]
    if "search_context_size" in spec:
        kwargs["search_context_size"] = spec["search_context_size"]
    if "external_web_access" in spec:
        kwargs["external_web_access"] = spec["external_web_access"]
    return WebSearchTool(**kwargs)


def _build_file_search(spec: Mapping[str, Any]) -> Any:
    from agents import FileSearchTool

    vector_store_ids = spec.get("vector_store_ids")
    if not isinstance(vector_store_ids, list) or not vector_store_ids:
        raise ValueError(
            "hosted tool 'file_search' requires 'vector_store_ids' (a non-empty list)."
        )
    kwargs: Dict[str, Any] = {"vector_store_ids": list(vector_store_ids)}
    if "max_num_results" in spec:
        kwargs["max_num_results"] = spec["max_num_results"]
    if "include_search_results" in spec:
        kwargs["include_search_results"] = spec["include_search_results"]
    if "ranking_options" in spec:
        kwargs["ranking_options"] = spec["ranking_options"]
    if "filters" in spec:
        kwargs["filters"] = spec["filters"]
    return FileSearchTool(**kwargs)


def _build_code_interpreter(spec: Mapping[str, Any]) -> Any:
    from agents import CodeInterpreterTool

    # The SDK's CodeInterpreterTool takes ``tool_config`` (an OpenAI
    # ``CodeInterpreter`` TypedDict). Accept either a fully-formed
    # ``tool_config`` mapping or a flat ``container`` shortcut.
    tool_config = spec.get("tool_config")
    if tool_config is None:
        container = spec.get("container", {"type": "auto"})
        tool_config = {"type": "code_interpreter", "container": container}
    return CodeInterpreterTool(tool_config=cast(Any, tool_config))


def _build_image_generation(spec: Mapping[str, Any]) -> Any:
    from agents import ImageGenerationTool

    tool_config = spec.get("tool_config")
    if tool_config is None:
        # Build a minimal ``ImageGeneration`` TypedDict from any flat keys.
        tool_config = {"type": "image_generation"}
        for key in (
            "action",
            "background",
            "input_fidelity",
            "input_image_mask",
            "model",
            "moderation",
            "output_compression",
            "output_format",
            "partial_images",
            "quality",
            "size",
        ):
            if key in spec:
                tool_config[key] = spec[key]
    return ImageGenerationTool(tool_config=cast(Any, tool_config))


def _build_computer(spec: Mapping[str, Any]) -> Any:
    # ComputerTool needs a Computer/AsyncComputer protocol implementation,
    # which can't be expressed in pure YAML. Surface a clear error so users
    # know to wire their backend in Python and pass the agent in via the
    # build_agent hook (or extend this registry from their own project).
    raise NotImplementedError(
        "Hosted tool 'computer' requires a Computer/AsyncComputer backend that "
        "cannot be constructed from YAML alone. Build a ComputerTool instance "
        "in Python (see agents.ComputerTool) and supply it via a custom "
        "AgentSpec.build_agent or a project tool registry."
    )


def _build_local_shell(spec: Mapping[str, Any]) -> Any:
    # LocalShellTool requires a Python ``executor`` callable. Users who want
    # this should wire it from Python; YAML alone isn't enough.
    raise NotImplementedError(
        "Hosted tool 'local_shell' requires an 'executor' callable that "
        "cannot be expressed in YAML. Build LocalShellTool in Python and "
        "register it via a project tool registry."
    )


def _build_hosted_mcp(spec: Mapping[str, Any]) -> Any:
    from agents import HostedMCPTool

    # Accept either a fully-formed ``tool_config`` or flat fields that mirror
    # the OpenAI ``Mcp`` TypedDict (server_label, server_url, etc.).
    tool_config = spec.get("tool_config")
    if tool_config is None:
        if "server_label" not in spec:
            raise ValueError(
                "hosted tool 'hosted_mcp' requires 'server_label' (and usually 'server_url')."
            )
        tool_config = {"type": "mcp", "server_label": spec["server_label"]}
        for key in (
            "server_url",
            "server_description",
            "headers",
            "allowed_tools",
            "authorization",
            "connector_id",
            "defer_loading",
            "require_approval",
        ):
            if key in spec:
                tool_config[key] = spec[key]
    return HostedMCPTool(tool_config=cast(Any, tool_config))


def _build_apply_patch(spec: Mapping[str, Any]) -> Any:
    # ApplyPatchTool requires an ``editor`` callable (ApplyPatchEditor) — a
    # Python implementation. Surface a clear error and let users register from
    # Python if they need it.
    raise NotImplementedError(
        "Hosted tool 'apply_patch' requires an 'editor' callable that "
        "cannot be expressed in YAML. Build ApplyPatchTool in Python and "
        "register it via a project tool registry."
    )


def _build_shell(spec: Mapping[str, Any]) -> Any:
    from agents import ShellTool

    # ShellTool with a hosted environment (container_auto / container_reference)
    # works without a Python executor. Local environment requires an executor
    # callable, which YAML cannot express.
    environment = spec.get("environment")
    if environment is None or (
        isinstance(environment, Mapping) and environment.get("type") == "local"
    ):
        raise NotImplementedError(
            "Hosted tool 'shell' with a local environment requires an "
            "'executor' callable that cannot be expressed in YAML. Either set "
            "'environment.type' to 'container_auto' / 'container_reference' "
            "for a hosted runtime, or build ShellTool in Python and register "
            "it via a project tool registry."
        )

    kwargs: Dict[str, Any] = {"environment": environment}
    if "name" in spec:
        kwargs["name"] = spec["name"]
    return ShellTool(**kwargs)


# ---------------------------------------------------------------------------
# Registry + public factory
# ---------------------------------------------------------------------------


HostedToolBuilder = Callable[[Mapping[str, Any]], Any]


HOSTED_TOOL_REGISTRY: Dict[str, HostedToolBuilder] = {
    "web_search": _build_web_search,
    "file_search": _build_file_search,
    "code_interpreter": _build_code_interpreter,
    "image_generation": _build_image_generation,
    "computer": _build_computer,
    "local_shell": _build_local_shell,
    "hosted_mcp": _build_hosted_mcp,
    "apply_patch": _build_apply_patch,
    "shell": _build_shell,
}


def build_hosted_tool(spec_dict: Mapping[str, Any]) -> Any:
    """Build an SDK hosted tool from a YAML mapping.

    The mapping must contain a ``hosted`` key naming one of
    ``HOSTED_TOOL_REGISTRY``. Remaining keys are forwarded to the builder.

    Raises:
        ValueError: if ``hosted`` is missing or names an unknown tool, or if
            required fields are missing.
        NotImplementedError: if the named tool requires a Python backend that
            YAML cannot express (e.g. ``computer``, ``local_shell``,
            ``apply_patch``, ``shell`` with a local environment).
    """
    if not isinstance(spec_dict, Mapping):
        raise ValueError(
            f"hosted tool spec must be a mapping, got {type(spec_dict).__name__}."
        )
    name = spec_dict.get("hosted")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("hosted tool spec must include a 'hosted' name.")
    builder = HOSTED_TOOL_REGISTRY.get(name)
    if builder is None:
        valid = ", ".join(sorted(HOSTED_TOOL_REGISTRY.keys()))
        raise ValueError(f"Unknown hosted tool '{name}'. Valid names: {valid}.")
    return builder(spec_dict)
