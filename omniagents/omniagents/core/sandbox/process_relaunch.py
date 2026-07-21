"""Re-exec the current Python process inside the ``omni-sandbox`` CLI.

Governed products (omni-code's ``governance.py`` preflight) need one
mechanical primitive: "relaunch this very command inside ``omni-sandbox
exec`` with the right binds so the sandboxed copy can still import
itself".  That mechanic is domain-generic; only the *policy translation*
(which platform profile applies, what its limits mean) belongs to the
product.  This module owns the mechanics:

* :func:`is_sandboxed` — authoritative detection that we are *already*
  inside a sandbox (Linux user-namespace check via ``/proc/self/uid_map``,
  ``OMNI_SANDBOXED`` env fallback elsewhere), so enforcement never nests.
* :func:`find_sandbox_binary` — binary discovery in the order
  ``OMNI_SANDBOX_BIN`` env var → caller-supplied packaged/sibling
  candidates → ``PATH``.  Returns ``None`` when absent; the caller owns
  the fail-closed refusal (its message names product install channels).
* :func:`editable_source_dirs` — source directories of ``pip install -e``
  installs in a venv (legacy ``.pth`` and modern ``__editable___*_finder``
  layouts); those live outside the venv and must be bind-mounted for
  imports to resolve inside the sandbox.
* :func:`omni_sandbox_exec_argv` — the one canonical ``omni-sandbox exec``
  argv builder, shared with the ``process`` sandbox backend
  (:mod:`omniagents.core.sandbox.process_extended`) so the wire format
  cannot drift between the two.
* :class:`SandboxRelaunchRequest` + :func:`relaunch_in_sandbox` — the
  typed request and the ``os.execvp`` re-exec itself.

Fail-closed semantics (byte-compatible with omni-code's governance, whose
tests pin the message shapes):

* A host-filtered network allowlist cannot be enforced by the process
  sandbox (bwrap/sandbox-exec/AppContainer have no reliable per-host
  egress filtering) — refuse to run rather than silently granting full
  egress.
* A disk quota cannot be enforced — refuse to run rather than silently
  dropping the restriction.
* Security-profile RELAXATIONS the sandbox cannot honor (it has no flags
  to weaken seccomp or the read-only root) are ignored with a stderr
  note: running *stricter* than asked is safe.
* ``pids_limit`` is passed through (``setrlimit(RLIMIT_NPROC)`` inside
  the CLI).
"""

from __future__ import annotations

import ast
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import NoReturn, Optional, Sequence

__all__ = [
    "SandboxBind",
    "SandboxRelaunchRequest",
    "build_relaunch_argv",
    "editable_source_dirs",
    "find_sandbox_binary",
    "is_sandboxed",
    "omni_sandbox_exec_argv",
    "relaunch_in_sandbox",
]


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


def find_sandbox_binary(
    candidates: Sequence[str | Path] = (),
) -> Optional[str]:
    """Find the omni-sandbox binary, or ``None`` when nothing resolves.

    Search order:

    1. ``OMNI_SANDBOX_BIN`` env var (explicit override; skipped, not
       fatal, when it does not point at an executable file)
    2. *candidates*, in order — the product's packaged-assets location(s)
       and sibling-checkout build paths
    3. ``PATH``

    The caller owns the missing-binary refusal: its error message names
    product-specific install channels this module cannot know.
    """
    env_bin = os.getenv("OMNI_SANDBOX_BIN")
    if env_bin:
        path = Path(env_bin)
        if path.is_file() and os.access(str(path), os.X_OK):
            return str(path)

    for candidate in candidates:
        path = Path(candidate)
        if path.is_file() and os.access(str(path), os.X_OK):
            return str(path)

    on_path = shutil.which("omni-sandbox")
    if on_path:
        return on_path

    return None


