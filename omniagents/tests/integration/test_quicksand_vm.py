"""Opt-in real VM proof; no model calls and no production services.

OMNI_TEST_QUICKSAND=1 pytest tests/integration/test_quicksand_vm.py -s --timeout=900
Requires omniagents[sandbox-quicksand] and the sibling Workplace package.
"""

import asyncio
import io
import os
import socket
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.environ.get("OMNI_TEST_QUICKSAND") != "1", reason="opt-in QEMU VM test"
    ),
]


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def test_factory_vm_persistence_terminal_and_network(tmp_path):
    import httpx
    import uvicorn
    from omniagents_workplace.server import Workplace, create_app, load_seed
    from omniagents.core.runtime.serve_harness import bring_up_session, teardown_session
    from omniagents.core.sandbox.profiles import ResolvedProfile, TerminalSpec
    from omniagents.core.sandbox.lifecycle_session import resolve_backend_session
    from omniagents.integrations.environment import GitHubConfig
    from omniagents.integrations.personal_workspace import refresh_environment

    host_port, preview_port = free_port(), free_port()
    workplace = Workplace(
        tmp_path / "workplace",
        base_url=f"http://127.0.0.1:{host_port}",
        seed=load_seed(),
        run_workflows=False,
    )
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(workplace, static_dir=tmp_path),
            host="127.0.0.1",
            port=host_port,
            log_level="error",
        )
    )
    serving = asyncio.create_task(server.serve())
    while not server.started:
        if serving.done():
            await serving
        await asyncio.sleep(0.05)
    library = tmp_path / "library"
    library.mkdir()
    (library / "input.txt").write_text("host artifact")
    profile = ResolvedProfile(
        raw={
            "client": {
                "type": "quicksand",
                "state_dir": str(tmp_path / "vms"),
                "binds": [{"source": str(library), "target": "/workspace/Library"}],
            },
            "options": {
                "memory": "1G",
                "cpus": 2,
                "network_mode": "full",
                "save_interval_seconds": 0,
                "setup": [
                    "apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git jq"
                ],
                "host_services": {7300: host_port},
                "port_forwards": {8000: preview_port},
                "exposed_ports": [8000],
            },
            "manifest": {"root": "/workspace"},
        },
        build=[],
        init=[],
        services=[],
        terminal=TerminalSpec(),
        sources=[],
    )
    state = tmp_path / "factory.json"
    brought = None
    try:

        async def boot():
            return await bring_up_session(
                profile=profile,
                sources_parsed=[],
                snapshot_ref="proof",
                snapshot_dir=None,
                state_path=state,
                durable=True,
            )

        brought = await boot()
        session = brought.session
        inner = resolve_backend_session(session)
        print("VM started:", inner.vm.accelerator, flush=True)
        result = await session.exec(
            "cat /workspace/Library/input.txt && printf guest-artifact > /workspace/Library/output.txt"
        )
        assert result.ok() and b"host artifact" in result.stdout, result.stderr
        assert (library / "output.txt").read_text() == "guest-artifact"
        await session.write(Path("binary"), io.BytesIO(b"binary\x00roundtrip"))
        assert (await session.read(Path("binary"))).read() == b"binary\x00roundtrip"
        result = await session.exec(
            "cd /workspace && git init -q repo && cd repo && git -c user.name=Test -c user.email=test@example.invalid commit --allow-empty -qm initial && git worktree add -qb work ../work && printf unfinished > ../work/draft"
        )
        assert result.ok(), result.stderr
        # Managed token changes use the same refresh path as the factory.
        await refresh_environment(session, GitHubConfig(token="proof-token-one"))
        await refresh_environment(session, GitHubConfig(token="proof-token-two"))
        assert (
            await session.exec('printf %s "$GH_TOKEN"')
        ).stdout == b"proof-token-two"
        assert (
            await session.exec(
                "python3 -c \"import urllib.request; print(urllib.request.urlopen('http://10.0.2.101:7300/healthz').status)\""
            )
        ).stdout.strip() == b"200"
        print(
            "Files, worktree, credential refresh, and Workplace access passed",
            flush=True,
        )

        terminal = await session.pty_exec_start(
            "bash --noprofile --norc", tty=True, yield_time_s=0.1
        )
        pid = terminal.process_id
        assert pid is not None
        await inner.pty_resize(pid, cols=111, rows=37)
        output = bytearray(terminal.output)

        async def consume():
            async for chunk in inner.pty_stream_output(pid):
                output.extend(chunk)

        stream = asyncio.create_task(consume())
        await session.pty_write_stdin(
            session_id=pid,
            chars="stty size; printf 'terminal-%s\\n' works\n",
            yield_time_s=0,
        )
        for _ in range(100):
            if b"37 111" in output and b"terminal-works" in output:
                break
            await asyncio.sleep(0.1)
        assert b"37 111" in output and b"terminal-works" in output, output
        await session.pty_write_stdin(
            session_id=pid, chars="sleep 60\n", yield_time_s=0
        )
        await asyncio.sleep(0.5)
        await session.pty_write_stdin(session_id=pid, chars="\x03", yield_time_s=0)
        await session.pty_write_stdin(
            session_id=pid, chars="printf 'interrupt-%s\\n' works\n", yield_time_s=0
        )
        for _ in range(100):
            if b"interrupt-works" in output:
                break
            await asyncio.sleep(0.1)
        assert b"interrupt-works" in output, output
        await session.pty_write_stdin(session_id=pid, chars="exit\n", yield_time_s=0)
        await asyncio.wait_for(stream, 15)
        print(
            "Interactive terminal streaming, resize, Ctrl-C, and exit passed",
            flush=True,
        )

        job = await session.pty_exec_start(
            "python3 -m http.server 8000 --bind 0.0.0.0 --directory /workspace",
            yield_time_s=0.1,
        )
        assert job.process_id
        async with httpx.AsyncClient() as http:
            for attempt in range(30):
                try:
                    response = await http.get(f"http://127.0.0.1:{preview_port}/binary")
                    response.raise_for_status()
                    assert response.content == b"binary\x00roundtrip"
                    break
                except httpx.HTTPError:
                    if attempt == 29:
                        raise
                    await asyncio.sleep(0.2)
        await session.pty_write_stdin(
            session_id=job.process_id, chars="\x03", yield_time_s=0
        )
        print("Guest preview and background process cancellation passed", flush=True)
        identity = brought.container_id
        await teardown_session(session, [], detach=True)
        brought = None
        # Init must not rerun on a saved environment.
        profile.init[:] = ["exit 99"]
        brought = await boot()
        assert brought.container_id == identity and brought.workspace_preserved
        result = await brought.session.exec(
            "jq --version && git -C /workspace/repo worktree list && cat /workspace/work/draft"
        )
        assert (
            result.ok()
            and b"unfinished" in result.stdout
            and b"/workspace/work" in result.stdout
        )
        assert (
            await brought.session.read(Path("binary"))
        ).read() == b"binary\x00roundtrip"
        print(
            "Package, worktree, edits, and identity survive factory restart", flush=True
        )
        await teardown_session(brought.session, [], detach=True)
        brought = None
        brought = await boot()
        result = await brought.session.exec("jq --version && cat /workspace/work/draft")
        assert result.ok() and b"unfinished" in result.stdout
        print("Second save/restart preserves inherited disk layers", flush=True)
        await teardown_session(brought.session, [], detach=True)
        brought = None
        from omniagents.integrations import quicksand_maintenance as maintenance

        saved = maintenance.reset(state, tmp_path / "backups")
        assert not state.exists()
        assert not Path(inner.state.directory).exists()
        assert (library / "output.txt").read_text() == "guest-artifact"
        maintenance.restore(state, saved, vm_root=tmp_path / "vms")
        brought = await boot()
        assert brought.container_id != identity
        result = await brought.session.exec(
            "jq --version && cat /workspace/work/draft && cat /workspace/Library/output.txt"
        )
        assert (
            result.ok()
            and b"unfinished" in result.stdout
            and b"guest-artifact" in result.stdout
        ), result.stderr
        print(
            "Library mount and backup/reset/restore passed after original VM deletion",
            flush=True,
        )

    finally:
        try:
            if brought:
                await teardown_session(brought.session, [], detach=True)
        finally:
            server.should_exit = True
            await serving
            workplace.close()


