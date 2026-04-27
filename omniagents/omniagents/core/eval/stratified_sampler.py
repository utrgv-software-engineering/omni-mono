from __future__ import annotations

import random
from typing import Any, Callable


class StratifiedBatchSampler:
    def __init__(
        self,
        trainset: list[Any],
        minibatch_size: int,
        label_getter: str | Callable[[Any], bool] = "label",
        *,
        pos_fraction: float | None = 0.5,
        min_pos_per_batch: int = 1,
        min_neg_per_batch: int = 1,
        with_replacement: bool = True,
        seed: int | None = 0,
        rng: random.Random | None = None,
    ) -> None:
        self.trainset = trainset
        self.minibatch_size = int(minibatch_size)
        self.pos_fraction = pos_fraction
        self.min_pos_per_batch = int(min_pos_per_batch)
        self.min_neg_per_batch = int(min_neg_per_batch)
        self.with_replacement = bool(with_replacement)
        self.rng = rng or random.Random(seed if seed is not None else 0)

        if isinstance(label_getter, str):
            key = str(label_getter)

            def _is_pos(x: Any) -> bool:
                try:
                    if isinstance(x, dict):
                        return bool(x.get(key))
                    return bool(getattr(x, key))
                except Exception:
                    return False

            self._is_pos: Callable[[Any], bool] = _is_pos
        else:
            self._is_pos = label_getter

        self.pos_idxs = [i for i, x in enumerate(self.trainset) if self._is_pos(x)]
        self.neg_idxs = [i for i, x in enumerate(self.trainset) if not self._is_pos(x)]

    def _sample(self, pool: list[int], k: int) -> list[int]:
        if k <= 0:
            return []
        if not pool:
            return []
        if self.with_replacement or k > len(pool):
            return [self.rng.choice(pool) for _ in range(k)]
        return self.rng.sample(pool, k)

    def _build_minibatch(self) -> list[int]:
        if not self.pos_idxs or not self.neg_idxs:
            batch = self._sample(self.pos_idxs or self.neg_idxs, self.minibatch_size)
            self.rng.shuffle(batch)
            return batch

        if self.pos_fraction is not None:
            p = max(
                self.min_pos_per_batch, round(self.pos_fraction * self.minibatch_size)
            )
        else:
            p = self.min_pos_per_batch
        p = min(p, self.minibatch_size - self.min_neg_per_batch)
        q = self.minibatch_size - p

        pos_part = self._sample(self.pos_idxs, p)
        neg_part = self._sample(self.neg_idxs, q)
        batch = pos_part + neg_part
        self.rng.shuffle(batch)
        return batch

    def next_minibatch_ids(self, loader: Any, state: Any) -> list[int]:
        """gepa >= 0.1.x BatchSampler protocol.

        Returns a list of DataIds (here, integer indices into the loader's
        ordered universe). The loader and state args are unused — sampling
        operates on the positives/negatives indexed at construction time."""
        return self._build_minibatch()

    def next_minibatch_indices(self, trainset_size: int, iteration: int) -> list[int]:
        """Legacy gepa < 0.1.x API. Kept as an alias for backwards compat."""
        return self._build_minibatch()
