"""Integration smoke tests: the HA adapter wires events → engine → detections.

These use pytest-homeassistant-custom-component (dev-only). The pure core
tests elsewhere run without Home Assistant.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant, State

from custom_components.hal1976.const import (
    ATTR_ACTIONS,
    ATTR_BEHAVIOUR_ID,
    CONF_ENTITIES,
    DOMAIN,
    EVENT_BEHAVIOUR_DETECTED,
)
from custom_components.hal1976.models import BehaviourAction
from custom_components.hal1976.seed import end_of_work_day
from custom_components.hal1976.yaml_store import BehaviourStore
from pytest_homeassistant_custom_component.common import MockConfigEntry

OFFICE = "binary_sensor.office_presence"
KITCHEN = "binary_sensor.kitchen_presence"


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


def _seed_confirmed_behaviour(hass: HomeAssistant) -> None:
    store = BehaviourStore(hass.config.path("hal1976", "behaviours.yaml"))
    behaviour = end_of_work_day()
    behaviour.actions = [
        BehaviourAction(
            "tts", "speak", "tts.home_assistant_cloud", {"message": "welcome home"}
        )
    ]
    store.save([behaviour])


@pytest.fixture(autouse=True)
def auto_enable_custom(enable_custom_integrations):  # noqa: ANN001, ARG001
    """Enable custom_components loading for every test in this module."""


async def test_setup_and_detection_fires_domain_event(
    hass: HomeAssistant,
) -> None:
    _seed_confirmed_behaviour(hass)

    captured: list[dict] = []
    hass.bus.async_listen(EVENT_BEHAVIOUR_DETECTED, lambda ev: captured.append(dict(ev.data)))

    entry = MockConfigEntry(
        domain=DOMAIN,
        source=SOURCE_USER,
        data={CONF_ENTITIES: [OFFICE, KITCHEN]},
        unique_id=DOMAIN,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # Wednesday evening: office off at 17:30, kitchen on at 17:32.
    wed = datetime(2026, 11, 25, 17, 30, tzinfo=timezone.utc)
    _fire_state_changed(hass, OFFICE, "on", "off", wed)
    _fire_state_changed(hass, KITCHEN, "off", "on", wed.replace(minute=32))
    await hass.async_block_till_done()

    assert len(captured) == 1
    event = captured[0]
    assert event[ATTR_BEHAVIOUR_ID] == "end_of_work_day"
    assert len(event[ATTR_ACTIONS]) == 1

    # Occurrence stats were persisted back to the YAML store.
    reloaded = BehaviourStore(hass.config.path("hal1976", "behaviours.yaml")).load()
    assert reloaded[0].occurrences == 1


async def test_events_for_unobserved_entities_ignored(
    hass: HomeAssistant,
) -> None:
    _seed_confirmed_behaviour(hass)

    captured: list[dict] = []
    hass.bus.async_listen(EVENT_BEHAVIOUR_DETECTED, lambda ev: captured.append(dict(ev.data)))

    entry = MockConfigEntry(
        domain=DOMAIN,
        source=SOURCE_USER,
        data={CONF_ENTITIES: [OFFICE, KITCHEN]},
        unique_id=DOMAIN,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    wed = datetime(2026, 11, 25, 17, 30, tzinfo=timezone.utc)
    _fire_state_changed(hass, "binary_sensor.garage_presence", "on", "off", wed)
    _fire_state_changed(hass, KITCHEN, "off", "on", wed.replace(minute=32))
    await hass.async_block_till_done()

    assert captured == []
