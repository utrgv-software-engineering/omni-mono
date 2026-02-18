import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Iterable, Optional

import requests

from omni_code.config import get_config_dir


GEMFURY_SIMPLE_INDEX_URL = "https://pypi.fury.io/ericmichael/omni-code/"
PIPX_PACKAGE_NAME = "omni-code"
DEFAULT_CHECK_INTERVAL_SECONDS = 60 * 60 * 24
DEFAULT_FAILURE_RETRY_SECONDS = 15 * 60


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


def get_current_version() -> str:
    try:
        return metadata.version("omni-code")
    except metadata.PackageNotFoundError:
        return "0.0.0"


def get_update_cache_path() -> Path:
    return get_config_dir() / "update.json"


def _parse_version_tuple(version: str) -> Optional[tuple[int, ...]]:
    if not re.fullmatch(r"\d+(?:\.\d+)*", version):
        return None
    return tuple(int(part) for part in version.split("."))


def _extract_versions_from_simple_index(html: str) -> list[str]:
    versions: set[str] = set()

    for match in re.finditer(r"href=[\"']([^\"']+)[\"']", html, flags=re.IGNORECASE):
        href = match.group(1)
        href = href.split("#", 1)[0].split("?", 1)[0]
        filename = href.rsplit("/", 1)[-1]

        if filename.startswith("omni_code-") and filename.endswith(".whl"):
            parts = filename.split("-")
            if len(parts) >= 2:
                versions.add(parts[1])
            continue

        if filename.startswith("omni-code-") and filename.endswith(".whl"):
            parts = filename.split("-")
            if len(parts) >= 2:
                versions.add(parts[1])
            continue

        if filename.startswith("omni-code-"):
            rest = filename[len("omni-code-") :]
            for suffix in (".tar.gz", ".zip"):
                if rest.endswith(suffix):
                    versions.add(rest[: -len(suffix)])
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
    simple_index_url: str = GEMFURY_SIMPLE_INDEX_URL,
    timeout_seconds: float = 2.5,
) -> Optional[str]:
    try:
        response = requests.get(simple_index_url, timeout=timeout_seconds)
    except requests.RequestException:
        return None
    if response.status_code != 200:
        return None

    versions = _extract_versions_from_simple_index(response.text)
    return _pick_latest_version(versions)


def check_for_update(*, simple_index_url: str = GEMFURY_SIMPLE_INDEX_URL) -> UpdateCheckResult:
    current = get_current_version()
    latest = fetch_latest_version(simple_index_url=simple_index_url)
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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")


def _get_check_interval_seconds() -> int:
    raw = os.getenv("OMNI_CODE_UPDATE_CHECK_INTERVAL_SECONDS")
    if not raw:
        return DEFAULT_CHECK_INTERVAL_SECONDS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_CHECK_INTERVAL_SECONDS
    return max(0, value)


def maybe_print_update_notice(*, simple_index_url: str = GEMFURY_SIMPLE_INDEX_URL) -> None:
    if (os.getenv("OMNI_CODE_UPDATE_CHECK") or "1").strip().lower() in ("0", "false", "no"):
        return

    cache_path = get_update_cache_path()
    cache = _load_update_cache(cache_path)

    interval = _get_check_interval_seconds()
    if cache.get("status") == "error":
        interval = min(interval, DEFAULT_FAILURE_RETRY_SECONDS)
    now = time.time()
    last_checked = cache.get("last_checked")

    result: Optional[UpdateCheckResult] = None

    if isinstance(last_checked, (int, float)) and now - float(last_checked) < interval:
        cached_latest = cache.get("latest_version")
        result = UpdateCheckResult(current_version=get_current_version(), latest_version=cached_latest)
    else:
        try:
            latest = fetch_latest_version(simple_index_url=simple_index_url)
        except Exception:
            latest = None

        if latest is None:
            latest = cache.get("latest_version")

        result = UpdateCheckResult(current_version=get_current_version(), latest_version=latest)
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
            f"Update available ({result.current_version} -> {result.latest_version}). Run: omni update"
        )


def ensure_pipx_available() -> None:
    if shutil.which("pipx"):
        return
    raise RuntimeError("pipx is required for omni update (pipx not found on PATH)")


def run_pipx_upgrade(*, extra_index_url: str = "https://pypi.fury.io/ericmichael/") -> None:
    ensure_pipx_available()
    cmd = [
        "pipx",
        "upgrade",
        PIPX_PACKAGE_NAME,
        "--pip-args",
        f"--extra-index-url {extra_index_url}",
    ]
    subprocess.run(cmd, check=True)
