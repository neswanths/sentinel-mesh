from __future__ import annotations

import asyncio
import contextlib
import math
import random
import uuid
from collections import defaultdict, deque
from typing import Any

from fastapi import WebSocket

from config import (
    APT_SUSPICION_FLOOR,
    APT_TARGET_COUNT,
    CONSENSUS_NEIGHBOUR_COUNT,
    CONSENSUS_THRESHOLD,
    CONSENSUS_WINDOW_TICKS,
    DANGER_SENSITIVITY_TICKS,
    DANGER_THRESHOLD,
    DECAY_HALF_LIFE_TICKS,
    DEMO_MODE,
    DETECTORS_PER_NODE,
    GOSSIP_MAX_HOPS,
    GOSSIP_THRESHOLD,
    MEMORY_DISTANCE_THRESHOLD,
    NODE_COUNT,
    TICK_INTERVAL_SECONDS,
)
from models import Attack, MeshEvent, NodeState


TOPOLOGY: dict[int, list[int]] = {
    0: [1, 2, 4, 15, 19],
    1: [0, 2, 6],
    2: [0, 1, 7],
    3: [4, 7, 8],
    4: [0, 3, 5, 10],
    5: [4, 6, 7, 9, 10],
    6: [1, 5, 7],
    7: [2, 3, 5, 6, 8],
    8: [3, 7, 9],
    9: [5, 8, 10],
    10: [4, 5, 9, 11, 12, 14],
    11: [10, 12, 16],
    12: [10, 11, 13],
    13: [12, 14, 18],
    14: [10, 13, 15, 16],
    15: [0, 14, 16, 17, 19],
    16: [11, 14, 15, 17],
    17: [15, 16, 18],
    18: [13, 17, 19],
    19: [0, 15, 18],
}

SIGNATURES: dict[str, tuple[float, float, float]] = {
    "brute": (0.78, 0.96, 0.42),
    "lateral": (0.54, 0.38, 0.71),
    "apt": (0.46, 0.44, 0.52),
}


