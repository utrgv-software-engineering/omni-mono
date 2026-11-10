"""Backup/restore/reset for factory VMs. Library mounts are backed up by Workplace."""

from __future__ import annotations

import json
import shutil
import time
import uuid
from pathlib import Path

from filelock import FileLock, Timeout
from omniagents.core.sandbox.quicksand import QuicksandState, _generation, _atomic_text


def _state(record):
    state = QuicksandState.model_validate(record["session_state"])
    directory = Path(state.directory)
    if (
        directory.name != str(state.session_id)
        or state.container_id != f"quicksand:{state.session_id}"
    ):
        raise ValueError("Invalid QuickSand workspace identity")
    return state, directory


def backup(record, destination, *, keep=3, _owner_locked=False):
    from .personal_workspace import write_state

    state, directory = _state(record)
    if keep < 1:
        raise ValueError("keep must be at least 1")
    if not directory.is_dir():
        raise FileNotFoundError("QuickSand workspace directory is missing")
    destination = Path(destination)
    target = destination / (
        time.strftime("personal-%Y%m%dT%H%M%SZ", time.gmtime())
        + "-"
        + uuid.uuid4().hex[:6]
    )
    partial = target.with_suffix(".partial")
    partial.mkdir(parents=True, mode=0o700)
    try:
        # New VMs have one persistent working disk. Ask its owner for an online
        # backup; never copy an open qcow2 or silently return yesterday's save.
        owner = FileLock(str(directory / "owner.lock"))
        acquired = False
        working = directory / "working"
        with FileLock(str(directory / "backup.lock"), timeout=120):
            if (working / "disk.qcow2").exists():
                if not _owner_locked:
                    try:
                        owner.acquire(timeout=0)
                        acquired = True
                    except Timeout:
                        pass
                if acquired or _owner_locked:
                    if (directory / "RUNNING").exists():
                        if acquired:
                            owner.release()
                        raise RuntimeError(
                            "QuickSand stopped uncleanly; inspect its working disk before backup"
                        )
                    generation = working
                else:
                    token = uuid.uuid4().hex
                    _atomic_text(directory / "backup.request", token)
                    deadline = time.monotonic() + 600
                    while True:
                        response = directory / "backup.result"
                        result = (
                            json.loads(response.read_text())
                            if response.exists()
                            else {}
                        )
                        if result.get("id") == token:
                            if not result.get("ok"):
                                raise RuntimeError(
                                    "QuickSand backup failed: "
                                    + result.get("error", "unknown error")
                                )
                            break
                        if time.monotonic() >= deadline:
                            raise TimeoutError(
                                "QuickSand owner did not complete the requested backup"
                            )
                        time.sleep(0.2)
                    generation = _generation(directory)
            else:
                generation = _generation(directory)
            try:
                with FileLock(str(directory / "snapshot.lock"), timeout=120):
                    _copy_backup(record, state, generation, partial, write_state)
            finally:
                if acquired:
                    owner.release()
        partial.rename(target)
        previous = sorted(
            (
                p
                for p in destination.glob("personal-*")
                if p != target and p.is_dir() and p.suffix != ".partial"
            ),
            key=lambda p: p.stat().st_mtime_ns,
            reverse=True,
        )
        for old in previous[keep - 1 :]:
            shutil.rmtree(old)
        return target
    except BaseException:
        shutil.rmtree(partial, ignore_errors=True)
        raise


def _copy_backup(record, state, generation, partial, write_state):
    saved = QuicksandState.model_validate_json(
        (generation / "omni-state.json").read_text()
    )
    if saved.session_id != state.session_id or saved.container_id != state.container_id:
        raise ValueError("Saved VM identity does not match the factory record")
    shutil.copytree(generation, partial / "vm")
    write_state(
        partial / "state.json",
        {**record, "session_state": saved.model_dump(mode="json")},
    )
    write_state(
        partial / "backup.json",
        {
            "backend": "quicksand",
            "saved_at": generation.stat().st_mtime,
            "mounts_included": False,
        },
    )


def restore(state_path, source, *, vm_root=None):
    from quicksand import Sandbox
    from .personal_workspace import read_state, write_state

    state_path, source = Path(state_path), Path(source)
    if state_path.exists():
        raise SystemExit("Reset the current workspace before restoring another")
    record = read_state(source / "state.json")
    state, original = _state(record)
    if not (source / "vm/disk.qcow2").is_file():
        Sandbox.validate_save(source / "vm")
    snapshot = QuicksandState.model_validate_json(
        (source / "vm/omni-state.json").read_text()
    )
    if (
        snapshot.session_id != state.session_id
        or snapshot.container_id != state.container_id
    ):
        raise ValueError("Backup state does not match its saved VM")
    identity = uuid.uuid4()
    root = Path(vm_root).expanduser().resolve() if vm_root else original.parent
    directory = root / str(identity)
    directory.mkdir(parents=True, mode=0o700, exist_ok=False)
    try:
        saved = snapshot.model_copy(
            update={
                "session_id": identity,
                "container_id": f"quicksand:{identity}",
                "directory": str(directory),
            }
        )
        name = "save-" + uuid.uuid4().hex
        shutil.copytree(source / "vm", directory / name)
        _atomic_text(directory / name / "omni-state.json", saved.model_dump_json())
        _atomic_text(directory / "CURRENT", name)
        write_state(
            state_path,
            {
                **record,
                "container_id": saved.container_id,
                "session_state": saved.model_dump(mode="json"),
            },
        )
        return saved.container_id
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise


def reset(state_path, destination, *, keep=3):
    from .personal_workspace import read_state

    state_path = Path(state_path)
    record = read_state(state_path)
    _, directory = _state(record)
    if not directory.is_dir():
        raise FileNotFoundError("QuickSand workspace directory is missing")
    if Path(destination).resolve().is_relative_to(directory.resolve()):
        raise ValueError("Reset backup destination must be outside the VM directory")
    with FileLock(str(directory / "owner.lock"), timeout=0):
        if (directory / "RUNNING").exists():
            raise RuntimeError(
                "Recover or shut down the VM cleanly before reset; unsaved work may exist"
            )
        saved = backup(record, destination, keep=keep, _owner_locked=True)
        # Never delete a backup stored inside the environment being reset.
        if saved.resolve().is_relative_to(directory.resolve()):
            raise ValueError(
                "Reset backup destination must be outside the VM directory"
            )
        shutil.rmtree(directory)
        state_path.unlink()
        return saved
