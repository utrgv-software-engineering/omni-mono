"""Base classes for JSON-RPC method definitions."""

from contextvars import ContextVar
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from omniagents.rpc.server import JsonRpcChannel

_current_channel: ContextVar[Optional["JsonRpcChannel"]] = ContextVar(
    "omniagents_rpc_channel", default=None
)


class RpcMethodsBase:
    """Base class for JSON-RPC method containers."""

    def __init__(self) -> None:
        self.channel = None

    @property
    def channel(self) -> Optional["JsonRpcChannel"]:
        return _current_channel.get()

    @channel.setter
    def channel(self, value: Optional["JsonRpcChannel"]) -> None:
        _current_channel.set(value)
