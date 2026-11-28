"""Unit tests for the pure recording module: session, prompts, guardrails."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from custom_components.phil9000 import recording
from custom_components.phil9000.models import BehaviourStatus
from custom_components.phil9000.recording import (
    RecordingSession,
    build_recording_prompt,
    direct_behaviour,
    parse_automation_reply,
    proposal_to_behaviour,
    state_to_action,
)

MOTION = "binary_sensor.office_motion"
DESK = "switch.phil_s_desk"
LAMP = "light.desk_lamp"


def _session() -> RecordingSession:
    """A session with one demonstration: motion on, then the desk switched on."""
    session = RecordingSession()
    session.set_mode("trigger")
    session.capture("trigger", MOTION, "off", "on", datetime(2026, 11, 25, 18, 0, tzinfo=UTC))
    session.capture(
        "trigger",
        "sensor.workbench_power",
        "low",
        "high",
        datetime(2026, 11, 25, 18, 1, 30, tzinfo=UTC),
    )
    session.set_mode("action")
    session.capture("action", DESK, "off", "on", datetime(2026, 11, 25, 18, 2, tzinfo=UTC))
    session.notes = "weekdays only"
    return session


# -- session model ---------------------------------------------------------


def test_set_mode_validates_and_stamps_start() -> None:
    session = RecordingSession()
    assert session.mode is None and session.started_at is None
    session.set_mode("trigger")
    assert session.mode == "trigger"
    assert session.started_at is not None
    session.set_mode("action")  # switching keeps the original start
    assert session.mode == "action"
    session.set_mode("off")
    assert session.mode is None
    with pytest.raises(ValueError):
        session.set_mode("everything")


def test_capture_appends_and_caps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(recording, "MAX_ITEMS_PER_MODE", 3)
    session = RecordingSession()
    for index in range(5):
        session.capture(
            "trigger", f"sensor.s{index}", "off", "on", datetime(2026, 11, 25, tzinfo=UTC)
        )
    assert [i.entity_id for i in session.triggers] == ["sensor.s2", "sensor.s3", "sensor.s4"]
    # the action bucket is independent
    session.capture("action", DESK, "off", "on", datetime(2026, 11, 25, tzinfo=UTC))
    assert len(session.actions) == 1


def test_remove_by_item_id() -> None:
    session = _session()
    item_id = session.triggers[0].item_id
    assert session.remove("trigger", item_id) is True
    assert [i.entity_id for i in session.triggers] == ["sensor.workbench_power"]
    assert session.remove("trigger", "nope") is False


def test_conversation_capped() -> None:
    monkeypatch_marker = RecordingSession()
    for index in range(15):
        monkeypatch_marker.add_turn("user" if index % 2 == 0 else "assistant", f"t{index}")
    assert len(monkeypatch_marker.conversation) == recording.MAX_CONVERSATION_TURNS
    assert monkeypatch_marker.conversation[-1]["text"] == "t14"
    assert monkeypatch_marker.conversation[0]["role"] == "assistant"


def test_round_trip_preserves_the_session() -> None:
    session = _session()
    session.proposal = {"name": "Work starts", "steps": [], "actions": []}
    session.add_turn("assistant", "Made you a thing.")
    restored = RecordingSession.from_dict(json.loads(json.dumps(session.to_dict())))
    assert restored.mode == session.mode
    assert [(i.entity_id, i.from_state, i.to_state) for i in restored.triggers] == [
        (i.entity_id, i.from_state, i.to_state) for i in session.triggers
    ]
    assert [i.item_id for i in restored.triggers] == [i.item_id for i in session.triggers]
    assert restored.notes == "weekdays only"
    assert restored.proposal == session.proposal
    assert restored.conversation == session.conversation
    assert restored.started_at == session.started_at


def test_from_dict_is_lenient() -> None:
    restored = RecordingSession.from_dict(
        {
            "mode": "bogus",  # unknown mode -> disarmed, not a crash
            "triggers": [{"entity_id": MOTION}, {"nope": 1}],  # rows without `at` skipped
            "actions": "not a list",
            "notes": 42,
            "proposal": ["nope"],  # only dicts are proposals
            "conversation": [{"role": "wizard", "text": "?"}],
            "started_at": "nonsense",
        }
    )
    assert restored.mode is None
    assert restored.triggers == []
    assert restored.actions == []
    assert restored.notes == "42"
    assert restored.proposal is None
    assert restored.conversation == []
    assert restored.started_at is None


def test_clear_discards_everything() -> None:
    session = _session()
    session.clear()
    assert session.is_empty()


# -- prompts ----------------------------------------------------------------


def test_first_prompt_shows_the_demonstration() -> None:
    session = _session()
    prompt = build_recording_prompt(session, known=[])
    assert MOTION in prompt
    assert f"{DESK} off -> on" in prompt
    assert "weekdays only" in prompt
    assert "sensor.workbench_power" in prompt
    # the gap between the two triggers is the model's cue for max_gap_seconds
    assert "(+90s)" in prompt


def test_refine_prompt_carries_proposal_conversation_and_request() -> None:
    session = _session()
    session.proposal = {"name": "Work starts", "steps": [{"entity_id": MOTION, "to_state": "on"}]}
    session.add_turn("assistant", "I wired motion to the desk.")
    prompt = build_recording_prompt(session, message="make it weekdays only", known=[])
    assert "Work starts" in prompt
    assert "make it weekdays only" in prompt
    assert "I wired motion to the desk." in prompt
    assert MOTION in prompt


def test_known_behaviours_are_listed() -> None:
    from custom_components.phil9000.seed import end_of_work_day

    known = [end_of_work_day()]
    prompt = build_recording_prompt(_session(), known=known)
    assert "End of work day" in prompt


# -- reply parsing -------------------------------------------------------------


def _reply(**overrides) -> str:  # noqa: ANN001
    payload = {
        "name": "Desk work start",
        "description": "Motion and power at the bench mean Phil starts work.",
        "steps": [
            {"entity_id": MOTION, "to_state": "on"},
            {"entity_id": "sensor.workbench_power", "to_state": "high", "max_gap_seconds": 240},
        ],
        "actions": [
            {"domain": "switch", "service": "turn_on", "entity_id": DESK, "data": {}},
        ],
        "reply": "Turns the desk on when you start work.",
    }
    payload.update(overrides)
    return f"```json\n{json.dumps(payload)}\n```"


def test_parse_valid_reply_normalises_the_proposal() -> None:
    result = parse_automation_reply(_reply(), _session())
    assert result is not None and result.proposal is not None
    proposal = result.proposal
    assert proposal["name"] == "Desk work start"
    assert [s["entity_id"] for s in proposal["steps"]] == [MOTION, "sensor.workbench_power"]
    assert proposal["steps"][0]["max_gap_seconds"] == 300  # default filled
    assert proposal["cooldown_seconds"] == 3600
    assert proposal["actions"][0]["service"] == "turn_on"
    assert result.dropped == []


def test_parse_rejects_non_json() -> None:
    assert parse_automation_reply("The user seems to start work.", _session()) is None


def test_hallucinated_step_entity_is_dropped_with_reason() -> None:
    reply = _reply(
        steps=[
            {"entity_id": MOTION, "to_state": "on"},
            {"entity_id": "light.chandelier", "to_state": "on"},
        ]
    )
    result = parse_automation_reply(reply, _session())
    assert result is not None and result.proposal is not None
    assert [s["entity_id"] for s in result.proposal["steps"]] == [MOTION]
    assert any("light.chandelier" in what and "not recorded" in why for what, why in result.dropped)


def test_proposal_with_no_usable_steps_is_rejected() -> None:
    result = parse_automation_reply(
        _reply(steps=[{"entity_id": "light.x", "to_state": "on"}]), _session()
    )
    assert result is not None
    assert result.proposal is None  # nothing detectable survives
    assert any(what == "<whole proposal>" for what, _ in result.dropped)


def test_step_state_mismatch_is_a_warning_not_a_drop() -> None:
    result = parse_automation_reply(
        _reply(steps=[{"entity_id": MOTION, "to_state": "off"}]), _session()
    )
    assert result is not None and result.proposal is not None
    assert result.proposal["steps"][0]["to_state"] == "off"
    assert any("differs from the recorded state" in warning for warning in result.warnings)


def test_action_validation() -> None:
    result = parse_automation_reply(
        _reply(
            actions=[
                {"domain": "light", "service": "turn_on", "entity_id": "just-a-name", "data": {}},
                {"domain": "Light", "service": "turn_on"},
                {"domain": "lock", "service": "lock", "entity_id": "lock.front", "data": {}},
                {"domain": "light", "service": "turn_on", "entity_id": LAMP, "data": "nope"},
            ]
        ),
        _session(),
    )
    assert result is not None
    assert result is not None and result.proposal is not None
    actions = result.proposal["actions"]
    # "Light" is normalised to light.turn_on (entity optional); the malformed
    # entity id and the string `data` are dropped.
    assert [f"{a['domain']}.{a['service']}" for a in actions] == ["light.turn_on", "lock.lock"]
    assert len(result.dropped) == 2
    # a notes-driven action on an unrecorded entity is kept with a warning
    result = parse_automation_reply(
        _reply(actions=[{"domain": "lock", "service": "lock", "entity_id": "lock.front"}]),
        _session(),
    )
    assert result is not None and result.proposal is not None
    assert len(result.proposal["actions"]) == 1
    assert any("lock.front" in warning for warning in result.warnings)


def test_time_window_both_or_neither_and_gaps_clamped() -> None:
    result = parse_automation_reply(
        _reply(time_start="21:30", time_end="nonsense", weekdays=[0, 1, 8, 5, 3]),
        _session(),
    )
    assert result is not None and result.proposal is not None
    proposal = result.proposal
    assert proposal["time_start"] is None and proposal["time_end"] is None
    assert proposal["weekdays"] == [1, 3, 5]
    result = parse_automation_reply(
        _reply(steps=[{"entity_id": MOTION, "to_state": "on", "max_gap_seconds": 10**9}]),
        _session(),
    )
    assert result is not None and result.proposal is not None
    assert result.proposal["steps"][0]["max_gap_seconds"] == 86400


def test_full_time_window_is_kept_as_hhmm() -> None:
    result = parse_automation_reply(_reply(time_start="21:30:15", time_end="06:00"), _session())
    assert result is not None and result.proposal is not None
    assert result.proposal["time_start"] == "21:30"
    assert result.proposal["time_end"] == "06:00"


# -- creating the behaviour ------------------------------------------------


def test_proposal_to_behaviour_is_confirmed_and_complete() -> None:
    result = parse_automation_reply(
        _reply(weekdays=[1, 2, 3, 4, 5], time_start="17:00", time_end="19:00"), _session()
    )
    assert result is not None and result.proposal is not None
    behaviour = proposal_to_behaviour(result.proposal)
    assert behaviour.id.startswith("rec-")
    assert behaviour.status is BehaviourStatus.CONFIRMED
    assert behaviour.confidence == 1.0
    assert behaviour.weekdays == {1, 2, 3, 4, 5}
    assert behaviour.time_start == datetime.strptime("17:00", "%H:%M").time()
    assert behaviour.actions[0].entity_id == DESK
    assert behaviour.cooldown_seconds == 3600


def test_state_to_action_mappings() -> None:
    for entity_id, to_state, service in [
        ("light.x", "on", "turn_on"),
        ("switch.x", "off", "turn_off"),
        ("cover.x", "closed", "close_cover"),
        ("media_player.x", "playing", "media_play"),
    ]:
        action = state_to_action(entity_id, to_state)
        assert action is not None and action.service == service
    assert state_to_action("input_select.x", "movie") is None


def test_direct_behaviour_replays_the_demonstration() -> None:
    session = _session()
    behaviour = direct_behaviour(session)
    assert behaviour.status is BehaviourStatus.CONFIRMED
    assert behaviour.name == "Recorded routine"
    assert [s.entity_id for s in behaviour.steps] == [MOTION, "sensor.workbench_power"]
    # observed gap 90s -> 180s with slack, within the sane band
    assert behaviour.steps[1].max_gap_seconds == 180
    assert behaviour.actions[0].domain == "switch"
    assert behaviour.actions[0].service == "turn_on"
    assert "weekdays only" in behaviour.description


def test_direct_behaviour_points_at_the_llm_when_needed() -> None:
    session = _session()
    session.actions = []
    session.capture(
        "action",
        "input_select.mode",
        "idle",
        "movie",
        datetime(2026, 11, 25, 18, 2, tzinfo=UTC),
    )
    with pytest.raises(ValueError, match="Propose automation"):
        direct_behaviour(session)

    empty = RecordingSession()
    with pytest.raises(ValueError, match="trigger"):
        direct_behaviour(empty)
    triggers_only = RecordingSession()
    triggers_only.capture("trigger", MOTION, "off", "on", datetime(2026, 11, 25, tzinfo=UTC))
    with pytest.raises(ValueError, match="action"):
        direct_behaviour(triggers_only)
