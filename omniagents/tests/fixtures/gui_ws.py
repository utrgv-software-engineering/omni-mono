"""Shared GUI protocol handshake helper for /ws integration tests.

Since the protocol initialize handshake landed on the GUI endpoint
(``gui_handshake=True`` in ``omniagents.backends.server.app``), every
request sent before ``initialize``/``initialized`` is answered with a
``-32010 Connection is not initialized`` error. Tests that speak the GUI
protocol must perform this handshake right after connecting.
"""

from omniagents.rpc.generated.gui_v2 import GUI_PROTOCOL_VERSION

GUI_HANDSHAKE_CALL_ID = "__gui_handshake__"


def perform_gui_handshake(ws, *, experimental=()):
    """Run the initialize/initialized handshake on a TestClient websocket."""
    ws.send_json(
        {
            "jsonrpc": "2.0",
            "id": GUI_HANDSHAKE_CALL_ID,
            "method": "initialize",
            "params": {
                "protocol_version": GUI_PROTOCOL_VERSION,
                "identity": {"name": "integration-tests", "version": "0.0.0"},
                "platform": {"os": "linux", "arch": "x86_64"},
                "capabilities": {
                    "realtime": False,
                    "mcp_apps": False,
                    "client_functions": False,
                    "approvals": True,
                    "artifacts": False,
                    "replay": True,
                    "terminal": True,
                    "experimental_operations": list(experimental),
                    "disabled_notifications": [],
                },
            },
        }
    )
    while True:
        message = ws.receive_json()
        if message.get("id") == GUI_HANDSHAKE_CALL_ID:
            break
    assert "result" in message, f"initialize failed: {message}"
    assert message["result"]["protocol_version"] == GUI_PROTOCOL_VERSION
    ws.send_json({"jsonrpc": "2.0", "method": "initialized", "params": {}})
    return message["result"]
