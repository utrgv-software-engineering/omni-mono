"""Encrypted credentials, as Rails keeps them: one file per environment.

``config/credentials/<environment>.yml.enc`` (beside the factory's
``workplace.yml``) is committed; it decrypts with
``config/credentials/<environment>.key``, which is not, or with the
``WORKPLACE_MASTER_KEY`` environment variable on a server. A workplace
config names a credential as ``${credentials.admin.password}``.

``omniagents-workplace credentials edit --environment production`` opens the
decrypted YAML in ``$EDITOR`` and encrypts it again on save; a new file
starts from a template with fresh secrets. AES-256-GCM, written as
``<ciphertext>--<nonce>--<tag>``, each base64, as Rails writes its own.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

__all__ = ["CredentialsError", "ENVIRONMENTS", "Credentials", "credentials_dir"]

ENVIRONMENTS = ("development", "test", "staging", "production")
KEY_VARIABLE = "WORKPLACE_MASTER_KEY"


class CredentialsError(Exception):
    pass


def credentials_dir(root: Path) -> Path:
    return Path(root) / "config" / "credentials"


def _cipher(key_hex: str):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    try:
        key = bytes.fromhex(key_hex.strip())
    except ValueError:
        raise CredentialsError("A credentials key is 64 hexadecimal characters")
    if len(key) != 32:
        raise CredentialsError(
            "A credentials key is 64 hexadecimal characters (256 bits)"
        )
    return AESGCM(key)


def encrypt(plaintext: str, key_hex: str) -> str:
    nonce = secrets.token_bytes(12)
    sealed = _cipher(key_hex).encrypt(nonce, plaintext.encode(), None)
    data, tag = sealed[:-16], sealed[-16:]
    return "--".join(base64.b64encode(part).decode() for part in (data, nonce, tag))


def decrypt(sealed: str, key_hex: str) -> str:
    from cryptography.exceptions import InvalidTag

    try:
        data, nonce, tag = (
            base64.b64decode(part) for part in sealed.strip().split("--")
        )
    except ValueError:
        raise CredentialsError(
            "The credentials file is not an encrypted credentials file"
        )
    try:
        return _cipher(key_hex).decrypt(nonce, data + tag, None).decode()
    except InvalidTag:
        raise CredentialsError(
            "The credentials do not decrypt with this key: it is another environment's, "
            "or the file was changed"
        )


class Credentials:
    """An environment's credentials, beside a factory's ``workplace.yml``."""

    def __init__(self, root: Path, environment: str):
        if environment not in ENVIRONMENTS:
            raise CredentialsError(
                f"An environment is one of {', '.join(ENVIRONMENTS)}"
            )
        self.root = Path(root)
        self.environment = environment
        self.path = credentials_dir(self.root) / f"{environment}.yml.enc"
        self.key_path = credentials_dir(self.root) / f"{environment}.key"
        self._values: Optional[Dict[str, Any]] = None

    def exists(self) -> bool:
        return self.path.is_file()

    def key(self) -> str:
        """``WORKPLACE_MASTER_KEY``, else the environment's key file."""
        value = os.environ.get(KEY_VARIABLE, "").strip()
        if value:
            return value
        if self.key_path.is_file():
            return self.key_path.read_text().strip()
        raise CredentialsError(
            f"No key for the {self.environment} credentials: set {KEY_VARIABLE}, or put it in "
            f"{self.key_path}"
        )

    def read(self) -> str:
        if not self.exists():
            raise CredentialsError(
                f"No {self.environment} credentials ({self.path}): make them with "
                f"`omniagents-workplace credentials edit --environment {self.environment}`"
            )
        return decrypt(self.path.read_text(), self.key())

    def values(self) -> Dict[str, Any]:
        if self._values is None:
            loaded = yaml.safe_load(self.read()) or {}
            if not isinstance(loaded, dict):
                raise CredentialsError(f"{self.path}: credentials are a mapping")
            self._values = loaded
        return self._values

    def get(self, dotted: str) -> Any:
        value: Any = self.values()
        for part in dotted.split("."):
            if not isinstance(value, dict) or part not in value:
                raise CredentialsError(
                    f"The {self.environment} credentials have no {dotted}"
                )
            value = value[part]
        return value

    def write(self, plaintext: str) -> None:
        loaded = yaml.safe_load(plaintext) or {}
        if not isinstance(loaded, dict):
            raise CredentialsError("Credentials are a YAML mapping")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(encrypt(plaintext, self.key()) + "\n")
        self._values = None

    def create(self) -> str:
        """A new key (unless one is set) and credentials from the template; the key."""
        if self.exists():
            raise CredentialsError(f"{self.path} exists")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            key = self.key()
        except CredentialsError:
            key = secrets.token_hex(32)
            fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(key + "\n")
        self.path.write_text(encrypt(template(self.environment), key) + "\n")
        _ignore_keys(self.root)
        return key


def template(environment: str) -> str:
    """A new environment's credentials: fresh secrets, and a first admin to fill in (or not)."""
    return f"""# The workplace's {environment} credentials. Edit them with:
#   omniagents-workplace credentials edit --environment {environment}
# They decrypt with config/credentials/{environment}.key (not committed) or,
# on a server, the WORKPLACE_MASTER_KEY environment variable.

# The workplace's first admin. Left empty, the first visit to the workplace
# (or the setup link `up` prints) asks who it is. Filled in, `up` makes them,
# and they sign in with this password.
admin:
  name: ""
  username: ""
  email: ""
  password: {json.dumps(secrets.token_urlsafe(18))}

# The factory's own server: only the workplace calls it, with this token.
factory:
  auth_token: {json.dumps(secrets.token_urlsafe(32))}
  admin_auth_token: {json.dumps(secrets.token_urlsafe(32))}
"""


def _ignore_keys(root: Path) -> None:
    """Key files are never committed: the factory's .gitignore says so."""
    ignore = Path(root) / ".gitignore"
    line = "config/credentials/*.key"
    existing = ignore.read_text() if ignore.is_file() else ""
    if line not in existing.splitlines():
        ignore.write_text(
            existing
            + ("" if existing.endswith("\n") or not existing else "\n")
            + line
            + "\n"
        )


def edit(credentials: Credentials) -> None:
    """Decrypt into ``$EDITOR``, encrypt what is saved (a new file starts from the template)."""
    if not credentials.exists():
        credentials.create()
        print(
            f"Made {credentials.path} and its key, {credentials.key_path} (do not commit the key)"
        )
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if not editor:
        raise CredentialsError(
            'Set EDITOR to the editor credentials open in (EDITOR="code --wait", EDITOR=vim)'
        )
    plaintext = credentials.read()
    with tempfile.TemporaryDirectory(prefix="workplace-credentials-") as tmp:
        scratch = Path(tmp) / f"{credentials.environment}.yml"
        fd = os.open(scratch, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(plaintext)
        subprocess.run([*shlex.split(editor), str(scratch)], check=True)
        changed = scratch.read_text()
    if changed == plaintext:
        print("Nothing changed")
        return
    credentials.write(changed)
    print(f"Saved {credentials.path}")
