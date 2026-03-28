"""No-op session encryption provider (default)."""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, Optional

from omniagents.core.providers.session_encryption.base import SessionEncryptionProvider


class NoneEncryption(SessionEncryptionProvider):
    """Returns a standard unencrypted SQLite connection."""

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config or {})

    def get_connection(self, db_path: str) -> sqlite3.Connection:
        return sqlite3.connect(db_path, check_same_thread=False)
