"""Presidio-based content scanner for PHI/PII detection.

Uses Microsoft Presidio for NLP-based entity recognition.  Detects
person names, phone numbers, dates, medical record numbers, and other
entities that regex alone cannot reliably catch.

Requires the ``presidio-analyzer`` and ``presidio-anonymizer`` optional
dependencies::

    pip install "omniagents[presidio]"
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from omniagents.core.providers.content_scanner.base import (
    ContentScanner,
    Finding,
    ScanResult,
)


class PresidioScanner(ContentScanner):
    """NLP-based PII/PHI scanner using Microsoft Presidio.

    Config::

        security:
          providers:
            content_scanner:
              type: presidio
              block: false             # true = tripwire (block content)
              language: en
              score_threshold: 0.5
              entities:                # optional — restrict to these types
                - PERSON
                - PHONE_NUMBER
                - EMAIL_ADDRESS
                - US_SSN
                - DATE_TIME
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config or {})
        cfg = config or {}
        self._block: bool = bool(cfg.get("block", False))
        self._language: str = cfg.get("language", "en")
        self._score_threshold: float = float(cfg.get("score_threshold", 0.5))
        self._entities: Optional[List[str]] = cfg.get("entities")  # None = all
        self._analyzer: Any = None
        self._anonymizer: Any = None

    def _ensure_engine(self) -> None:
        if self._analyzer is None:
            from presidio_analyzer import AnalyzerEngine
            from presidio_anonymizer import AnonymizerEngine

            self._analyzer = AnalyzerEngine()
            self._anonymizer = AnonymizerEngine()

    async def scan(self, content: str, direction: str = "output") -> ScanResult:
        """Scan *content* for PII/PHI entities.

        Returns a :class:`ScanResult` with findings and optionally
        redacted content.
        """
        self._ensure_engine()

        results = self._analyzer.analyze(
            text=content,
            language=self._language,
            score_threshold=self._score_threshold,
            entities=self._entities,
        )

        if not results:
            return ScanResult(allowed=True)

        findings = [
            Finding(
                pattern_name=r.entity_type,
                matched_text=f"[{r.entity_type}]",
                start=r.start,
                end=r.end,
            )
            for r in results
        ]

        from presidio_anonymizer.entities import OperatorConfig

        anonymized = self._anonymizer.anonymize(
            text=content,
            analyzer_results=results,
            operators={
                "DEFAULT": OperatorConfig("replace", {"new_value": "[REDACTED]"})
            },
        )

        return ScanResult(
            allowed=not self._block,
            findings=findings,
            redacted_content=anonymized.text,
        )
