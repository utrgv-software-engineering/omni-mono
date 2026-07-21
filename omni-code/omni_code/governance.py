"""Governance-driven sandbox enforcement — pure policy translation.

When authenticated to the management plane, checks the policy and, if it
requires sandboxing, translates the sandbox profile into a framework
relaunch request and re-execs the current command inside omni-sandbox.
The mechanics — namespace detection, binary discovery, venv/editable bind
computation, argv construction, execvp — live in
``omniagents.core.sandbox.process_relaunch``; this module owns only
omni-code policy: fresh policy fetch, profile resolution (with the legacy
single-profile fallback), governed-image pull with digest pinning,
version gating, and the fail-closed decision tree. The process sandbox is
all-or-nothing for network: a policy demanding a host-filtered allowlist
causes a hard stop.

Security model:
- Fail closed: if governance is required and can't be enforced, refuse to run
- OMNI_SANDBOXED is verified by checking we're actually in a namespace,
  not just by trusting the env var
- Policy is fetched fresh from the platform, not read from local cache alone
- Missing sandbox binary = hard stop, not a warning
- No fallbacks to hardcoded defaults — the platform is the sole source of truth
"""

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from omniagents.core.sandbox.process_relaunch import (
    SandboxBind,
    SandboxRelaunchRequest,
    is_sandboxed,  # noqa: F401  (re-exported; also used below)
    relaunch_in_sandbox,
)
from omniagents.core.sandbox.process_relaunch import (
    find_sandbox_binary as _find_sandbox_binary,
)


def find_sandbox_binary() -> Optional[str]:
    """Find the omni-sandbox binary.

    The crate lives in the standalone omni-sandbox repository (extracted
    from this repo's former sandbox-cli/). Search order (framework helper):

    1. ``OMNI_SANDBOX_BIN`` env var (explicit override)
    2. Packaged ``omni_code/assets/bin/`` (wheel-shipped binary)
    3. Sibling checkout ``../omni-sandbox/target/{release,debug}/``
    4. ``PATH``
    """
    pkg_dir = Path(__file__).resolve().parent
    return _find_sandbox_binary(
        candidates=[
            # 2. Packaged with omni-code
            pkg_dir / "assets" / "bin" / "omni-sandbox",
            # 3. Sibling checkout of the standalone omni-sandbox repo
            pkg_dir.parent.parent / "omni-sandbox" / "target" / "release" / "omni-sandbox",
            pkg_dir.parent.parent / "omni-sandbox" / "target" / "debug" / "omni-sandbox",
        ]
    )


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
        "sandbox", "describe",
        "-h", "--help", "help",
    }:
        return

    # Check if platform credentials exist — if not, no governance to enforce
    from omniagents.core.providers.platform.client import is_authenticated
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

    # Resolve sandbox config from the new sandbox_profiles array
    sandbox_config = _resolve_sandbox_requirements(policy)
    if not sandbox_config:
        return

    backend = sandbox_config["backend"]

    # For container backends (docker/podman), pull the governed image first
    if backend in ("docker", "podman"):
        _pull_governed_image(sandbox_config, policy)
        # Container sandbox is managed by the compute provider, not bwrap re-exec
        return

    # For QEMU, the launcher handles VM lifecycle — not a re-exec scenario
    if backend == "qemu":
        return

    # For bwrap, check version requirements then re-exec
    if backend == "bwrap":
        _check_bwrap_versions(sandbox_config)
        binary = find_sandbox_binary()
        if not binary:
            print(
                "Error: Governance policy requires sandbox isolation but omni-sandbox "
                "binary is not installed. Build it from the standalone omni-sandbox "
                "repo (sibling checkout ../omni-sandbox):\n"
                "  cd ../omni-sandbox && cargo build --release\n"
                "Or set OMNI_SANDBOX_BIN, or install omni-sandbox to your PATH.",
                file=sys.stderr,
            )
            sys.exit(1)
        _exec_in_sandbox(binary, sandbox_config)


def _fetch_fresh_policy() -> Optional[dict]:
    """Fetch policy directly from the platform. Does NOT use local cache —
    the cache can be tampered with, so sandbox decisions use a fresh read."""
    from omniagents.core.providers.platform.client import fetch_policy

    return fetch_policy()


