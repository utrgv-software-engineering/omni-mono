"""Unit tests for the ACI exec-channel sentinel protocol (no Azure needed).

A fake WebSocket simulates the container PTY: it echoes the command line back
(as a real terminal does) and emits the marker/base64 frames the channel
expects. This pins the parsing — exit-code extraction, stdout/stderr
separation, binary-safety, and echo-robustness — so it can't regress without
hitting a real subscription.
"""

from __future__ import annotations

import asyncio
import base64
import re

from omniagents.core.sandbox.aci_engine import (
    AciConfig,
    AciContainerSpec,
    AciExecChannel,
    AciManager,
)


class FakeExecWs:
    """Scripts PTY responses by inspecting what the channel sends.

    Configured with the bytes each redirect file should contain and the exit
    code. Every response includes the echoed input line first (literal ``%d`` /
    marker-in-echo) so the channel's regexes prove they ignore echo.
    """

    def __init__(self, *, exit_code: int, stdout: bytes, stderr: bytes) -> None:
        self._exit = exit_code
        self._files = {"o": stdout, "e": stderr}
        self._frames: list[str] = []

    async def send(self, data: str) -> None:
        echo = data.rstrip("\n")  # the terminal echoes the typed line
        mx = re.search(r"__OMNIX_([0-9a-f]+)__%d", data)
        if mx:
            tok = mx.group(1)
            self._frames.append(echo + "\n")
            self._frames.append(f"__OMNIX_{tok}__{self._exit}\n")
            return
        # Chunked, verified file read:
        #   echo {s}; dd if=PATH bs=N skip=B count=1 … | base64; echo; echo {e};
        #   dd … | wc -c; echo {z}
        # Respond with the requested block's base64 + its true length, mirroring
        # the real protocol so the channel's reassembly/verification is exercised.
        mb = re.search(r"__OMNS_([0-9a-f]+)_(\d+)_(\d+)__; dd if=(\S+) bs=(\d+) skip=(\d+)", data)
        if mb:
            tok, block, attempt = mb.group(1), int(mb.group(2)), int(mb.group(3))
            path, bs, skip = mb.group(4), int(mb.group(5)), int(mb.group(6))
            which = "o" if path.endswith(f".omni_o_{tok}") else "e"
            chunk = self._files[which][skip * bs : (skip + 1) * bs]
            payload = base64.b64encode(chunk).decode()
            s = f"__OMNS_{tok}_{block}_{attempt}__"
            e = f"__OMNE_{tok}_{block}_{attempt}__"
            z = f"__OMNZ_{tok}_{block}_{attempt}__"
            self._frames.append(echo + "\n")
            body = (payload + "\n") if payload else ""
            self._frames.append(f"{s}\n{body}\n{e}\n{len(chunk)}\n{z}\n")
            return
        # rm cleanup or anything else: no output
        return

    async def recv(self) -> str:
        if not self._frames:
            raise AssertionError("channel read more frames than the fake produced")
        return self._frames.pop(0)


def _run(exit_code: int, stdout: bytes, stderr: bytes):
    chan = AciExecChannel(FakeExecWs(exit_code=exit_code, stdout=stdout, stderr=stderr))
    return asyncio.run(chan.run("any-cmd"))


def test_success_with_stdout_and_empty_stderr():
    r = _run(0, b"hello\nLinux\n", b"")
    assert r.exit_code == 0
    assert r.stdout == b"hello\nLinux\n"
    assert r.stderr == b""


def test_failure_separates_stderr():
    r = _run(2, b"", b"ls: cannot access '/nope': No such file or directory\n")
    assert r.exit_code == 2
    assert r.stdout == b""
    assert b"No such file" in r.stderr


def test_nonzero_exit_code():
    r = _run(1, b"", b"")
    assert r.exit_code == 1
    assert r.stdout == b"" and r.stderr == b""


def test_binary_safe_roundtrip():
    payload = b"\x00\x01\x02ABC\xff\xfe"
    r = _run(0, payload, b"")
    assert r.stdout == payload


