"""Base secrets provider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class SecretsProvider(ABC):
    """Base class for secrets providers.

    Abstracts secret retrieval so that enterprise deployments can
    use vaults (Azure Key Vault, HashiCorp Vault) while open-source
    users keep using environment variables.
    """

    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config

    @abstractmethod
    def get_secret(self, key: str) -> Optional[str]:
        """Retrieve a secret by key.  Returns ``None`` if not found."""
        ...