def _resolve_sandbox_requirements(policy: dict) -> Optional[dict]:
    """Determine sandbox requirements from the policy's sandbox_profiles array.

    The platform returns sandbox_profiles as an array of Machine governed
    resource profiles. Each has a backend, image_spec, resource_limits,
    network_mode, security_profile, and volume_policy.

    Selection logic:
    - If OMNI_SANDBOX_BACKEND is set, pick the profile matching that backend
    - Otherwise pick the first bwrap profile (local default), then docker, then any
    """
    profiles = policy.get("sandbox_profiles", [])

    # Legacy fallback: if the policy still uses the old singular sandbox_profile
    # format, convert it. This handles older platform versions during rollout.
    if not profiles:
        legacy = policy.get("sandbox_profile", {})
        if not legacy:
            return None
        sandbox_mode = legacy.get("sandbox_mode")
        if sandbox_mode not in ("bwrap", "podman", "docker", "qemu"):
            # Check if safety_mode or network targets require sandbox
            security = policy.get("security", {})
            network_targets = policy.get("network_allowlist", [])
            if security.get("safety_mode") != "enforced" and not network_targets:
                return None
            sandbox_mode = sandbox_mode or "bwrap"

        return {
            "backend": sandbox_mode,
            "network_targets": policy.get("network_allowlist", []),
            "resource_limits": legacy.get("resource_limits", {}),
            "network_mode": legacy.get("network_mode", "allowlist"),
            "image_spec": {},
            "security_profile": {},
        }

    # New format: select the best profile for local execution.
    # Only consider profiles whose compute_target allows "local".
    local_profiles = [
        p for p in profiles
        if p.get("compute_target", "either") in ("local", "either")
    ]

    if not local_profiles:
        # All profiles are cloud-only — nothing to enforce locally.
        # The launcher will use the cloud compute API instead.
        return None

    preferred_backend = os.getenv("OMNI_SANDBOX_BACKEND")
    profile = None

    if preferred_backend:
        profile = next((p for p in local_profiles if p.get("backend") == preferred_backend), None)
        if not profile:
            print(
                f"Error: OMNI_SANDBOX_BACKEND={preferred_backend} but no matching "
                f"local sandbox profile in policy. Available: "
                f"{', '.join(p.get('backend', '?') for p in local_profiles)}",
                file=sys.stderr,
            )
            sys.exit(1)
    else:
        # Prefer bwrap for local use, then docker, then first available
        for backend_pref in ("bwrap", "docker", "podman", "qemu"):
            profile = next((p for p in local_profiles if p.get("backend") == backend_pref), None)
            if profile:
                break
        if not profile:
            profile = local_profiles[0]

    network_targets = policy.get("network_allowlist", [])

    return {
        "backend": profile["backend"],
        "variant": profile.get("variant"),
        "compute_target": profile.get("compute_target", "either"),
        "resource_id": profile.get("resource_id"),
        "image_spec": profile.get("image_spec", {}),
        "network_targets": network_targets,
        "resource_limits": profile.get("resource_limits", {}),
        "network_mode": profile.get("network_mode", "allowlist"),
        "security_profile": profile.get("security_profile", {}),
        "volume_policy": profile.get("volume_policy", {}),
        "image_pull_token_url": profile.get("image_pull_token_url"),
    }


