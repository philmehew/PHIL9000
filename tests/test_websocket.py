"""Websocket API tests (Milestone 4): the frontend's data surface."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from homeassistant.core import HomeAssistant

from custom_components.phil9000 import analysis
from custom_components.phil9000.const import (
    CONF_ENTITIES,
    CONF_EXCLUDE_ENTITIES,
    DOMAIN,
    EVENT_BEHAVIOUR_DETECTED,
)
from custom_components.phil9000.models import BehaviourStatus
from custom_components.phil9000.seed import end_of_work_day
from tests.helpers import (
    KITCHEN,
    OFFICE,
    _fire_state_changed,
    capture_events,
    fire_evening,
    flush_saves,
    reload_behaviours,
    seed_behaviours,
    setup_entry,
)

pytest.importorskip("pytest_homeassistant_custom_component")


@pytest.fixture(autouse=True)
def auto_enable_custom(enable_custom_integrations):  # noqa: ANN001, ARG001
    """Enable custom_components loading for every test in this module."""


async def _send(client, msg_id: int, payload: dict) -> dict:
    await client.send_json({"id": msg_id, **payload})
    return await client.receive_json()


async def test_behaviours_list_confirm_and_evidence(hass: HomeAssistant, hass_ws_client) -> None:
    behaviour = end_of_work_day(status=BehaviourStatus.CANDIDATE)
    seed_behaviours(hass, behaviour)
    # The sensor entities exist, so the (exact-id) subscription expands to them.
    hass.states.async_set(OFFICE, "on")
    hass.states.async_set(KITCHEN, "off")
    detections = capture_events(hass, EVENT_BEHAVIOUR_DETECTED)
    await setup_entry(hass)

    client = await hass_ws_client(hass)

    # List: the candidate is visible with its steps, not yet confirmed.
    out = await _send(client, 1, {"type": "phil9000/behaviours"})
    assert out["success"] is True
    assert out["result"]["observed_entities"] == sorted(
        [OFFICE, KITCHEN]
    )  # exact-id subscription expanded
    listed = out["result"]["behaviours"][0]
    assert listed["id"] == "end_of_work_day"
    assert listed["status"] == "candidate"
    assert len(listed["steps"]) == 2
    assert listed["last_detection"] is None

    # Confirm via websocket: persisted, and a later match fires with evidence.
    out = await _send(
        client,
        2,
        {
            "type": "phil9000/set_status",
            "behaviour_id": "end_of_work_day",
            "status": "confirmed",
        },
    )
    assert out["success"] is True
    assert reload_behaviours(hass)[0].status is BehaviourStatus.CONFIRMED

    await fire_evening(hass, day=25)  # Wednesday
    assert len(detections) == 1

    out = await _send(client, 3, {"type": "phil9000/behaviours"})
    listed = out["result"]["behaviours"][0]
    assert listed["status"] == "confirmed"
    assert listed["occurrences"] == 1
    evidence = listed["last_detection"]
    assert evidence is not None
    assert len(evidence["observations"]) == 2
    assert evidence["observations"][0]["entity_id"] == "binary_sensor.office_presence"


async def test_set_actions_persists_and_next_detection_uses_them(
    hass: HomeAssistant, hass_ws_client
) -> None:
    seed_behaviours(hass, end_of_work_day())
    await setup_entry(hass)

    client = await hass_ws_client(hass)
    out = await _send(
        client,
        1,
        {
            "type": "phil9000/set_actions",
            "behaviour_id": "end_of_work_day",
            "actions": [
                {
                    "domain": "tts",
                    "service": "speak",
                    "entity_id": "tts.home_assistant_cloud",
                    "data": {"message": "welcome home"},
                },
                {"domain": "switch", "service": "turn_off", "entity_id": "switch.desk"},
            ],
        },
    )
    assert out["success"] is True

    reloaded = reload_behaviours(hass)[0]
    assert len(reloaded.actions) == 2
    assert reloaded.actions[0].data == {"message": "welcome home"}
    # entity_id defaulted in the schema
    assert reloaded.actions[1].entity_id == "switch.desk"

    detections = capture_events(hass, EVENT_BEHAVIOUR_DETECTED)
    await fire_evening(hass, day=25)
    assert len(detections) == 1
    assert len(detections[0]["actions"]) == 2


async def test_websocket_error_paths(hass: HomeAssistant, hass_ws_client) -> None:
    seed_behaviours(hass, end_of_work_day())
    await setup_entry(hass)
    client = await hass_ws_client(hass)

    # Unknown behaviour id -> not_found error.
    out = await _send(
        client,
        1,
        {
            "type": "phil9000/set_status",
            "behaviour_id": "nope",
            "status": "confirmed",
        },
    )
    assert out["success"] is False
    assert out["error"]["code"] == "not_found"
    assert "nope" in out["error"]["message"]

    # Invalid status value -> rejected by the command schema.
    out = await _send(
        client,
        2,
        {
            "type": "phil9000/set_status",
            "behaviour_id": "end_of_work_day",
            "status": "autopilot",
        },
    )
    assert out["success"] is False


# -- activity feed ------------------------------------------------------------


async def test_activity_hint_when_no_recorder(hass: HomeAssistant, hass_ws_client) -> None:
    seed_behaviours(hass, end_of_work_day())
    await setup_entry(hass)
    client = await hass_ws_client(hass)

    out = await _send(client, 1, {"type": "phil9000/activity"})
    assert out["success"] is True
    assert out["result"]["items"] == []
    assert "recorder" in out["result"]["hint"]


async def test_activity_returns_feed_rows(hass: HomeAssistant, hass_ws_client, monkeypatch) -> None:
    seed_behaviours(hass, end_of_work_day())
    hass.states.async_set(OFFICE, "off")
    await setup_entry(hass)
    client = await hass_ws_client(hass)

    captured: dict = {}
    rows = [
        {
            "entity_id": OFFICE,
            "at": "2026-11-22T17:30:00+00:00",
            "old_state": "on",
            "new_state": "off",
        }
    ]

    async def fake_fetch(hass_arg, entity_ids, limit):  # noqa: ANN001
        captured["entity_ids"] = entity_ids
        captured["limit"] = limit
        return rows

    monkeypatch.setattr(analysis, "async_fetch_activity", fake_fetch)
    out = await _send(client, 1, {"type": "phil9000/activity"})
    assert out["success"] is True
    assert out["result"]["items"] == rows
    assert captured["entity_ids"] == sorted([OFFICE])
    assert captured["limit"] == 1000  # full-size feed: 3h window, capped at 1000 rows
    # Diagnostics the panel renders under the heading.
    assert out["result"]["observed"] == 1
    assert out["result"]["window_hours"] == analysis.ACTIVITY_LOOKBACK_HOURS
    assert out["result"]["observed_entities"] == sorted([OFFICE])


async def test_subscribe_activity_pushes_live_rows(hass: HomeAssistant, hass_ws_client) -> None:
    behaviour = end_of_work_day()  # CONFIRMED, so we also see the detection path
    seed_behaviours(hass, behaviour)
    await setup_entry(hass)
    client = await hass_ws_client(hass)

    await client.send_json({"id": 7, "type": "phil9000/subscribe_activity"})
    out = await client.receive_json()
    assert out["success"] is True

    await fire_evening(hass, day=25)

    push = await client.receive_json()
    assert push["id"] == 7 and push["type"] == "event"
    assert push["event"]["entity_id"] == OFFICE
    assert push["event"]["old_state"] == "on"
    assert push["event"]["new_state"] == "off"

    push = await client.receive_json()
    assert push["event"]["entity_id"] == KITCHEN
    assert push["event"]["new_state"] == "on"

    # Attribute-only changes are not activity: a follow-up request must come
    # back next, with no queued push before it.
    _fire_state_changed(hass, OFFICE, "off", "off", datetime(2026, 11, 25, 17, 40, tzinfo=UTC))
    await hass.async_block_till_done()
    await client.send_json({"id": 8, "type": "phil9000/behaviours"})
    out = await client.receive_json()
    assert out["id"] == 8 and out["success"] is True


# -- run_analysis and rename --------------------------------------------------


async def test_run_analysis_ws_error_and_result(
    hass: HomeAssistant, hass_ws_client, monkeypatch
) -> None:
    seed_behaviours(hass, end_of_work_day())
    await setup_entry(hass)
    client = await hass_ws_client(hass)

    # No recorder in the harness -> friendly error, not a crash.
    out = await _send(client, 1, {"type": "phil9000/run_analysis"})
    assert out["success"] is False
    assert "recorder" in out["error"]["message"]

    async def fake_run(hass_arg, entry, runtime, **kwargs):  # noqa: ANN001
        assert kwargs.get("trigger") == "manual"
        return {"refined": ["Refined one"], "new": ["Brand new"]}

    monkeypatch.setattr(analysis, "async_run_analysis", fake_run)
    out = await _send(client, 2, {"type": "phil9000/run_analysis"})
    assert out["success"] is True
    assert out["result"] == {"refined": ["Refined one"], "new": ["Brand new"]}


async def test_analysis_log_ws_fetch_and_live_push(hass: HomeAssistant, hass_ws_client) -> None:
    from custom_components.phil9000.analysis import _log_run

    seed_behaviours(hass, end_of_work_day())
    entry = await setup_entry(hass)
    runtime = hass.data[DOMAIN][entry.entry_id]
    runtime.analysis_log.clear()  # the store persists across tests by design
    client = await hass_ws_client(hass)

    out = await _send(client, 1, {"type": "phil9000/analysis_log"})
    assert out["success"] is True and out["result"]["entries"] == []

    await client.send_json({"id": 2, "type": "phil9000/subscribe_analysis_log"})
    assert (await client.receive_json())["success"] is True

    _log_run(runtime, "info", "Run started (manual)")
    push = await client.receive_json()
    assert push["id"] == 2 and push["type"] == "event"
    assert push["event"]["message"] == "Run started (manual)"
    assert push["event"]["level"] == "info"

    _log_run(runtime, "error", "Run failed: boom")
    push = await client.receive_json()
    assert push["event"]["level"] == "error"

    # Fetch returns the buffer newest-first.
    out = await _send(client, 3, {"type": "phil9000/analysis_log"})
    assert [e["message"] for e in out["result"]["entries"]] == [
        "Run failed: boom",
        "Run started (manual)",
    ]


async def test_set_name_persists_and_flags_custom_name(hass: HomeAssistant, hass_ws_client) -> None:
    seed_behaviours(hass, end_of_work_day(status=BehaviourStatus.CANDIDATE))
    await setup_entry(hass)
    client = await hass_ws_client(hass)

    out = await _send(
        client,
        1,
        {
            "type": "phil9000/set_name",
            "behaviour_id": "end_of_work_day",
            "name": "Dad clocks off",
        },
    )
    assert out["success"] is True
    reloaded = reload_behaviours(hass)[0]
    assert reloaded.name == "Dad clocks off"
    assert reloaded.custom_name is True

    out = await _send(client, 2, {"type": "phil9000/set_name", "behaviour_id": "nope", "name": "X"})
    assert out["success"] is False
    assert out["error"]["code"] == "not_found"


# -- monitoring and mutes ------------------------------------------------------


async def test_monitoring_reports_specs_and_mutes(hass: HomeAssistant, hass_ws_client) -> None:
    await setup_entry(
        hass,
        data={
            CONF_ENTITIES: ["binary_sensor", "light.desk"],
            CONF_EXCLUDE_ENTITIES: [KITCHEN],
        },
    )
    client = await hass_ws_client(hass)

    out = await _send(client, 1, {"type": "phil9000/monitoring"})
    assert out["success"] is True
    result = out["result"]
    assert result["domains"] == ["binary_sensor"]
    assert result["entities"] == ["light.desk"]
    assert result["muted"] == [KITCHEN]
    assert isinstance(result["observed"], list)


async def test_mute_entity_persists_and_stops_live_rows(
    hass: HomeAssistant, hass_ws_client
) -> None:
    seed_behaviours(hass, end_of_work_day())
    entry = await setup_entry(hass)
    runtime = hass.data[DOMAIN][entry.entry_id]
    client = await hass_ws_client(hass)

    await client.send_json({"id": 7, "type": "phil9000/subscribe_activity"})
    assert (await client.receive_json())["success"] is True

    out = await _send(client, 8, {"type": "phil9000/mute_entity", "entity_id": OFFICE})
    assert out["success"] is True, out
    assert out["result"] == {"muted": [OFFICE]}
    # Persisted on the entry, applied in place — same runtime, no reload.
    assert entry.data[CONF_EXCLUDE_ENTITIES] == [OFFICE]
    assert hass.data[DOMAIN][entry.entry_id] is runtime

    # The live subscription keeps working; only the office rows are gone.
    await fire_evening(hass, day=25)
    push = await client.receive_json()
    assert push["id"] == 7 and push["type"] == "event"
    assert push["event"]["entity_id"] == KITCHEN
    assert reload_behaviours(hass)[0].occurrences == 0  # half a sequence: no match

    # Muting twice is a no-op with the same answer.
    out = await _send(client, 9, {"type": "phil9000/mute_entity", "entity_id": OFFICE})
    assert out["success"] is True
    assert out["result"] == {"muted": [OFFICE]}
    assert entry.data[CONF_EXCLUDE_ENTITIES] == [OFFICE]


async def test_unmute_entity_restores_live_rows(hass: HomeAssistant, hass_ws_client) -> None:
    seed_behaviours(hass, end_of_work_day())
    entry = await setup_entry(
        hass, data={CONF_ENTITIES: [OFFICE, KITCHEN], CONF_EXCLUDE_ENTITIES: [OFFICE]}
    )
    client = await hass_ws_client(hass)

    await client.send_json({"id": 7, "type": "phil9000/subscribe_activity"})
    assert (await client.receive_json())["success"] is True

    await fire_evening(hass, day=25)  # office muted: kitchen only
    assert (await client.receive_json())["event"]["entity_id"] == KITCHEN

    out = await _send(client, 8, {"type": "phil9000/unmute_entity", "entity_id": OFFICE})
    assert out["success"] is True
    assert out["result"] == {"muted": []}
    assert CONF_EXCLUDE_ENTITIES not in entry.data or not entry.data[CONF_EXCLUDE_ENTITIES]

    await fire_evening(hass, day=26)  # both steps now observed: both rows
    assert (await client.receive_json())["event"]["entity_id"] == OFFICE
    assert (await client.receive_json())["event"]["entity_id"] == KITCHEN
    await flush_saves(hass)  # occurrence bumps persist on a debounce
    assert reload_behaviours(hass)[0].occurrences == 1


async def test_mute_rejects_invalid_entity_id(hass: HomeAssistant, hass_ws_client) -> None:
    await setup_entry(hass)
    client = await hass_ws_client(hass)

    out = await _send(client, 1, {"type": "phil9000/mute_entity", "entity_id": "not an id"})
    assert out["success"] is False
    assert out["error"]["code"] == "invalid_format"


# -- set_observed (the panel's entity browser) ---------------------------------


async def test_set_observed_untick_drops_spec_and_mutes(
    hass: HomeAssistant, hass_ws_client
) -> None:
    entry = await setup_entry(hass)  # explicit specs: [OFFICE, KITCHEN]
    runtime = hass.data[DOMAIN][entry.entry_id]
    client = await hass_ws_client(hass)

    out = await _send(
        client, 1, {"type": "phil9000/set_observed", "entity_id": OFFICE, "observed": False}
    )
    assert out["success"] is True, out
    assert entry.data[CONF_ENTITIES] == [KITCHEN]
    assert entry.data[CONF_EXCLUDE_ENTITIES] == [OFFICE]
    assert not runtime.subscriptions.matches(OFFICE)
    assert runtime.subscriptions.matches(KITCHEN)


async def test_set_observed_tick_covers_unsubscribed_entity(
    hass: HomeAssistant, hass_ws_client
) -> None:
    entry = await setup_entry(hass)
    runtime = hass.data[DOMAIN][entry.entry_id]
    client = await hass_ws_client(hass)
    hass.states.async_set("sensor.hallway_temp", "19")

    out = await _send(
        client,
        1,
        {"type": "phil9000/set_observed", "entity_id": "sensor.hallway_temp", "observed": True},
    )
    assert out["success"] is True, out
    assert "sensor.hallway_temp" in entry.data[CONF_ENTITIES]
    assert runtime.subscriptions.matches("sensor.hallway_temp")

    # Re-ticking an already-covered entity changes nothing (still no domain
    # spec here: the default entry uses exact ids).
    out = await _send(
        client, 2, {"type": "phil9000/set_observed", "entity_id": KITCHEN, "observed": True}
    )
    assert out["result"]["explicit"] == sorted([KITCHEN, OFFICE, "sensor.hallway_temp"])


async def test_set_observed_untick_mutes_domain_covered_entity(
    hass: HomeAssistant, hass_ws_client
) -> None:
    entry = await setup_entry(hass, data={CONF_ENTITIES: ["binary_sensor"]})
    runtime = hass.data[DOMAIN][entry.entry_id]
    client = await hass_ws_client(hass)

    out = await _send(
        client, 1, {"type": "phil9000/set_observed", "entity_id": OFFICE, "observed": False}
    )
    assert out["success"] is True, out
    # Removing the domain spec would nuke every sensor: muting is the way.
    assert entry.data[CONF_ENTITIES] == ["binary_sensor"]
    assert entry.data[CONF_EXCLUDE_ENTITIES] == [OFFICE]
    assert not runtime.subscriptions.matches(OFFICE)

    # Re-tick unmutes without leaving a redundant explicit spec behind.
    out = await _send(
        client, 2, {"type": "phil9000/set_observed", "entity_id": OFFICE, "observed": True}
    )
    assert out["result"]["muted"] == []
    assert entry.data[CONF_ENTITIES] == ["binary_sensor"]
    assert CONF_EXCLUDE_ENTITIES not in entry.data


async def test_set_observed_untick_of_unobserved_entity_is_noop(
    hass: HomeAssistant, hass_ws_client
) -> None:
    entry = await setup_entry(hass)
    client = await hass_ws_client(hass)

    out = await _send(
        client, 1, {"type": "phil9000/set_observed", "entity_id": "sensor.other", "observed": False}
    )
    assert out["success"] is True, out
    assert out["result"]["muted"] == []  # no phantom mute recorded
    assert CONF_EXCLUDE_ENTITIES not in entry.data


# -- domain checklist -----------------------------------------------------------


async def test_monitoring_lists_domain_options_with_labels(
    hass: HomeAssistant, hass_ws_client
) -> None:
    await setup_entry(hass, data={CONF_ENTITIES: ["binary_sensor", "light.desk"]})
    client = await hass_ws_client(hass)

    out = await _send(client, 1, {"type": "phil9000/monitoring"})
    options = {d["value"]: d for d in out["result"]["domain_options"]}
    assert "binary_sensor" in options and "switch" in options
    # Compact label for the checklist; the examples moved to a hover hint.
    assert options["binary_sensor"]["label"] == "Binary sensors"
    assert options["binary_sensor"]["hint"] == "motion, doors, presence"
    assert options["switch"]["hint"] == ""
    assert out["result"]["domains"] == ["binary_sensor"]


async def test_set_domains_swaps_checklist_and_keeps_panel_specs(
    hass: HomeAssistant, hass_ws_client
) -> None:
    entry = await setup_entry(
        hass,
        data={
            CONF_ENTITIES: ["binary_sensor", "light.desk"],
            CONF_EXCLUDE_ENTITIES: [KITCHEN],
        },
    )
    runtime = hass.data[DOMAIN][entry.entry_id]
    client = await hass_ws_client(hass)

    out = await _send(client, 1, {"type": "phil9000/set_domains", "domains": ["switch"]})
    assert out["success"] is True, out
    assert entry.data[CONF_ENTITIES] == sorted(["switch", "light.desk"])
    assert entry.data[CONF_EXCLUDE_ENTITIES] == [KITCHEN]  # mutes untouched
    assert not runtime.subscriptions.matches(OFFICE)  # domain unchecked live
    assert runtime.subscriptions.matches("light.desk")

    out = await _send(client, 2, {"type": "phil9000/set_domains", "domains": ["binary_sensor"]})
    assert runtime.subscriptions.matches(OFFICE)

    out = await _send(client, 3, {"type": "phil9000/set_domains", "domains": ["bogus_domain"]})
    assert out["success"] is False
    assert out["error"]["code"] == "invalid_format"


# -- admin gating ----------------------------------------------------------------


async def test_mutating_commands_require_admin(
    hass: HomeAssistant, hass_ws_client, hass_read_only_access_token
) -> None:
    """Reads stay open; anything that amends needs an admin user."""
    seed_behaviours(hass, end_of_work_day())
    entry = await setup_entry(hass)
    client = await hass_ws_client(hass, access_token=hass_read_only_access_token)

    # Reads work for everyone.
    out = await _send(client, 1, {"type": "phil9000/behaviours"})
    assert out["success"] is True
    assert out["result"]["behaviours"][0]["status"] == "confirmed"
    out = await _send(client, 2, {"type": "phil9000/monitoring"})
    assert out["success"] is True

    mutations: list[dict] = [
        {"type": "phil9000/set_status", "behaviour_id": "end_of_work_day", "status": "dismissed"},
        {"type": "phil9000/set_name", "behaviour_id": "end_of_work_day", "name": "Nope"},
        {"type": "phil9000/set_actions", "behaviour_id": "end_of_work_day", "actions": []},
        {"type": "phil9000/mute_entity", "entity_id": OFFICE},
        {"type": "phil9000/unmute_entity", "entity_id": OFFICE},
        {"type": "phil9000/set_observed", "entity_id": OFFICE, "observed": False},
        {"type": "phil9000/set_domains", "domains": ["switch"]},
        {"type": "phil9000/run_analysis"},
    ]
    for i, payload in enumerate(mutations, start=3):
        out = await _send(client, i, payload)
        assert out["success"] is False, payload
        assert out["error"]["code"] == "unauthorized", payload

    # And nothing actually changed.
    runtime = hass.data[DOMAIN][entry.entry_id]
    assert runtime.engine.behaviours["end_of_work_day"].status is BehaviourStatus.CONFIRMED
    assert entry.data == {CONF_ENTITIES: [OFFICE, KITCHEN]}
    assert not entry.data.get(CONF_EXCLUDE_ENTITIES)
