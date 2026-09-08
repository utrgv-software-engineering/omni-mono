"""Evidence-based retirement of a local runtime, without replaying its work."""

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys


def process_identity():
    if sys.platform != "linux":
        return None
    try:
        return {
            "machine": hashlib.sha256(Path("/etc/machine-id").read_bytes()).hexdigest(),
            "boot": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            "pid": os.getpid(),
            "pid_namespace": os.readlink("/proc/self/ns/pid"),
            "start": Path(f"/proc/{os.getpid()}/stat")
            .read_text()
            .rsplit(") ", 1)[1]
            .split()[19],
        }
    except OSError:
        return None


def process_is_dead(identity):
    """PID reuse and a reboot are safe only on the same identified machine."""
    current = process_identity()
    if not isinstance(identity, dict) or current is None:
        return False
    if identity.get("machine") != current["machine"]:
        return False
    if identity.get("boot") != current["boot"]:
        return bool(identity.get("boot"))
    if identity.get("pid_namespace") != current["pid_namespace"]:
        return False
    pid = identity.get("pid")
    if type(pid) is not int or pid <= 0 or not identity.get("start"):
        return False
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()
        return fields[0] == "Z" or fields[19] != identity["start"]
    except FileNotFoundError:
        return True
    except (OSError, IndexError):
        return False


def cleanup_confirmed(recovery, owner, thread_id):
    if not recovery or recovery.get("scope") != "host":
        return False
    identity = recovery.get("process")
    current = process_identity()
    if (
        isinstance(identity, dict)
        and current is not None
        and identity.get("machine") == current["machine"]
        and identity.get("boot")
        and identity["boot"] != current["boot"]
    ):
        # A reboot retires all local executors even if their final journal
        # writes were interrupted. Tool outcomes still remain unknown.
        return True
    for filename in recovery.get("executions", []):
        try:
            record = json.loads(Path(filename).read_text())
        except (OSError, ValueError, TypeError):
            return False
        if not isinstance(record, dict) or record.get("run_owner") != {
            "thread_id": thread_id,
            **owner,
        }:
            return False
        if record.get("state") == "not_started":
            continue
        if (
            record.get("state") not in ("completed", "interrupted")
            or record.get("descendants_reaped") is not True
        ):
            return False
    return True


def recovery_snapshot(store, thread_id):
    return store.mutate_queue(
        thread_id,
        lambda state, receipt: (
            deepcopy(
                {
                    "owner": state.get("runtime_owner"),
                    "recovery": state.get("runtime_recovery"),
                }
            ),
            None,
        ),
    )


def recover_runtime_owner(store, thread_id, *, abandon_unknown=False):
    """Retire a dead, cleaned-up host runtime; an explicit operator can abandon legacy unknown work.

    Abandonment does not claim an external command stopped or completed. Its
    durable tool claims stay unknown and claimed queued work becomes failed.
    A provably live local runtime is never superseded, including by abandonment.
    """
    snapshot = recovery_snapshot(store, thread_id)
    owner, recovery = snapshot["owner"], snapshot["recovery"]
    if owner is None:
        return {"recovered": False, "reason": "no_runtime_owner"}
    identity = recovery.get("process") if isinstance(recovery, dict) else None
    dead = process_is_dead(identity)
    if identity and not dead:
        return {"recovered": False, "reason": "runtime_alive_or_unverifiable"}
    proven = dead and cleanup_confirmed(recovery, owner, thread_id)
    if not proven and not abandon_unknown:
        return {
            "recovered": False,
            "reason": "cleanup_unconfirmed" if dead else "legacy_owner_unverifiable",
        }

    def retire(state, receipt):
        if (
            state.get("runtime_owner") != owner
            or state.get("runtime_recovery") != recovery
        ):
            return {"recovered": False, "reason": "owner_changed"}, None
        state["last_runtime_owner"] = owner
        state["runtime_owner"] = None
        state["runtime_recovery"] = None
        state["last_runtime_recovery"] = {
            "owner": owner,
            "mode": "verified" if proven else "operator_abandonment",
        }
        for entry in state["items"]:
            if entry.get("owner") is not None:
                entry["owner"] = None
                entry["item"].update(
                    state="failed",
                    error="Interrupted run recovered; previous actions may have taken effect. Inspect results before retrying.",
                )
        return {
            "recovered": True,
            "mode": state["last_runtime_recovery"]["mode"],
            "run_id": owner["run_id"],
        }, None

    return store.mutate_queue(thread_id, retire)
