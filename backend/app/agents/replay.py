"""Replay buffer persistence for long-running self-play training."""

from __future__ import annotations

import pickle
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Sequence


@dataclass
class ReplayBuffer:
    capacity: int = 20000
    items: List[Dict[str, object]] = field(default_factory=list)

    def add_examples(self, examples: Sequence[Dict[str, object]]) -> None:
        if not examples:
            return
        self.items.extend(examples)
        if len(self.items) > self.capacity:
            self.items = self.items[-self.capacity :]

    def sample(self, n: int, seed: int = 0) -> List[Dict[str, object]]:
        if n <= 0 or not self.items:
            return []
        if n >= len(self.items):
            return list(self.items)
        rng = random.Random(seed)
        idxs = rng.sample(range(len(self.items)), k=n)
        return [self.items[i] for i in idxs]

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "capacity": self.capacity,
            "items": self.items,
        }
        with path.open("wb") as f:
            pickle.dump(payload, f)

    @classmethod
    def load(cls, path: Path) -> "ReplayBuffer":
        with path.open("rb") as f:
            payload = pickle.load(f)
        buf = cls(capacity=int(payload.get("capacity", 20000)))
        buf.items = list(payload.get("items", []))
        return buf
