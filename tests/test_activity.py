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


def test_startup_recoveries_are_dropped_but_offline_events_stay():
    # The wall of rows every HA restart produces: unavailable/unknown -> real
    # state. Coming back online is not household activity; going offline is.
    rows = [
        row(OFFICE, "2026-11-22T07:00:00+00:00", "on", "unavailable"),  # startup
        row(OFFICE, "2026-11-22T07:00:05+00:00", "off", "unknown"),  # startup
        row(OFFICE, "2026-11-22T17:30:00+00:00", "off", "on"),  # real change
        row(OFFICE, "2026-11-22T18:00:00+00:00", "unavailable", "on"),  # offline!
    ]
    feed = dedupe_activity(rows)
    assert [(r["old_state"], r["new_state"]) for r in feed] == [
        ("on", "unavailable"),
        ("on", "off"),
    ]


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
        row(KITCHEN, "2026-11-22T17:32:00+00:00", "on"),
        row(OFFICE, "2026-11-22T17:30:00+00:00", "off", "on"),
        row(OFFICE, "2026-11-21T17:30:00+00:00", "on", "off"),
    ]
    feed = dedupe_activity(list(reversed(rows)))
    assert [r["at"] for r in feed] == [
        "2026-11-22T17:32:00+00:00",
        "2026-11-22T17:30:00+00:00",
        "2026-11-21T17:30:00+00:00",
    ]


def test_default_limit_is_1000():
    rows = [
        row("sensor.x", f"2026-11-22T{h // 60:02d}:{h % 60:02d}:00+00:00", str(i))
        for h, i in enumerate(range(DEFAULT_LIMIT + 200))
    ]
    assert len(dedupe_activity(rows)) == DEFAULT_LIMIT
