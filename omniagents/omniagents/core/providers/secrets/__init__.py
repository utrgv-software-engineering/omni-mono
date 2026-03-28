"""Secrets provider domain."""

from omniagents.core.providers.secrets.base import SecretsProvider
from omniagents.core.providers.secrets.env import EnvSecretsProvider
from omniagents.core.providers import register_provider

register_provider("secrets", "none", EnvSecretsProvider)
register_provider("secrets", "env", EnvSecretsProvider)

__all__ = ["SecretsProvider", "EnvSecretsProvider"]
