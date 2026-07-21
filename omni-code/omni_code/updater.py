"""omni-code binding for the framework's generic pipx self-update flow.

The mechanism — simple-index scraping, version comparison, the throttled
startup notice, and the ``pipx upgrade`` flow — lives in
``omniagents.core.updater``. This module only describes the omni-code
distribution (Gemfury index, package name, config-dir cache,
``OMNI_CODE_*`` env knobs) and re-exports the bound flow for omni-code's
call sites (``omni_code.cli`` startup notice, ``omni update``).
"""

from __future__ import annotations

from omni_code.config import get_config_dir
from omniagents.core.updater import (  # noqa: F401
    UpdateCheckResult,
    UpdaterConfig,
    get_current_version as _get_current_version,
)
from omniagents.core.updater import (
    check_for_update as _check_for_update,
    maybe_print_update_notice as _maybe_print_update_notice,
    run_pipx_upgrade as _run_pipx_upgrade,
)

GEMFURY_SIMPLE_INDEX_URL = "https://pypi.fury.io/ericmichael/omni-code/"
PIPX_PACKAGE_NAME = "omni-code"

UPDATER_CONFIG = UpdaterConfig(
    simple_index_url=GEMFURY_SIMPLE_INDEX_URL,
    package_name=PIPX_PACKAGE_NAME,
    cache_dir=get_config_dir,
    env_prefix="OMNI_CODE",
    update_command_hint="omni update",
)


def get_current_version() -> str:
    return _get_current_version(PIPX_PACKAGE_NAME)


def check_for_update() -> UpdateCheckResult:
    return _check_for_update(UPDATER_CONFIG)


def maybe_print_update_notice() -> None:
    _maybe_print_update_notice(UPDATER_CONFIG)


def run_pipx_upgrade() -> None:
    _run_pipx_upgrade(UPDATER_CONFIG)
