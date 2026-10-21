"""Environment-variable secrets provider (default)."""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from omniagents.core.providers.secrets.base import SecretsProvider


class EnvSecretsProvider(SecretsProvider):
    """Retrieves secrets from ``os.getenv()``.

    This is the zero-config default — identical to current behaviour.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config or {})

    def get_secret(self, key: str) -> Optional[str]:
        return os.getenv(key) or os.getenv(key.upper())
