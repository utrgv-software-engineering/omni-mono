"""Native desktop proof without model calls; OMNI_TEST_QUICKSAND=1 to run."""

import asyncio
import os

import pytest

from omniagents.core.runtime.serve_harness import bring_up_session, teardown_session
from omniagents.core.sandbox.desktop import SandboxDesktop
from omniagents.core.sandbox.profiles import ResolvedProfile, TerminalSpec


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.environ.get("OMNI_TEST_QUICKSAND") != "1", reason="opt-in VM test"
)
async def test_native_desktop_input_and_resume(tmp_path):
    profile = ResolvedProfile(
        raw={
            "client": {"type": "quicksand", "state_dir": str(tmp_path / "vms")},
            "options": {
                "image": "ubuntu-desktop",
                "enable_display": True,
                "memory": "2G",
                "disk_size": "10G",
                "save_interval_seconds": 0,
            },
            "manifest": {"root": "/workspace"},
        },
        build=[],
        init=[],
        services=[],
        terminal=TerminalSpec(),
        sources=[],
    )
    state = tmp_path / "state.json"
    brought = None

    async def boot():
        return await bring_up_session(
            profile=profile,
            sources_parsed=[],
            snapshot_ref="desktop-proof",
            snapshot_dir=None,
            state_path=state,
            durable=True,
        )

    async def ready(desktop):
        for _ in range(90):
            result = await desktop.vm.execute("pgrep -x xfce4-panel")
            if result.exit_code == 0:
                await asyncio.sleep(15)
                return
            await asyncio.sleep(1)
        raise AssertionError("Desktop did not become ready")

    try:
        brought = await boot()
        desktop = SandboxDesktop(brought.session)
        await ready(desktop)
        png = await desktop.screenshot()
        assert png.startswith(b"\x89PNG") and len(png) > 10000
        (tmp_path / "desktop.png").write_bytes(png)
        width, height = await desktop.vm.query_display_size()
        await desktop.click(width // 2, height // 2)
        await desktop.press_key(["CTRL", "ALT", "T"])
        await asyncio.sleep(3)
        (tmp_path / "terminal.png").write_bytes(await desktop.screenshot())
        await desktop.type_text(
            "printf desktop-input-ok > /home/quicksand/desktop-input-proof"
        )
        await desktop.press_key(["RET"])
        for _ in range(15):
            result = await desktop.vm.execute("cat /home/quicksand/desktop-input-proof")
            if result.exit_code == 0:
                break
            await asyncio.sleep(1)
        (tmp_path / "typed.png").write_bytes(await desktop.screenshot())
        assert result.stdout.strip() == "desktop-input-ok", result
        await desktop.scroll("down", 1)
        reader, writer = await asyncio.open_connection("127.0.0.1", desktop.vm.vnc_port)
        assert (await reader.readexactly(12)).startswith(b"RFB ")
        writer.close()
        await writer.wait_closed()
        await teardown_session(brought.session, [], detach=True)
        brought = None
        brought = await boot()
        desktop = SandboxDesktop(brought.session)
        await ready(desktop)
        assert (await desktop.screenshot()).startswith(b"\x89PNG")
        assert (
            await desktop.vm.execute("cat /home/quicksand/desktop-input-proof")
        ).stdout.strip() == "desktop-input-ok"
    finally:
        if brought:
            await teardown_session(brought.session, [], destroy=True, state_path=state)
