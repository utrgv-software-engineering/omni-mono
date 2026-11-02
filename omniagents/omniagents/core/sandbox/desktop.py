"""QuickSand native or guest VNC display control; never uses the host desktop."""

import asyncio
import io
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from .lifecycle_session import resolve_backend_session


class SandboxDesktop:
    def __init__(self, session):
        self.session = resolve_backend_session(session)

    @property
    def vm(self):
        state = getattr(self.session, "state", None)
        options = getattr(state, "options", None)
        if not (
            getattr(options, "enable_display", False)
            or getattr(options, "desktop_vnc_port", None)
        ):
            raise RuntimeError("This workspace has no enabled VM desktop")
        vm = getattr(self.session, "vm", None)
        if vm is None or not vm.is_running:
            raise RuntimeError("The workspace VM is not running")
        return vm

    @property
    def guest_vnc(self):
        options = getattr(getattr(self.session, "state", None), "options", None)
        return getattr(options, "desktop_vnc_port", None)

    @property
    def vnc_port(self):
        vm = self.vm
        if self.guest_vnc:
            port = self.session.state.options.port_forwards.get(self.guest_vnc)
            if not port:
                raise RuntimeError("The VM desktop has no local port mapping")
            return port
        return vm.vnc_port

    @asynccontextmanager
    async def _vnc(self, *, input_events=False):
        import asyncvnc

        # Only the running VM's configured loopback forward is reachable here.
        async with asyncio.timeout(20):
            async with asyncvnc.connect("127.0.0.1", self.vnc_port) as client:
                yield client
                await client.drain()
                if input_events:
                    # Complete a protocol round trip before disconnecting. A
                    # socket drain alone lets x11vnc drop queued input on close.
                    await client.screenshot()

    @staticmethod
    def _check_position(x, y, width, height):
        if not (0 <= x < width and 0 <= y < height):
            raise ValueError(f"Coordinates must be inside {width}×{height} pixels")

    async def screenshot(self):
        if self.guest_vnc:
            from PIL import Image

            async with self._vnc() as client:
                pixels = await client.screenshot()
            out = io.BytesIO()
            Image.fromarray(pixels).save(out, format="PNG")
            return out.getvalue()
        with tempfile.TemporaryDirectory(prefix="omni-desktop-") as directory:
            path = Path(directory) / "screen.png"
            await self.vm.screenshot(path)
            return path.read_bytes()

    async def move(self, x: int, y: int):
        if self.guest_vnc:
            async with self._vnc(input_events=True) as client:
                self._check_position(x, y, client.video.width, client.video.height)
                client.mouse.move(x, y)
            return
        width, height = await self.vm.query_display_size()
        self._check_position(x, y, width, height)
        await self.vm.mouse_move(
            round(x * 32767 / max(1, width - 1)),
            round(y * 32767 / max(1, height - 1)),
        )

    async def click(self, x: int, y: int, button="left", double=False):
        if button not in {"left", "right", "middle"}:
            raise ValueError("Unknown mouse button")
        if self.guest_vnc:
            async with self._vnc(input_events=True) as client:
                self._check_position(x, y, client.video.width, client.video.height)
                client.mouse.move(x, y)
                for _ in range(2 if double else 1):
                    client.mouse.click({"left": 0, "middle": 1, "right": 2}[button])
            return
        await self.move(x, y)
        await self.vm.mouse_click(button, double=double)

    async def scroll(self, direction: str, clicks: int):
        if direction not in {"up", "down"} or not 1 <= clicks <= 100:
            raise ValueError("Scroll requires up/down and 1–100 clicks")
        if self.guest_vnc:
            async with self._vnc(input_events=True) as client:
                getattr(client.mouse, "scroll_" + direction)(clicks)
            return
        for _ in range(clicks):
            await self.vm.mouse_click("wheel-" + direction)

    async def type_text(self, text: str):
        if self.guest_vnc:
            import asyncvnc

            # Validate the whole input before sending any keystrokes.
            keys = [
                {"\n": "Return", "\t": "Tab", "\r": "Return"}.get(c, c) for c in text
            ]
            if any(key not in asyncvnc.key_codes for key in keys):
                raise ValueError(
                    "Text contains characters unsupported by the VM keyboard"
                )
            async with self._vnc(input_events=True) as client:
                for key in keys:
                    client.keyboard.press(key)
            return
        # The native virtual keyboard only supports mapped characters. Reject
        # unsupported text before typing anything rather than silently losing it.
        if not text.isascii():
            raise ValueError(
                "QuickSand's virtual keyboard currently requires ASCII text"
            )
        await self.vm.type_text(text)

    async def press_key(self, keys: list[str]):
        if not keys or len(keys) > 8:
            raise ValueError("Provide 1–8 simultaneous key names")
        if self.guest_vnc:
            import asyncvnc

            aliases = {
                "CTRL": "Ctrl",
                "ALT": "Alt",
                "SHIFT": "Shift",
                "META": "Super",
                "SUPER": "Super",
                "RET": "Return",
                "ENTER": "Return",
                "ESC": "Escape",
                "TAB": "Tab",
                "SPC": "space",
                "SPACE": "space",
                "BACKSPACE": "BackSpace",
                "BS": "BackSpace",
                "DELETE": "Delete",
                "DEL": "Delete",
                "UP": "Up",
                "DOWN": "Down",
                "LEFT": "Left",
                "RIGHT": "Right",
                "HOME": "Home",
                "END": "End",
                "PGUP": "Page_Up",
                "PGDN": "Page_Down",
            }
            parsed = [
                aliases.get(key.upper(), key.lower() if len(key) == 1 else key.upper())
                for key in keys
            ]
            if any(key not in asyncvnc.key_codes for key in parsed):
                raise ValueError("Unknown VM keyboard key")
            async with self._vnc(input_events=True) as client:
                client.keyboard.press(*parsed)
            return
        from quicksand import Key

        try:
            parsed = [getattr(Key, key.upper()) for key in keys]
        except AttributeError as exc:
            raise ValueError(
                "Use QuickSand key names, e.g. CTRL, ALT, RET, TAB, A"
            ) from exc
        await self.vm.press_key(*parsed)


async def relay_vnc(websocket, port: int):
    """Bridge an already-authorized binary WebSocket to this VM's local VNC."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    tasks = []
    try:
        await websocket.accept()

        async def upstream():
            while True:
                data = await websocket.receive_bytes()
                writer.write(data)
                await writer.drain()

        async def downstream():
            while data := await reader.read(65536):
                await websocket.send_bytes(data)

        tasks = [asyncio.create_task(upstream()), asyncio.create_task(downstream())]
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