async def test_file_reads_survive_exclusive_guest_commands_and_real_save(tmp_path):
    from quicksand import Sandbox, NetworkMode
    from agents.sandbox.manifest import Manifest
    from omniagents.core.sandbox.quicksand import QuicksandClient, QuicksandSession

    client = QuicksandClient(state_dir=tmp_path / "state")
    session = QuicksandSession(
        (await client.create(manifest=Manifest(root="/workspace"))).state
    )
    vm = Sandbox(
        image="ubuntu",
        memory="512M",
        cpus=1,
        network_mode=NetworkMode.NONE,
        enable_display=False,
    )
    session.vm = vm
    exclusive = saving = None
    try:
        await vm.start()
        result = await session.exec(
            "mkdir -p /workspace/repro; printf existing-file > /workspace/repro/read.txt"
        )
        assert result.ok()
        path = Path("/workspace/repro/read.txt")
        assert (await session.read(path)).read() == b"existing-file"
        ready = asyncio.Event()
        exclusive = asyncio.create_task(
            vm.execute(
                "printf busy; sleep 1",
                exclusive=True,
                stdin=b"",
                on_stdout=lambda _: ready.set(),
            )
        )
        await asyncio.wait_for(ready.wait(), 15)
        assert (await session.read(path)).read() == b"existing-file"
        await exclusive
        # Exercise the narrow admission race even if a save starts outside our gate.
        saving = asyncio.create_task(vm.save("read-race", workspace=tmp_path))
        while not saving.done():
            assert (await session.read(path)).read() == b"existing-file"
            await asyncio.sleep(0.01)
        await saving
        assert (await session.read(path)).read() == b"existing-file"
    finally:
        for task in (exclusive, saving):
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)
        await vm.stop()
