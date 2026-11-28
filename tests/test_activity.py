"""Pure tests for the activity feed merging rules (no Home Assistant)."""

from __future__ import annotations

from custom_components.phil9000.activity import DEFAULT_LIMIT, dedupe_activity

OFFICE = "binary_sensor.office_presence"
KITCHEN = "binary_sensor.kitchen_presence"


def row(entity: str, at: str, new: str, old: str | None = None, **extra) -> dict:
    return {"entity_id": entity, "at": at, "old_state": old, "new_state": new, **extra}


def test_newest_first_and_capped():
    rows = [
        row(OFFICE, "2026-11-22T08:00:00+00:00", "on", "off"),
        row(KITCHEN, "2026-11-22T17:32:00+00:00", "on", "off"),
        row(OFFICE, "2026-11-22T17:30:00+00:00", "off", "on"),
    ]
    feed = dedupe_activity(rows, limit=2)
    assert [(r["entity_id"], r["new_state"]) for r in feed] == [
        (KITCHEN, "on"),
        (OFFICE, "off"),
    ]


def test_attribute_only_rows_are_dropped():
    rows = [
        row(OFFICE, "2026-11-22T17:30:00+00:00", "off", "on"),
        row(OFFICE, "2026-11-22T17:31:00+00:00", "off", "off", state_changed=False),
    ]
    assert [r["at"] for r in dedupe_activity(rows)] == ["2026-11-22T17:30:00+00:00"]


def test_availability_transitions_are_dropped():
    # The walls every HA restart produces, in both directions: entities being
    # marked unavailable/unknown as the old instance tears down, and their
    # first real states afterwards. None of it is household activity.
    rows = [
        row(OFFICE, "2026-11-22T06:59:55+00:00", "unavailable", "on"),  # teardown
        row(OFFICE, "2026-11-22T07:00:00+00:00", "on", "unavailable"),  # startup
        row(OFFICE, "2026-11-22T07:00:05+00:00", "off", "unknown"),  # startup
        row(OFFICE, "2026-11-22T17:30:00+00:00", "off", "on"),  # real change
    ]
    feed = dedupe_activity(rows)
    assert [(r["old_state"], r["new_state"]) for r in feed] == [("on", "off")]


def test_consecutive_same_state_collapses():
    rows = [
        row(OFFICE, "2026-11-22T17:30:00+00:00", "off", "on"),
        row(OFFICE, "2026-11-22T17:35:00+00:00", "off", "off"),  # no-op duplicate
    ]
    feed = dedupe_activity(rows)
    assert [r["at"] for r in feed] == ["2026-11-22T17:30:00+00:00"]


def test_toggle_sequences_are_kept():
    rows = [
        row(OFFICE, "2026-11-22T17:30:00+00:00", "off", "on"),
        row(OFFICE, "2026-11-22T17:40:00+00:00", "on", "off"),
        row(OFFICE, "2026-11-22T17:50:00+00:00", "off", "on"),
    ]
    assert len(dedupe_activity(rows)) == 3


def test_rows_without_timestamp_are_dropped():
    assert dedupe_activity([row(OFFICE, "", "on")]) == []


def test_input_order_does_not_matter():
    rows = [
        row(KITCHEN, "2026-11-22T17:32:00+00:00", "on", "off"),
        row(OFFICE, "2026-11-22T17:30:00+00:00", "off", "on"),
        row(OFFICE, "2026-11-21T17:30:00+00:00", "on", "off"),
    ]
    feed = dedupe_activity(list(reversed(rows)))
    assert [r["at"] for r in feed] == [
        "2026-11-22T17:32:00+00:00",
        "2026-11-22T17:30:00+00:00",
        "2026-11-21T17:30:00+00:00",
    ]


def test_rows_without_previous_state_are_dropped():
    # "— -> idle": the first entry for an entity in the window proves
    # nothing changed, only that we started watching it (newly-added
    # AirPlay players, entities appearing after a restart, ...).
    rows = [
        row("media_player.office_airplay", "2026-11-22T08:00:00+00:00", "idle"),
        row(OFFICE, "2026-11-22T17:30:00+00:00", "off", "on"),
    ]
    assert [r["entity_id"] for r in dedupe_activity(rows)] == [OFFICE]