def editable_source_dirs(venv_dir: Path) -> list[Path]:
    """Return source directories of editable installs in the given venv.

    Handles two pip layouts:
      * Legacy ``.pth`` files containing literal absolute paths.
      * Modern ``__editable___<name>_finder.py`` modules with a ``MAPPING``
        dict mapping import names to absolute source paths.

    Those paths live outside the venv and must be bind-mounted separately
    for imports to resolve inside the sandbox.
    """
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


@dataclass(frozen=True)
class SandboxBind:
    """One bind mount for the sandboxed process (read-only by default)."""

    path: str
    writable: bool = False


def omni_sandbox_exec_argv(
    binary: str,
    workspace: str,
    *,
    allow_net: bool = False,
    pids_limit: int | None = None,
    protected_subpaths: Sequence[str] = (),
    binds: Sequence[SandboxBind] = (),
    command: Sequence[str] = (),
) -> list[str]:
    """Build the canonical ``omni-sandbox exec`` argv.

    The single wire-format authority, shared by the ``process`` sandbox
    backend and the governance relaunch path.  Every flag emitted here is
    one the Rust CLI accepts (clap hard-fails on unknown flags, which
    would break sandboxing entirely):

    * ``--net`` only for unrestricted network (the CLI's ``--net-allow``
      is a hard error by design and is never emitted),
    * one ``--protect`` per subpath — a comma-joined value would be read
      as a single, wrong subpath name; no subpaths → no flag, so the
      binary's own defaults apply,
    * ``--ro-bind`` / ``--rw-bind`` per mount, in caller order.
    """
    argv = [binary, "exec", "--workspace", workspace]
    if allow_net:
        argv.append("--net")
    if pids_limit:
        argv.extend(["--pids-limit", str(pids_limit)])
    for subpath in protected_subpaths:
        argv.extend(["--protect", subpath])
    for bind in binds:
        argv.extend(["--rw-bind" if bind.writable else "--ro-bind", bind.path])
    argv.append("--")
    argv.extend(command)
    return argv


@dataclass(frozen=True)
class SandboxRelaunchRequest:
    """What to enforce when re-execing the current process under omni-sandbox.

    The caller (a product's governance layer) translates its policy into
    this request; the helper owns argv construction, venv/editable-source
    bind discovery, the fail-closed refusals, and the ``execvp``.

    Attributes:
        workspace: Directory mounted read-write as the sandbox workspace.
        network: ``"none"`` (default, egress blocked) or ``"unrestricted"``
            (``--net``).
        network_allow_hosts: Host allowlist the policy demands. Non-empty
            → fail closed: the process sandbox cannot do per-host egress
            filtering.
        pids_limit: Max process count, passed through as ``--pids-limit``.
        disk_limit_gb: Disk quota the policy demands. Set → fail closed:
            the process sandbox cannot enforce disk quotas.
        ignored_relaxations: Names of security-profile relaxations the
            sandbox cannot honor (e.g. ``"seccomp_enabled=false"``);
            noted on stderr, then ignored — running stricter is safe.
        protected_subpaths: One ``--protect`` per entry.
        binds: Extra mounts (credentials dir, session home, ...), emitted
            in order before the automatically discovered venv/editable
            binds.
        backend_label: Backend name interpolated into refusal messages.
        argv: The command to re-exec, defaulting to ``sys.argv``. Its
            first element is resolved via ``os.path.realpath`` so the
            SAME entry script (e.g. a launcher-managed venv's) re-execs —
            not whatever is first on the sandbox's PATH.
    """

    workspace: str
    network: str = "none"
    network_allow_hosts: Sequence[str] = ()
    pids_limit: int | None = None
    disk_limit_gb: int | float | None = None
    ignored_relaxations: Sequence[str] = ()
    protected_subpaths: Sequence[str] = ()
    binds: Sequence[SandboxBind] = field(default_factory=tuple)
    backend_label: str = "bwrap"
    argv: Sequence[str] | None = None


