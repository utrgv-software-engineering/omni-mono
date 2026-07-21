"""omni-code binding for the shared OmniAgents management-plane client.

The platform client — device-code sign-in, credential store, token
refresh, and the unified policy client — lives in
``omniagents.core.providers.platform.client`` so any OmniAgents product
can use it. This module exists only to bind it to omni-code's
conventions:

- Point the credential store at omni-code's config dir
  (``~/.config/omni_code/platform.json``).
- Scope user-level policy fetches to the ``omni_code`` project slug.

It then re-exports the client API that omni-code's call sites use
(governance, model governance, the auth CLI), so they can keep importing
from ``omni_code.platform``.
"""

from __future__ import annotations

from omni_code.config import get_config_dir
from omniagents.core.providers.platform import client as _client

# The management plane scopes policy to a project slug.
POLICY_PROJECT_SLUG = "omni_code"

# Bind the shared client to omni-code's conventions on import.
_client.set_product_slug(POLICY_PROJECT_SLUG)
_client.set_credential_store_dir(get_config_dir())

# Re-export the API omni-code uses.
from omniagents.core.providers.platform.client import (  # noqa: E402,F401
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
