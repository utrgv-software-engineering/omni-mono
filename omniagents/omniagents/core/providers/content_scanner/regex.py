"""Regex-based content scanner for common PII/PHI patterns.

Ships with omniagents — no external dependencies required.  Detects:
  - US Social Security Numbers (SSN)
  - Medical Record Numbers (MRN patterns)
  - Credit card numbers (Luhn-checkable 13-19 digit sequences)
  - API keys / bearer tokens
  - Email addresses
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from omniagents.core.providers.content_scanner.base import (
    ContentScanner,
    Finding,
    ScanResult,
)

# ── Default pattern library ──────────────────────────────────────────

_DEFAULT_PATTERNS: List[Tuple[str, re.Pattern]] = [
    (
        "ssn",
        re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    ),
    (
        "credit_card",
        re.compile(r"\b(?:\d[ -]*?){13,19}\b"),
    ),
    (
        "mrn",
        re.compile(r"\bMRN[:\s#]*\d{5,12}\b", re.IGNORECASE),
    ),
    (
        "api_key",
        re.compile(
            r"\b(?:sk-[a-zA-Z0-9]{20,}|AKIA[0-9A-Z]{16}|ghp_[a-zA-Z0-9]{36})\b"
        ),
    ),
    (
        "bearer_token",
        re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE),
    ),
    (
        "email",
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),
    ),
]


def _mask(text: str, keep: int = 4) -> str:
    """Mask all but the last *keep* characters."""
    if len(text) <= keep:
        return "***"
    return "***" + text[-keep:]


class RegexScanner(ContentScanner):
    """Pattern-based sensitive data detection.

    Config options::

        security:
          providers:
            content_scanner:
              type: regex
              block: false            # true = tripwire (block content)
              extra_patterns:         # additional regex patterns
                - name: custom_id
                  pattern: "ID-\\d{8}"
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config or {})
        self._block = bool((config or {}).get("block", False))
        self._patterns = list(_DEFAULT_PATTERNS)
        for extra in (config or {}).get("extra_patterns", []):
            name = extra.get("name", "custom")
            pattern = extra.get("pattern")
            if pattern:
                try:
                    self._patterns.append((name, re.compile(pattern)))
                except re.error:
                    pass

    async def scan(self, content: str, direction: str = "output") -> ScanResult:
        findings: List[Finding] = []
        for name, pattern in self._patterns:
            for match in pattern.finditer(content):
                findings.append(
                    Finding(
                        pattern_name=name,
                        matched_text=_mask(match.group()),
                        start=match.start(),
                        end=match.end(),
                    )
                )
        if not findings:
            return ScanResult(allowed=True)

        redacted = content
        # Redact in reverse order to preserve positions
        for f in sorted(findings, key=lambda x: x.start, reverse=True):
            redacted = redacted[: f.start] + f"[{f.pattern_name.upper()}]" + redacted[f.end :]

        return ScanResult(
            allowed=not self._block,
            findings=findings,
            redacted_content=redacted,
        )
