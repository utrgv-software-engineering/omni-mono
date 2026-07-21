"""omni-code import surface for the shared OmniAgents Codex (ChatGPT OAuth) provider.

The Codex provider — token store, OAuth flows, refreshing client, model
discovery, and the ``openai-oauth`` provider-builder registration — lives in
``omniagents.core.providers.codex``. Its omni-code binding (the token store
at ``~/.config/omni_code/codex.json``, where the desktop launcher writes) is
no longer set here: the framework's token store defaults from the product
identity declared in project.yml (installed by ``omni_code.identity``).

This module exists only for import-surface stability: omni-code's call sites
keep importing from ``omni_code.codex_auth``.
"""

from __future__ import annotations

from omni_code import identity  # noqa: F401  — installs the product identity

# Re-export the API omni-code uses.
from omniagents.core.providers.codex import (  # noqa: F401
    CODEX_BASE_URL,
    CODEX_CLIENT_VERSION,
    build_codex_openai_client,
    clear_tokens,
    device_login,
    is_authenticated,
    list_codex_models,
    load_tokens,
    register_codex_provider,
    save_tokens,
)
