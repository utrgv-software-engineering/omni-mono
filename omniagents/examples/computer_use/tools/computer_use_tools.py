from omniagents import function_tool
import pyautogui
from io import BytesIO
import base64
import time


@function_tool
def move_mouse(x: int, y: int, duration: float | None = None) -> str:
    """Move the mouse cursor to an absolute screen position.

    Args:
        x: Target X coordinate in pixels.
        y: Target Y coordinate in pixels.
        duration: Optional seconds to animate the move; 0 for instant.

    Returns:
        A short status string of the final coordinates.

    Notes:
        Prefer image-anchored clicking (`wait_for_image`/`click_image`) over raw coordinates
        when reliability is important.
    """
    pyautogui.moveTo(x, y, duration=duration or 0)
    return f"moved to {x},{y}"


@function_tool
def move_mouse_rel(dx: int, dy: int, duration: float | None = None) -> str:
    """Move the mouse cursor relative to its current position.

    Args:
        dx: Delta X in pixels (positive = right, negative = left).
        dy: Delta Y in pixels (positive = down, negative = up).
        duration: Optional seconds to animate the move; 0 for instant.

    Returns:
        A short status string of the applied deltas.
    """
    pyautogui.moveRel(dx, dy, duration=duration or 0)
    return f"moved by {dx},{dy}"


@function_tool
def click(
    x: int | None = None,
    y: int | None = None,
    button: str = "left",
    clicks: int = 1,
    interval: float = 0.0,
) -> str:
    """Click the mouse button at the current or specified position.

    Args:
        x: Optional X coordinate; if omitted, uses current cursor position.
        y: Optional Y coordinate; if omitted, uses current cursor position.
        button: Mouse button: 'left', 'right', or 'middle'.
        clicks: Number of clicks to perform (e.g., 2 for double-click).
        interval: Seconds between clicks when clicks > 1.

    Returns:
        "clicked" status string.

    Example:
        click(button="left", clicks=2)  # Double-click at current cursor
    """
    if x is not None and y is not None:
        pyautogui.click(x=x, y=y, clicks=clicks, interval=interval, button=button)
    else:
        pyautogui.click(clicks=clicks, interval=interval, button=button)
    return "clicked"


@function_tool
def double_click(
    x: int | None = None,
    y: int | None = None,
    button: str = "left",
    interval: float = 0.0,
    duration: float | None = None,
) -> str:
    """Perform a double-click at the current or specified position.

    Args:
        x: Optional X coordinate.
        y: Optional Y coordinate.
        button: Mouse button to use; default 'left'.
        interval: Seconds between the two clicks.
        duration: Optional seconds to move to the location before clicking.

    Returns:
        "double_clicked" status string.
    """
    pyautogui.doubleClick(
        x=x, y=y, button=button, interval=interval, duration=duration or 0
    )
    return "double_clicked"


@function_tool
def right_click(
    x: int | None = None,
    y: int | None = None,
    interval: float = 0.0,
    duration: float | None = None,
) -> str:
    """Right-click at the current or specified position.

    Args:
        x: Optional X coordinate.
        y: Optional Y coordinate.
        interval: Inter-click interval (kept for parity).
        duration: Optional seconds to move before clicking.

    Returns:
        "right_clicked" status string.
    """
    pyautogui.rightClick(x=x, y=y, interval=interval, duration=duration or 0)
    return "right_clicked"


@function_tool
def middle_click(
    x: int | None = None,
    y: int | None = None,
    interval: float = 0.0,
    duration: float | None = None,
) -> str:
    """Middle-click at the current or specified position.

    Args:
        x: Optional X coordinate.
        y: Optional Y coordinate.
        interval: Inter-click interval (kept for parity).
        duration: Optional seconds to move before clicking.

    Returns:
        "middle_clicked" status string.
    """
    pyautogui.middleClick(x=x, y=y, interval=interval, duration=duration or 0)
    return "middle_clicked"


