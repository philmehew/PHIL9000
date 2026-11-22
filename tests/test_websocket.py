"""Websocket API tests (Milestone 4): the frontend's data surface."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant

from custom_components.phil9000.const import EVENT_BEHAVIOUR_DETECTED
from custom_components.phil9000.models import BehaviourStatus
from custom_components.phil9000.seed import end_of_work_day
from tests.helpers import (
    KITCHEN,
    OFFICE,
    capture_events,
    fire_evening,
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
