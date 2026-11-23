"""Integration tests for the HA adapter: events → engine → actions/services.

These use pytest-homeassistant-custom-component (dev-only). The pure core
tests elsewhere run without Home Assistant. Shared harness helpers live in
tests/helpers.py.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er

from custom_components.phil9000.const import (
    ATTR_ACTIONS,
    ATTR_BEHAVIOUR_ID,
    CONF_ENTITIES,
    CONF_EXCLUDE_ENTITIES,
    DOMAIN,
    EVENT_BEHAVIOUR_DETECTED,
    OPTION_NOTIFY_ENTITY,
    SERVICE_CONFIRM_BEHAVIOUR,
    SERVICE_DISMISS_BEHAVIOUR,
    SERVICE_RUN_ANALYSIS,
)
from custom_components.phil9000.models import BehaviourAction, BehaviourStatus
from custom_components.phil9000.seed import end_of_work_day
from tests.helpers import (
    ACTION_DOMAIN,
    ACTION_SERVICE,
    ACTION_TARGET,
    KITCHEN,
    OFFICE,
    _fire_state_changed,
    capture_calls,
    capture_events,
    fire_evening,
    flush_saves,
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
        BehaviourAction(ACTION_DOMAIN, ACTION_SERVICE, ACTION_TARGET, {"note": "welcome home"})
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

    await flush_saves(hass)  # occurrence bumps persist on a debounce
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


async def test_domain_subscription_observes_entities_not_listed(
    hass: HomeAssistant,
) -> None:
    # Subscribing to the binary_sensor domain must cover entities that were
    # never named in the config — including ones added after setup.
    seed_behaviours(hass, end_of_work_day())
    detections = capture_events(hass, EVENT_BEHAVIOUR_DETECTED)

    await setup_entry(hass, data={CONF_ENTITIES: ["binary_sensor"]})
    await fire_evening(hass, day=25)  # Wednesday

    assert len(detections) == 1


async def test_excluded_entity_stops_the_sequence(
    hass: HomeAssistant,
) -> None:
    seed_behaviours(hass, end_of_work_day())
    detections = capture_events(hass, EVENT_BEHAVIOUR_DETECTED)

    await setup_entry(
        hass,
        data={CONF_ENTITIES: ["binary_sensor"], CONF_EXCLUDE_ENTITIES: [KITCHEN]},
    )
    await fire_evening(hass, day=25)

    assert detections == []


async def test_hidden_entities_not_observed_via_domain(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    seed_behaviours(hass, end_of_work_day())
    detections = capture_events(hass, EVENT_BEHAVIOUR_DETECTED)
    entity_registry.async_get_or_create(
        "binary_sensor",
        "test",
        "office_presence",
        suggested_object_id="office_presence",
        hidden_by=er.RegistryEntryHider.INTEGRATION,
    )

    await setup_entry(hass, data={CONF_ENTITIES: ["binary_sensor"]})
    await fire_evening(hass, day=25)

    assert detections == []  # the hidden office sensor breaks the sequence


# -- candidate behaviours: nudges, never actions -----------------------------


async def test_candidate_nudges_once_and_never_fires(hass: HomeAssistant) -> None:
    actions = capture_calls(hass, ACTION_DOMAIN, ACTION_SERVICE)
    notifies = capture_calls(hass, "notify", "send_message")
    behaviour = end_of_work_day(status=BehaviourStatus.CANDIDATE)
    behaviour.actions = [BehaviourAction(ACTION_DOMAIN, ACTION_SERVICE, ACTION_TARGET, {})]
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
    await flush_saves(hass)  # occurrence bumps persist on a debounce
    assert reload_behaviours(hass)[0].occurrences == 2


# -- confirm / dismiss services ----------------------------------------------


async def test_confirm_service_promotes_candidate_and_later_detection_acts(
    hass: HomeAssistant,
) -> None:
    actions = capture_calls(hass, ACTION_DOMAIN, ACTION_SERVICE)
    capture_calls(hass, "notify", "send_message")
    behaviour = end_of_work_day(status=BehaviourStatus.CANDIDATE)
    behaviour.actions = [BehaviourAction(ACTION_DOMAIN, ACTION_SERVICE, ACTION_TARGET, {})]
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
    await flush_saves(hass)  # occurrence bumps persist on a debounce
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
    await flush_saves(hass)  # occurrence bumps persist on a debounce
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

    path = Path(__file__).resolve().parent.parent / ("custom_components/phil9000/services.yaml")
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


# -- attribute filtering and the live activity feed ---------------------------


async def test_attribute_only_changes_are_not_observed(hass: HomeAssistant) -> None:
    seed_behaviours(hass, end_of_work_day())  # CONFIRMED
    detections = capture_events(hass, EVENT_BEHAVIOUR_DETECTED)
    await setup_entry(hass)

    evening = datetime(2026, 11, 25, 17, 30, tzinfo=UTC)
    # Attribute-only office event (state stays "off"), then the kitchen step:
    # no observation, so the sequence must not match.
    _fire_state_changed(hass, OFFICE, "off", "off", evening)
    _fire_state_changed(hass, KITCHEN, "off", "on", evening + timedelta(minutes=2))
    await hass.async_block_till_done()

    assert detections == []
    assert reload_behaviours(hass)[0].occurrences == 0


async def test_activity_subscribers_receive_live_rows(hass: HomeAssistant) -> None:
    seed_behaviours(hass, end_of_work_day())
    entry = await setup_entry(hass)
    runtime = hass.data[DOMAIN][entry.entry_id]
    rows: list[dict] = []
    runtime.activity_subscribers.append(rows.append)

    await fire_evening(hass, day=25)

    assert [(r["entity_id"], r["new_state"]) for r in rows] == [
        (OFFICE, "off"),
        (KITCHEN, "on"),
    ]
    assert rows[0]["old_state"] == "on"


async def test_subscription_changes_apply_in_place_without_reload(hass: HomeAssistant) -> None:
    seed_behaviours(hass, end_of_work_day())  # CONFIRMED
    detections = capture_events(hass, EVENT_BEHAVIOUR_DETECTED)
    entry = await setup_entry(hass)
    runtime = hass.data[DOMAIN][entry.entry_id]
    subs_before = runtime.subscriptions

    # A data-only change (what the panel's mute button does): the listener
    # swaps Subscriptions on the live runtime instead of reloading the entry.
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_EXCLUDE_ENTITIES: [OFFICE]}
    )
    await hass.async_block_till_done()

    assert hass.data[DOMAIN][entry.entry_id] is runtime  # not reloaded
    assert runtime.subscriptions is not subs_before
    assert not runtime.subscriptions.matches(OFFICE)
    assert runtime.subscriptions.matches(KITCHEN)

    # The swap is effective immediately: the muted entity stops being observed.
    await fire_evening(hass, day=25)
    assert detections == []


async def test_services_reject_non_admin_users(hass: HomeAssistant, hass_read_only_user) -> None:
    """Amending via services is admin-only; internal (userless) calls pass."""
    from homeassistant.core import Context
    from homeassistant.exceptions import Unauthorized

    seed_behaviours(hass, end_of_work_day())
    entry = await setup_entry(hass)

    payloads = {
        SERVICE_CONFIRM_BEHAVIOUR: {"behaviour_id": "end_of_work_day"},
        SERVICE_DISMISS_BEHAVIOUR: {"behaviour_id": "end_of_work_day"},
        SERVICE_RUN_ANALYSIS: {},
    }
    for service, data in payloads.items():
        with pytest.raises(Unauthorized):
            await hass.services.async_call(
                DOMAIN,
                service,
                data,
                blocking=True,
                context=Context(user_id=hass_read_only_user.id),
            )

    runtime = hass.data[DOMAIN][entry.entry_id]
    assert runtime.engine.behaviours["end_of_work_day"].status is BehaviourStatus.CONFIRMED


async def test_occurrence_saves_are_debounced_and_deduplicate(hass: HomeAssistant) -> None:
    seed_behaviours(hass, end_of_work_day())
    await setup_entry(hass)

    await fire_evening(hass, day=23)
    await fire_evening(hass, day=24)
    await hass.async_block_till_done()

    # Counters moved in memory but the file write is deferred (and collapses
    # the two bumps into one save, not one per match).
    assert reload_behaviours(hass)[0].occurrences == 0

    await flush_saves(hass)
    assert reload_behaviours(hass)[0].occurrences == 2


async def test_detection_payload_has_no_attribute_bloat(hass: HomeAssistant) -> None:
    seed_behaviours(hass, end_of_work_day())
    await setup_entry(hass)
    events = capture_events(hass, EVENT_BEHAVIOUR_DETECTED)

    await fire_evening(hass, day=23)
    await hass.async_block_till_done()

    assert events, "expected a detection event"
    matched = events[0]["matched"]
    assert matched and "attributes" not in matched[0]  # dropped as dead weight
    assert "entity_id" in matched[0] and "new_state" in matched[0]