def test_both_streams_populated():
    r = _run(3, b"out-data", b"err-data")
    assert r.exit_code == 3
    assert r.stdout == b"out-data"
    assert r.stderr == b"err-data"


# --- group construction (IP exposure / subnet) ---------------------------- #


def _manager() -> AciManager:
    # A dummy credential keeps DefaultAzureCredential (and its env probing) out
    # of the picture; _build_group never touches the management client.
    return AciManager(AciConfig("sub", "rg", "southcentralus"), credential=object())


def test_build_group_no_ports_has_no_ip():
    grp = _manager()._build_group(AciContainerSpec(name="g", image="img"))
    assert grp.ip_address is None
    assert grp.subnet_ids is None


def test_build_group_public_ip_when_no_subnet():
    grp = _manager()._build_group(
        AciContainerSpec(name="g", image="img", exposed_ports=(8080, 6080))
    )
    assert grp.subnet_ids is None
    assert grp.ip_address is not None
    assert grp.ip_address.type == "Public"
    assert [p.port for p in grp.ip_address.ports] == [8080, 6080]


def test_build_group_private_ip_when_subnet():
    sid = "/subscriptions/s/resourceGroups/rg/providers/Microsoft.Network/virtualNetworks/v/subnets/aci"
    grp = _manager()._build_group(
        AciContainerSpec(name="g", image="img", exposed_ports=(8080,), subnet_id=sid)
    )
    assert grp.ip_address is not None
    assert grp.ip_address.type == "Private"
    assert [s.id for s in grp.subnet_ids] == [sid]


def test_build_group_subnet_without_ports_joins_vnet_no_ip():
    # A no-service sandbox still joins the subnet (private IP) so it can reach
    # private endpoints, but exposes no ports.
    sid = "/subscriptions/s/resourceGroups/rg/providers/Microsoft.Network/virtualNetworks/v/subnets/aci"
    grp = _manager()._build_group(AciContainerSpec(name="g", image="img", subnet_id=sid))
    assert grp.ip_address is None
    assert [s.id for s in grp.subnet_ids] == [sid]


# --- Azure Files path mapping ------------------------------------------------ #


def _files_channel():
    from omniagents.core.sandbox.aci_engine import AciFileShare, AciFilesChannel

    return AciFilesChannel(
        AciFileShare(account_name="a", account_key="k", share_name="s", mount_path="/workspace")
    )


def test_rel_path_under_mount():
    ch = _files_channel()
    assert ch.rel_path("/workspace/foo/bar.txt") == "foo/bar.txt"
    assert ch.rel_path("/workspace/top") == "top"
    assert ch.rel_path("/workspace") == ""  # the mount root itself


def test_rel_path_off_mount_is_none():
    ch = _files_channel()
    assert ch.rel_path("/tmp/x") is None
    assert ch.rel_path("/workspaces/x") is None  # near-miss prefix, not under mount
    assert ch.rel_path("/home/user/.bashrc") is None


# --- registry: managed-identity pull ---------------------------------------- #


def test_build_group_registry_managed_identity():
    from omniagents.core.sandbox.aci_engine import AciRegistry

    mi = "/subscriptions/s/resourceGroups/rg/providers/Microsoft.ManagedIdentity/userAssignedIdentities/mi"
    grp = _manager()._build_group(
        AciContainerSpec(
            name="g", image="acr.azurecr.io/img:latest",
            registry=AciRegistry(server="acr.azurecr.io", identity=mi),
        )
    )
    cred = grp.image_registry_credentials[0]
    assert cred.identity == mi
    assert cred.password is None
    assert grp.identity is not None
    assert grp.identity.type == "UserAssigned"
    assert mi in grp.identity.user_assigned_identities


def test_build_group_registry_admin_creds_still_work():
    from omniagents.core.sandbox.aci_engine import AciRegistry

    grp = _manager()._build_group(
        AciContainerSpec(
            name="g", image="acr.azurecr.io/img:latest",
            registry=AciRegistry(server="acr.azurecr.io", username="u", password="p"),
        )
    )
    cred = grp.image_registry_credentials[0]
    assert cred.username == "u" and cred.password == "p"
    assert grp.identity is None
