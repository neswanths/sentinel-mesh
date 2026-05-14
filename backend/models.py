from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

AttackKind = Literal["brute", "lateral", "apt"]


@dataclass
class Attack:
    kind: AttackKind
    intensity: float
    signature: tuple[float, float, float]
    episode_id: int
    started_tick: int


@dataclass
class NodeState:
    id: int
    baseline: tuple[float, float, float]
    detectors: list[tuple[float, float, float]]
    neighbours: list[int]
    suspicion: float = 0.0
    threshold_modifier: float = 1.0
    sensitized_until: int = 0
    attack: Attack | None = None
    danger_emitted_episode: int | None = None
    consensus_episode: int | None = None
    flagged: bool = False
    memory_hits: int = 0
    last_detector_count: int = 0

    def reset_runtime(self) -> None:
        self.suspicion = 0.0
        self.threshold_modifier = 1.0
        self.sensitized_until = 0
        self.attack = None
        self.danger_emitted_episode = None
        self.consensus_episode = None
        self.flagged = False
        self.memory_hits = 0
        self.last_detector_count = 0

    @property
    def status(self) -> str:
        if self.flagged or self.suspicion >= 1.0:
            return "flagged"
        if self.suspicion >= 0.7:
            return "high"
        if self.suspicion >= 0.5:
            return "alerted"
        if self.suspicion >= 0.3:
            return "elevated"
        return "normal"


@dataclass
class MeshEvent:
    type: str
    tick: int
    message: str
    node: int | None = None
    target: int | None = None
    source: int | None = None
    nodes: list[int] = field(default_factory=list)
    severity: str = "info"
    id: str = ""

    def as_dict(self) -> dict:
        return {
            "id": self.id or f"{self.tick}:{self.type}:{self.node}:{self.source}:{self.target}",
            "type": self.type,
            "tick": self.tick,
            "message": self.message,
            "node": self.node,
            "target": self.target,
            "source": self.source,
            "nodes": self.nodes,
            "severity": self.severity,
        }
