"""The workplace's people: one account each, signed in to with a password.

An account is a person in all three services at once: a Slack member, a
GitHub user and a Linear user, created with it and linked to it (``slack_id``,
``github_login``, ``linear_id``). The UI acts as the signed-in account.

A new workplace has no accounts; its setup makes the first, an admin. An
admin adds people, and each gets an invite link to choose a password with
(an admin makes a new one to reset a password). Sign-ins last 30 days.
Passwords are kept as scrypt hashes, and sign-in and invite tokens as their
SHA-256.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
import time
import uuid
from dataclasses import dataclass, field
from functools import wraps
from typing import Dict, List, Optional, Tuple

from fastapi import HTTPException, Request

from .store import Store
from .security import audit

__all__ = [
    "Account",
    "AccountError",
    "Accounts",
    "SESSION_COOKIE",
    "admin",
    "me",
    "valid_username",
]

SESSION_COOKIE = "workplace_session"
SESSION_SECONDS = 30 * 86400
INVITE_SECONDS = 7 * 86400
#: Wrong passwords for one username, within the window, before signing in as it waits.
MAX_FAILURES = 10
FAILURE_WINDOW = 15 * 60
MIN_PASSWORD = 8
MAX_PASSWORD = 4096

_USERNAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,38}$")


class AccountError(Exception):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.message = message
        self.status = status


def valid_username(name: str) -> bool:
    return bool(_USERNAME.match(name or ""))


@dataclass
class Account:
    id: str
    username: str
    name: str
    email: str
    slack_id: str
    github_login: str
    linear_id: str
    admin: bool = False
    disabled: bool = False
    #: Versioned scrypt hash; ``None`` until the person chooses a password.
    password: Optional[str] = None
    created_at: float = field(default_factory=time.time)


@dataclass
class _Token:
    """A sign-in or an invite, by its token's hash."""

    id: str
    account_id: str
    created_at: float
    expires_at: float


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=5, dklen=32)
    return f"scrypt$v2${salt.hex()}${digest.hex()}"


def _check_password(password: str, stored: Optional[str]) -> bool:
    if not stored or not stored.startswith("scrypt$"):
        return False
    try:
        parts = stored.split("$")
        if len(parts) == 3:
            _, salt, digest = parts
            parallelism = (
                1  # Legacy hashes are upgraded after successful authentication.
            )
        elif len(parts) == 4 and parts[1] == "v2":
            _, _, salt, digest = parts
            parallelism = 5
        else:
            return False
        tried = hashlib.scrypt(
            password.encode(),
            salt=bytes.fromhex(salt),
            n=2**14,
            r=8,
            p=parallelism,
            dklen=32,
        )
        return hmac.compare_digest(tried.hex(), digest)
    except (ValueError, TypeError):
        return False


def _locked(fn):
    @wraps(fn)
    def call(self, *args, **kwargs):
        with self._lock:
            return fn(self, *args, **kwargs)

    return call


