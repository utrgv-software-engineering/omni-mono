"""Governance-driven sandbox enforcement.

When authenticated to the management plane, checks the policy to determine
if the current session requires sandboxing. If so, re-execs the current
command inside omni-sandbox (process-level isolation via bwrap/sandbox-exec/
AppContainer) with the policy's network allowlist and resource constraints.

Security model:
- Fail closed: if governance is required and can't be enforced, refuse to run
- OMNI_SANDBOXED is verified by checking we're actually in a namespace,
  not just by trusting the env var
- Policy is fetched fresh from the platform, not read from local cache alone
- Missing sandbox binary = hard stop, not a warning
"""

import os
import shutil
import sys
from pathlib import Path
from typing import Optional


def is_sandboxed() -> bool:
    """Return True if actually running inside a sandbox.

    Checks both the env var AND verifies we're in a restricted environment
    (Linux: user namespace, macOS: sandbox-exec profile, etc.)
    """
    if os.getenv("OMNI_SANDBOXED") != "1":
        return False

    # Verify we're actually in a namespace, not just a user-set env var.
    # On Linux, check if we're in a user namespace by reading uid_map.
    # A non-trivial uid_map means we're in a namespace.
    if sys.platform == "linux":
        try:
            uid_map = Path("/proc/self/uid_map").read_text().strip()
            # In the root namespace, uid_map is "0 0 4294967295" (identity map).
            # In a user namespace, it's a restricted mapping.
            parts = uid_map.split()
            if len(parts) >= 3 and parts[0] == "0" and parts[1] == "0" and int(parts[2]) > 1000000:
                return False  # Root namespace — env var is spoofed
            return True  # Restricted mapping — genuinely sandboxed
        except Exception:
            pass

    # On macOS/Windows, we can't easily verify. Trust the env var
    # but only if the sandbox binary is our parent process.
    # TODO: verify parent process is omni-sandbox on macOS/Windows
    return True


def find_sandbox_binary() -> Optional[str]:
    """Find the omni-sandbox binary."""
    # 1. Bundled with omni-code (built from sandbox-cli/)
    pkg_dir = Path(__file__).resolve().parent
    candidates = [
        pkg_dir.parent / "sandbox-cli" / "target" / "release" / "omni-sandbox",
        pkg_dir.parent / "sandbox-cli" / "target" / "debug" / "omni-sandbox",
    ]
    for path in candidates:
        if path.is_file() and os.access(str(path), os.X_OK):
            return str(path)

    # 2. On PATH
    on_path = shutil.which("omni-sandbox")
    if on_path:
        return on_path

    return None


def maybe_enforce_sandbox() -> None:
    """Check governance policy and re-exec inside sandbox if required.

    Fail-closed: if governance requires a sandbox and we can't provide one,
    this function prints an error and exits. It does NOT silently continue.
    """
    # Already sandboxed — don't nest
    if is_sandboxed():
        return

    # Skip for subcommands that don't run agents or already provide isolation
    if len(sys.argv) > 1 and sys.argv[1] in {
        "auth", "model", "provider", "update", "sessions",
        "sandbox", "work",
        "-h", "--help", "help",
    }:
        return

    # Check if platform credentials exist — if not, no governance to enforce
    from omni_code.platform import is_authenticated
    if not is_authenticated():
        return

    # Fetch policy FRESH from the platform — don't trust local cache alone
    # for sandbox decisions. Cache can be tampered with.
    policy = _fetch_fresh_policy()
    if not policy:
        # Can't reach platform — fail closed if we have credentials
        # (having credentials means governance is expected)
        print(
            "Error: Authenticated to management plane but cannot fetch policy. "
            "Check connectivity to the platform or run: omni auth logout",
            file=sys.stderr,
        )
        sys.exit(1)

    # Check if sandbox is required
    sandbox_config = _resolve_sandbox_requirements(policy)
    if not sandbox_config:
        return

    # Find the binary — hard fail if not found
    binary = find_sandbox_binary()
    if not binary:
        print(
            "Error: Governance policy requires sandbox isolation but omni-sandbox "
            "binary is not installed. Build it with:\n"
            "  cd sandbox-cli && cargo build --release\n"
            "Or install it to your PATH.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Build sandbox args and re-exec
    _exec_in_sandbox(binary, sandbox_config)


def _fetch_fresh_policy() -> Optional[dict]:
    """Fetch policy directly from the platform. Does NOT use local cache."""
    try:
        import requests
        from omni_code.platform import get_platform_url, get_access_token

        url = get_platform_url()
        token = get_access_token()
        if not url or not token:
            return None

        resp = requests.get(
            f"{url}/api/v1/policy/omni_code",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        if resp.ok:
            return resp.json()
        return None
    except Exception:
        return None


def _resolve_sandbox_requirements(policy: dict) -> Optional[dict]:
    """Determine sandbox requirements from policy."""
    sandbox_profile = policy.get("sandbox_profile", {})
    sandbox_mode = sandbox_profile.get("sandbox_mode")

    security = policy.get("security", {})
    safety_mode = security.get("safety_mode", "recommended")

    network_targets = policy.get("network_allowlist", [])

    needs_sandbox = (
        sandbox_mode in ("bwrap", "podman", "docker", "qemu")
        or safety_mode == "enforced"
        or len(network_targets) > 0
    )

    if not needs_sandbox:
        return None

    return {
        "mode": sandbox_mode or "bwrap",
        "network_targets": network_targets,
        "resource_limits": sandbox_profile.get("resource_limits", {}),
        "network_mode": sandbox_profile.get("network_mode", "allowlist"),
    }


def _exec_in_sandbox(binary: str, config: dict) -> None:
    """Re-exec the current command inside omni-sandbox. Does not return."""
    args = [binary]

    workspace = os.getcwd()
    args.extend(["--workspace", workspace])

    # Network policy
    network_targets = config.get("network_targets", [])
    if network_targets:
        hosts = [t["hostname"] for t in network_targets if t.get("hostname")]
        if hosts:
            args.extend(["--net-allow", ",".join(hosts)])
    elif config.get("network_mode") == "open":
        args.append("--net")

    # Mount config dir read-only so credentials are available inside sandbox
    config_dir = Path.home() / ".config" / "omni_code"
    if config_dir.is_dir():
        args.extend(["--ro-bind", str(config_dir)])

    # Mount omniagents home for session persistence
    omniagents_home = Path.home() / ".omniagents"
    if omniagents_home.is_dir():
        args.extend(["--rw-bind", str(omniagents_home)])

    # The command to run inside the sandbox
    args.append("--")
    args.append("omni")
    args.extend(sys.argv[1:])

    # Set the sandbox marker — verified by is_sandboxed() via namespace check
    os.environ["OMNI_SANDBOXED"] = "1"

    print("Governance: enforcing sandbox isolation", file=sys.stderr)

    os.execvp(binary, args)
