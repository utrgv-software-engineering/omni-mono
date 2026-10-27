"""Versioned memories and access grants in the workplace database."""

from __future__ import annotations

import logging
from datetime import date as calendar_date
import re
import time
import uuid
from typing import Any

from ..canvas.state import CanvasError


class MemoryError(CanvasError):
    pass


class AdminPrincipal(str):
    """Request-local UI authority. Never reconstructed from a token or stored grant."""


class MemoryState:
    def __init__(self, store):
        self.store = store
        self.listeners = []
        with store._lock:
            store._db.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(id UNINDEXED, text, tags)"
            )

    def subscribe(self, listener):
        self.listeners.append(listener)
        return lambda: self.listeners.remove(listener)

    def _emit(self, kind, memory):
        for listener in list(self.listeners):
            try:
                listener(kind, {"memory": memory})
            except Exception:
                logging.getLogger(__name__).exception("Memory event listener failed")

    def provision(self, principal, kind):
        """Idempotent defaults: own store writable; team readable by apps."""
        with self.store.transaction():
            stores = dict(self.store.docs("memory.store"))
            own = f"{kind}:{principal}"
            for key, title in (
                ("team", "Team"),
                (own, "Personal" if kind == "personal" else "Agent"),
            ):
                if key not in stores:
                    self.store.put("memory.store", key, {"id": key, "name": title})
            grants = dict(self.store.docs("memory.grant"))
            for key, access in (
                (own, "write"),
                ("team", "write" if kind == "personal" else "read"),
            ):
                ident = f"{principal}/{key}"
                if ident not in grants:
                    self.grant(principal, key, access)
        return own

    def grant(self, principal, store, access):
        if access not in ("read", "write", "none"):
            raise MemoryError("Access must be read, write or none")
        if store not in dict(self.store.docs("memory.store")):
            raise MemoryError("Unknown store", 404)
        self.store.put(
            "memory.grant",
            f"{principal}/{store}",
            {"principal": principal, "store": store, "access": access},
        )

    def remove_legacy_admin_grants(self, account_ids, owned_stores=()):
        # Old UI visits wrote indistinguishable admin/manual cross-store grants.
        # Revoke these once, fail closed, and let admins explicitly regrant access.
        if self.store.meta("memory.admin_grants_migrated"):
            return
        removed = 0
        with self.store.transaction():
            for key, grant in self.store.docs("memory.grant"):
                who = grant["principal"]
                owned = any(
                    who == owner
                    and (
                        grant["store"] == store
                        or grant["store"].startswith(store + "/")
                    )
                    for owner, store in owned_stores
                )
                if (
                    who in account_ids
                    and not owned
                    and grant["store"] not in ("team", f"personal:{who}")
                ):
                    self.store.delete("memory.grant", key)
                    removed += 1
            self.store.set_meta("memory.admin_grants_migrated", True)
        if removed:
            logging.getLogger(__name__).warning(
                "Revoked %d legacy cross-store account grants; admins can regrant explicitly",
                removed,
            )

    def stores(self, principal):
        if isinstance(principal, AdminPrincipal):
            return [
                {**s, "access": "write"} for _, s in self.store.docs("memory.store")
            ]
        grants = {
            g["store"]: g["access"]
            for _, g in self.store.docs("memory.grant")
            if g["principal"] == principal and g["access"] != "none"
        }
        return [
            {**s, "access": grants[k]}
            for k, s in self.store.docs("memory.store")
            if k in grants
        ]

    def _access(self, principal, store, write=False):
        # An app owns role stores below its own store; never another app's namespace.
        prefix = f"agent:{principal}/"
        if store.startswith(prefix) and re.fullmatch(
            r"[A-Za-z0-9_. -]{1,80}", store[len(prefix) :]
        ):
            with self.store.transaction():
                if store not in dict(self.store.docs("memory.store")):
                    parent = f"agent:{principal}"
                    if not any(
                        g["principal"] == principal
                        and g["store"] == parent
                        and g["access"] == "write"
                        for _, g in self.store.docs("memory.grant")
                    ):
                        raise MemoryError("No access to this memory store", 403)
                    self.store.put(
                        "memory.store",
                        store,
                        {"id": store, "name": store[len(prefix) :]},
                    )
                    self.grant(principal, store, "write")
                    for _, grant in self.store.docs("memory.grant"):
                        if grant["store"] == parent and grant["principal"] != principal:
                            self.grant(grant["principal"], store, grant["access"])
        access = next(
            (s["access"] for s in self.stores(principal) if s["id"] == store), None
        )
        if access is None or (write and access != "write"):
            raise MemoryError("No access to this memory store", 403)

    def _selected(self, principal, stores):
        selected = (
            [s["id"] for s in self.stores(principal)] if stores is None else stores
        )
        for store in selected:
            self._access(principal, store)
        return set(selected)

    def _active(self, memory):
        return (
            not memory.get("forgotten")
            and not memory.get("superseded_by")
            and (memory.get("expires_at") is None or memory["expires_at"] > time.time())
        )

    def get(self, principal, id):
        m = dict(self.store.docs("memory.item")).get(id)
        if m is None:
            raise MemoryError("Unknown memory", 404)
        self._access(principal, m["store"])
        return {
            **m,
            "history": [
                v for _, v in self.store.docs("memory.version") if v["id"] == id
            ],
        }

    def search(self, principal, query="", stores=None, limit=20, kind=None):
        selected = self._selected(principal, stores)
        limit = max(1, min(int(limit), 100))
        terms = re.findall(r"\w+", query, flags=re.UNICODE)[:64]
        with self.store._lock:
            ranks = (
                dict(
                    self.store._db.execute(
                        "SELECT id, bm25(memory_fts) FROM memory_fts WHERE memory_fts MATCH ?",
                        (" OR ".join('"' + t + '"' for t in terms),),
                    ).fetchall()
                )
                if terms
                else None
            )
            items = [
                m
                for _, m in self.store.docs("memory.item")
                if m["store"] in selected
                and self._active(m)
                and (kind is None or m["kind"] == kind)
                and (ranks is None or m["id"] in ranks)
            ]
        items.sort(
            key=lambda m: ((ranks or {}).get(m["id"], 0), -m["updated_at"], m["id"])
        )
        return items[:limit]

    def core(self, principal, stores=None):
        selected = self._selected(principal, stores)
        return sorted(
            (
                m
                for _, m in self.store.docs("memory.item")
                if m["store"] in selected and m["kind"] == "core" and self._active(m)
            ),
            key=lambda m: (m["store"], m["id"]),
        )

    def _save(self, m, author, reason=""):
        m = {
            **m,
            "version": m.get("version", 0) + 1,
            "updated_at": time.time(),
            "author": author,
        }
        self.store.put("memory.item", m["id"], m)
        self.store.put(
            "memory.version", f"{m['id']}:{m['version']:08d}", {**m, "reason": reason}
        )
        self.store._db.execute("DELETE FROM memory_fts WHERE id = ?", (m["id"],))
        self.store._db.execute(
            "INSERT INTO memory_fts(id,text,tags) VALUES (?,?,?)",
            (m["id"], m["text"], " ".join(m["tags"])),
        )
        return m

    def _validate(self, store, text, kind, excluding=None):
        if kind not in ("core", "fact", "log", "note"):
            raise MemoryError("Kind must be core, fact, log or note")
        if not text.strip() or len(text) > 16000:
            raise MemoryError("Memory text must contain 1–16000 characters")
        if kind == "core":
            core = [
                m
                for _, m in self.store.docs("memory.item")
                if m["store"] == store
                and m["kind"] == "core"
                and m["id"] != excluding
                and self._active(m)
            ]
            if len(core) >= 20 or sum(len(m["text"]) for m in core) + len(text) > 8000:
                raise MemoryError(
                    "Core memory is limited to 20 entries and 8000 characters per store"
                )

    def remember(
        self,
        principal,
        store,
        text,
        kind="fact",
        tags=None,
        expires_at=None,
        source=None,
        date=None,
    ):
        with self.store.transaction():
            self._access(principal, store, True)
            self._validate(store, text, kind)
            if kind == "log":
                try:
                    calendar_date.fromisoformat(date or "")
                except (ValueError, TypeError):
                    raise MemoryError("A log requires an ISO date (YYYY-MM-DD)")

            if tags is not None and (
                len(tags) > 32
                or any(not isinstance(t, str) or len(t) > 200 for t in tags)
            ):
                raise MemoryError("Use up to 32 tags, each up to 200 characters")
            if kind == "note" and expires_at is None:
                expires_at = time.time() + 3600
            m = self._save(
                {
                    "id": uuid.uuid4().hex,
                    "store": store,
                    "text": text.strip(),
                    "kind": kind,
                    "tags": tags or [],
                    "expires_at": expires_at,
                    "source": source or {},
                    "date": date,
                    "created_at": time.time(),
                },
                principal,
            )
        self._emit("memory_created", m)
        return m

    def update(self, principal, id, text):
        with self.store.transaction():
            m = self.get(principal, id)
            m.pop("history")
            self._access(principal, m["store"], True)
            self._validate(m["store"], text, m["kind"], id)
            m = self._save({**m, "text": text.strip()}, principal)
        self._emit("memory_updated", m)
        return m

    def forget(self, principal, id, reason):
        if not reason.strip():
            raise MemoryError("A reason is required")
        with self.store.transaction():
            m = self.get(principal, id)
            m.pop("history")
            self._access(principal, m["store"], True)
            m = self._save({**m, "forgotten": True}, principal, reason)
        self._emit("memory_forgotten", m)
        return m

    def supersede(self, principal, old_ids, text, source=None):
        if not old_ids:
            raise MemoryError("Give at least one memory to supersede")
        with self.store.transaction():
            old = [self.get(principal, id) for id in dict.fromkeys(old_ids)]
            for m in old:
                self._access(principal, m["store"], True)
            if len({m["store"] for m in old}) != 1:
                raise MemoryError("Superseded memories must belong to one store")
            self._validate(old[0]["store"], text, "fact")
            new = self._save(
                {
                    "id": uuid.uuid4().hex,
                    "store": old[0]["store"],
                    "text": text.strip(),
                    "kind": "fact",
                    "tags": old[0]["tags"],
                    "source": source if source is not None else old[0]["source"],
                    "expires_at": None,
                    "created_at": time.time(),
                },
                principal,
            )
            changed = []
            for m in old:
                m.pop("history")
                changed.append(
                    self._save(
                        {**m, "superseded_by": new["id"]}, principal, "superseded"
                    )
                )
        self._emit("memory_created", new)
        for m in changed:
            self._emit("memory_updated", m)
        return new

    def expire_tag(self, tag):
        """A closed issue/PR retires its scratch notes, preserving their history."""
        changed = []
        with self.store.transaction():
            for _, m in self.store.docs("memory.item"):
                if m["kind"] == "note" and tag in m["tags"] and self._active(m):
                    changed.append(
                        self._save(
                            {**m, "expires_at": time.time()},
                            "system",
                            "issue or PR closed",
                        )
                    )
        for m in changed:
            self._emit("memory_updated", m)
