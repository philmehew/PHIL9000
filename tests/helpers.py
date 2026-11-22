"""Shared helpers for the HA-harness tests (see tests/test_integration.py)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant, ServiceCall, State
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.phil9000.const import CONF_ENTITIES, DOMAIN
from custom_components.phil9000.yaml_store import BehaviourStore

OFFICE = "binary_sensor.office_presence"
KITCHEN = "binary_sensor.kitchen_presence"

# A fake service domain used to spy on action execution.
ACTION_DOMAIN = "test_automation"
ACTION_SERVICE = "capture"
ACTION_TARGET = "test.target_entity"


def behaviours_path(hass: HomeAssistant) -> Path:
    return Path(hass.config.path("phil9000", "behaviours.yaml"))


def seed_behaviours(hass: HomeAssistant, *behaviours) -> None:
    BehaviourStore(behaviours_path(hass)).save(list(behaviours))


def reload_behaviours(hass: HomeAssistant):
    return BehaviourStore(behaviours_path(hass)).load()


def capture_calls(hass: HomeAssistant, domain: str, service: str) -> list[ServiceCall]:
    calls: list[ServiceCall] = []
    hass.services.async_register(domain, service, calls.append)
    return calls


def capture_events(hass: HomeAssistant, event_type: str) -> list[dict]:
    events: list[dict] = []
    hass.bus.async_listen(event_type, lambda ev: events.append(dict(ev.data)))
    return events


async def setup_entry(hass: HomeAssistant, options: dict | None = None) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        source=SOURCE_USER,
        data={CONF_ENTITIES: [OFFICE, KITCHEN]},
        options=options or {},
        unique_id=DOMAIN,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _fire_state_changed(
    hass: HomeAssistant, entity_id: str, old: str, new: str, when: datetime
) -> None:
    hass.bus.async_fire(
        "state_changed",
        {
            "entity_id": entity_id,
            "old_state": State(entity_id, old, {}, last_changed=when),
            "new_state": State(entity_id, new, {}, last_changed=when),
        },
    )


async def fire_evening(hass: HomeAssistant, day: int) -> None:
    """Office off at 17:30, kitchen on at 17:32 on a November 2026 weekday."""
    evening = datetime(2026, 11, day, 17, 30, tzinfo=UTC)
    _fire_state_changed(hass, OFFICE, "on", "off", evening)
    _fire_state_changed(hass, KITCHEN, "off", "on", evening.replace(minute=32))
    await hass.async_block_till_done()
