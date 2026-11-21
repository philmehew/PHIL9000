"""HAL1976 — learns household behaviour from Home Assistant sensor data.

The pure domain logic (models, detection engine, YAML store) lives in this
package with **no Home Assistant imports**: every ``homeassistant.*`` import
here is function-local so the core modules stay importable and testable
without Home Assistant installed. ``tests/test_purity.py`` guards this
boundary. This module is the thin adapter over the event bus and storage.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .const import (
    ATTR_ACTIONS,
    ATTR_BEHAVIOUR_ID,
    ATTR_BEHAVIOUR_NAME,
    ATTR_CONFIDENCE,
    ATTR_MATCHED,
    ATTR_OCCURRENCES,
    ATTR_STATUS,
    BEHAVIOURS_FILE,
    CONFIG_DIR,
    CONF_ENTITIES,
    DOMAIN,
    EVENT_BEHAVIOUR_DETECTED,
)
from .detect import Detection, DetectionEngine
from .models import (
    Behaviour,
    BehaviourAction,
    BehaviourStatus,
    BehaviourStep,
    Observation,
)
from .yaml_store import BehaviourStore

__all__ = [
    "Behaviour",
    "BehaviourAction",
    "BehaviourStatus",
    "BehaviourStep",
    "Detection",
    "DetectionEngine",
    "Observation",
    "BehaviourStore",
]

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import Event, HomeAssistant

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class HalRuntime:
    """Per-config-entry runtime state, stored in hass.data[DOMAIN]."""

    engine: DetectionEngine
    store: BehaviourStore


async def async_setup(hass: HomeAssistant, config: dict) -> bool:  # noqa: ARG001
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Load behaviours from YAML, start the engine, watch the event bus."""
    from homeassistant.helpers.event import (  # function-local: purity guard
        async_track_state_change_event,
    )

    store = BehaviourStore(hass.config.path(CONFIG_DIR, BEHAVIOURS_FILE))
    behaviours = await hass.async_add_executor_job(store.load)

    engine = DetectionEngine(behaviours, on_detect=_detection_callback(hass, entry))
    runtime = HalRuntime(engine=engine, store=store)
    hass.data[DOMAIN][entry.entry_id] = runtime

    entities: list[str] = list(entry.data[CONF_ENTITIES])
    unsub = async_track_state_change_event(
        hass, entities, _event_handler(hass, runtime)
    )
    entry.async_on_unload(unsub)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    _LOGGER.debug(
        "HAL1976 observing %d entities, %d behaviours loaded",
        len(entities),
        len(behaviours),
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    runtime: HalRuntime | None = hass.data[DOMAIN].pop(entry.entry_id, None)
    if runtime is not None:
        await _async_save_stats(hass, runtime)
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Entity selection or options changed: reload so subscriptions match."""
    await hass.config_entries.async_reload(entry.entry_id)


def _detection_callback(hass: HomeAssistant, entry: ConfigEntry):  # noqa: ANN202
    """Build the engine's on_detect hook: fire the hal1976 domain event."""

    def on_detect(behaviour, matched) -> None:  # noqa: ANN001
        hass.bus.async_fire(
            EVENT_BEHAVIOUR_DETECTED,
            {
                ATTR_BEHAVIOUR_ID: behaviour.id,
                ATTR_BEHAVIOUR_NAME: behaviour.name,
                ATTR_STATUS: behaviour.status.value,
                ATTR_CONFIDENCE: round(behaviour.confidence, 3),
                ATTR_OCCURRENCES: behaviour.occurrences,
                ATTR_MATCHED: [obs.to_dict() for obs in matched],
                # M3 turns these into actual service calls via the action
                # executor; for now automations can consume them verbatim.
                ATTR_ACTIONS: [action.to_dict() for action in behaviour.actions],
            },
        )

    return on_detect


def _event_handler(hass: HomeAssistant, runtime: HalRuntime):  # noqa: ANN202
    """Translate state_changed events into Observations for the engine."""
    from homeassistant.core import callback  # function-local: purity guard

    engine = runtime.engine

    @callback
    def handle_event(event: Event) -> None:
        new_state = event.data.get("new_state")
        if new_state is None:  # entity removed
            return
        observation = Observation(
            entity_id=event.data["entity_id"],
            new_state=new_state.state,
            old_state=event.data["old_state"].state
            if event.data.get("old_state")
            else None,
            timestamp=new_state.last_changed,
            attributes=dict(new_state.attributes),
        )
        total_before = sum(b.occurrences for b in engine.behaviours.values())
        for detection in engine.observe(observation):
            trail = " → ".join(
                f"{o.entity_id}={o.new_state}" for o in detection.matched
            )
            _LOGGER.info(
                "Detected %s (confidence %.0f%%): %s",
                detection.behaviour.name,
                detection.behaviour.confidence * 100,
                trail,
            )
        # The engine tallies occurrences (even for candidates and matches
        # suppressed by cooldown); persist whenever any counter moved.
        if sum(b.occurrences for b in engine.behaviours.values()) > total_before:
            hass.async_create_task(_async_save_stats(hass, runtime))

    return handle_event


async def _async_save_stats(hass: HomeAssistant, runtime: HalRuntime) -> None:
    try:
        await hass.async_add_executor_job(
            runtime.store.save, list(runtime.engine.behaviours.values())
        )
    except Exception:  # noqa: BLE001 — persistence must not take the loop down
        _LOGGER.exception("Failed to persist HAL1976 behaviour stats")
