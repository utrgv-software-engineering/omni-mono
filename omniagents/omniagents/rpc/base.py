"""Base classes for JSON-RPC method definitions."""

from typing import Optional


class RpcMethodsBase:
    """Base class for JSON-RPC method containers."""

    def __init__(self) -> None:
        self.channel: Optional['JsonRpcChannel'] = None