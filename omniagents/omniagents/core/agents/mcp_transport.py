"""Compatibility settings for bundled MCP App resources over HTTP."""

from functools import wraps

MCP_RESOURCE_EVENT_BYTES = 64 * 1024 * 1024


def install_resource_transport_support():
    import agents.mcp.server as sdk

    if not getattr(sdk, "MCP_V2", False):
        return
    from mcp.client.streamable_http import streamable_http_client

    transport = sdk.streamable_http_client_v2
    if getattr(transport, "_omni_resource_support", False):
        return

    @wraps(transport)
    def with_resources(*args, **kwargs):
        # MCP 2 introduced a 1 MiB SSE default. Bundled HTML apps (including
        # OpenAI's reference app) exceed it. This is a transport allocation
        # budget, independent of model context or displayed message lengths.
        kwargs.setdefault("max_sse_event_size", MCP_RESOURCE_EVENT_BYTES)
        return streamable_http_client(*args, **kwargs)

    with_resources._omni_resource_support = True
    sdk.streamable_http_client_v2 = with_resources
