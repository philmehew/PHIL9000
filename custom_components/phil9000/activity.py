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

from collections.abc import Callable
from datetime import datetime
from typing import Any

# Rows kept in the feed: the panel box shows ~25 rows visibly and scrolls,
# but keeps a full recent history so re-opening the panel or scrolling back
# still has context. ~1000 rows ≈ 150 KB of JSON over the websocket — cheap
# on anything that runs Home Assistant. With the noise rules in place the
# cap (not the lookback window) is the usual bound.
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

    Either end unavailable/unknown (device bookkeeping), no previous state
    at all ("— -> idle": the first entry for an entity proves nothing
    changed, only that we started watching it), or the same state on both
    ends ("off -> off"): HA writes restored states at startup as fresh
    rows even when nothing changed, and the significant-changes query
    keeps those.
    """
    return (
        row.get("old_state") in RECOVERY_STATES
        or row.get("new_state") in RECOVERY_STATES
        or row.get("old_state") is None
        or row.get("old_state") == row.get("new_state")
    )


def merge_event_history(
    state_rows: list[dict[str, Any]],
    event_rows: list[dict[str, Any]],
    limit: int = DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    """Merge recorder rows with PHIL9000's own event ring (newest first, capped).

    An event entity writes a recorder row only when the *type* of its last
    firing changes — repeat presses never reach the states table, while a
    type change appears in both sources. For entities the ring covers, the
    ring is therefore the authoritative history: recorder rows for them are
    dropped rather than risking doubled or thinned presses.
    """
    covered = {row.get("entity_id") for row in event_rows}
    merged = [row for row in state_rows if row.get("entity_id") not in covered]
    merged.extend(event_rows)
    return dedupe_activity(merged, limit)


def ring_history(
    event_rows: list[dict[str, Any]],
    since: datetime,
    matches: Callable[[str], bool] = lambda entity_id: True,  # noqa: ARG005
) -> dict[str, list[tuple[datetime, str]]]:
    """Recent ring firings as ``entity_id -> [(timestamp, event type), ...]``.

    The same shape the recorder gives the analyser, so button and remote
    presses fold into the analysis digest exactly like state changes — the
    model gets to see "the user pressed the bedside button", which is the
    loudest human signal a smart home produces. Rows before ``since`` or for
    entities no longer observed (muted after the fact) are skipped.
    """
    history: dict[str, list[tuple[datetime, str]]] = {}
    for row in event_rows:
        entity_id = row.get("entity_id")
        event_type = row.get("new_state")
        if not isinstance(entity_id, str) or not isinstance(event_type, str):
            continue
        if not matches(entity_id):
            continue
        try:
            at = datetime.fromisoformat(str(row.get("at")))
        except ValueError:
            continue
        if at >= since:
            history.setdefault(entity_id, []).append((at, event_type))
    return history


def dedupe_activity(rows: list[dict[str, Any]], limit: int = DEFAULT_LIMIT) -> list[dict[str, Any]]:
    """Merge activity rows: drop non-changes, noise and no-ops, cap.

    Rows flagged ``"event": True`` (event-entity firings from PHIL9000's
    rolling buffer) bypass the state-transition rules: an event has no
    previous state, and two presses of the same type in a row are real,
    separate activity.
    """
    by_entity: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if not row.get("at") or row.get("state_changed") is False:
            continue
        if not row.get("event") and is_feed_noise(row):
            continue
        by_entity.setdefault(row["entity_id"], []).append(row)

    survivors: list[dict[str, Any]] = []
    for entity_rows in by_entity.values():
        entity_rows.sort(key=lambda r: r["at"])
        previous_new: str | None = None
        for row in entity_rows:
            if (
                not row.get("event")
                and previous_new is not None
                and row.get("new_state") == previous_new
            ):
                continue  # no-op row: entity ended in the same state
            previous_new = row.get("new_state")
            survivors.append(row)

    survivors.sort(key=lambda r: r["at"], reverse=True)
    return survivors[:limit]
