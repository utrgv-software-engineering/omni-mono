from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple, Callable
import ast

CALL_TYPES = {
    "function_call": ("name", "arguments"),
    "custom_tool_call": ("name", "input"),
}

OUTPUT_TYPES = {
    "function_call_output",
    "custom_tool_call_output",
}


def _parse_json_maybe(value: Any) -> Optional[dict]:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            try:
                obj = ast.literal_eval(value)
            except Exception:
                return None
            return obj if isinstance(obj, dict) else None
    return None


def parse_tool_calls(
    history: List[Dict[str, Any]], include_types: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    calls: List[Dict[str, Any]] = []
    allowed = set(include_types) if include_types else set(CALL_TYPES.keys())
    for idx, item in enumerate(history or []):
        if not isinstance(item, dict):
            continue
        t = item.get("type")
        if t not in allowed:
            continue
        name_key, args_key = CALL_TYPES.get(t, (None, None))
        args_raw = item.get(args_key) if args_key else None
        args = _parse_json_maybe(args_raw)
        calls.append(
            {
                "index": idx,
                "type": t,
                "name": item.get(name_key) if name_key else None,
                "call_id": item.get("call_id"),
                "args": args if args is not None else args_raw,
                "raw": item,
            }
        )
    return calls


def parse_tool_outputs(history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    outs: List[Dict[str, Any]] = []
    for idx, item in enumerate(history or []):
        if not isinstance(item, dict):
            continue
        t = item.get("type")
        if t not in OUTPUT_TYPES:
            continue
        raw_output = item.get("output")
        output_json = _parse_json_maybe(raw_output)
        outs.append(
            {
                "index": idx,
                "type": t,
                "call_id": item.get("call_id"),
                "output": raw_output,
                "output_json": output_json,
                "raw": item,
            }
        )
    return outs


def link_outputs(
    calls: List[Dict[str, Any]], outs: List[Dict[str, Any]]
) -> Dict[str, Dict[str, Any]]:
    by_id: Dict[str, Dict[str, Any]] = {}
    idx: Dict[str, Dict[str, Any]] = {}
    for c in calls:
        cid = c.get("call_id")
        if cid:
            idx[cid] = c
    for o in outs:
        cid = o.get("call_id")
        if not cid:
            continue
        call = idx.get(cid)
        if call:
            by_id[cid] = {"call": call, "output": o}
    return by_id


def final_assistant_text(history: List[Dict[str, Any]]) -> Optional[str]:
    for it in reversed(history or []):
        if not isinstance(it, dict):
            continue
        if it.get("role") != "assistant":
            continue
        content = it.get("content")
        if isinstance(content, str):
            text = content.strip()
            if text:
                return text
        if isinstance(content, list) and it.get("type") == "message":
            txt = ""
            for part in content:
                if (
                    isinstance(part, dict)
                    and part.get("type") == "output_text"
                    and isinstance(part.get("text"), str)
                ):
                    txt += part.get("text")
            if txt:
                return txt.strip()
    return None


def _in_window(index: int, after: Optional[int], before: Optional[int]) -> bool:
    if after is not None and index <= after:
        return False
    if before is not None and index >= before:
        return False
    return True


def assert_args_subset(
    args: Any,
    expected: Dict[str, Any],
    *,
    coerce_numbers: bool = True,
    tol: float = 1e-6,
) -> bool:
    if not isinstance(expected, dict):
        return False
    if not isinstance(args, dict):
        return False
    for k, v in expected.items():
        if k not in args:
            return False
        av = args[k]
        if coerce_numbers:
            try:
                if isinstance(v, (int, float)) and isinstance(av, (int, float)):
                    if abs(float(av) - float(v)) > tol:
                        return False
                    continue
            except Exception:
                pass
        if av != v:
            return False
    return True


def assert_tool_called(
    history: List[Dict[str, Any]],
    tool_name: str,
    args: Optional[Dict[str, Any]] = None,
    count: Optional[int] = None,
    *,
    after: Optional[int] = None,
    before: Optional[int] = None,
) -> Tuple[bool, Dict[str, Any]]:
    calls = [
        c
        for c in parse_tool_calls(history)
        if c.get("name") == tool_name and _in_window(c.get("index", -1), after, before)
    ]
    if args is not None:
        calls = [c for c in calls if assert_args_subset(c.get("args"), args)]
    if count is None:
        ok = len(calls) >= 1
    else:
        ok = len(calls) == count
    return ok, {"matches": calls, "count": len(calls)}


def assert_no_tool_called(
    history: List[Dict[str, Any]],
    tool_name: str,
    *,
    after: Optional[int] = None,
    before: Optional[int] = None,
) -> Tuple[bool, Dict[str, Any]]:
    ok, info = assert_tool_called(
        history, tool_name, None, None, after=after, before=before
    )
    return (not ok), info


def assert_tool_call_sequence(
    history: List[Dict[str, Any]],
    sequence: List[str],
    *,
    ordered: bool = True,
    contiguous: bool = False,
    window: Optional[Tuple[int, int]] = None,
) -> Tuple[bool, Dict[str, Any]]:
    calls = parse_tool_calls(history)
    if window:
        calls = [
            c for c in calls if _in_window(c.get("index", -1), window[0], window[1])
        ]
    names = [c.get("name") for c in calls]
    if not ordered:
        present = all(n in names for n in sequence)
        return present, {"calls": calls}
    seq_idx = 0
    last_pos = -1
    positions: List[int] = []
    for i, n in enumerate(names):
        if seq_idx >= len(sequence):
            break
        if n == sequence[seq_idx]:
            if contiguous and last_pos != -1 and i != last_pos + 1:
                return False, {
                    "reason": "not_contiguous",
                    "positions": positions,
                    "calls": calls,
                }
            positions.append(i)
            last_pos = i
            seq_idx += 1
    ok = seq_idx == len(sequence)
    return ok, {"positions": positions, "calls": calls}


def assert_tool_output_satisfies(
    history: List[Dict[str, Any]],
    tool_name: str,
    predicate: Callable[[Dict[str, Any]], bool],
    *,
    for_call: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, Dict[str, Any]]:
    calls = parse_tool_calls(history)
    outs = parse_tool_outputs(history)
    links = link_outputs(calls, outs)
    target: Optional[Dict[str, Any]] = None
    if for_call and for_call.get("call_id"):
        target = links.get(for_call.get("call_id"), {}).get("output")
    else:
        for c in calls:
            if c.get("name") == tool_name and c.get("call_id") in links:
                target = links[c.get("call_id")]["output"]
                break
    if not target:
        return False, {"reason": "no_output"}
    out_json = target.get("output_json") or {}
    try:
        ok = bool(predicate(out_json))
    except Exception:
        ok = False
    return ok, {"output": target}


def assert_coords_propagated(
    history: List[Dict[str, Any]],
    from_call: str,
    to_call: str,
    *,
    tol: float = 1e-2,
) -> Tuple[bool, Dict[str, Any]]:
    calls = parse_tool_calls(history)
    outs = parse_tool_outputs(history)
    links = link_outputs(calls, outs)
    src = next((c for c in calls if c.get("name") == from_call), None)
    if not src:
        return False, {"reason": "no_source_call"}
    link = links.get(src.get("call_id"))
    if not link or not link.get("output"):
        return False, {"reason": "no_source_output"}
    try:
        first = (link["output"].get("output_json") or {}).get("results") or []
        first = first[0]
        lat = float(first.get("latitude"))
        lon = float(first.get("longitude"))
    except Exception:
        return False, {"reason": "invalid_source_coords"}
    idx_src = src.get("index", -1)
    dest = next(
        (
            c
            for c in calls
            if c.get("name") == to_call and c.get("index", 1 << 30) > idx_src
        ),
        None,
    )
    if not dest:
        return False, {"reason": "no_dest_call"}
    args = dest.get("args") or {}
    try:
        wlat = float(args.get("latitude"))
        wlon = float(args.get("longitude"))
    except Exception:
        return False, {"reason": "invalid_dest_coords"}
    if abs(lat - wlat) <= tol and abs(lon - wlon) <= tol:
        return True, {"from": [lat, lon], "to": [wlat, wlon]}
    return False, {"reason": "coords_mismatch", "from": [lat, lon], "to": [wlat, wlon]}


def assert_assistant_contains(
    history: List[Dict[str, Any]],
    must_include: Optional[List[str]] = None,
    must_not_include: Optional[List[str]] = None,
) -> Tuple[bool, Dict[str, Any]]:
    text = final_assistant_text(history) or ""
    low = text.lower()
    if must_include:
        for w in must_include:
            if str(w).lower() not in low:
                return False, {"reason": "missing", "missing": w, "text": text}
    if must_not_include:
        for w in must_not_include:
            if str(w).lower() in low:
                return False, {"reason": "forbidden", "forbidden": w, "text": text}
    return True, {"text": text}


def assert_tool_arg_matches(
    history: List[Dict[str, Any]],
    tool_names: List[str] | str,
    *,
    arg: Optional[str] = None,
    pattern: Optional[str] = None,
    regex: bool = False,
    predicate: Optional[Callable[[str], bool]] = None,
    negate: bool = False,
) -> Tuple[bool, Dict[str, Any]]:
    """Assert that some call to *tool_names* has an argument matching a
    pattern/predicate — or, with ``negate=True``, that none does.

    This is the primitive behind "the agent never ran a forbidden
    command" / "no PHI left in an outbound request" style measures:
    scan the named tools' calls, extract the argument value (a specific
    key via ``arg``, or the whole args dict serialized when ``arg`` is
    None), and test it.

    Args:
        history: Transcript history items.
        tool_names: One tool name or a list of them to scan.
        arg: Argument key to test. When None, the entire args payload is
            serialized to a string and tested.
        pattern: Substring to look for (case-insensitive), or a regular
            expression when ``regex=True``.
        regex: Treat *pattern* as a regex (``re.search``, IGNORECASE).
        predicate: Custom test taking the extracted string value. May be
            combined with *pattern*; a call matches if either does.
        negate: When True the assertion passes only if NO call matches
            (and the info dict carries the offending matches on failure).

    Returns:
        (ok, info) — info["matches"] holds the matching calls, each as
        {"name", "index", "value"}.
    """
    import re as _re

    names = [tool_names] if isinstance(tool_names, str) else list(tool_names)
    matches: List[Dict[str, Any]] = []
    scanned = 0
    for call in parse_tool_calls(history):
        if call.get("name") not in names:
            continue
        scanned += 1
        args = call.get("args")
        if arg is not None:
            if not isinstance(args, dict) or arg not in args:
                continue
            value = args.get(arg)
            value_str = value if isinstance(value, str) else json.dumps(value)
        else:
            value_str = (
                json.dumps(args) if isinstance(args, (dict, list)) else str(args or "")
            )

        matched = False
        if pattern is not None:
            if regex:
                matched = bool(_re.search(pattern, value_str, _re.IGNORECASE))
            else:
                matched = pattern.lower() in value_str.lower()
        if not matched and predicate is not None:
            try:
                matched = bool(predicate(value_str))
            except Exception:
                matched = False
        if matched:
            matches.append(
                {
                    "name": call.get("name"),
                    "index": call.get("index"),
                    "value": value_str,
                }
            )

    info = {"matches": matches, "scanned": scanned}
    if negate:
        return (len(matches) == 0), info
    return (len(matches) > 0), info


def assert_sql_rows(
    db_path: str,
    query: str,
    params: Tuple[Any, ...] = (),
    *,
    min_rows: int = 1,
) -> Tuple[bool, Dict[str, Any]]:
    """Assert a sqlite query against *db_path* returns at least
    *min_rows* rows.

    The conventional *db_path* is the environment-staged database the
    framework already blesses via ``context.data["db_path"]`` (the same
    key ``until_sql`` reads). Returns (ok, info) with the fetched rows.
    """
    import sqlite3

    try:
        # Read-only URI mode: verification must never mutate the staged db.
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
        try:
            rows = conn.execute(query, params).fetchall()
        finally:
            conn.close()
    except Exception as e:
        return False, {"reason": f"query failed: {e}", "rows": []}
    return (len(rows) >= min_rows), {"rows": rows, "count": len(rows)}


def assert_sql_no_rows(
    db_path: str,
    query: str,
    params: Tuple[Any, ...] = (),
) -> Tuple[bool, Dict[str, Any]]:
    """Assert a sqlite query against *db_path* returns zero rows."""
    ok, info = assert_sql_rows(db_path, query, params, min_rows=1)
    if "reason" in info:
        return False, info
    return (not ok), info
