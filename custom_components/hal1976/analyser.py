"""LLM analyser helpers (Milestone 5): summaries, prompts, proposal parsing.

Pure Python — no Home Assistant imports (guarded by tests/test_purity.py).
The HA plumbing (recorder reads, conversation-agent call, scheduling) lives
in analysis.py. This module is deterministic and fully unit-testable, which
is where the guardrails matter: the LLM's output only becomes a Behaviour if
it survives parse_proposals() validation, and proposals are always CANDIDATEs
with no actions attached — actions are the user's to define.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, time

from .models import Behaviour, BehaviourStatus, BehaviourStep

DEFAULT_LOOKBACK_DAYS = 7
MAX_EVENTS_PER_ENTITY = 240
MAX_PROPOSALS_PER_RUN = 5
PROPOSAL_PREFIX = "llm-"

PROMPT_TEMPLATE = """You analyse smart-home sensor history to find recurring household behaviour \
patterns.

History of entity state changes (timestamps are UTC):
{summary}

Known behaviours — do not re-propose any of these, including dismissed ones:
{known}

Reply with ONLY a JSON array (no prose, no code fences). Each element must have:
  "name": short human name,
  "description": one sentence of what you noticed,
  "steps": [{{"entity_id": "...", "to_state": "...", "max_gap_seconds": 300}}, ...],
and optionally:
  "weekdays": ISO weekday numbers (1=Mon..7=Sun) if the pattern only occurs on some days,
  "time_start"/"time_end": "HH:MM" UTC window in which the first step usually occurs.

