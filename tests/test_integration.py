"""Integration tests for the HA adapter: events → engine → actions/services.

These use pytest-homeassistant-custom-component (dev-only). The pure core
tests elsewhere run without Home Assistant. Shared harness helpers live in
tests/helpers.py.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from custom_components.hal1976.const import (
    ATTR_ACTIONS,
    ATTR_BEHAVIOUR_ID,
    DOMAIN,
    EVENT_BEHAVIOUR_DETECTED,
    OPTION_NOTIFY_ENTITY,
    SERVICE_CONFIRM_BEHAVIOUR,
    SERVICE_DISMISS_BEHAVIOUR,
    SERVICE_RUN_ANALYSIS,
)
from custom_components.hal1976.models import BehaviourAction, BehaviourStatus
from custom_components.hal1976.seed import end_of_work_day
from tests.helpers import (
    ACTION_DOMAIN,
    ACTION_SERVICE,
    ACTION_TARGET,
    KITCHEN,
    _fire_state_changed,
    capture_calls,
    capture_events,
    fire_evening,
    reload_behaviours,
    seed_behaviours,
    setup_entry,
)


@pytest.fixture(autouse=True)
def auto_enable_custom(enable_custom_integrations):  # noqa: ANN001, ARG001
    """Enable custom_components loading for every test in this module."""


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

    evening = datetime(2026, 11, 25, 17, 30, tzinfo=UTC)
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
    from pathlib import Path

    from homeassistant.util.yaml import load_yaml_dict

    path = Path(__file__).resolve().parent.parent / (
        "custom_components/hal1976/services.yaml"
    )
    declared = load_yaml_dict(str(path))
    assert set(declared) == {
        SERVICE_CONFIRM_BEHAVIOUR,
        SERVICE_DISMISS_BEHAVIOUR,
        SERVICE_RUN_ANALYSIS,
    }
    for name, service in declared.items():
        if name == SERVICE_RUN_ANALYSIS:
            continue  # takes no fields
        assert "fields" in service and "behaviour_id" in service["fields"]