@function_tool
def mouse_down(x: int | None = None, y: int | None = None, button: str = "left") -> str:
    """Press and hold a mouse button down.

    Args:
        x: Optional X coordinate; if omitted uses current cursor position.
        y: Optional Y coordinate.
        button: 'left', 'right', or 'middle'.

    Returns:
        "mouse_down" status string.
    """
    if x is not None and y is not None:
        pyautogui.mouseDown(x=x, y=y, button=button)
    else:
        pyautogui.mouseDown(button=button)
    return "mouse_down"


@function_tool
def mouse_up(x: int | None = None, y: int | None = None, button: str = "left") -> str:
    """Release a mouse button.

    Args:
        x: Optional X coordinate; if omitted uses current cursor position.
        y: Optional Y coordinate.
        button: 'left', 'right', or 'middle'.

    Returns:
        "mouse_up" status string.
    """
    if x is not None and y is not None:
        pyautogui.mouseUp(x=x, y=y, button=button)
    else:
        pyautogui.mouseUp(button=button)
    return "mouse_up"


@function_tool
def drag_to(x: int, y: int, duration: float | None = None, button: str = "left") -> str:
    """Drag the mouse from current position to an absolute coordinate.

    Args:
        x: Target X coordinate.
        y: Target Y coordinate.
        duration: Seconds to drag; 0 for instant.
        button: Mouse button to hold during drag.

    Returns:
        A status string with destination coordinates.
    """
    pyautogui.dragTo(x=x, y=y, duration=duration or 0, button=button)
    return f"dragged to {x},{y}"


@function_tool
def drag_rel(
    dx: int, dy: int, duration: float | None = None, button: str = "left"
) -> str:
    """Drag the mouse by a relative offset from current position.

    Args:
        dx: Delta X in pixels.
        dy: Delta Y in pixels.
        duration: Seconds to drag; 0 for instant.
        button: Mouse button to hold during drag.

    Returns:
        A status string with the deltas applied.
    """
    pyautogui.dragRel(dx, dy, duration=duration or 0, button=button)
    return f"dragged by {dx},{dy}"


@function_tool
def scroll(clicks: int, x: int | None = None, y: int | None = None) -> str:
    """Scroll the mouse wheel vertically.

    Args:
        clicks: Positive scrolls up; negative scrolls down.
        x: Optional X coordinate to direct the scroll event.
        y: Optional Y coordinate to direct the scroll event.

    Returns:
        A status string indicating the amount scrolled.
    """
    if x is not None and y is not None:
        pyautogui.scroll(clicks, x=x, y=y)
    else:
        pyautogui.scroll(clicks)
    return f"scrolled {clicks}"


@function_tool
def hscroll(clicks: int, x: int | None = None, y: int | None = None) -> str:
    """Scroll horizontally if supported by the OS (Linux/X11).

    Args:
        clicks: Positive scrolls right; negative scrolls left.
        x: Optional X coordinate for the event.
        y: Optional Y coordinate for the event.

    Returns:
        A status string indicating the horizontal scroll amount.
    """
    if x is not None and y is not None:
        pyautogui.hscroll(clicks, x=x, y=y)
    else:
        pyautogui.hscroll(clicks)
    return f"hscrolled {clicks}"


@function_tool
def vscroll(clicks: int, x: int | None = None, y: int | None = None) -> str:
    """Scroll vertically (explicit) if supported; alias of `scroll` on some OSes.

    Args:
        clicks: Positive scrolls up; negative scrolls down.
        x: Optional X coordinate for the event.
        y: Optional Y coordinate for the event.

    Returns:
        A status string indicating the vertical scroll amount.
    """
    if x is not None and y is not None:
        pyautogui.vscroll(clicks, x=x, y=y)
    else:
        pyautogui.vscroll(clicks)
    return f"vscrolled {clicks}"


@function_tool
def type_text(text: str, interval: float = 0.0) -> str:
    """Type a string at the current input focus.

    Args:
        text: The text to type.
        interval: Seconds between characters.

    Returns:
        The number of characters typed as a string.
    """
    pyautogui.write(text, interval=interval)
    return str(len(text))


@function_tool
def press_key(key: str, presses: int | None = 1, interval: float | None = 0.0) -> str:
    """Press a single key one or more times.

    Args:
        key: Key name (e.g., 'enter', 'esc', 'tab', 'a').
        presses: Number of times to press.
        interval: Seconds between presses.

    Returns:
        "pressed" status string.
    """
    pyautogui.press(key, presses=presses or 1, interval=interval or 0.0)
    return "pressed"


