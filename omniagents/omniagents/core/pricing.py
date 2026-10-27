"""What model calls cost: the project's price list, applied to their tokens.

A project keeps its models' prices in code (``paths.pricing`` in
``project.yml``), in US dollars per million tokens::

    models:
      gpt-6-astra:
        input: 10.00
        cached_input: 1.00
        cache_write: 12.50      # optional: without it, cache writes cost as input
        output: 50.00           # reasoning tokens are output tokens
        source: https://...     # where the prices are from, and when
        checked: 2026-10-24

A model a subscription runs (ChatGPT's, through the Codex backend) has no
per-token price. Its API prices give what the same calls would cost on the
API: an *API-equivalent* cost.

A model's prices are found by its name, or the longest name it starts with
(a dated snapshot, ``gpt-6-astra-2026-09-01``, is ``gpt-6-astra``'s).
Costs are worked out when they are read, from the stored tokens, so a price
change applies to every run.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

import yaml

__all__ = ["Price", "PriceList", "load_price_list", "project_price_list"]

_MILLION = 1_000_000


@dataclass(frozen=True)
class Price:
    input: float
    cached_input: float
    output: float
    cache_write: Optional[float] = None
    source: Optional[str] = None
    checked: Optional[str] = None

    def cost(self, usage: Mapping[str, Any]) -> float:
        """US dollars for ``usage`` (``input_tokens``, of which ``cached_tokens`` and
        ``cache_write_tokens``, and ``output_tokens``)."""
        cached = int(usage.get("cached_tokens") or 0)
        written = int(usage.get("cache_write_tokens") or 0)
        fresh = max(0, int(usage.get("input_tokens") or 0) - cached - written)
        write_price = self.input if self.cache_write is None else self.cache_write
        return (
            fresh * self.input
            + cached * self.cached_input
            + written * write_price
            + int(usage.get("output_tokens") or 0) * self.output
        ) / _MILLION


class PriceList:
    def __init__(self, prices: Dict[str, Price]):
        self.prices = prices

    def price(self, model: Optional[str]) -> Optional[Price]:
        if not model:
            return None
        name = model.rsplit("/", 1)[
            -1
        ]  # a catalog ref (codex/gpt-6-astra) is its model's
        if name in self.prices:
            return self.prices[name]
        starts = [known for known in self.prices if name.startswith(known + "-")]
        return self.prices[max(starts, key=len)] if starts else None

    def cost(self, model: Optional[str], usage: Mapping[str, Any]) -> Optional[float]:
        """US dollars, or ``None`` when the list has no price for ``model``."""
        price = self.price(model)
        return None if price is None else price.cost(usage)


def _number(
    entry: Mapping[str, Any], key: str, where: str, required: bool = True
) -> Optional[float]:
    value = entry.get(key)
    if value is None:
        if required:
            raise ValueError(
                f"{where}: {key} is required (US dollars per million tokens)"
            )
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(
            f"{where}: {key} is a number of US dollars per million tokens, not {value!r}"
        )


def load_price_list(path: Path) -> PriceList:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    models = data.get("models") if isinstance(data, Mapping) else None
    if not isinstance(models, Mapping):
        raise ValueError(
            f"{path}: expected `models:`, a mapping of model names to prices"
        )
    prices: Dict[str, Price] = {}
    for name, entry in models.items():
        where = f"{path}: {name}"
        if not isinstance(entry, Mapping):
            raise ValueError(f"{where}: expected input, cached_input and output prices")
        prices[str(name)] = Price(
            input=_number(entry, "input", where),
            cached_input=_number(entry, "cached_input", where),
            output=_number(entry, "output", where),
            cache_write=_number(entry, "cache_write", where, required=False),
            source=str(entry["source"]) if entry.get("source") else None,
            checked=str(entry["checked"]) if entry.get("checked") else None,
        )
    return PriceList(prices)


def project_price_list(project_root: Path) -> Optional[PriceList]:
    """The project's prices (``paths.pricing`` in its ``project.yml``), or ``None``."""
    manifest = Path(project_root) / "project.yml"
    data = (
        yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
        if manifest.is_file()
        else {}
    )
    configured = (
        (data.get("paths") or {}).get("pricing") if isinstance(data, dict) else None
    )
    return load_price_list(Path(project_root) / str(configured)) if configured else None
