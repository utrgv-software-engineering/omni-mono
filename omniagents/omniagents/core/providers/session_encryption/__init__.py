"""Session encryption provider domain."""

from omniagents.core.providers.session_encryption.base import SessionEncryptionProvider
from omniagents.core.providers.session_encryption.none import NoneEncryption
from omniagents.core.providers import register_provider

register_provider("session_encryption", "none", NoneEncryption)

# SQLCipher — only registered when pysqlcipher3 is installed
try:
    from omniagents.core.providers.session_encryption.sqlcipher import (
        SQLCipherEncryption,
    )

    register_provider("session_encryption", "sqlcipher", SQLCipherEncryption)
except ImportError:
    pass

__all__ = ["SessionEncryptionProvider", "NoneEncryption"]