@function_tool
def press_keys(keys: list[str], interval: float | None = 0.0) -> str:
    """Press a sequence of keys in order (not a chord).

    Args:
        keys: Ordered list of key names to press sequentially.
        interval: Seconds between each key press.

    Returns:
        "pressed_keys" status string.

    Note:
        For modifier combinations (e.g., Copy), use `hotkey(['ctrl','c'])`.
    """
    pyautogui.press(keys, presses=1, interval=interval or 0.0)
    return "pressed_keys"


@function_tool
def hotkey(keys: list[str], interval: float | None = 0.0) -> str:
    """Press keys as a chorded hotkey (e.g., Ctrl+C, Cmd+L).

    Args:
        keys: List of key names pressed down in order and released in reverse.
        interval: Seconds between pressing each key.

    Returns:
        "hotkey" status string.
    """
    pyautogui.hotkey(*keys, interval=interval or 0.0)
    return "hotkey"


@function_tool
def key_down(key: str) -> str:
    """Hold a key down without releasing it.

    Args:
        key: Key name (e.g., 'shift', 'ctrl', 'a').

    Returns:
        "key_down" status string.
    """
    pyautogui.keyDown(key)
    return "key_down"


@function_tool
def key_up(key: str) -> str:
    """Release a previously held key.

    Args:
        key: Key name to release.

    Returns:
        "key_up" status string.
    """
    pyautogui.keyUp(key)
    return "key_up"


@function_tool
def get_position() -> str:
    """Get the current mouse cursor position.

    Returns:
        A string formatted as "x,y" representing the cursor coordinates.
    """
    p = pyautogui.position()
    return f"{p.x},{p.y}"


@function_tool
def get_screen_size() -> dict:
    """Return the primary screen size in pixels.

    Returns:
        {"width": int, "height": int}
    """
    s = pyautogui.size()
    return {"width": int(s.width), "height": int(s.height)}


@function_tool
def on_screen(x: int, y: int) -> dict:
    """Check whether a coordinate lies within the primary screen bounds.

    Args:
        x: X coordinate.
        y: Y coordinate.

    Returns:
        {"on_screen": bool}
    """
    return {"on_screen": bool(pyautogui.onScreen(x, y))}


@function_tool
def pixel_at(x: int, y: int) -> dict:
    """Read the RGB color of a pixel at a screen coordinate.

    Args:
        x: X coordinate.
        y: Y coordinate.

    Returns:
        {"r": int, "g": int, "b": int}
    """
    c = pyautogui.pixel(x, y)
    if isinstance(c, tuple):
        r, g, b = c[0], c[1], c[2]
    else:
        r = g = b = 0
    return {"r": int(r), "g": int(g), "b": int(b)}


@function_tool
def pixel_matches_color(
    x: int, y: int, r: int, g: int, b: int, tolerance: int | None = 0
) -> dict:
    """Check if a pixel color approximately matches an RGB value.

    Args:
        x: X coordinate.
        y: Y coordinate.
        r: Red component (0–255).
        g: Green component (0–255).
        b: Blue component (0–255).
        tolerance: Allowed per-channel deviation.

    Returns:
        {"matches": bool}
    """
    ok = pyautogui.pixelMatchesColor(
        x, y, (int(r), int(g), int(b)), tolerance=int(tolerance or 0)
    )
    return {"matches": bool(ok)}


@function_tool
def take_screenshot(
    x: int | None = None,
    y: int | None = None,
    width: int | None = None,
    height: int | None = None,
    detail: str | None = "auto",
) -> dict:
    """Capture a full-screen or region screenshot and return an image for the LLM.

    Args:
        x: Left coordinate for region (inclusive).
        y: Top coordinate for region (inclusive).
        width: Width of region.
        height: Height of region.
        detail: Vision detail hint: "low", "high", or "auto".

    Returns:
        {"type": "image", "image_url": data_uri, "detail": str}

    Notes:
        If no region is provided, captures the entire screen.
    """
    region = None
    if x is not None and y is not None and width is not None and height is not None:
        region = (x, y, width, height)
    buf = BytesIO()
    pyautogui.screenshot(region=region).save(buf, format="PNG")
    data_uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode(
        "ascii"
    )
    return {"type": "image", "image_url": data_uri, "detail": detail}


