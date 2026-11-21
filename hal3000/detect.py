"""Detection engine: pure-Python matching of live events against behaviours.

No Home Assistant imports, fully deterministic, unit-testable with synthetic
event streams. Only CONFIRMED behaviours fire detections; candidates merely
accumulate occurrences (confidence grows, never auto-promotes).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, time
from typing import Any

from .models import Behaviour, BehaviourStatus, Observation

logger = logging.getLogger(__name__)

DetectionCallback = Callable[[Behaviour, list[Observation]], Any]


def _in_time_window(moment: time, start: time | None, end: time | None) -> bool:
    """True if `moment` is inside [start, end]; wraps midnight if start > end."""
    if start is None or end is None:
        return True
    if start <= end:
        return start <= moment <= end
    return moment >= start or moment <= end  # e.g. 22:00–06:00


class Detection:
    """A completed match of a behaviour's full step sequence."""

    def __init__(self, behaviour: Behaviour, matched: list[Observation]) -> None:
        self.behaviour = behaviour
        self.matched = matched

    def __repr__(self) -> str:  # pragma: no cover
        return f"Detection(behaviour={self.behaviour.name!r}, steps={len(self.matched)})"


class DetectionEngine:
    """Matches a live stream of Observations against a set of Behaviours.

    Usage: feed observations in chronological order via `observe()`; each call
    returns the detections completed by that event (and invokes `on_detect`).

    Per-behaviour partial matches are tracked. A partial match is broken when
    an observed entity involved in the pending steps takes a different state,
    or when the gap to the next step exceeds its `max_gap_seconds` (checked
    lazily on the next event for that entity). A completed confirmed behaviour
    does not re-fire within its cooldown.
    """

    def __init__(
        self,
        behaviours: list[Behaviour],
        on_detect: DetectionCallback | None = None,
    ) -> None:
        self.behaviours = {b.id: b for b in behaviours}
        self.on_detect = on_detect
        # behaviour_id -> matched observations so far.
        self._partial: dict[str, list[Observation]] = {}
        # behaviour_id -> timestamp of last completed match (cooldowns).
        self._last_fired: dict[str, datetime] = {}

    # -- public API ----------------------------------------------------

    def add_behaviour(self, behaviour: Behaviour) -> None:
        self.behaviours[behaviour.id] = behaviour

    def remove_behaviour(self, behaviour_id: str) -> None:
        self.behaviours.pop(behaviour_id, None)
        self._partial.pop(behaviour_id, None)
        self._last_fired.pop(behaviour_id, None)

    def observe(self, observation: Observation) -> list[Detection]:
        """Process one observation; return detections completed by this event."""
        detections: list[Detection] = []
        for behaviour in self.behaviours.values():
            detection = self._advance(behaviour, observation)
            if detection is not None:
                detections.append(detection)
                if self.on_detect is not None:
                    self.on_detect(detection.behaviour, detection.matched)
        return detections

    def observe_all(self, observations: list[Observation]) -> list[Detection]:
        """Convenience for replaying a (chronological) synthetic event stream."""
        detections: list[Detection] = []
        for obs in observations:
            detections.extend(self.observe(obs))
        return detections

    # -- matching ------------------------------------------------------

    def _advance(
        self, behaviour: Behaviour, observation: Observation
    ) -> Detection | None:
        if not behaviour.steps:
            return None
        partial = self._partial.get(behaviour.id)

        if partial is None:
            return self._try_start(behaviour, observation)
        return self._try_continue(behaviour, observation, partial)

    def _try_start(
        self, behaviour: Behaviour, observation: Observation
    ) -> Detection | None:
        """No partial match: does this event start the sequence?"""
        first = behaviour.steps[0]
        if not self._matches(first, observation):
            return None
        # Day-of-week and time-window constraints apply to the first step.
        if behaviour.weekdays and observation.timestamp.isoweekday() not in behaviour.weekdays:
            return None
        if not _in_time_window(
            observation.timestamp.time(), behaviour.time_start, behaviour.time_end
        ):
            return None
        self._partial[behaviour.id] = [observation]
        if len(behaviour.steps) == 1:
            return self._complete(behaviour, observation.timestamp)
        return None

    def _try_continue(
        self, behaviour: Behaviour, observation: Observation, partial: list[Observation]
    ) -> Detection | None:
        """A partial match is in progress: does this event advance or break it?"""
        next_index = len(partial)
        if next_index >= len(behaviour.steps):
            self._partial.pop(behaviour.id, None)
            return None
        first = behaviour.steps[0]
        step = behaviour.steps[next_index]

        # A fresh first-step event while a partial is open (e.g. a stale
        # sequence that started too early): restart from here.
        if (
            observation.entity_id == first.entity_id
            and observation.entity_id != step.entity_id
            and self._matches(first, observation)
        ):
            self._partial.pop(behaviour.id, None)
            return self._try_start(behaviour, observation)

        # A previously-matched entity took a different state: pattern broken.
        # (Other entities' events are simply ignored.)
        if (
            observation.entity_id != step.entity_id
            and observation.entity_id in {p.entity_id for p in partial}
            and observation.new_state != next(
                p.new_state for p in partial if p.entity_id == observation.entity_id
            )
        ):
            self._partial.pop(behaviour.id, None)
            return None

        # Advance an in-progress match with the expected next step?
        if observation.entity_id == step.entity_id:
            if not self._matches(step, observation):
                self._partial.pop(behaviour.id, None)
                return None
            gap = (observation.timestamp - partial[-1].timestamp).total_seconds()
            if gap < 0:
                self._partial.pop(behaviour.id, None)
                return None
            if gap > step.max_gap_seconds:
                self._partial.pop(behaviour.id, None)
                # This same event may start a fresh sequence.
                return self._try_start(behaviour, observation)
            partial.append(observation)
            if len(partial) == len(behaviour.steps):
                return self._complete(behaviour, observation.timestamp)
        return None

    @staticmethod
    def _matches(step: Any, observation: Observation) -> bool:
        return (
            observation.entity_id == step.entity_id
            and observation.new_state == step.to_state
        )

    def _complete(self, behaviour: Behaviour, moment: datetime) -> Detection | None:
        matched = self._partial.pop(behaviour.id)
        behaviour.occurrences += 1
        behaviour.confidence = min(1.0, 1.0 - (1.0 / (behaviour.occurrences + 1)))
        if behaviour.status is not BehaviourStatus.CONFIRMED:
            logger.info(
                "Behaviour %r matched but is %s; not firing",
                behaviour.name,
                behaviour.status.value,
            )
            return None
        if self._cooldown_active(behaviour, moment):
            logger.debug("Behaviour %r in cooldown; suppressed", behaviour.name)
            return None
        self._last_fired[behaviour.id] = moment
        return Detection(behaviour, matched)

    def _cooldown_active(self, behaviour: Behaviour, moment: datetime) -> bool:
        last = self._last_fired.get(behaviour.id)
        if last is None:
            return False
        return (moment - last).total_seconds() < behaviour.cooldown_seconds