Rules:
- A pattern needs 2 or more steps in a stable order; only use entities in the history.
- max_gap_seconds is the typical maximum gap between consecutive steps (default 300).
- Propose at most {cap} convincing patterns; if nothing is convincing, return [].
"""


# -- history summarisation ---------------------------------------------------


def summarise_history(
    events_by_entity: dict[str, list[tuple[datetime, str]]],
) -> str:
    """Compact per-entity, per-day digest of state changes for the prompt.

    Input: entity_id -> list of (timestamp, state) in chronological order.
    Output lines look like:
      binary_sensor.office_presence: Mon 2026-11-23 off@17:30, on@08:05 | ...
    Each entity's digest is capped at MAX_EVENTS_PER_ENTITY (most recent kept).
    """
    lines: list[str] = []
    for entity_id in sorted(events_by_entity):
        events = events_by_entity[entity_id][-MAX_EVENTS_PER_ENTITY:]
        truncated = len(events_by_entity[entity_id]) - len(events)
        by_day: dict[str, list[str]] = {}
        for moment, state in events:
            local = moment.astimezone(UTC)
            by_day.setdefault(local.strftime("%a %Y-%m-%d"), []).append(
                f"{state}@{local.strftime('%H:%M')}"
            )
        day_parts = [f"{day} {', '.join(states)}" for day, states in sorted(by_day.items())]
        if truncated > 0:
            day_parts.insert(0, f"(+{truncated} earlier events)")
        lines.append(f"{entity_id}: " + " | ".join(day_parts))
    return "\n".join(lines)


# -- prompting -----------------------------------------------------------------


def build_prompt(summary: str, existing: list[Behaviour]) -> str:
    """Prompt for the conversation agent, steering it away from known patterns."""
    if existing:
        known = "\n".join(
            f"- {b.name} [{b.status.value}]: {b.description or 'no description'}"
            for b in existing
        )
    else:
        known = "- none"
    return PROMPT_TEMPLATE.format(summary=summary, known=known, cap=MAX_PROPOSALS_PER_RUN)


# -- proposal validation ---------------------------------------------------------


def _signature(
    steps: list[BehaviourStep], weekdays: set[int], time_start, time_end
) -> str:
    parts = (
        ";".join(f"{s.entity_id}={s.to_state}" for s in steps),
        ",".join(str(d) for d in sorted(weekdays)),
        time_start.isoformat() if time_start else "",
        time_end.isoformat() if time_end else "",
    )
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:8]


def behaviour_signature(behaviour: Behaviour) -> str:
    return _signature(
        behaviour.steps, behaviour.weekdays, behaviour.time_start, behaviour.time_end
    )


def _parse_hhmm(value: object) -> time | None:
    if not isinstance(value, str):
        return None
    try:
        return time.fromisoformat(value.strip())
    except ValueError:
        return None


def _to_behaviour(item: object) -> Behaviour | None:
    """Validate one proposed pattern; return None (drop it) if malformed."""
    if not isinstance(item, dict):
        return None
    name = str(item.get("name", "")).strip()
    steps_raw = item.get("steps")
    if not name or not isinstance(steps_raw, list) or len(steps_raw) < 2:
        return None

    steps: list[BehaviourStep] = []
    for step_raw in steps_raw:
        if not isinstance(step_raw, dict):
            return None
        entity_id = str(step_raw.get("entity_id", "")).strip().lower()
        to_state = step_raw.get("to_state")
        if "." not in entity_id or not isinstance(to_state, str) or not to_state:
            return None
        try:
            gap = int(step_raw.get("max_gap_seconds", 300))
        except (TypeError, ValueError):
            gap = 300
        steps.append(
            BehaviourStep(entity_id, to_state, max(5, min(gap, 24 * 3600)))
        )

    weekdays: set[int] = set()
    raw_days = item.get("weekdays")
    if isinstance(raw_days, list):
        for day in raw_days:
            try:
                number = int(day)
            except (TypeError, ValueError):
                continue
            if 1 <= number <= 7:
                weekdays.add(number)

    # Both time bounds or neither, so the engine's window stays coherent.
    time_start = _parse_hhmm(item.get("time_start"))
    time_end = _parse_hhmm(item.get("time_end"))
    if time_start is None or time_end is None:
        time_start = time_end = None

    signature = _signature(steps, weekdays, time_start, time_end)
    return Behaviour(
        id=f"{PROPOSAL_PREFIX}{signature}",
        name=name[:120],
        description=str(item.get("description", "")).strip()[:500],
        steps=steps,
        # LLM proposals never carry actions: acting is the user's decision
        # (confirm via hal1976.confirm_behaviour, actions via the panel/YAML).
        actions=[],
        weekdays=weekdays,
        time_start=time_start,
        time_end=time_end,
        status=BehaviourStatus.CANDIDATE,
    )


def _extract_json(text: str):
    """Best-effort extraction of the proposals JSON from an LLM reply."""
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        start, end = text.find("["), text.rfind("]")
        if start == -1 or end <= start:
            return None
        candidate = text[start : end + 1]
    try:
        return json.loads(candidate)
    except (ValueError, TypeError):
        return None


def parse_proposals(
    text: str, existing: list[Behaviour] | None = None
) -> list[Behaviour]:
    """Turn an LLM reply into validated candidate behaviours.

    Invalid replies yield []; individual malformed proposals are dropped.
    Proposals duplicating an existing behaviour (same steps + schedule) are
    skipped, as are duplicates within the reply. Deterministic ids
    (llm-<hash>) make repeated analyser runs idempotent.
    """
    payload = _extract_json(text)
    if isinstance(payload, dict):
        payload = payload.get("behaviours")
    if not isinstance(payload, list):
        return []

    seen = {behaviour_signature(b) for b in existing or []}
    seen.update(
        b.id.removeprefix(PROPOSAL_PREFIX)
        for b in existing or []
        if b.id.startswith(PROPOSAL_PREFIX)
    )
    proposals: list[Behaviour] = []
    for item in payload:
        behaviour = _to_behaviour(item)
        if behaviour is None:
            continue
        signature = behaviour.id.removeprefix(PROPOSAL_PREFIX)
        if signature in seen:
            continue
        seen.add(signature)
        proposals.append(behaviour)
        if len(proposals) >= MAX_PROPOSALS_PER_RUN:
            break
    return proposals