def build_relaunch_argv(binary: str, request: SandboxRelaunchRequest) -> list[str]:
    """Build the full re-exec argv for *request*, enforcing fail-closed rules.

    Prints a refusal and raises ``SystemExit(1)`` for restrictions the
    process sandbox cannot enforce (host-filtered network allowlist, disk
    quota); prints a note for relaxations it cannot honor. Message shapes
    are byte-compatible with omni-code's governance, whose tests pin them.
    """
    # Fail closed: the process sandbox (bwrap/sandbox-exec/AppContainer) can
    # only do all-or-nothing network isolation. If the policy demands a
    # host-filtered allowlist, we cannot honor it here — refuse to run rather
    # than silently granting full egress.
    if request.network_allow_hosts:
        print(
            "Error: Governance policy requires a host-filtered network "
            f"allowlist ({', '.join(request.network_allow_hosts)}), but the "
            "process sandbox "
            f"backend ({request.backend_label}) cannot enforce "
            "host-level network filtering. Use a container backend that "
            "supports network allowlisting (set OMNI_SANDBOX_BACKEND="
            "docker or podman, if the policy provides one), or ask your "
            "platform admin to set the policy's network mode to 'open' "
            "or remove the allowlist.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Fail closed: a disk quota is a restriction the process sandbox
    # (bwrap/sandbox-exec/AppContainer) cannot enforce — refuse to run rather
    # than silently dropping it.
    if request.disk_limit_gb:
        print(
            "Error: Governance policy sets a disk limit "
            f"({request.disk_limit_gb} GB), but the process sandbox backend "
            f"({request.backend_label}) cannot enforce disk quotas. "
            "Use a container backend that supports disk limits (set "
            "OMNI_SANDBOX_BACKEND=docker or podman, if the policy provides "
            "one), or ask your platform admin to remove disk_gb from this "
            "profile's resource limits.",
            file=sys.stderr,
        )
        sys.exit(1)

    # The process sandbox has no flags to RELAX isolation (seccomp is always
    # on; the root filesystem is always read-only). Ignoring a relaxation
    # means running stricter than the policy asks, which is safe — note it
    # and continue.
    if request.ignored_relaxations:
        print(
            "Governance: process sandbox ignores security-profile relaxations "
            f"({', '.join(request.ignored_relaxations)}); running with full isolation.",
            file=sys.stderr,
        )

    # The command to run inside the sandbox.
    # Use the absolute path of the entry script so we re-exec the SAME
    # program (e.g. a launcher-managed venv's), not whatever happens to be
    # first on the sandbox's PATH (which can pick up an unrelated install —
    # e.g. a mise shim — that lacks our dependencies).
    outer_argv = list(request.argv) if request.argv is not None else list(sys.argv)
    inner_entry = os.path.realpath(outer_argv[0])

    binds = list(request.binds)

    # The venv that owns this entry script must be visible inside the sandbox
    # so the inner exec can find both the script and its Python interpreter.
    venv_dir = Path(inner_entry).parent.parent
    if venv_dir.is_dir():
        binds.append(SandboxBind(str(venv_dir)))

    # Editable installs (`pip install -e`) keep their source outside the venv;
    # without binding those directories the sandboxed Python can't import them.
    for src_dir in editable_source_dirs(venv_dir):
        binds.append(SandboxBind(str(src_dir)))

    return omni_sandbox_exec_argv(
        binary,
        request.workspace,
        allow_net=request.network == "unrestricted",
        pids_limit=request.pids_limit,
        protected_subpaths=request.protected_subpaths,
        binds=binds,
        command=[inner_entry, *outer_argv[1:]],
    )


def relaunch_in_sandbox(binary: str, request: SandboxRelaunchRequest) -> NoReturn:
    """Re-exec the current command inside omni-sandbox. Does not return."""
    args = build_relaunch_argv(binary, request)

    # Set the sandbox marker — verified by is_sandboxed() via namespace check
    os.environ["OMNI_SANDBOXED"] = "1"

    print("Governance: enforcing sandbox isolation", file=sys.stderr)

    os.execvp(binary, args)