def _region_tuple(region: list[int] | None) -> tuple[int, int, int, int] | None:
    if not region:
        return None
    if len(region) != 4:
        raise ValueError("region must be [left, top, width, height]")
    return int(region[0]), int(region[1]), int(region[2]), int(region[3])


@function_tool
def locate_on_screen(
    image_path: str,
    confidence: float | None = None,
    grayscale: bool | None = None,
    region: list[int] | None = None,
) -> dict:
    """Locate the first occurrence of an image on the screen.

    Args:
        image_path: Path to the template image file.
        confidence: Optional match confidence (requires OpenCV for fuzzy matching).
        grayscale: If true, match in grayscale.
        region: Optional region [left, top, width, height] to search within.

    Returns:
        {"found": bool, "left": int, "top": int, "width": int, "height": int} when found; else {"found": False}.
    """
    kwargs: dict = {}
    if confidence is not None:
        kwargs["confidence"] = float(confidence)
    if grayscale is not None:
        kwargs["grayscale"] = bool(grayscale)
    box = pyautogui.locateOnScreen(image_path, region=_region_tuple(region), **kwargs)
    if box is None:
        return {"found": False}
    return {
        "found": True,
        "left": int(box.left),
        "top": int(box.top),
        "width": int(box.width),
        "height": int(box.height),
    }


@function_tool
def locate_center_on_screen(
    image_path: str,
    confidence: float | None = None,
    grayscale: bool | None = None,
    region: list[int] | None = None,
) -> dict:
    """Locate an image on the screen and return its center point.

    Args:
        image_path: Path to the template image file.
        confidence: Optional match confidence (requires OpenCV for fuzzy matching).
        grayscale: If true, match in grayscale.
        region: Optional region [left, top, width, height] to search within.

    Returns:
        {"found": bool, "x": int, "y": int} when found; else {"found": False}.
    """
    kwargs: dict = {}
    if confidence is not None:
        kwargs["confidence"] = float(confidence)
    if grayscale is not None:
        kwargs["grayscale"] = bool(grayscale)
    pt = pyautogui.locateCenterOnScreen(
        image_path, region=_region_tuple(region), **kwargs
    )
    if pt is None:
        return {"found": False}
    return {"found": True, "x": int(pt.x), "y": int(pt.y)}


@function_tool
def locate_all_on_screen(
    image_path: str,
    confidence: float | None = None,
    grayscale: bool | None = None,
    region: list[int] | None = None,
    max_results: int | None = None,
) -> dict:
    """Locate all occurrences of an image on the screen.

    Args:
        image_path: Path to the template image file.
        confidence: Optional match confidence (requires OpenCV for fuzzy matching).
        grayscale: If true, match in grayscale.
        region: Optional region [left, top, width, height] to search within.
        max_results: Optional maximum number of matches to return.

    Returns:
        {"results": [{"left","top","width","height"}, ...], "count": int}
    """
    kwargs: dict = {}
    if confidence is not None:
        kwargs["confidence"] = float(confidence)
    if grayscale is not None:
        kwargs["grayscale"] = bool(grayscale)
    results = []
    for box in pyautogui.locateAllOnScreen(
        image_path, region=_region_tuple(region), **kwargs
    ):
        results.append(
            {
                "left": int(box.left),
                "top": int(box.top),
                "width": int(box.width),
                "height": int(box.height),
            }
        )
        if max_results is not None and len(results) >= int(max_results):
            break
    return {"results": results, "count": len(results)}


