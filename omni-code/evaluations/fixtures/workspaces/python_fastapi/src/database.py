"""In-memory data store.

Provides a simple dict-backed storage layer so the app runs without
an external database. Each entity type gets its own table (dict).
"""

from typing import Any, Dict, Optional

_tables: Dict[str, Dict[int, Dict[str, Any]]] = {
    "users": {},
    "posts": {},
    "comments": {},
}

_sequences: Dict[str, int] = {
    "users": 0,
    "posts": 0,
    "comments": 0,
}


def next_id(table: str) -> int:
    _sequences[table] = _sequences.get(table, 0) + 1
    return _sequences[table]


def insert(table: str, record: Dict[str, Any]) -> Dict[str, Any]:
    rid = next_id(table)
    record["id"] = rid
    _tables[table][rid] = record
    return record


def get(table: str, record_id: int) -> Optional[Dict[str, Any]]:
    return _tables[table].get(record_id)


def get_all(table: str) -> list:
    return list(_tables[table].values())


def update(table: str, record_id: int, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if record_id not in _tables[table]:
        return None
    _tables[table][record_id].update(data)
    return _tables[table][record_id]


def delete(table: str, record_id: int) -> bool:
    return _tables[table].pop(record_id, None) is not None


def query(table: str, **filters: Any) -> list:
    results = []
    for record in _tables[table].values():
        if all(record.get(k) == v for k, v in filters.items()):
            results.append(record)
    return results


def reset() -> None:
    for t in _tables:
        _tables[t].clear()
    for s in _sequences:
        _sequences[s] = 0