def _check_bwrap_versions(config: dict) -> None:
    """Check that local omni-code and omni-sandbox versions meet the policy minimum.

    Stays in omni-code (not the framework relaunch helper): the image_spec
    keys are this product's deployment-policy schema, and both remediation
    messages name product install channels (the sibling omni-sandbox
    checkout, ``pip install --upgrade omni-code``) the framework cannot know.
    """
    image_spec = config.get("image_spec", {})

    required_sandbox_version = image_spec.get("sandbox_cli_version")
    if required_sandbox_version:
        binary = find_sandbox_binary()
        if binary:
            try:
                result = subprocess.run(
                    [binary, "--version"], capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0:
                    installed = result.stdout.strip().split()[-1]  # "omni-sandbox 0.2.0" → "0.2.0"
                    if not _version_gte(installed, required_sandbox_version):
                        print(
                            f"Error: Governance requires omni-sandbox >= {required_sandbox_version} "
                            f"but {installed} is installed. Update from the standalone "
                            f"omni-sandbox repo:\n"
                            f"  cd ../omni-sandbox && cargo build --release",
                            file=sys.stderr,
                        )
                        sys.exit(1)
            except Exception:
                pass  # Can't check version — proceed and let binary handle it

    required_omni_code_version = image_spec.get("omni_code_version")
    if required_omni_code_version:
        try:
            from omni_code import __version__
            if not _version_gte(__version__, required_omni_code_version):
                print(
                    f"Error: Governance requires omni-code >= {required_omni_code_version} "
                    f"but {__version__} is installed. Update with:\n"
                    f"  pip install --upgrade omni-code",
                    file=sys.stderr,
                )
                sys.exit(1)
        except ImportError:
            pass


def _version_gte(installed: str, required: str) -> bool:
    """Return True if installed version >= required version (semver-ish)."""
    try:
        inst_parts = [int(x) for x in installed.split(".")]
        req_parts = [int(x) for x in required.split(".")]
        return inst_parts >= req_parts
    except (ValueError, AttributeError):
        return False


def _pull_governed_image(config: dict, policy: dict) -> None:
    """Pull the governed container image using a platform-vended pull token.

    For docker/podman backends, the platform vends a short-lived, scoped
    ACR pull token. The user never needs direct registry credentials.
    """
    image_spec = config.get("image_spec", {})
    registry = image_spec.get("registry")
    repository = image_spec.get("repository")
    tag = image_spec.get("tag", "latest")
    expected_digest = image_spec.get("digest")

    if not repository:
        print(
            "Error: Sandbox profile has no image repository. "
            "The Machine resource may be misconfigured.",
            file=sys.stderr,
        )
        sys.exit(1)

    full_ref = f"{registry}/{repository}:{tag}" if registry else f"{repository}:{tag}"
    backend = config["backend"]  # "docker" or "podman"

    # Get pull token from the platform
    token_url = config.get("image_pull_token_url")
    if token_url and registry:
        creds = _fetch_pull_token(token_url)
        if creds:
            _registry_login(backend, registry, creds)
        else:
            print(
                f"Error: Failed to obtain pull token for {registry}. "
                "Cannot pull governed image.",
                file=sys.stderr,
            )
            sys.exit(1)

    # Pull the image
    print(f"Governance: pulling governed image {full_ref}", file=sys.stderr)
    result = subprocess.run(
        [backend, "pull", full_ref],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(
            f"Error: Failed to pull governed image {full_ref}:\n{result.stderr}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Verify digest post-pull (fail-closed on mismatch)
    if expected_digest:
        actual_digest = _get_image_digest(backend, full_ref)
        if actual_digest and actual_digest != expected_digest:
            print(
                f"Error: IMAGE DIGEST MISMATCH for {full_ref}\n"
                f"  Expected: {expected_digest}\n"
                f"  Actual:   {actual_digest}\n"
                "Image may be tampered with. Refusing to use it.",
                file=sys.stderr,
            )
            # Remove the suspect image
            subprocess.run(
                [backend, "rmi", full_ref],
                capture_output=True, text=True
            )
            sys.exit(1)

    # Store the resolved image ref for the sandbox to use
    os.environ["OMNI_GOVERNED_IMAGE"] = full_ref
    if expected_digest:
        os.environ["OMNI_GOVERNED_IMAGE_DIGEST"] = expected_digest


def _fetch_pull_token(token_url: str) -> Optional[dict]:
    """Fetch a scoped pull token from the platform's image-pull-token endpoint."""
    try:
        import requests
        from omniagents.core.providers.platform.client import get_platform_url, get_access_token

        url = get_platform_url()
        token = get_access_token()
        if not url or not token:
            return None

        # Replace {session_id} placeholder with the current session
        session_id = os.getenv("OMNI_SESSION_ID", "")
        resolved_url = token_url.replace("{session_id}", session_id)

        resp = requests.get(
            f"{url}{resolved_url}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        if resp.ok:
            return resp.json()
        return None
    except Exception:
        return None


def _registry_login(backend: str, registry: str, creds: dict) -> None:
    """Authenticate to the container registry using platform-vended credentials."""
    username = creds.get("username", "00000000-0000-0000-0000-000000000000")
    password = creds.get("password", "")

    result = subprocess.run(
        [backend, "login", registry, "--username", username, "--password-stdin"],
        input=password, capture_output=True, text=True
    )
    if result.returncode != 0:
        print(
            f"Warning: Registry login to {registry} failed: {result.stderr}",
            file=sys.stderr,
        )


def _get_image_digest(backend: str, image_ref: str) -> Optional[str]:
    """Get the digest of a locally pulled image."""
    result = subprocess.run(
        [backend, "inspect", "--format", "{{.RepoDigests}}", image_ref],
        capture_output=True, text=True
    )
    if result.returncode == 0 and result.stdout.strip():
        # Output is like: [registry/repo@sha256:abc123]
        output = result.stdout.strip().strip("[]")
        if "@" in output:
            return output.split("@", 1)[1]
    return None


def _exec_in_sandbox(binary: str, config: dict) -> None:
    """Translate the resolved policy config into a relaunch request and
    re-exec inside omni-sandbox (framework mechanics). Does not return
    when enforcement succeeds; fail-closed refusals exit(1)."""
    network_targets = config.get("network_targets", [])
    hosts = [t["hostname"] for t in network_targets if t.get("hostname")]
    limits = config.get("resource_limits", {})
    security = config.get("security_profile", {})

    # Relaxations the process sandbox cannot honor — the framework notes
    # and ignores them (running stricter than the policy asks is safe).
    ignored_relaxations = []
    if security.get("seccomp_enabled") is False:
        ignored_relaxations.append("seccomp_enabled=false")
    if security.get("read_only_root") is False:
        ignored_relaxations.append("read_only_root=false")

    binds = []
    # Mount config dir read-only so credentials are available inside sandbox
    from omniagents.core.config import product_config_dir

    config_dir = product_config_dir()
    if config_dir.is_dir():
        binds.append(SandboxBind(str(config_dir)))
    # Mount omniagents home for session persistence
    omniagents_home = Path.home() / ".omniagents"
    if omniagents_home.is_dir():
        binds.append(SandboxBind(str(omniagents_home), writable=True))

    relaunch_in_sandbox(
        binary,
        SandboxRelaunchRequest(
            workspace=os.getcwd(),
            network=(
                "unrestricted"
                if not network_targets and config.get("network_mode") == "open"
                else "none"
            ),
            network_allow_hosts=hosts,
            pids_limit=limits.get("pids_limit") or None,
            disk_limit_gb=limits.get("disk_gb") or None,
            ignored_relaxations=ignored_relaxations,
            protected_subpaths=security.get("protected_subpaths") or (),
            binds=binds,
            backend_label=config.get("backend", "bwrap"),
        ),
    )
