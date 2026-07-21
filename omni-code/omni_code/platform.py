"""omni-code import surface for the shared OmniAgents management-plane client.

The platform client — device-code sign-in, credential store, token
refresh, and the unified policy client — lives in
``omniagents.core.providers.platform.client``. Its omni-code binding
(the ``omni_code`` policy slug, the ``~/.config/omni_code`` credential
store) is no longer set here: it flows from the product identity
declared in project.yml (installed by the ``omni_code`` package import), which the
client defaults from when nothing is bound explicitly.

This module exists only for import-surface stability: omni-code's call
sites (governance, model governance, the auth CLI) keep importing from
``omni_code.platform``.
"""

from __future__ import annotations


# Re-export the API omni-code uses.
from omniagents.core.providers.platform.client import (  # noqa: F401
    PLATFORM_CRED_FILE,
    clear_credentials,
    device_code_login,
    fetch_policy,
    get_access_token,
    get_platform_url,
    is_authenticated,
    load_credentials,
    refresh_access_token,
    resolve_platform,
    save_credentials,
)
