"""Domain models for HAL1976 behaviours, patterns, and observations.

Pure Python — no Home Assistant imports. Everything is UI-agnostic data that
can be serialised to/from YAML (see yaml_store.py).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, time
from enum import StrEnum
from typing import Any


class BehaviourStatus(StrEnum):
    """Lifecycle of a learned behaviour.

    Only CONFIRMED behaviours are ever executed. Candidates are learning;
    dismissed ones are ignored (kept for the record so the LLM analyser can
    avoid re-proposing them).
    """

    CANDIDATE = "candidate"
    CONFIRMED = "confirmed"
    DISMISSED = "dismissed"


@dataclass(frozen=True, slots=True)
class Observation:
    """A timestamped entity state change (e.g. office presence → off at 17:30)."""

    entity_id: str
    new_state: str
    timestamp: datetime
    old_state: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "new_state": self.new_state,
            "timestamp": self.timestamp.isoformat(),
            "old_state": self.old_state,
            "attributes": self.attributes,
        }


@dataclass(slots=True)
class BehaviourStep:
    """One step in a behaviour's sequence of state changes.

    The first step has no gap constraint; each subsequent step must occur
    within `max_gap_seconds` of the previous step's timestamp.
    """

    entity_id: str
    to_state: str
    max_gap_seconds: int = 300

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "to_state": self.to_state,
            "max_gap_seconds": self.max_gap_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BehaviourStep:
        return cls(
            entity_id=data["entity_id"],
            to_state=data["to_state"],
            max_gap_seconds=int(data.get("max_gap_seconds", 300)),
        )


@dataclass(slots=True)
class BehaviourAction:
    """A single Home Assistant service call to run when a behaviour is detected.

    Modelled as a list on Behaviour from day one so action sequences can be
    added later without a schema change (MVP executes each entry in order).
    """

    domain: str
    service: str
    entity_id: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "service": self.service,
            "entity_id": self.entity_id,
            "data": self.data,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BehaviourAction:
        return cls(
            domain=data["domain"],
            service=data["service"],
            entity_id=data["entity_id"],
            data=dict(data.get("data", {})),
        )


@dataclass(slots=True)
class Behaviour:
    """A learned household behaviour (e.g. 'end of work day').

    Attributes:
        steps: ordered sequence of state changes that constitute the behaviour.
        weekdays: ISO weekday numbers that must match (1=Mon ... 7=Sun).
            Empty means every day.
        time_start / time_end: window in which the *first* step must occur.
            None means any time. If start > end the window wraps midnight.
        confidence: 0.0–1.0, grows as occurrences are observed. Never
            auto-promotes to CONFIRMED — only the user confirms.
        occurrences: how many times the step sequence has been observed.
        cooldown_seconds: minimum time between detections of this behaviour.
    """

    id: str
    name: str
    description: str = ""
    steps: list[BehaviourStep] = field(default_factory=list)
    actions: list[BehaviourAction] = field(default_factory=list)
    weekdays: set[int] = field(default_factory=set)
    time_start: time | None = None
    time_end: time | None = None
    confidence: float = 0.0
    occurrences: int = 0
    status: BehaviourStatus = BehaviourStatus.CANDIDATE
    cooldown_seconds: int = 3600

    def __post_init__(self) -> None:
        if not self.id:
            self.id = uuid.uuid4().hex[:12]
        if isinstance(self.status, str):
            self.status = BehaviourStatus(self.status)

    # -- serialisation -------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "steps": [s.to_dict() for s in self.steps],
            "actions": [a.to_dict() for a in self.actions],
            "weekdays": sorted(self.weekdays),
            "time_start": self.time_start.isoformat() if self.time_start else None,
            "time_end": self.time_end.isoformat() if self.time_end else None,
            "confidence": round(self.confidence, 3),
            "occurrences": self.occurrences,
            "status": self.status.value,
            "cooldown_seconds": self.cooldown_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Behaviour:
        time_start = data.get("time_start")
        time_end = data.get("time_end")
        return cls(
            id=data["id"],
            name=data["name"],
            description=data.get("description", ""),
            steps=[BehaviourStep.from_dict(s) for s in data.get("steps", [])],
            actions=[BehaviourAction.from_dict(a) for a in data.get("actions", [])],
            weekdays=set(data.get("weekdays", [])),
            time_start=time.fromisoformat(time_start) if time_start else None,
            time_end=time.fromisoformat(time_end) if time_end else None,
            confidence=float(data.get("confidence", 0.0)),
            occurrences=int(data.get("occurrences", 0)),
            status=BehaviourStatus(data.get("status", BehaviourStatus.CANDIDATE.value)),
            cooldown_seconds=int(data.get("cooldown_seconds", 3600)),
        )
