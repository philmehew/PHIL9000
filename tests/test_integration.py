"""Integration tests for the HA adapter: events → engine → actions/services.

These use pytest-homeassistant-custom-component (dev-only). The pure core
tests elsewhere run without Home Assistant.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant, ServiceCall, State
from homeassistant.exceptions import ServiceValidationError

from custom_components.hal1976.const import (
    ATTR_ACTIONS,
    ATTR_BEHAVIOUR_ID,
    CONF_ENTITIES,
    DOMAIN,
    EVENT_BEHAVIOUR_DETECTED,
    OPTION_NOTIFY_ENTITY,
    SERVICE_CONFIRM_BEHAVIOUR,
    SERVICE_DISMISS_BEHAVIOUR,
)
from custom_components.hal1976.models import BehaviourAction, BehaviourStatus
from custom_components.hal1976.seed import end_of_work_day
from custom_components.hal1976.yaml_store import BehaviourStore
from pytest_homeassistant_custom_component.common import MockConfigEntry

OFFICE = "binary_sensor.office_presence"
KITCHEN = "binary_sensor.kitchen_presence"
ACTION_DOMAIN = "test_automation"
ACTION_SERVICE = "capture"
ACTION_TARGET = "test.target_entity"


@pytest.fixture(autouse=True)
def auto_enable_custom(enable_custom_integrations):  # noqa: ANN001, ARG001
    """Enable custom_components loading for every test in this module."""


# -- helpers ---------------------------------------------------------------


def behaviours_path(hass: HomeAssistant):
    return hass.config.path("hal1976", "behaviours.yaml")


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


async def setup_entry(
    hass: HomeAssistant, options: dict | None = None
) -> MockConfigEntry:
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
    evening = datetime(2026, 11, day, 17, 30, tzinfo=timezone.utc)
    _fire_state_changed(hass, OFFICE, "on", "off", evening)
    _fire_state_changed(hass, KITCHEN, "off", "on", evening.replace(minute=32))
    await hass.async_block_till_done()


# -- confirmed behaviours: event + action execution -------------------------


async def test_confirmed_behaviour_fires_event_and_runs_actions(
    hass: HomeAssistant,
) -> None:
    actions = capture_calls(hass, ACTION_DOMAIN, ACTION_SERVICE)
    behaviour = end_of_work_day()  # seeded as CONFIRMED
    behaviour.actions = [
        BehaviourAction(
            ACTION_DOMAIN, ACTION_SERVICE, ACTION_TARGET, {"note": "welcome home"}
        )
    ]
    seed_behaviours(hass, behaviour)
    detections = capture_events(hass, EVENT_BEHAVIOUR_DETECTED)

    await setup_entry(hass)
    await fire_evening(hass, day=25)  # Wednesday

    assert len(detections) == 1
    event = detections[0]
    assert event[ATTR_BEHAVIOUR_ID] == "end_of_work_day"
    assert len(event[ATTR_ACTIONS]) == 1

    assert len(actions) == 1
    assert actions[0].data["note"] == "welcome home"
    assert actions[0].data["entity_id"] == ACTION_TARGET  # target merged in

    assert reload_behaviours(hass)[0].occurrences == 1


async def test_events_for_unobserved_entities_ignored(hass: HomeAssistant) -> None:
    behaviour = end_of_work_day()
    seed_behaviours(hass, behaviour)
    detections = capture_events(hass, EVENT_BEHAVIOUR_DETECTED)

    await setup_entry(hass)

    evening = datetime(2026, 11, 25, 17, 30, tzinfo=timezone.utc)
    _fire_state_changed(hass, "binary_sensor.garage_presence", "on", "off", evening)
    _fire_state_changed(hass, KITCHEN, "off", "on", evening.replace(minute=32))
    await hass.async_block_till_done()

    assert detections == []


# -- candidate behaviours: nudges, never actions -----------------------------


async def test_candidate_nudges_once_and_never_fires(hass: HomeAssistant) -> None:
    actions = capture_calls(hass, ACTION_DOMAIN, ACTION_SERVICE)
    notifies = capture_calls(hass, "notify", "send_message")
    behaviour = end_of_work_day(status=BehaviourStatus.CANDIDATE)
    behaviour.actions = [
        BehaviourAction(ACTION_DOMAIN, ACTION_SERVICE, ACTION_TARGET, {})
    ]
    seed_behaviours(hass, behaviour)
    detections = capture_events(hass, EVENT_BEHAVIOUR_DETECTED)

    await setup_entry(hass, options={OPTION_NOTIFY_ENTITY: "notify.mobile_phone"})

    await fire_evening(hass, day=23)  # Monday: first sighting
    assert detections == []
    assert actions == []
    assert len(notifies) == 1
    assert "end_of_work_day" in notifies[0].data["message"]
    assert notifies[0].data["entity_id"] == "notify.mobile_phone"

    await fire_evening(hass, day=24)  # Tuesday: second sighting, no repeat nudge
    assert detections == []
    assert actions == []
    assert len(notifies) == 1
    assert reload_behaviours(hass)[0].occurrences == 2


# -- confirm / dismiss services ----------------------------------------------


async def test_confirm_service_promotes_candidate_and_later_detection_acts(
    hass: HomeAssistant,
) -> None:
    actions = capture_calls(hass, ACTION_DOMAIN, ACTION_SERVICE)
    capture_calls(hass, "notify", "send_message")
    behaviour = end_of_work_day(status=BehaviourStatus.CANDIDATE)
    behaviour.actions = [
        BehaviourAction(ACTION_DOMAIN, ACTION_SERVICE, ACTION_TARGET, {})
    ]
    seed_behaviours(hass, behaviour)
    detections = capture_events(hass, EVENT_BEHAVIOUR_DETECTED)

    await setup_entry(hass, options={OPTION_NOTIFY_ENTITY: "notify.mobile_phone"})
    await fire_evening(hass, day=23)  # Monday: candidate sighting, nothing fires
    assert detections == []

    await hass.services.async_call(
        DOMAIN,
        SERVICE_CONFIRM_BEHAVIOUR,
        {ATTR_BEHAVIOUR_ID: "end_of_work_day"},
        blocking=True,
    )
    assert reload_behaviours(hass)[0].status is BehaviourStatus.CONFIRMED

    await fire_evening(hass, day=24)  # Tuesday: now confirmed → event + action
    assert len(detections) == 1
    assert len(actions) == 1
    assert reload_behaviours(hass)[0].occurrences == 2


async def test_dismiss_service_stops_events_and_nudges(hass: HomeAssistant) -> None:
    notifies = capture_calls(hass, "notify", "send_message")
    behaviour = end_of_work_day(status=BehaviourStatus.CANDIDATE)
    seed_behaviours(hass, behaviour)
    detections = capture_events(hass, EVENT_BEHAVIOUR_DETECTED)

    await setup_entry(hass, options={OPTION_NOTIFY_ENTITY: "notify.mobile_phone"})

    await hass.services.async_call(
        DOMAIN,
        SERVICE_DISMISS_BEHAVIOUR,
        {ATTR_BEHAVIOUR_ID: "end_of_work_day"},
        blocking=True,
    )
    assert reload_behaviours(hass)[0].status is BehaviourStatus.DISMISSED

    await fire_evening(hass, day=23)
    assert detections == []
    assert notifies == []
    # Occurrences keep tallying for the record, but nothing else happens.
    assert reload_behaviours(hass)[0].occurrences == 1


async def test_status_services_reject_unknown_behaviour_id(
    hass: HomeAssistant,
) -> None:
    seed_behaviours(hass, end_of_work_day())
    await setup_entry(hass)

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_CONFIRM_BEHAVIOUR,
            {ATTR_BEHAVIOUR_ID: "no_such_behaviour"},
            blocking=True,
        )


def test_services_yaml_matches_registered_services() -> None:
    """services.yaml (loaded by the HA frontend) stays in sync with the code."""
    from homeassistant.util.yaml import load_yaml_dict

    path = Path(__file__).resolve().parent.parent / (
        "custom_components/hal1976/services.yaml"
    )
    declared = load_yaml_dict(str(path))
    assert set(declared) == {SERVICE_CONFIRM_BEHAVIOUR, SERVICE_DISMISS_BEHAVIOUR}
    for service in declared.values():
        assert "fields" in service and "behaviour_id" in service["fields"]
