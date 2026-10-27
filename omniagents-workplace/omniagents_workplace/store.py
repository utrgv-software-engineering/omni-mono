"""A workplace's durable state: one SQLite file in its data directory.

The services keep their state in memory, as dataclasses, and every change
they make is an event (their ``subscribe``). Each service's persistence
(``slack/persist.py``, ``github/persist.py``, ``linear/persist.py``) writes
the entities an event names, so the file always holds what the services
hold; a workplace started on the same data directory loads them back.

Three tables:

- ``docs``: entities as JSON documents, by ``kind`` and ``key``.
- ``blobs``: bytes (uploaded files), content-addressed: a document holds
  ``{"$blob": "<sha256>"}`` where the dataclass held the bytes.
- ``meta``: single values (counters, a workspace's name), as JSON.

Repositories' content is git, in ``git/`` beside the file.
"""

from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import json
import sqlite3
import threading
import typing
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple, Type, TypeVar

__all__ = ["Store", "decode", "encode"]

T = TypeVar("T")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (kind TEXT NOT NULL, key TEXT NOT NULL, doc TEXT NOT NULL,
                                 PRIMARY KEY (kind, key)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS blobs (sha TEXT PRIMARY KEY, data BLOB NOT NULL) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
"""


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(
            str(self.path), check_same_thread=False, isolation_level=None
        )
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.executescript(_SCHEMA)
        self._lock = threading.RLock()
        self._depth = 0

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @contextlib.contextmanager
    def transaction(self) -> Iterator[None]:
        """Writes inside it commit together (they nest)."""
        with self._lock:
            if self._depth == 0:
                self._db.execute("BEGIN")
            self._depth += 1
            try:
                yield
            except BaseException:
                self._depth -= 1
                if self._depth == 0:
                    self._db.execute("ROLLBACK")
                raise
            self._depth -= 1
            if self._depth == 0:
                self._db.execute("COMMIT")

    # -- documents ------------------------------------------------------------

    def put(
        self, kind: str, key: str, obj: Any, *, exclude: Tuple[str, ...] = ()
    ) -> None:
        doc = encode(obj, self, exclude=exclude)
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO docs (kind, key, doc) VALUES (?, ?, ?)",
                (kind, key, json.dumps(doc, separators=(",", ":"), sort_keys=True)),
            )

    def delete(self, kind: str, key: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM docs WHERE kind = ? AND key = ?", (kind, key))

    def delete_prefix(self, kind: str, prefix: str) -> None:
        with self._lock:
            self._db.execute(
                "DELETE FROM docs WHERE kind = ? AND substr(key, 1, ?) = ?",
                (kind, len(prefix), prefix),
            )

    def delete_kind(self, kind: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM docs WHERE kind = ?", (kind,))

    def docs(self, kind: str) -> List[Tuple[str, Dict[str, Any]]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT key, doc FROM docs WHERE kind = ? ORDER BY key", (kind,)
            ).fetchall()
        return [(key, json.loads(doc)) for key, doc in rows]

    def load(self, kind: str, cls: Type[T], **extra: Any) -> List[T]:
        """The ``kind`` documents as ``cls`` instances (``extra``: fields documents leave out)."""
        return [decode(cls, doc, self, **extra) for _, doc in self.docs(kind)]

    # -- blobs and single values ------------------------------------------------

    def put_blob(self, data: bytes) -> str:
        sha = hashlib.sha256(data).hexdigest()
        with self._lock:
            self._db.execute(
                "INSERT OR IGNORE INTO blobs (sha, data) VALUES (?, ?)", (sha, data)
            )
        return sha

    def blob(self, sha: str) -> bytes:
        with self._lock:
            row = self._db.execute(
                "SELECT data FROM blobs WHERE sha = ?", (sha,)
            ).fetchone()
        if row is None:
            raise KeyError(f"blob {sha} is missing")
        return bytes(row[0])

    def meta(self, key: str, default: Any = None) -> Any:
        with self._lock:
            row = self._db.execute(
                "SELECT value FROM meta WHERE key = ?", (key,)
            ).fetchone()
        return json.loads(row[0]) if row else default

    def set_meta(self, key: str, value: Any) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                (key, json.dumps(value)),
            )

    def is_empty(self) -> bool:
        with self._lock:
            return self._db.execute("SELECT 1 FROM meta LIMIT 1").fetchone() is None


# -- dataclasses <-> documents ----------------------------------------------------


def encode(
    obj: Any, store: Optional[Store] = None, *, exclude: Tuple[str, ...] = ()
) -> Any:
    """``obj`` as JSON: a dataclass by its fields, bytes as a blob reference."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {
            f.name: encode(getattr(obj, f.name), store)
            for f in dataclasses.fields(obj)
            if f.name not in exclude
        }
    if isinstance(obj, (bytes, bytearray)):
        if store is None:
            return {"$blob": hashlib.sha256(bytes(obj)).hexdigest()}
        return {"$blob": store.put_blob(bytes(obj))}
    if isinstance(obj, (list, tuple, set)):
        return [encode(v, store) for v in obj]
    if isinstance(obj, dict):
        return {str(k): encode(v, store) for k, v in obj.items()}
    return obj


def decode(
    cls: Type[T], doc: Dict[str, Any], store: Optional[Store] = None, **extra: Any
) -> T:
    """A ``cls`` from its document; fields the document lacks take their defaults."""
    hints = _hints(cls)
    kwargs: Dict[str, Any] = {}
    for f in dataclasses.fields(cls):
        if f.name in extra:
            kwargs[f.name] = extra[f.name]
        elif f.name in doc:
            kwargs[f.name] = _value(hints.get(f.name, Any), doc[f.name], store)
    return cls(**kwargs)


_HINTS: Dict[type, Dict[str, Any]] = {}


def _hints(cls: type) -> Dict[str, Any]:
    if cls not in _HINTS:
        _HINTS[cls] = typing.get_type_hints(cls)
    return _HINTS[cls]


def _value(hint: Any, value: Any, store: Optional[Store]) -> Any:
    if value is None:
        return None
    origin = typing.get_origin(hint)
    args = typing.get_args(hint)
    if origin is typing.Union:
        inner = [a for a in args if a is not type(None)]
        return _value(inner[0], value, store) if len(inner) == 1 else value
    if hint is bytes:
        if store is None:
            raise ValueError("a blob needs the store it is in")
        return store.blob(value["$blob"])
    if isinstance(hint, type) and dataclasses.is_dataclass(hint):
        return decode(hint, value, store)
    if origin in (list, List):
        return [_value(args[0] if args else Any, v, store) for v in value]
    if origin in (dict, Dict):
        key_type, value_type = (args + (Any, Any))[:2] if args else (Any, Any)
        return {
            (int(k) if key_type is int else k): _value(value_type, v, store)
            for k, v in value.items()
        }
    if hint is float and isinstance(value, int):
        return float(value)
    return value