@function_tool
def wait_for_image(
    image_path: str,
    timeout: float | None = 10.0,
    interval: float | None = 0.5,
    confidence: float | None = None,
    grayscale: bool | None = None,
    region: list[int] | None = None,
) -> dict:
    """Poll the screen until an image appears or timeout elapses.

    Args:
        image_path: Path to the template image file.
        timeout: Seconds to wait before giving up.
        interval: Seconds between attempts.
        confidence: Optional match confidence (requires OpenCV for fuzzy matching).
        grayscale: If true, match in grayscale.
        region: Optional region [left, top, width, height] to search within.

    Returns:
        When found: {"found": True, "left", "top", "width", "height", "x", "y"}.
        When not found: {"found": False}.
    """
    end = time.time() + float(timeout or 0)
    kwargs: dict = {}
    if confidence is not None:
        kwargs["confidence"] = float(confidence)
    if grayscale is not None:
        kwargs["grayscale"] = bool(grayscale)
    while True:
        box = pyautogui.locateOnScreen(
            image_path, region=_region_tuple(region), **kwargs
        )
        if box is not None:
            cx = int(box.left + box.width // 2)
            cy = int(box.top + box.height // 2)
            return {
                "found": True,
                "left": int(box.left),
                "top": int(box.top),
                "width": int(box.width),
                "height": int(box.height),
                "x": cx,
                "y": cy,
            }
        if timeout is not None and time.time() >= end:
            return {"found": False}
        time.sleep(float(interval or 0.5))


@function_tool
def click_image(
    image_path: str,
    clicks: int | None = 1,
    button: str | None = "left",
    confidence: float | None = None,
    grayscale: bool | None = None,
    timeout: float | None = None,
) -> dict:
    """Wait for an image and click its center once found.

    Args:
        image_path: Path to the template image.
        clicks: Number of clicks to perform.
        button: Mouse button to use.
        confidence: Optional match confidence (requires OpenCV for fuzzy matching).
        grayscale: If true, match in grayscale.
        timeout: Optional seconds to wait before giving up.

    Returns:
        {"clicked": True, "x": int, "y": int} on success; {"clicked": False} otherwise.
    """
    found = wait_for_image(
        image_path,
        timeout=timeout,
        interval=0.25,
        confidence=confidence,
        grayscale=grayscale,
    )
    if not found.get("found"):
        return {"clicked": False}
    x = (
        int(found["x"])
        if "x" in found
        else int(found["left"]) + int(found["width"]) // 2
    )
    y = (
        int(found["y"])
        if "y" in found
        else int(found["top"]) + int(found["height"]) // 2
    )
    pyautogui.click(x=x, y=y, clicks=int(clicks or 1), button=button or "left")
    return {"clicked": True, "x": x, "y": y}


@function_tool
def set_pause(seconds: float) -> dict:
    """Set a global pause after every PyAutoGUI public function.

    Args:
        seconds: Pause duration applied after each call.

    Returns:
        {"pause": float}
    """
    pyautogui.PAUSE = float(seconds)
    return {"pause": float(seconds)}


@function_tool
def set_failsafe(enabled: bool) -> dict:
    """Enable or disable PyAutoGUI fail-safe (move mouse to a corner to abort).

    Args:
        enabled: True to enable, False to disable.

    Returns:
        {"failsafe": bool}

    Warning:
        Disabling fail-safe is not recommended.
    """
    pyautogui.FAILSAFE = bool(enabled)
    return {"failsafe": bool(enabled)}


@function_tool
def sleep(seconds: float) -> str:
    """Pause execution for a number of seconds.

    Args:
        seconds: Duration to sleep.

    Returns:
        A status message indicating the sleep duration.
    """
    pyautogui.sleep(float(seconds))
    return f"slept {float(seconds)}"


@function_tool
def get_info() -> dict:
    """Return PyAutoGUI and environment info for diagnostics.

    Returns:
        {
          "platform": str,
          "python_version": str,
          "pyautogui_version": str,
          "executable": str,
          "resolution": {"width": int, "height": int},
          "timestamp": str,
        }
    """
    platform, python_version, pyautogui_version, executable, size, ts = (
        pyautogui.getInfo()
    )
    return {
        "platform": str(platform),
        "python_version": str(python_version),
        "pyautogui_version": str(pyautogui_version),
        "executable": str(executable),
        "resolution": {"width": int(size[0]), "height": int(size[1])},
        "timestamp": str(ts),
    }