def test_same_state_rows_are_dropped():
    # "off -> off": HA writes restored states at startup as fresh rows even
    # when nothing changed; the significant-changes query keeps them, so the
    # backfill derives old from the previous row and shows a fake change.
    rows = [
        row(OFFICE, "2026-11-22T17:30:00+00:00", "off", "on"),
        row(OFFICE, "2026-11-22T17:35:00+00:00", "off", "off"),  # startup restore
        row(KITCHEN, "2026-11-22T17:36:00+00:00", "on", "on"),  # same, first row
    ]
    feed = dedupe_activity(rows)
    assert [(r["entity_id"], r["old_state"], r["new_state"]) for r in feed] == [
        (OFFICE, "on", "off")
    ]


def test_default_limit_is_1000():
    rows = [
        row(
            "sensor.x",
            f"2026-11-22T{h // 60:02d}:{h % 60:02d}:00+00:00",
            str(i),
            str(i - 1),
        )
        for h, i in enumerate(range(DEFAULT_LIMIT + 200))
    ]
    assert len(dedupe_activity(rows)) == DEFAULT_LIMIT


# -- event-entity firings (buttons, remotes) ---------------------------------

from custom_components.phil9000.activity import merge_event_history, ring_history  # noqa: E402

BUTTON = "event.bedside_button"


def event_row(entity: str, at: str, fired: str) -> dict:
    return row(entity, at, fired, None, event=True)


def test_event_rows_bypass_the_state_transition_rules():
    # A press has no previous state, and two presses of the same type are
    # real, separate activity — the rules that squash state no-ops must not
    # touch them.
    rows = [
        event_row(BUTTON, "2026-11-22T21:00:00+00:00", "toggle"),
        event_row(BUTTON, "2026-11-22T21:00:30+00:00", "toggle"),
    ]
    feed = dedupe_activity(rows)
    assert [r["at"] for r in feed] == [
        "2026-11-22T21:00:30+00:00",
        "2026-11-22T21:00:00+00:00",
    ]


def test_merge_event_history_replaces_recorder_rows_for_presses():
    # The recorder saw one row (type changed off→toggle); the ring saw both
    # presses. The ring wins for its entities; other entities are untouched.
    state_rows = [
        row(BUTTON, "2026-11-22T21:00:30+00:00", "toggle", "off"),
        row(OFFICE, "2026-11-22T17:30:00+00:00", "off", "on"),
    ]
    events = [
        event_row(BUTTON, "2026-11-22T21:00:00+00:00", "toggle"),
        event_row(BUTTON, "2026-11-22T21:00:30+00:00", "toggle"),
    ]
    feed = merge_event_history(state_rows, events)
    assert [(r["entity_id"], r["at"]) for r in feed] == [
        (BUTTON, "2026-11-22T21:00:30+00:00"),
        (BUTTON, "2026-11-22T21:00:00+00:00"),
        (OFFICE, "2026-11-22T17:30:00+00:00"),
    ]


def test_ring_history_filters_window_and_observations():
    from datetime import UTC, datetime

    since = datetime(2026, 11, 20, tzinfo=UTC)
    rows = [
        event_row(BUTTON, "2026-11-22T21:00:00+00:00", "toggle"),
        event_row(BUTTON, "2026-11-19T21:00:00+00:00", "toggle"),  # before `since`
        event_row("event.muted_cam", "2026-11-22T21:30:00+00:00", "motion"),  # not observed
    ]
    history = ring_history(rows, since, matches=lambda e: e == BUTTON)
    assert list(history) == [BUTTON]
    assert history[BUTTON] == [(datetime.fromisoformat("2026-11-22T21:00:00+00:00"), "toggle")]


def test_ring_history_ignores_malformed_rows():
    from datetime import UTC, datetime

    rows = [
        {"entity_id": BUTTON, "at": "not-a-date", "new_state": "toggle"},
        {"entity_id": BUTTON, "at": "2026-11-22T21:00:00+00:00", "new_state": None},
    ]
    assert ring_history(rows, datetime(2026, 11, 20, tzinfo=UTC)) == {}
