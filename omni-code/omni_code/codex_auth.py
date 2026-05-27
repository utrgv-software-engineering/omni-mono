"""omni-code binding for the shared OmniAgents Codex (ChatGPT OAuth) provider.

The Codex provider — token store, OAuth flows, refreshing client, model
discovery, and the ``openai-oauth`` provider-builder registration — lives in
``omniagents.core.providers.codex`` so any OmniAgents agent can use it. This
module exists only to bind it to omni-code's conventions:

- Point the token store at omni-code's config dir (``~/.config/omni_code``),
  which is where the desktop launcher writes ``codex.json``.

It then re-exports the provider API that omni-code's call sites use, so they
can keep importing from ``omni_code.codex_auth``.
"""

from __future__ import annotations

from omni_code.config import get_config_dir
from omniagents.core.providers import codex as _codex

# Bind the shared provider's token store to omni-code's config dir on import.
_codex.set_token_store_dir(get_config_dir())

# Re-export the API omni-code uses.
from omniagents.core.providers.codex import (  # noqa: E402,F401
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
