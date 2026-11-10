"""Computer-use tools bound to the current execution environment."""

import base64
from typing import Literal

from agents.tool import ToolOutputImage
from omniagents.core.tools import function_tool


def _desktop():
    from omniagents.workspace import current_workspace
    from omniagents.core.sandbox.desktop import SandboxDesktop

    session = getattr(current_workspace(), "session", None)
    if session is None:
        raise RuntimeError("Desktop control requires a VM workspace")
    return SandboxDesktop(session)


@function_tool(running_summary="Viewing VM desktop...")
async def desktop_screenshot() -> list[ToolOutputImage]:
    """See the current VM desktop. Mouse coordinates use this image's pixels."""
    data = await _desktop().screenshot()
    return [
        ToolOutputImage(
            type="image",
            image_url="data:image/png;base64," + base64.b64encode(data).decode(),
        )
    ]


@function_tool(running_summary="Clicking VM desktop...")
async def desktop_click(
    x: int,
    y: int,
    button: Literal["left", "right", "middle"] = "left",
    double: bool = False,
) -> str:
    """Click a pixel position on the VM desktop; optionally double-click."""
    await _desktop().click(x, y, button, double)
    return "Clicked"


@function_tool(running_summary="Moving VM pointer...")
async def desktop_move(x: int, y: int) -> str:
    """Move the VM pointer to a screenshot pixel position, e.g. to hover."""
    await _desktop().move(x, y)
    return "Moved"


@function_tool(running_summary="Typing in VM desktop...")
async def desktop_type(text: str) -> str:
    """Type ASCII text into the focused VM application using its virtual keyboard."""
    await _desktop().type_text(text)
    return "Typed"


@function_tool(running_summary="Pressing VM keys...")
async def desktop_press_key(keys: list[str]) -> str:
    """Press a key or chord in the VM. Use QuickSand names: RET, TAB, ESC, CTRL, ALT, SHIFT, A, etc."""
    await _desktop().press_key(keys)
    return "Pressed"


@function_tool(running_summary="Scrolling VM desktop...")
async def desktop_scroll(direction: Literal["up", "down"], clicks: int = 3) -> str:
    """Scroll the VM mouse wheel at its current position."""
    await _desktop().scroll(direction, clicks)
    return "Scrolled"


@function_tool
async def desktop_show() -> dict:
    """Open the interactive VM desktop beside chat so the user can see or control it.

    Desktop control and screenshot tools do not open the viewer automatically.
    """
    from omniagents.core.tools.presentation import present_view

    if not _desktop().vnc_port:
        raise RuntimeError("The workspace desktop is unavailable")
    present_view("desktop", "Desktop")
    return {"available": True}