class Accounts:
    def __init__(
        self,
        store: Store,
        *,
        min_password: int = MIN_PASSWORD,
        session_seconds: int = SESSION_SECONDS,
    ):
        self.store = store
        self.min_password = min_password
        if not 300 <= session_seconds <= SESSION_SECONDS:
            raise ValueError("Session lifetime must be between 300 and 2592000 seconds")
        self.session_seconds = session_seconds
        self._lock = threading.RLock()
        #: Account ID (or unknown) -> recent failed password attempts.
        self._failures: Dict[str, List[float]] = dict(store.docs("account.failures"))
        self._dummy_password = _hash_password(secrets.token_urlsafe(32))
        self.accounts: Dict[str, Account] = {
            a.id: a for a in store.load("account", Account)
        }
        self._sessions: Dict[str, _Token] = {
            t.id: t for t in store.load("account.session", _Token)
        }
        self._invites: Dict[str, _Token] = {
            t.id: t for t in store.load("account.invite", _Token)
        }

    # -- lookups ----------------------------------------------------------------

    def by_username(self, username: str) -> Optional[Account]:
        lowered = (username or "").strip().lower()
        return next((a for a in self.accounts.values() if a.username == lowered), None)

    def by_slack(self, slack_id: str) -> Optional[Account]:
        return next((a for a in self.accounts.values() if a.slack_id == slack_id), None)

    def people(self) -> List[Account]:
        return sorted(self.accounts.values(), key=lambda a: a.created_at)

    def needs_setup(self) -> bool:
        return not any(a.admin for a in self.accounts.values())

    # -- accounts -----------------------------------------------------------------

    def add(self, account: Account) -> Account:
        with self._lock:
            if self.by_username(account.username):
                raise AccountError(f"{account.username} is taken")
            self.accounts[account.id] = account
            self._save(account)
        return account

    def update(self, account: Account, **fields) -> Account:
        with self._lock:
            role_changed = "admin" in fields and fields["admin"] != account.admin
            for key, value in fields.items():
                if key not in ("name", "email", "admin", "disabled"):
                    raise AccountError(f"{key} cannot be changed")
                setattr(account, key, value)
            if account.disabled or role_changed:
                self._end_sessions(account.id)
                self._end_invites(account.id)
            self._save(account)
            audit(
                "account.updated",
                account_id=account.id,
                admin=account.admin,
                disabled=account.disabled,
            )
        return account

    @staticmethod
    def new_id() -> str:
        return str(uuid.uuid4())

    def validate_password(self, password: str) -> None:
        if len(password or "") > MAX_PASSWORD:
            raise AccountError(f"A password has at most {MAX_PASSWORD} characters")
        if len(password or "") < self.min_password:
            raise AccountError(
                f"A password has at least {self.min_password} characters"
            )

    def set_password(self, account: Account, password: str) -> None:
        self.validate_password(password)
        with self._lock:
            account.password = _hash_password(password)
            self._end_sessions(account.id)
            self._end_invites(account.id)
            self._save(account)
            audit("password.changed", account_id=account.id)

    # -- signing in ---------------------------------------------------------------

    @_locked
    def sign_in(self, username: str, password: str) -> Tuple[Account, str]:
        account = self.verify_password(username, password)
        audit("auth.signed_in", account_id=account.id)
        return account, self.start_session(account)

    @_locked
    def change_password(self, account: Account, current: str, new: str) -> str:
        self.verify_password(account.username, current)
        self.set_password(account, new)
        return self.start_session(account)

    @_locked
    def verify_password(self, username: str, password: str) -> Account:
        """Verify credentials without creating a session; enforce persisted throttling."""
        account = self.by_username(username)
        # Unknown usernames share one bucket: unbounded attacker-chosen keys
        # must not grow memory/database, and failures survive server restarts.
        key = account.id if account is not None else "unknown"
        now = time.time()
        with self._lock:
            recent = [
                t for t in self._failures.get(key, []) if t > now - FAILURE_WINDOW
            ]
            self._failures[key] = recent
            if len(recent) >= MAX_FAILURES:
                wait = int(recent[0] + FAILURE_WINDOW - now) // 60 + 1
                audit("auth.account_limited", account_id=key)
                raise AccountError(
                    f"Too many wrong passwords: try again in {wait} minutes", 429
                )
        candidate = password or ""
        stored = account.password if account is not None else None
        valid = len(candidate) <= MAX_PASSWORD and _check_password(
            candidate, stored or self._dummy_password
        )
        if account is None or account.disabled or not stored or not valid:
            with self._lock:
                self._failures.setdefault(key, []).append(now)
                self.store.put("account.failures", key, self._failures[key])
            # The same answer whether the account exists or not.
            audit("auth.failed", account_id=key)
            raise AccountError("Wrong username or password", 401)
        if len(candidate) < self.min_password:
            audit("auth.password_reset_required", account_id=account.id)
            raise AccountError(
                "Ask an admin for a password-reset invite before signing in", 403
            )
        if not account.password.startswith("scrypt$v2$"):
            account.password = _hash_password(candidate)
            self._save(account)
        with self._lock:
            self._failures.pop(key, None)
            self.store.delete("account.failures", key)
        return account

    def start_session(self, account: Account) -> str:
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self._lock:
            if account.disabled:
                raise AccountError("Account disabled", 403)
            entry = _Token(
                _hash_token(token), account.id, now, now + self.session_seconds
            )
            self._sessions[entry.id] = entry
            self.store.put("account.session", entry.id, entry)
        return token

    def signed_in(self, token: Optional[str]) -> Optional[Account]:
        if not token:
            return None
        entry = self._sessions.get(_hash_token(token))
        if (
            entry is None
            or entry.expires_at <= time.time()
            or entry.created_at + self.session_seconds <= time.time()
        ):
            return None
        account = self.accounts.get(entry.account_id)
        return account if account is not None and not account.disabled else None

    def sign_out(self, token: Optional[str]) -> None:
        if not token:
            return
        with self._lock:
            key = _hash_token(token)
            entry = self._sessions.pop(key, None)
            if entry is not None:
                audit("auth.signed_out", account_id=entry.account_id)
                self.store.delete("account.session", key)

    def _end_sessions(self, account_id: str) -> None:
        for key in [k for k, t in self._sessions.items() if t.account_id == account_id]:
            del self._sessions[key]
            self.store.delete("account.session", key)

    # -- invites -------------------------------------------------------------------

    def invite(self, account: Account) -> str:
        """A one-time link token the person chooses a password with (any earlier one stops working)."""
        token = secrets.token_urlsafe(24)
        now = time.time()
        with self._lock:
            for key in [
                k for k, t in self._invites.items() if t.account_id == account.id
            ]:
                del self._invites[key]
                self.store.delete("account.invite", key)
            entry = _Token(_hash_token(token), account.id, now, now + INVITE_SECONDS)
            self._invites[entry.id] = entry
            self.store.put("account.invite", entry.id, entry)
            audit("invite.created", account_id=account.id)
        return token

    def invited(self, token: str) -> Optional[Account]:
        entry = self._invites.get(_hash_token(token or ""))
        if entry is None or entry.expires_at < time.time():
            return None
        account = self.accounts.get(entry.account_id)
        return account if account is not None and not account.disabled else None

    @_locked
    def accept_invite(self, token: str, password: str) -> Tuple[Account, str]:
        account = self.invited(token)
        if account is None:
            raise AccountError("This invite link has expired or was already used", 404)
        self.set_password(account, password)
        audit("invite.accepted", account_id=account.id)
        with self._lock:
            key = _hash_token(token)
            self._invites.pop(key, None)
            self.store.delete("account.invite", key)
            self._end_sessions(account.id)
        return account, self.start_session(account)

    def _end_invites(self, account_id: str) -> None:
        for key in [k for k, t in self._invites.items() if t.account_id == account_id]:
            del self._invites[key]
            self.store.delete("account.invite", key)

    def _save(self, account: Account) -> None:
        self.store.put("account", account.id, account)


def me(request: Request) -> Account:
    """The signed-in account (the UI's API runs behind ``server.SignedIn``)."""
    account = getattr(request.state, "account", None)
    if account is None:
        raise HTTPException(status_code=401, detail="Sign in")
    return account


def admin(request: Request) -> Account:
    account = me(request)
    if not account.admin:
        raise HTTPException(status_code=403, detail="Only an admin can do that")
    return account
