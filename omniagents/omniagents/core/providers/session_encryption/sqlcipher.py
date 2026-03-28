"""SQLCipher session encryption provider.

Encrypts the session SQLite database at rest using SQLCipher.

Requires the ``pysqlcipher3`` optional dependency::

    pip install "omniagents[sqlcipher]"
"""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, Optional

from omniagents.core.providers.session_encryption.base import SessionEncryptionProvider


class SQLCipherEncryption(SessionEncryptionProvider):
    """Returns an encrypted SQLCipher database connection.

    The encryption key is sourced from the SecretsProvider using the
    secret name configured in ``key_secret`` (default:
    ``OMNIAGENTS_SESSION_DB_KEY``).

    Config::

        security:
          providers:
            session_encryption:
              type: sqlcipher
              key_secret: OMNIAGENTS_SESSION_DB_KEY
            secrets:
              type: env   # key read from os.getenv("OMNIAGENTS_SESSION_DB_KEY")
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config or {})
        self._key_secret_name: str = (config or {}).get(
            "key_secret", "OMNIAGENTS_SESSION_DB_KEY"
        )
        self._key: str | None = None

    def _resolve_key(self) -> str:
        if self._key is None:
            import os

            # Try secrets provider first, fall back to direct env var lookup
            try:
                from omniagents.core.providers import resolve_provider

                # Ensure secrets domain is loaded
                import omniagents.core.providers.secrets  # noqa: F401

                secrets_cfg = (self.config or {}).get("_secrets_config")
                secrets = resolve_provider("secrets", secrets_cfg)
                self._key = secrets.get_secret(self._key_secret_name)
            except Exception:
                # Fall back to direct environment variable
                self._key = os.getenv(self._key_secret_name)

            if not self._key:
                raise RuntimeError(
                    f"SQLCipher encryption key not found: "
                    f"secret '{self._key_secret_name}' is empty. "
                    f"Set the environment variable or configure a secrets provider."
                )
        return self._key

    def get_connection(self, db_path: str) -> sqlite3.Connection:
        """Return an encrypted SQLCipher connection."""
        try:
            from pysqlcipher3 import dbapi2 as sqlcipher
        except ImportError:
            try:
                import sqlcipher3 as sqlcipher  # type: ignore[no-redef]
            except ImportError:
                raise ImportError(
                    "SQLCipher provider requires pysqlcipher3 or sqlcipher3. "
                    "Install with: pip install pysqlcipher3"
                )

        key = self._resolve_key()
        conn = sqlcipher.connect(db_path, check_same_thread=False)
        conn.execute(f"PRAGMA key = \"{key}\"")
        conn.execute("PRAGMA cipher_memory_security = ON")
        return conn