class Simulation:
    def __init__(self) -> None:
        self.tick = 0
        self.episode_id = 0
        self.nodes = self._make_nodes()
        self.memory_cells: list[tuple[float, float, float]] = []
        self.observers: set[WebSocket] = set()
        self.attackers: set[WebSocket] = set()
        self.recent_events: deque[MeshEvent] = deque(maxlen=200)
        self.tick_events: list[MeshEvent] = []
        self.gossip_seen: set[str] = set()
        self.gossip_counts: dict[str, int] = defaultdict(int)
        self.neighbour_confirmations: dict[int, dict[int, int]] = defaultdict(dict)
        self.apt_detected_episode: int | None = None
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    def _make_nodes(self) -> dict[int, NodeState]:
        random.seed(42)
        nodes: dict[int, NodeState] = {}
        hubs = {0, 5, 10, 15}
        for node_id in range(NODE_COUNT):
            base_connection = 0.72 if node_id in hubs else 0.32 + (node_id % 5) * 0.05
            baseline = (
                round(base_connection, 3),
                round(0.12 + (node_id % 4) * 0.035, 3),
                round(0.22 + (node_id % 6) * 0.028, 3),
            )
            detectors = self._negative_selection(baseline)
            nodes[node_id] = NodeState(
                id=node_id,
                baseline=baseline,
                detectors=detectors,
                neighbours=TOPOLOGY[node_id],
            )
        return nodes

    def _negative_selection(
        self, baseline: tuple[float, float, float]
    ) -> list[tuple[float, float, float]]:
        detectors: list[tuple[float, float, float]] = []
        while len(detectors) < DETECTORS_PER_NODE:
            candidate = (random.random(), random.random(), random.random())
            if self._distance(candidate, baseline) > 0.34:
                detectors.append(candidate)
        return detectors

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        while True:
            async with self._lock:
                self.tick += 1
                self.tick_events = []
                self._step()
                payload = self.state_payload()
                attacker_events = [event.as_dict() for event in self.tick_events]

            await self._broadcast_observers(payload)
            if attacker_events:
                await self._broadcast_attackers(
                    {"type": "events", "events": attacker_events}
                )
            await asyncio.sleep(TICK_INTERVAL_SECONDS)

    def _step(self) -> None:
        decay_factor = math.pow(0.5, 1 / DECAY_HALF_LIFE_TICKS)

        for node in self.nodes.values():
            if node.sensitized_until and self.tick > node.sensitized_until:
                node.sensitized_until = 0
                node.threshold_modifier = 1.0

            if node.attack is None:
                node.suspicion *= decay_factor
                if node.suspicion < 0.0001:
                    node.suspicion = 0.0
                node.last_detector_count = 0
                continue

            ramp = self._attack_ramp(node)
            if DEMO_MODE:
                node.suspicion = min(self._suspicion_cap(node), node.suspicion + ramp)
                node.last_detector_count = max(1, int(ramp * 1200))
            else:
                detector_hits = self._detector_hits(node, node.attack.signature)
                node.last_detector_count = detector_hits
                node.suspicion = min(
                    self._suspicion_cap(node),
                    node.suspicion + detector_hits * 0.0015,
                )

        for node in self.nodes.values():
            if node.attack and node.suspicion >= DANGER_THRESHOLD:
                self._emit_danger_once(node)

        for node in self.nodes.values():
            if node.suspicion >= GOSSIP_THRESHOLD and self.tick % 8 == 0:
                self._emit_gossip(node.id)

        for node in self.nodes.values():
            self._check_consensus(node)

        self._check_apt()

    def _attack_ramp(self, node: NodeState) -> float:
        assert node.attack is not None
        if node.attack.kind == "brute":
            base = 0.0038
        elif node.attack.kind == "lateral":
            base = 0.00175
        else:
            base = 0.00115

        sensitized_multiplier = 5.0 if node.threshold_modifier < 1.0 else 1.0
        memory_multiplier = 2.0 if self._memory_match(node.attack.signature) else 1.0
        if memory_multiplier > 1.0:
            node.memory_hits += 1
        return base * node.attack.intensity * sensitized_multiplier * memory_multiplier

    def _suspicion_cap(self, node: NodeState) -> float:
        if node.attack and node.attack.kind == "apt":
            return 0.46
        return 1.0

    def _detector_hits(
        self, node: NodeState, signature: tuple[float, float, float]
    ) -> int:
        sensitivity_radius = 0.2 * node.threshold_modifier
        return sum(
            1 for detector in node.detectors if self._distance(detector, signature) < sensitivity_radius
        )

    def _emit_danger_once(self, node: NodeState) -> None:
        assert node.attack is not None
        if node.danger_emitted_episode == node.attack.episode_id:
            return
        node.danger_emitted_episode = node.attack.episode_id
        for neighbour_id in node.neighbours:
            neighbour = self.nodes[neighbour_id]
            neighbour.threshold_modifier = 0.42
            neighbour.sensitized_until = max(
                neighbour.sensitized_until, self.tick + DANGER_SENSITIVITY_TICKS
            )
            self._add_event(
                "DANGER_SIGNAL",
                f"Node {node.id} sensitized neighbour {neighbour_id}",
                node=node.id,
                target=neighbour_id,
                severity="warning",
            )

    def _emit_gossip(self, source_id: int) -> None:
        message_id = f"{source_id}:{self.tick}:{uuid.uuid4().hex[:8]}"
        frontier = [(source_id, neighbour, 1) for neighbour in self.nodes[source_id].neighbours]
        while frontier:
            origin, target, hop = frontier.pop(0)
            seen_key = f"{message_id}:{origin}:{target}"
            if seen_key in self.gossip_seen:
                continue
            self.gossip_seen.add(seen_key)
            edge_key = f"{origin}->{target}"
            self.gossip_counts[edge_key] += 1
            if source_id in self.nodes[target].neighbours:
                self.neighbour_confirmations[source_id][target] = self.tick
            self._add_event(
                "GOSSIP",
                f"Gossip {source_id}->{target}",
                node=source_id,
                source=origin,
                target=target,
                severity="info",
            )
            if hop >= GOSSIP_MAX_HOPS:
                continue
            for next_id in self.nodes[target].neighbours:
                if next_id != origin:
                    frontier.append((target, next_id, hop + 1))

    def _check_consensus(self, node: NodeState) -> None:
        if node.attack is None or node.suspicion < CONSENSUS_THRESHOLD:
            return
        if node.consensus_episode == node.attack.episode_id:
            return
        confirmations = [
            neighbour_id
            for neighbour_id, seen_tick in self.neighbour_confirmations[node.id].items()
            if self.tick - seen_tick <= CONSENSUS_WINDOW_TICKS
        ]
        if len(confirmations) < CONSENSUS_NEIGHBOUR_COUNT:
            return
        node.flagged = True
        node.consensus_episode = node.attack.episode_id
        self.memory_cells.append(node.attack.signature)
        self._add_event(
            "CONSENSUS_FLAG",
            f"Consensus flagged Node {node.id} with {len(confirmations)} neighbours",
            node=node.id,
            nodes=[node.id, *confirmations[:3]],
            severity="critical",
        )
        self._add_event(
            "MEMORY_CELL",
            f"Memory cell stored for Node {node.id} signature",
            node=node.id,
            severity="info",
        )

    def _check_apt(self) -> None:
        apt_nodes = [
            node
            for node in self.nodes.values()
            if node.attack
            and node.attack.kind == "apt"
            and node.suspicion >= APT_SUSPICION_FLOOR
        ]
        if len(apt_nodes) < APT_TARGET_COUNT:
            return
        episode = apt_nodes[0].attack.episode_id
        if self.apt_detected_episode == episode:
            return
        self.apt_detected_episode = episode
        self._add_event(
            "APT_DETECTED",
            "APT_DETECTED: distributed low-intensity pattern confirmed",
            nodes=[node.id for node in apt_nodes],
            severity="critical",
        )

    def _add_event(
        self,
        event_type: str,
        message: str,
        *,
        node: int | None = None,
        target: int | None = None,
        source: int | None = None,
        nodes: list[int] | None = None,
        severity: str = "info",
    ) -> None:
        event = MeshEvent(
            type=event_type,
            tick=self.tick,
            message=message,
            node=node,
            target=target,
            source=source,
            nodes=nodes or [],
            severity=severity,
            id=f"{self.tick}:{event_type}:{uuid.uuid4().hex[:8]}",
        )
        self.tick_events.append(event)
        self.recent_events.appendleft(event)

    async def command(self, raw: str) -> dict[str, Any]:
        async with self._lock:
            command = raw.strip()
            if not command:
                return {"type": "ack", "level": "muted", "message": ""}
            parts = command.split()
            verb = parts[0].lower()

            if verb == "help":
                return {
                    "type": "ack",
                    "level": "info",
                    "message": (
                        "Commands: attack brute <node>, attack lateral <node>, "
                        "attack apt <n1> <n2> <n3> <n4> <n5>, move <node>, "
                        "stop <node>, stop --all, status"
                    ),
                }

            if verb == "status":
                active = [n.id for n in self.nodes.values() if n.attack]
                return {
                    "type": "ack",
                    "level": "info",
                    "message": f"tick={self.tick} active_attacks={active} memory_cells={len(self.memory_cells)}",
                }

            if verb == "stop":
                return self._command_stop(parts)

            if verb == "move":
                return self._command_move(parts)

            if verb == "attack":
                return self._command_attack(parts)

            return {
                "type": "ack",
                "level": "error",
                "message": f"Unknown command: {command}. Type 'help'.",
            }

    def _command_attack(self, parts: list[str]) -> dict[str, Any]:
        if len(parts) < 3:
            return {"type": "ack", "level": "error", "message": "Usage: attack <brute|lateral|apt> <node...>"}
        kind = parts[1].lower()
        if kind not in SIGNATURES:
            return {"type": "ack", "level": "error", "message": "Attack kind must be brute, lateral, or apt."}

        try:
            nodes = [int(part) for part in parts[2:] if not part.startswith("--")]
        except ValueError:
            return {"type": "ack", "level": "error", "message": "Node ids must be numbers."}

        if kind in {"brute", "lateral"}:
            if not nodes:
                return {"type": "ack", "level": "error", "message": "Provide one node id."}
            node_ids = [nodes[0]]
        else:
            node_ids = nodes[:APT_TARGET_COUNT] if nodes else [3, 7, 11, 15, 19]
            if len(node_ids) < APT_TARGET_COUNT:
                return {"type": "ack", "level": "error", "message": "APT requires five node ids."}

        invalid = [node_id for node_id in node_ids if node_id not in self.nodes]
        if invalid:
            return {"type": "ack", "level": "error", "message": f"Invalid node ids: {invalid}"}

        self.episode_id += 1
        intensity = 1.0
        for node_id in node_ids:
            self.nodes[node_id].attack = Attack(
                kind=kind,  # type: ignore[arg-type]
                intensity=intensity,
                signature=SIGNATURES[kind],
                episode_id=self.episode_id,
                started_tick=self.tick,
            )
            self.nodes[node_id].flagged = False
            self.nodes[node_id].consensus_episode = None

        self._add_event(
            "ATTACK_STARTED",
            f"{kind.upper()} attack started on nodes {node_ids}",
            nodes=node_ids,
            severity="warning",
        )
        return {"type": "ack", "level": "success", "message": f"Attack accepted: {kind} -> {node_ids}"}

    def _command_move(self, parts: list[str]) -> dict[str, Any]:
        if len(parts) < 2:
            return {"type": "ack", "level": "error", "message": "Usage: move <node>"}
        try:
            target = int(parts[1])
        except ValueError:
            return {"type": "ack", "level": "error", "message": "Node id must be a number."}
        if target not in self.nodes:
            return {"type": "ack", "level": "error", "message": f"Invalid node id: {target}"}
        self.episode_id += 1
        self.nodes[target].attack = Attack(
            kind="lateral",
            intensity=1.0,
            signature=SIGNATURES["lateral"],
            episode_id=self.episode_id,
            started_tick=self.tick,
        )
        self.nodes[target].flagged = False
        self.nodes[target].consensus_episode = None
        self._add_event(
            "ATTACK_STARTED",
            f"LATERAL movement started on Node {target}",
            node=target,
            nodes=[target],
            severity="warning",
        )
        return {"type": "ack", "level": "success", "message": f"Lateral movement accepted: node {target}"}

    def _command_stop(self, parts: list[str]) -> dict[str, Any]:
        if len(parts) >= 2 and parts[1] == "--all":
            for node in self.nodes.values():
                node.reset_runtime()
            self.gossip_seen.clear()
            self.gossip_counts.clear()
            self.neighbour_confirmations.clear()
            self.apt_detected_episode = None
            self._add_event("RESET", "Network reset to baseline", severity="success")
            return {"type": "ack", "level": "success", "message": "All attacks stopped. Mesh reset to baseline."}
        if len(parts) < 2:
            return {"type": "ack", "level": "error", "message": "Usage: stop <node> or stop --all"}
        try:
            node_id = int(parts[1])
        except ValueError:
            return {"type": "ack", "level": "error", "message": "Node id must be a number."}
        if node_id not in self.nodes:
            return {"type": "ack", "level": "error", "message": f"Invalid node id: {node_id}"}
        self.nodes[node_id].attack = None
        self.nodes[node_id].danger_emitted_episode = None
        self._add_event("ATTACK_STOPPED", f"Attack stopped on Node {node_id}", node=node_id, severity="success")
        return {"type": "ack", "level": "success", "message": f"Attack stopped on node {node_id}."}

    def state_payload(self) -> dict[str, Any]:
        return {
            "type": "state",
            "tick": self.tick,
            "demoMode": DEMO_MODE,
            "nodes": [
                {
                    "id": node.id,
                    "suspicion": round(node.suspicion, 5),
                    "status": node.status,
                    "flagged": node.flagged,
                    "neighbours": node.neighbours,
                    "detectors": len(node.detectors),
                    "detectorHits": node.last_detector_count,
                    "sensitized": self.tick <= node.sensitized_until if node.sensitized_until else False,
                    "underAttack": node.attack is not None,
                }
                for node in self.nodes.values()
            ],
            "links": self.links_payload(),
            "events": [event.as_dict() for event in self.tick_events],
            "eventLog": [event.as_dict() for event in list(self.recent_events)[:40]],
            "gossip": [
                {"edge": edge, "count": count}
                for edge, count in sorted(
                    self.gossip_counts.items(), key=lambda item: item[1], reverse=True
                )[:8]
            ],
            "memoryCells": len(self.memory_cells),
        }

    def links_payload(self) -> list[dict[str, int]]:
        links: list[dict[str, int]] = []
        seen: set[tuple[int, int]] = set()
        for source, targets in TOPOLOGY.items():
            for target in targets:
                key = tuple(sorted((source, target)))
                if key not in seen:
                    links.append({"source": source, "target": target})
                    seen.add(key)
        return links

    async def connect_observer(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.observers.add(websocket)
        await websocket.send_json(self.state_payload())

    async def connect_attacker(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.attackers.add(websocket)
        await websocket.send_json(
            {
                "type": "hello",
                "message": "Connected to mesh network. 20 nodes online.",
            }
        )

    async def disconnect_observer(self, websocket: WebSocket) -> None:
        self.observers.discard(websocket)

    async def disconnect_attacker(self, websocket: WebSocket) -> None:
        self.attackers.discard(websocket)

    async def _broadcast_observers(self, payload: dict[str, Any]) -> None:
        for websocket in set(self.observers):
            with contextlib.suppress(Exception):
                await websocket.send_json(payload)

    async def _broadcast_attackers(self, payload: dict[str, Any]) -> None:
        for websocket in set(self.attackers):
            with contextlib.suppress(Exception):
                await websocket.send_json(payload)

    @staticmethod
    def _distance(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
        return math.sqrt(sum((left - right) ** 2 for left, right in zip(a, b)))

    def _memory_match(self, signature: tuple[float, float, float]) -> bool:
        return any(
            self._distance(signature, memory) < MEMORY_DISTANCE_THRESHOLD
            for memory in self.memory_cells
        )


simulation = Simulation()
