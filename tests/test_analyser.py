"""Pure tests for the analyser: summarise, prompt, and proposal validation.

These run without Home Assistant. The guardrails matter most here: the LLM
can only ever produce inert, schema-valid CANDIDATE behaviours.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, time, timedelta

from custom_components.phil9000.analyser import (
    MAX_PROPOSALS_PER_RUN,
    PROPOSAL_PREFIX,
    behaviour_signature,
    build_prompt,
    parse_analysis_reply,
    summarise_history,
)
from custom_components.phil9000.models import Behaviour, BehaviourStatus, BehaviourStep
from custom_components.phil9000.seed import end_of_work_day

OFFICE = "binary_sensor.office_presence"
KITCHEN = "binary_sensor.kitchen_presence"


def parse_new(text: str, existing: list[Behaviour] | None = None) -> list[Behaviour]:
    """New proposals from a raw (possibly legacy-array) reply.

    The old parse_new() API is gone; these tests exercise the same
    extraction/validation/dedupe pipeline through the refinement parser.
    """
    return parse_analysis_reply(text, existing or []).new


def _events(day_offsets=3):
    """Evening handoff at 17:30/17:32 UTC on consecutive days."""
    events = {OFFICE: [], KITCHEN: []}
    for offset in range(day_offsets):
        day = datetime(2026, 11, 20 + offset, tzinfo=UTC)
        events[OFFICE].append((day.replace(hour=8), "on"))
        events[OFFICE].append((day.replace(hour=17, minute=30), "off"))
        events[KITCHEN].append((day.replace(hour=17, minute=32), "on"))
    return events


# -- summarise ----------------------------------------------------------------


def test_summarise_lists_entities_days_and_states():
    summary = summarise_history(_events())
    lines = summary.splitlines()
    assert len(lines) == 2  # one line per entity, sorted
    assert lines[0].startswith(KITCHEN)
    assert "off@17:30" in summary and "on@17:32" in summary
    assert "Fri 2026-11-20" in summary


def test_summarise_caps_long_histories():
    events = {"sensor.x": [(datetime(2026, 1, 1, tzinfo=UTC), str(i)) for i in range(300)]}
    summary = summarise_history(events)
    assert "(+240 earlier events)" in summary  # cap is 60, most recent kept


def test_summarise_shrinks_to_fit_the_char_budget():
    # A pathological window (many entities, all flapping) must not produce a
    # prompt the LLM will choke on: the per-entity cap halves until the
    # digest fits SUMMARY_CHAR_BUDGET.
    from custom_components.phil9000.analyser import SUMMARY_CHAR_BUDGET

    events = {
        f"sensor.s{i}": [
            (datetime(2026, 11, 1, tzinfo=UTC) + timedelta(hours=h), str(h % 2)) for h in range(200)
        ]
        for i in range(50)
    }
    summary = summarise_history(events)
    assert len(summary) <= SUMMARY_CHAR_BUDGET
    assert "(+earlier" not in summary  # truncation notes say how much was cut
    assert "(+" in summary


def test_summarise_drops_constant_state_spam_but_keeps_rare_markers():
    # A constant state repeated past the cap is refresh spam (a real week
    # had one entity reporting 'off' 5,700 times) — but the same state a
    # handful of times at distinct moments is a rhythm marker and stays.
    spam = {
        "light.spam": [(datetime(2026, 11, 2, hour=h % 24, tzinfo=UTC), "off") for h in range(80)],
    }
    assert "light.spam" not in summarise_history(spam)

    marker = {
        "binary_sensor.kitchen": [
            (datetime(2026, 11, 20 + d, hour=17, minute=32, tzinfo=UTC), "on") for d in range(3)
        ],
    }
    summary = summarise_history(marker)
    assert "binary_sensor.kitchen" in summary
    assert "on@17:32" in summary


# -- prompt --------------------------------------------------------------------


def test_prompt_includes_summary_and_behaviours_with_ids():
    prompt = build_prompt("OFFICE TEXT", [end_of_work_day()])
    assert "OFFICE TEXT" in prompt
    assert "End of work day" in prompt
    assert "confirmed" in prompt
    assert "end_of_work_day" in prompt  # ids so the LLM can reference candidates
    assert "ONLY a JSON object" in prompt
    assert "NEVER put a confirmed" in prompt
    empty = build_prompt("S", [])
    assert "Current behaviours (JSON" in empty


# -- proposal parsing ------------------------------------------------------------


def _proposal_json(**overrides) -> str:
    proposal = {
        "name": "Evening handoff",
        "description": "Office off then kitchen on.",
        "steps": [
            {"entity_id": OFFICE, "to_state": "off"},
            {"entity_id": KITCHEN, "to_state": "on", "max_gap_seconds": 240},
        ],
        "weekdays": [1, 2, 3, 4, 5],
        "time_start": "17:00",
        "time_end": "19:00",
    }
    proposal.update(overrides)
    return json.dumps([proposal])


def test_valid_proposals_become_inert_candidates():
    proposals = parse_new(_proposal_json())
    assert len(proposals) == 1
    behaviour = proposals[0]
    assert behaviour.status is BehaviourStatus.CANDIDATE
    assert behaviour.actions == []  # LLM never attaches actions
    assert behaviour.occurrences == 0
    assert behaviour.id.startswith(PROPOSAL_PREFIX)
    assert behaviour.steps[1].max_gap_seconds == 240
    assert behaviour.weekdays == {1, 2, 3, 4, 5}
    assert behaviour.time_start.hour == 17


def test_proposals_survive_markdown_fencing_and_prose():
    fenced = f"Here you go:\n```json\n{_proposal_json()}\n```\nHope that helps!"
    assert len(parse_new(fenced)) == 1
    prose = f"I found this pattern: {_proposal_json()} end of report."
    assert len(parse_new(prose)) == 1


def test_wrapped_object_and_empty_replies():
    wrapped = json.dumps({"behaviours": json.loads(_proposal_json())})
    assert len(parse_new(wrapped)) == 1
    assert parse_new("Sorry, no patterns found.") == []
    assert parse_new("") == []
    assert parse_new("[oops") == []


def test_malformed_proposals_are_dropped_individually():
    reply = json.dumps(
        [
            {"name": "One step only", "steps": [{"entity_id": OFFICE, "to_state": "off"}]},
            {
                "name": "No entity",
                "steps": [
                    {"entity_id": "office", "to_state": "off"},
                    {"entity_id": KITCHEN, "to_state": "on"},
                ],
            },
            {"name": "Not a dict"},
            json.loads(_proposal_json())[0],
        ]
    )
    proposals = parse_new(reply)
    assert [b.name for b in proposals] == ["Evening handoff"]


def test_actions_from_llm_are_ignored_and_bad_fields_defaulted():
    reply = json.dumps(
        [
            {
                "name": "Tempting automation",
                "steps": [
                    {"entity_id": OFFICE, "to_state": "off"},
                    {"entity_id": KITCHEN, "to_state": "on", "max_gap_seconds": "huge"},
                ],
                "weekdays": [1, 9, "x", None],
                "time_start": "17:00",  # no time_end -> window dropped entirely
                "actions": [{"domain": "switch", "service": "turn_off"}],
            }
        ]
    )
    behaviour = parse_new(reply)[0]
    assert behaviour.actions == []
    assert behaviour.steps[1].max_gap_seconds == 300  # invalid gap -> default
    assert behaviour.weekdays == {1}
    assert behaviour.time_start is None and behaviour.time_end is None


def test_duplicates_of_existing_behaviours_are_skipped():
    existing = end_of_work_day()
    same = json.dumps(
        [
            {
                "name": "Slightly different name",
                "steps": [
                    {"entity_id": OFFICE, "to_state": "off"},
                    {"entity_id": KITCHEN, "to_state": "on", "max_gap_seconds": 300},
                ],
                "weekdays": [1, 2, 3, 4, 5],
                "time_start": "16:00",
                "time_end": "19:00",
            }
        ]
    )
    assert parse_new(same, [existing]) == []


def test_ids_are_deterministic_and_reruns_are_idempotent():
    first = parse_new(_proposal_json())
    again = parse_new(_proposal_json(), first)
    second_run = parse_new(_proposal_json())
    assert again == []
    assert first[0].id == second_run[0].id


def test_proposal_cap_is_enforced():
    reply = json.dumps(
        [
            {
                "name": f"Pattern {i}",
                "steps": [
                    {"entity_id": f"sensor.a{i}", "to_state": "on"},
                    {"entity_id": f"sensor.b{i}", "to_state": "off"},
                ],
            }
            for i in range(MAX_PROPOSALS_PER_RUN + 3)
        ]
    )
    assert len(parse_new(reply)) == MAX_PROPOSALS_PER_RUN


def test_signature_is_stable_and_action_free():
    proposals = parse_new(_proposal_json())
    assert behaviour_signature(proposals[0]) == behaviour_signature(parse_new(_proposal_json())[0])


# -- refinement protocol -------------------------------------------------------


def _candidate() -> Behaviour:
    behaviour = end_of_work_day(status=BehaviourStatus.CANDIDATE)
    behaviour.occurrences = 3
    behaviour.actions = []
    return behaviour


def _update_reply(candidate_id: str, **overrides) -> str:
    update = {
        "id": candidate_id,
        "name": "Work's done",
        "description": "Refined: consistently 17:30, gaps under 3 minutes.",
        "steps": [
            {"entity_id": OFFICE, "to_state": "off"},
            {"entity_id": KITCHEN, "to_state": "on", "max_gap_seconds": 180},
        ],
        "weekdays": [1, 2, 3, 4, 5],
        "time_start": "17:00",
        "time_end": "18:30",
    }
    update.update(overrides)
    return json.dumps({"updates": [update], "new": []})


def test_refinement_updates_candidate_definition():
    candidate = _candidate()
    result = parse_analysis_reply(_update_reply(candidate.id), [candidate])

    assert result.refined == [candidate]  # the engine's own object, in place
    assert candidate.steps[1].max_gap_seconds == 180
    assert candidate.time_start == time(17, 0)
    assert candidate.time_end == time(18, 30)
    assert candidate.name == "Work's done"
    assert "Refined" in candidate.description
    # User-owned fields survive untouched.
    assert candidate.status is BehaviourStatus.CANDIDATE
    assert candidate.occurrences == 3
    assert candidate.cooldown_seconds == 3600


def test_confirmed_and_dismissed_are_hard_fixed():
    confirmed = end_of_work_day()
    dismissed = end_of_work_day(status=BehaviourStatus.DISMISSED)
    dismissed.id = "dismissed_one"

    reply = json.dumps(
        {
            "updates": [
                json.loads(_update_reply(confirmed.id))["updates"][0],
                json.loads(_update_reply(dismissed.id))["updates"][0],
            ],
            "new": [],
        }
    )
    result = parse_analysis_reply(reply, [confirmed, dismissed])

    assert result.refined == []
    assert confirmed.steps[1].max_gap_seconds == 300
    assert confirmed.name == "End of work day"
    assert dismissed.steps[1].max_gap_seconds == 300


def test_update_with_unknown_id_is_dropped():
    result = parse_analysis_reply(_update_reply("nope"), [_candidate()])
    assert result.refined == []


def test_update_collision_with_other_behaviour_is_dropped():
    candidate = _candidate()
    other = Behaviour(
        id="other",
        name="Other",
        steps=[
            BehaviourStep(OFFICE, "off"),
            BehaviourStep(KITCHEN, "on", max_gap_seconds=180),
        ],
        weekdays={1, 2, 3, 4, 5},
        time_start=time(17, 0),
        time_end=time(18, 30),
    )
    result = parse_analysis_reply(_update_reply(candidate.id), [candidate, other])
    assert result.refined == []
    assert candidate.steps[1].max_gap_seconds == 300  # left alone


def test_user_named_candidate_keeps_its_name_through_refinement():
    candidate = _candidate()
    candidate.name = "Dad clocks off"
    candidate.custom_name = True
    parse_analysis_reply(_update_reply(candidate.id), [candidate])
    assert candidate.name == "Dad clocks off"
    assert candidate.steps[1].max_gap_seconds == 180  # definition still refined


def test_mixed_reply_refines_and_adds():
    candidate = _candidate()
    new_pattern = {
        "name": "Morning coffee",
        "steps": [
            {"entity_id": "switch.coffee_machine", "to_state": "on"},
            {"entity_id": "binary_sensor.kitchen_presence", "to_state": "on"},
        ],
    }
    reply = json.dumps(
        {
            "updates": [json.loads(_update_reply(candidate.id))["updates"][0]],
            "new": [new_pattern],
        }
    )
    result = parse_analysis_reply(reply, [candidate])
    assert result.refined == [candidate]
    assert [b.name for b in result.new] == ["Morning coffee"]
    assert result.new[0].status is BehaviourStatus.CANDIDATE


def test_legacy_plain_array_reply_is_treated_as_new_only():
    candidate = _candidate()
    result = parse_analysis_reply(_proposal_json(), [candidate])
    assert result.refined == []
    assert [b.name for b in result.new] == ["Evening handoff"]


def test_refined_new_proposals_still_dedupe_across_runs():
    result = parse_analysis_reply(json.dumps({"new": json.loads(_proposal_json())}), [])
    assert len(result.new) == 1
    again = parse_analysis_reply(json.dumps({"new": json.loads(_proposal_json())}), result.new)
    assert again.new == []


def test_unparseable_and_empty_replies_yield_nothing():
    for babble in ["", "no json here", "[oops", json.dumps({"updates": "nope"})]:
        result = parse_analysis_reply(babble, [_candidate()])
        assert result.refined == []
        assert result.new == []


def test_custom_prompt_template_is_used():
    template = "HISTORY: {summary}\nKNOWN: {known}\ncap={cap}."
    prompt = build_prompt("office off", [], template)
    assert prompt == f"HISTORY: office off\nKNOWN: []\ncap={MAX_PROPOSALS_PER_RUN}."


def test_blank_template_falls_back_to_builtin():
    assert build_prompt("s", [], None) == build_prompt("s", [])
    assert build_prompt("s", [], "") == build_prompt("s", [])
