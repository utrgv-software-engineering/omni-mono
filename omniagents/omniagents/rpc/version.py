"""GUI version checks shared by the server and Python client.

Only stable major.minor.patch releases are negotiated. Prerelease identifiers
must not silently claim compatibility with a released contract.
"""

import re
from typing import Any


def parse_gui_version(value: Any) -> tuple[int, int, int] | None:
    if (
        not isinstance(value, str)
        or len(value) > 50
        or not re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", value)
    ):
        return None
    parts = tuple(int(part) for part in value.split("."))
    if any(part > 9007199254740991 for part in parts):
        return None
    return parts[0], parts[1], parts[2]


def supports_gui_version(server_version: Any, client_version: Any) -> bool:
    server = parse_gui_version(server_version)
    client = parse_gui_version(client_version)
    return bool(server and client and server[0] == client[0] and server >= client)
