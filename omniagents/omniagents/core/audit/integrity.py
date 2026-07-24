"""HMAC chain integrity for audit logs.

When a signing key is configured (via SecretsProvider), each audit log
entry includes an ``_hmac`` field that chains to the previous entry.
This creates a tamper-evident log: modifying or removing any entry
breaks the chain.

If no signing key is available, HMAC fields are omitted and the log
behaves exactly as before — zero cost for open-source users.
"""

from __future__ import annotations

import hashlib
import hmac as _hmac
import json
from typing import Optional

HMAC_FIELD = "_hmac"
CHAIN_SEED = "omniagents_audit_v1"


def compute_hmac(entry_json: str, previous_hmac: str, signing_key: str) -> str:
    """Compute HMAC-SHA256 over ``previous_hmac + entry_json``."""
    message = (previous_hmac + entry_json).encode("utf-8")
    return _hmac.new(signing_key.encode("utf-8"), message, hashlib.sha256).hexdigest()


def verify_audit_log(path: str, signing_key: str) -> tuple[bool, int, Optional[str]]:
    """Verify HMAC chain integrity of a JSON-lines audit log.

    Returns ``(valid, lines_checked, error_message)``.

    Unsigned entries (no ``_hmac`` field) are silently skipped so that
    logs from open-source deployments can be verified without error.
    """
    prev = CHAIN_SEED
    line_num = 0
    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            line_num += 1
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                return False, line_num, f"Line {line_num}: invalid JSON"
            stored = obj.pop(HMAC_FIELD, None)
            if stored is None:
                continue  # unsigned entry — skip
            canonical = json.dumps(obj, sort_keys=True, default=str, ensure_ascii=False)
            expected = compute_hmac(canonical, prev, signing_key)
            if not _hmac.compare_digest(stored, expected):
                return False, line_num, f"Line {line_num}: HMAC mismatch"
            prev = stored
    return True, line_num, None
