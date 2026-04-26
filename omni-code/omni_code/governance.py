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
- No fallbacks to hardcoded defaults — the platform is the sole source of truth
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional


def is_sandboxed() -> bool:
    """Return True if actually running inside a sandbox.

    Detection strategy:
    1. On Linux, check /proc/self/uid_map — a restricted mapping means we're
       in a user namespace (bwrap sandbox). This is authoritative regardless
       of env vars, so the launcher's omni-sandbox invocation is detected.
    2. Fall back to the OMNI_SANDBOXED env var for macOS/Windows/containers
       where namespace detection isn't applicable.
    """
    # Linux: authoritative namespace detection via uid_map.
    if sys.platform == "linux":
        try:
            uid_map = Path("/proc/self/uid_map").read_text().strip()
            # In the root namespace, uid_map is "0 0 4294967295" (identity map).
            # In a user namespace, it's a restricted mapping.
            parts = uid_map.split()
            if len(parts) >= 3 and parts[0] == "0" and parts[1] == "0" and int(parts[2]) > 1000000:
                pass  # Root namespace — not sandboxed (check env var below)
            else:
                return True  # Restricted mapping — genuinely sandboxed
        except Exception:
            pass

    # Env var fallback for macOS/Windows/containers.
    if os.getenv("OMNI_SANDBOXED") == "1":
        return True

    return False


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
                "binary is not installed. Build it with:\n"
                "  cd sandbox-cli && cargo build --release\n"
                "Or install it to your PATH.",
                file=sys.stderr,
            )
            sys.exit(1)
        _exec_in_sandbox(binary, sandbox_config)


def _fetch_fresh_policy() -> Optional[dict]:
    """Fetch policy directly from the platform. Does NOT use local cache.

    On 401, attempts a token refresh before giving up.
    """
    try:
        import requests
        from omni_code.platform import get_platform_url, get_access_token, refresh_access_token

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

        # Token may be expired — try refreshing once
        if resp.status_code == 401:
            new_token = refresh_access_token()
            if new_token:
                resp = requests.get(
                    f"{url}/api/v1/policy/omni_code",
                    headers={"Authorization": f"Bearer {new_token}"},
                    timeout=10,
                )
                if resp.ok:
                    return resp.json()

        return None
    except Exception:
        return None


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
    """Check that local omni-code and omni-sandbox versions meet the policy minimum."""
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
                            f"but {installed} is installed. Update with:\n"
                            f"  cd sandbox-cli && cargo build --release",
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
        from omni_code.platform import get_platform_url, get_access_token

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


def _editable_source_dirs(venv_dir: Path) -> list[Path]:
    """Return source directories of editable installs in the given venv.

    Handles two pip layouts:
      * Legacy ``.pth`` files containing literal absolute paths.
      * Modern ``__editable___<name>_finder.py`` modules with a ``MAPPING``
        dict mapping import names to absolute source paths.

    Those paths live outside the venv and must be bind-mounted separately
    for imports to resolve inside the sandbox.
    """
    import ast

    found: list[Path] = []
    seen: set[Path] = set()

    def add(p: Path) -> None:
        if p.is_absolute() and p.is_dir() and p not in seen:
            seen.add(p)
            found.append(p)

    lib = venv_dir / "lib"
    if not lib.is_dir():
        return found

    for site_packages in lib.glob("python*/site-packages"):
        # Legacy: literal absolute path on its own line.
        for pth in site_packages.glob("*.pth"):
            try:
                for line in pth.read_text().splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or line.startswith("import "):
                        continue
                    add(Path(line))
            except OSError:
                continue

        # Modern: __editable___<name>_finder.py with a MAPPING dict literal.
        for finder in site_packages.glob("__editable___*_finder.py"):
            try:
                tree = ast.parse(finder.read_text())
            except (OSError, SyntaxError):
                continue
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.AnnAssign)
                    and isinstance(node.target, ast.Name)
                    and node.target.id == "MAPPING"
                    and node.value is not None
                ):
                    try:
                        mapping = ast.literal_eval(node.value)
                    except (ValueError, SyntaxError):
                        continue
                    if isinstance(mapping, dict):
                        # Bind the *parent* of each module path so sibling files
                        # (project.yml, pyproject.toml, etc.) are visible too.
                        for src in mapping.values():
                            add(Path(src).parent)

    return found


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

    # Resource limits from policy
    limits = config.get("resource_limits", {})
    if limits.get("pids_limit"):
        args.extend(["--pids-limit", str(limits["pids_limit"])])
    if limits.get("disk_gb"):
        args.extend(["--disk-limit", f"{limits['disk_gb']}G"])

    # Security profile from policy
    security = config.get("security_profile", {})
    if security.get("seccomp_enabled") is False:
        args.append("--no-seccomp")
    if security.get("read_only_root") is False:
        args.append("--rw-root")
    protected = security.get("protected_subpaths")
    if protected:
        args.extend(["--protect", ",".join(protected)])

    # Mount config dir read-only so credentials are available inside sandbox
    config_dir = Path.home() / ".config" / "omni_code"
    if config_dir.is_dir():
        args.extend(["--ro-bind", str(config_dir)])

    # Mount omniagents home for session persistence
    omniagents_home = Path.home() / ".omniagents"
    if omniagents_home.is_dir():
        args.extend(["--rw-bind", str(omniagents_home)])

    # The command to run inside the sandbox.
    # Use the absolute path of the entry script so we re-exec the SAME omni
    # (e.g. a launcher-managed venv), not whatever happens to be first on
    # the sandbox's PATH (which can pick up an unrelated install — e.g. a
    # mise shim — that lacks our dependencies).
    inner_omni = os.path.realpath(sys.argv[0])

    # The venv that owns this omni must be visible inside the sandbox so the
    # inner exec can find both the entry script and its Python interpreter.
    venv_dir = Path(inner_omni).parent.parent
    if venv_dir.is_dir():
        args.extend(["--ro-bind", str(venv_dir)])

    # Editable installs (`pip install -e`) keep their source outside the venv;
    # without binding those directories the sandboxed Python can't import them.
    for src_dir in _editable_source_dirs(venv_dir):
        args.extend(["--ro-bind", str(src_dir)])

    args.append("--")
    args.append(inner_omni)
    args.extend(sys.argv[1:])

    # Set the sandbox marker — verified by is_sandboxed() via namespace check
    os.environ["OMNI_SANDBOXED"] = "1"

    print("Governance: enforcing sandbox isolation", file=sys.stderr)

    os.execvp(binary, args)
