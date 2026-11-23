"""Activity feed helpers: merge state-change rows into a newest-first feed.

Pure module — must not import Home Assistant (guarded by tests/test_purity.py).

A feed row is a plain dict::

    {"entity_id": ..., "at": iso-utc-timestamp,
     "old_state": str | None, "new_state": str}

Rows come from Home Assistant's recorder (per entity, any order) and from the
live event subscription (chronological). The recorder stores a row for *every*
state_changed event, including attribute-only changes; the adapter marks those
with ``"state_changed": False`` and they are dropped here, as are consecutive
rows that leave an entity in the same state (no-ops the feed should not show).

Timestamps must be aware UTC isoformat strings; they sort lexicographically
("+00:00" is less than any fractional-second or later digit).
"""

from __future__ import annotations

from typing import Any

# Rows kept in the feed: the panel box shows ~25 rows visibly and scrolls,
# but keeps a full recent history so re-opening the panel or scrolling back
# still has context. ~1000 rows ≈ 150 KB of JSON over the websocket — cheap
# on anything that runs Home Assistant.
DEFAULT_LIMIT = 1000

# Transitions *involving* these states are not household activity: every HA
# restart (and every device reconnect) marks restored entities unavailable /
# unknown and then pushes their first real state, so the recorder fills with
# "unavailable -> on" walls — and, when the old instance tears down, with
# "on -> unavailable" ones. The user's feed is household behaviour, not
# device bookkeeping: HA surfaces device errors elsewhere, and behaviour
# detection still sees every state change (only the feed filters).
RECOVERY_STATES = frozenset({"unavailable", "unknown"})


def is_feed_noise(row: dict[str, Any]) -> bool:
    """True for rows the feed must not show.

    Either end unavailable/unknown (device bookkeeping), or no previous
    state at all ("— -> idle"): the first entry for an entity in the
    window proves nothing changed, only that we started watching it.
    """
    return (
        row.get("old_state") in RECOVERY_STATES
        or row.get("new_state") in RECOVERY_STATES
        or row.get("old_state") is None
    )


def dedupe_activity(rows: list[dict[str, Any]], limit: int = DEFAULT_LIMIT) -> list[dict[str, Any]]:
    """Merge activity rows: drop non-changes, noise and no-ops, cap."""
    by_entity: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if not row.get("at") or row.get("state_changed") is False:
            continue
        if is_feed_noise(row):
            continue
        by_entity.setdefault(row["entity_id"], []).append(row)

    survivors: list[dict[str, Any]] = []
    for entity_rows in by_entity.values():
        entity_rows.sort(key=lambda r: r["at"])
        previous_new: str | None = None
        for row in entity_rows:
            if previous_new is not None and row.get("new_state") == previous_new:
                continue  # no-op row: entity ended in the same state
            previous_new = row.get("new_state")
            survivors.append(row)

    survivors.sort(key=lambda r: r["at"], reverse=True)
    return survivors[:limit]
