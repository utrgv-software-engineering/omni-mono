"""Generic pipx self-update flow for products wrapping the framework.

Each omniagents product is a distinct pipx-installed distribution served
from its own PEP-503 simple index. Products describe themselves with an
:class:`UpdaterConfig` — simple-index URL, pipx package name, cache dir,
env-var prefix — and reuse the whole flow instead of copying it:

- :func:`check_for_update` — scrape the simple index and compare against
  the installed distribution version.
- :func:`maybe_print_update_notice` — throttled startup notice backed by
  a JSON cache file (``update.json`` in the config's cache dir), with a
  shorter retry interval after a failed check.
- :func:`run_update_command` — the ``<prog> update`` CLI flow
  (check/confirm/upgrade via ``pipx upgrade``), parameterized by program
  name like :func:`omniagents.cli.run_codex_auth_command` and
  :func:`omniagents.core.models.cli.run_model_command`.

Environment variables are derived from ``UpdaterConfig.env_prefix``
(e.g. prefix ``"OMNI_CODE"``):

- ``<PREFIX>_UPDATE_CHECK`` — set to ``0``/``false``/``no`` to disable
  the startup update notice entirely.
- ``<PREFIX>_UPDATE_CHECK_INTERVAL_SECONDS`` — seconds between index
  checks for the startup notice (default 86400; failures retry after at
  most 900 seconds).

Lifted from omni-code.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, replace
from importlib import metadata
from pathlib import Path
from typing import Callable, Iterable, Optional, Union

import requests

__all__ = [
    "UpdaterConfig",
    "UpdateCheckResult",
    "check_for_update",
    "fetch_latest_version",
    "get_current_version",
    "get_update_cache_path",
    "maybe_print_update_notice",
    "run_pipx_upgrade",
    "run_update_command",
]

DEFAULT_CHECK_INTERVAL_SECONDS = 60 * 60 * 24
DEFAULT_FAILURE_RETRY_SECONDS = 15 * 60


@dataclass(frozen=True)
class UpdaterConfig:
    """Product-side knobs for the generic self-update flow.

    Args:
        simple_index_url: PEP-503 "simple" project page listing the
            product's release files (e.g. a Gemfury/devpi project URL).
        package_name: Distribution name as known to pipx and installed
            metadata (e.g. ``"omni-code"``).
        cache_dir: Directory holding the update-notice throttle cache
            (``update.json``). May be a ``Path`` or a zero-arg callable
            returning one, so products can defer to an env-sensitive
            config-dir lookup at call time.
        env_prefix: Prefix for the flow's environment variables — see
            the module docstring for the ``<PREFIX>_*`` names produced.
        update_command_hint: Command shown in the update notice (e.g.
            ``"omni update"``). Defaults to ``pipx upgrade <package>``.
        pip_extra_index_url: ``--extra-index-url`` passed to pipx during
            upgrade. Defaults to ``simple_index_url`` with its trailing
            ``<package>/`` segment stripped (the index root).
    """

    simple_index_url: str
    package_name: str
    cache_dir: Union[Path, Callable[[], Path]]
    env_prefix: str
    update_command_hint: Optional[str] = None
    pip_extra_index_url: Optional[str] = None

    def resolve_cache_dir(self) -> Path:
        if callable(self.cache_dir):
            return Path(self.cache_dir())
        return Path(self.cache_dir)

    @property
    def resolved_update_command_hint(self) -> str:
        return self.update_command_hint or f"pipx upgrade {self.package_name}"

    @property
    def resolved_extra_index_url(self) -> Optional[str]:
        if self.pip_extra_index_url:
            return self.pip_extra_index_url
        trimmed = self.simple_index_url.rstrip("/")
        suffix = "/" + self.package_name
        if trimmed.endswith(suffix):
            return trimmed[: -len(self.package_name)]
        return None

    def env(self, name: str) -> Optional[str]:
        return os.getenv(f"{self.env_prefix}_{name}")


@dataclass(frozen=True)
class UpdateCheckResult:
    current_version: str
    latest_version: Optional[str]

    @property
    def update_available(self) -> bool:
        if not self.latest_version:
            return False
        current = _parse_version_tuple(self.current_version)
        latest = _parse_version_tuple(self.latest_version)
        if current is None or latest is None:
            return self.current_version != self.latest_version
        return latest > current


def get_current_version(package_name: str) -> str:
    try:
        return metadata.version(package_name)
    except metadata.PackageNotFoundError:
        return "0.0.0"


def get_update_cache_path(config: UpdaterConfig) -> Path:
    return config.resolve_cache_dir() / "update.json"


def _parse_version_tuple(version: str) -> Optional[tuple[int, ...]]:
    if not re.fullmatch(r"\d+(?:\.\d+)*", version):
        return None
    return tuple(int(part) for part in version.split("."))


def _extract_versions_from_simple_index(html: str, package_name: str) -> list[str]:
    """Collect release versions for ``package_name`` from a simple-index page."""
    normalized = re.sub(r"[-_.]+", "-", package_name).lower()
    dashed_prefix = normalized + "-"
    snake_prefix = normalized.replace("-", "_") + "-"

    versions: set[str] = set()

    for match in re.finditer(r"href=[\"']([^\"']+)[\"']", html, flags=re.IGNORECASE):
        href = match.group(1)
        href = href.split("#", 1)[0].split("?", 1)[0]
        filename = href.rsplit("/", 1)[-1]

        if filename.endswith(".whl"):
            for prefix in (snake_prefix, dashed_prefix):
                if filename.startswith(prefix):
                    rest = filename[len(prefix) :]
                    if rest:
                        versions.add(rest.split("-", 1)[0])
                    break
            continue

        for prefix in (dashed_prefix, snake_prefix):
            if filename.startswith(prefix):
                rest = filename[len(prefix) :]
                for suffix in (".tar.gz", ".zip"):
                    if rest.endswith(suffix):
                        versions.add(rest[: -len(suffix)])
                        break
                break

    return sorted(versions)


def _pick_latest_version(versions: Iterable[str]) -> Optional[str]:
    parsed: list[tuple[tuple[int, ...], str]] = []
    for version in versions:
        parsed_tuple = _parse_version_tuple(version)
        if parsed_tuple is None:
            continue
        parsed.append((parsed_tuple, version))

    if not parsed:
        return None

    parsed.sort(key=lambda item: item[0])
    return parsed[-1][1]


def fetch_latest_version(
    *,
    simple_index_url: str,
    package_name: str,
    timeout_seconds: float = 2.5,
) -> Optional[str]:
    try:
        response = requests.get(simple_index_url, timeout=timeout_seconds)
    except requests.RequestException:
        return None
    if response.status_code != 200:
        return None

    versions = _extract_versions_from_simple_index(response.text, package_name)
    return _pick_latest_version(versions)


def check_for_update(config: UpdaterConfig) -> UpdateCheckResult:
    current = get_current_version(config.package_name)
    latest = fetch_latest_version(
        simple_index_url=config.simple_index_url,
        package_name=config.package_name,
    )
    return UpdateCheckResult(current_version=current, latest_version=latest)


def _load_update_cache(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except FileNotFoundError:
        return {}
    except Exception:
        return {}
    return {}


def _write_update_cache(path: Path, data: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
    except OSError:
        pass  # read-only filesystem (e.g., inside a sandbox container)


def _get_check_interval_seconds(config: UpdaterConfig) -> int:
    raw = config.env("UPDATE_CHECK_INTERVAL_SECONDS")
    if not raw:
        return DEFAULT_CHECK_INTERVAL_SECONDS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_CHECK_INTERVAL_SECONDS
    return max(0, value)


def maybe_print_update_notice(config: UpdaterConfig) -> None:
    """Print a throttled "update available" notice for startup banners.

    Checks at most once per ``<PREFIX>_UPDATE_CHECK_INTERVAL_SECONDS``
    (cached in ``update.json`` under the config's cache dir), retrying
    sooner after failures; disabled by ``<PREFIX>_UPDATE_CHECK=0``.
    """
    if (config.env("UPDATE_CHECK") or "1").strip().lower() in ("0", "false", "no"):
        return

    cache_path = get_update_cache_path(config)
    cache = _load_update_cache(cache_path)

    interval = _get_check_interval_seconds(config)
    if cache.get("status") == "error":
        interval = min(interval, DEFAULT_FAILURE_RETRY_SECONDS)
    now = time.time()
    last_checked = cache.get("last_checked")

    result: Optional[UpdateCheckResult] = None

    if isinstance(last_checked, (int, float)) and now - float(last_checked) < interval:
        cached_latest = cache.get("latest_version")
        result = UpdateCheckResult(
            current_version=get_current_version(config.package_name),
            latest_version=cached_latest,
        )
    else:
        try:
            latest = fetch_latest_version(
                simple_index_url=config.simple_index_url,
                package_name=config.package_name,
            )
        except Exception:
            latest = None

        if latest is None:
            latest = cache.get("latest_version")

        result = UpdateCheckResult(
            current_version=get_current_version(config.package_name),
            latest_version=latest,
        )
        _write_update_cache(
            cache_path,
            {
                "last_checked": now,
                "latest_version": latest,
                "status": "ok" if latest else "error",
            },
        )

    if result.update_available and result.latest_version:
        print(
            f"Update available ({result.current_version} -> {result.latest_version})."
            f" Run: {config.resolved_update_command_hint}"
        )


def _ensure_pipx_available(config: UpdaterConfig) -> None:
    if shutil.which("pipx"):
        return
    raise RuntimeError(
        f"pipx is required to update {config.package_name} (pipx not found on PATH)"
    )


def run_pipx_upgrade(config: UpdaterConfig) -> None:
    _ensure_pipx_available(config)
    cmd = ["pipx", "upgrade", config.package_name]
    extra_index_url = config.resolved_extra_index_url
    if extra_index_url:
        cmd += ["--pip-args", f"--extra-index-url {extra_index_url}"]
    subprocess.run(cmd, check=True)


def _prompt_confirm(prompt: str) -> bool:
    raw = input(prompt).strip().lower()
    return raw in ("y", "yes")


def run_update_command(
    argv=None,
    *,
    config: UpdaterConfig,
    prog: Optional[str] = None,
) -> int:
    """Run a ``<prog> update`` command (check, confirm, pipx upgrade).

    Public so products wrapping the framework CLI can reuse the exact
    same flow under their own program name (pass ``prog``, e.g.
    ``"omni update"``) instead of copying it. Returns a process exit
    status: 0 on success/up-to-date/declined, 1 when the latest version
    cannot be determined or when ``--check`` finds an update available.
    """
    prog = prog or f"{config.package_name} update"
    parser = argparse.ArgumentParser(
        prog=prog,
        description=f"Update {config.package_name} (pipx install recommended)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Only check for updates (exit 1 if an update is available)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Do not prompt; proceed with upgrade",
    )
    parser.add_argument(
        "--simple-index-url",
        default=config.simple_index_url,
        help="Override the simple index URL",
    )

    args = parser.parse_args(argv)

    if args.simple_index_url != config.simple_index_url:
        config = replace(config, simple_index_url=args.simple_index_url)

    result = check_for_update(config)

    if not result.latest_version:
        print("Unable to determine latest version.")
        return 1

    if not result.update_available:
        print(f"{config.package_name} is up to date ({result.current_version}).")
        return 0

    print(f"Update available: {result.current_version} -> {result.latest_version}")

    if args.check:
        return 1

    if not args.yes:
        if not _prompt_confirm("Run pipx upgrade now? [y/N]: "):
            return 0

    run_pipx_upgrade(config)
    rerun = prog.split()[0]
    print(f"Upgrade complete. Re-run `{rerun}` to use the new version.")
    return 0
