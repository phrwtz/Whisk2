"""Common policy interfaces for Whisk agents."""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Protocol, Tuple

from .env import WhiskEnv
from ..game import Mark

Coord = Tuple[int, int]


class Agent(Protocol):
    """Minimal interface all baseline/trainable agents should satisfy."""

    name: str

    def select_action(self, env: WhiskEnv, mark: Mark, rng: random.Random) -> Coord:
        """Return one legal coordinate for `mark` in the current env state."""


@dataclass
class AgentSpec:
    name: str
