"""LLM analyser helpers (Milestone 5 + refinement): summaries, prompts, parsing.

Pure Python — no Home Assistant imports (guarded by tests/test_purity.py).
The HA plumbing (recorder reads, conversation-agent call, scheduling) lives
in analysis.py. This module is deterministic and fully unit-testable, which
is where the guardrails matter: the LLM's output only becomes a Behaviour if
it survives validation, and proposals are always CANDIDATEs with no actions
attached — actions are the user's to define.

Refinement protocol: candidates are *refined* run after run (tighter windows,
better steps and descriptions) until the user accepts them. Confirmed and
dismissed behaviours are hard-fixed: the LLM may never update them, and any
"update" naming such an id is dropped.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, time
from typing import Any

from .models import Behaviour, BehaviourStatus, BehaviourStep

DEFAULT_LOOKBACK_DAYS = 7
MAX_EVENTS_PER_ENTITY = 60
# Hard ceiling on the summary text (~70k tokens). Oversized prompts are what
# silently killed real-world runs: a whole-domain week was 225k tokens, which
# local LLM servers reject or starve of output budget. When the digest at the
# full per-entity cap exceeds this, the cap is halved until it fits.
SUMMARY_CHAR_BUDGET = 100_000


def _reply_item_schema(*, with_id: bool) -> dict[str, Any]:
    """JSON Schema for one "updates" or "new" element of the LLM's reply.

    This documents the reply contract the prompt asks for and the parser
    enforces. It is written with only keywords probatio's
    ``from_json_schema`` honours, so it can be handed to HA's ai_task
    structured output once Home Assistant's merged LLM custom serializer
    stops forwarding probatio's UNSUPPORTED sentinel into ``to_openapi``
    (HA 2026.11 dies with "'_Unsupported' object is not subscriptable").
    Descriptions are descriptive only — they help models that render the
    schema.
    """
    properties: dict[str, Any] = {}
    if with_id:
        properties["id"] = {
            "type": "string",
            "description": "the existing candidate's id, exactly as given",
        }
    properties.update(
        {
            "name": {"type": "string", "minLength": 1, "description": "short human name"},
            "description": {
                "type": "string",
                "description": "one sentence of what the person seems to be doing",
            },
            "steps": {
                "type": "array",
                "minItems": 2,
                "items": {
                    "type": "object",
                    "properties": {
                        "entity_id": {
                            "type": "string",
                            "minLength": 3,
                            "description": "entity id from the history, e.g. light.porch_light",
                        },
                        "to_state": {
                            "type": "string",
                            "minLength": 1,
                            "description": "the state the entity changes to",
                        },
                        "max_gap_seconds": {
                            "type": "integer",
                            "minimum": 5,
                            "maximum": 86400,
                            "description": "typical maximum gap after the previous step",
                        },
                    },
                    "required": ["entity_id", "to_state"],
                },
            },
            "weekdays": {
                "type": "array",
                "items": {"type": "integer", "minimum": 1, "maximum": 7},
                "description": (
                    "ISO weekday numbers (1=Mon..7=Sun) if the pattern only occurs on some days"
                ),
            },
            "time_start": {"type": "string", "description": "HH:MM UTC window start"},
            "time_end": {"type": "string", "description": "HH:MM UTC window end"},
        }
    )
    required = (["id"] if with_id else []) + ["name", "description", "steps"]
    return {"type": "object", "properties": properties, "required": required}


ANALYSIS_REPLY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "updates": {"type": "array", "items": _reply_item_schema(with_id=True)},
        "new": {"type": "array", "items": _reply_item_schema(with_id=False)},
    },
    "required": ["updates", "new"],
}
MAX_PROPOSALS_PER_RUN = 5
MAX_UPDATES_PER_RUN = 5
PROPOSAL_PREFIX = "llm-"

PROMPT_TEMPLATE = """You are a curious household-behaviour analyst. Below is a week of smart-home \
state changes. Your job is to spot the human routines behind the data — not the \
automations.

History of entity state changes (timestamps are UTC, one line per entity, days in \
order):
{summary}

Entities named event.* are event entities — buttons, remotes, doorbells: what is \
shown is the type of each firing, so "toggle@17:30" means the user pressed that \
button at 17:30. Presses are deliberate human actions and among the strongest \
routine markers there are (a bedside button at 22:00 every weeknight); the same \
type repeating is real activity, never refresh spam. A press followed within \
seconds by device changes is the user teaching by hand — propose the whole \
sequence with the press as the first step, using the event type as its to_state.

Current behaviours (JSON; "status" is the user's decision, not yours):
{known}

Reply with ONLY a JSON object (no prose, no code fences):
{{"updates": [...], "new": [...]}}

"updates" — refine existing CANDIDATE behaviours whose definition the recent
history supports (tighten or widen time windows and gaps, correct steps,
sharpen name/description). Each element must have:
  "id": the existing candidate's id (exactly as given),
  plus the FULL revised definition: "name", "description", "steps",
  and optionally "weekdays", "time_start"/"time_end".
"new" — genuinely new recurring patterns not covered by any current behaviour.
Each element must have:
  "name": short human name,
  "description": one sentence of what the PERSON seems to be doing,
  "steps": [{{"entity_id": "...", "to_state": "...", "max_gap_seconds": 300}}, ...],
and optionally:
  "weekdays": ISO weekday numbers (1=Mon..7=Sun) if the pattern only occurs on some days,
  "time_start"/"time_end": "HH:MM" UTC window in which the first step usually occurs.

Think like a detective studying this household:
- Rhythms that repeat across days at similar times: waking, starting work, finishing
  work, meals, TV or cinema sessions, bedtime. A change that recurs on 3+ days
  within a roughly ±45-minute window is a routine worth proposing.
- Room-to-room movement: audio following a person between speakers, lights trailing
  them through the house, a work room going quiet as an evening room wakes up.
- Sequences with human meaning, where steps tend to follow each other: work-session
  markers ending, the same wind-down chain every night, morning getting-up clusters.
- Weekday/weekend differences: later weekend wake times, different evening shapes —
  use "weekdays" to say which days a pattern belongs to.
- Steps don't have to be seconds apart. Set max_gap_seconds to the real rhythm
  (e.g. 3600 for a slow wind-down over an hour); 300 is only the default.

Ignore, because they are automations or noise rather than behaviour:
- Sensor→light echoes (motion or presence switching lights within seconds) — the
  house already automates those.
- Entities that never change state, or repeat one state on a timer (e.g. a
  thermostat reporting "auto" every 30 minutes, a floodlight on motion).
- One-offs: a pattern must have recurred on multiple days before you propose it.

The current behaviours list is what the household ALREADY knows — do not treat
it as a hint of what to find, and do not anchor on it. Dismissed patterns are
dead ends the user rejected: never restate one, not even with small variations
(shifted times, extra or dropped steps). Look for what is NOT in that list:
other rooms, other times of day, routines nobody has named yet.

Be creative but honest: prefer a plausible, well-argued proposal over silence —
the user confirms or dismisses each candidate, so an interesting guess is valuable
and an empty reply is not. Describe what the person is doing (e.g. "ends the work
day in the garden office around 18:00: office audio stops, front-room lights come
on shortly after"), not just which entities changed.

Rules:
- You may only update ids whose status is "candidate". NEVER put a confirmed
  or dismissed id in "updates" — those are hard-fixed by the user. If a
  confirmed pattern has a genuinely different variant, propose it in "new".
- Never re-propose or restate dismissed patterns, in whole or in variation.
- A pattern needs 2 or more steps in a stable order; only use entities in the history.
- At most {cap} updates and {cap} new patterns; return empty lists only when the
  history genuinely shows no recurring multi-day routine.
"""


# -- history summarisation ---------------------------------------------------


def summarise_history(
    events_by_entity: dict[str, list[tuple[datetime, str]]],
) -> str:
    """Compact per-entity, per-day digest of state changes for the prompt.

    Input: entity_id -> list of (timestamp, state) in chronological order.
    Output lines look like:
      binary_sensor.office_presence: Mon 2026-11-23 off@17:30, on@08:05 | ...
    Each entity's digest is capped at the per-entity event cap (most recent
    kept). If the full digest exceeds SUMMARY_CHAR_BUDGET the cap is halved
    until it fits, so a longer analysis window or more observed domains can
    never produce a prompt the agent will choke on.

    Entities that never left one state carry little pattern information, so
    their digest is compressed to the last report per day (the daily timing
    is the only signal they hold). A constant state repeated past the
    per-entity cap is pure refresh spam (a real week had one entity
    reporting 'off' 5,700 times) and is dropped outright. A handful of
    same-state events at distinct times is still a rhythm marker and is
    kept.
    """

    def build(cap: int) -> list[str]:
        out: list[str] = []
        for entity_id in sorted(events_by_entity):
            all_events = events_by_entity[entity_id]
            # Every firing of an event entity (button, remote) is a discrete
            # human action: "toggle" repeated five times is five presses, not
            # one state refreshing on a timer. Never compress or drop them.
            constant = len({state for _, state in all_events}) <= 1 and not entity_id.startswith(
                "event."
            )
            if constant and len(all_events) > MAX_EVENTS_PER_ENTITY:
                continue
            if constant:
                latest_by_day: dict[str, tuple[datetime, str]] = {}
                for moment, state in all_events:
                    latest_by_day[moment.astimezone(UTC).strftime("%a %Y-%m-%d")] = (moment, state)
                all_events = [latest_by_day[day] for day in sorted(latest_by_day)]
            events = all_events[-cap:]
            truncated = len(all_events) - len(events)
            by_day: dict[str, list[str]] = {}
            for moment, state in events:
                local = moment.astimezone(UTC)
                by_day.setdefault(local.strftime("%a %Y-%m-%d"), []).append(
                    f"{state}@{local.strftime('%H:%M')}"
                )
            day_parts = [f"{day} {', '.join(states)}" for day, states in sorted(by_day.items())]
            if truncated > 0:
                day_parts.insert(0, f"(+{truncated} earlier events)")
            out.append(f"{entity_id}: " + " | ".join(day_parts))
        return out

    cap = MAX_EVENTS_PER_ENTITY
    lines = build(cap)
    while len("\n".join(lines)) > SUMMARY_CHAR_BUDGET and cap > 10:
        cap = max(10, cap // 2)
        lines = build(cap)
    return "\n".join(lines)


# -- prompting -----------------------------------------------------------------


def _render_existing(existing: list[Behaviour]) -> str:
    """Current behaviours as JSON the LLM can reference and refine."""
    if not existing:
        return "[]"
    rendered = [
        {
            "id": b.id,
            "name": b.name,
            "description": b.description,
            "status": b.status.value,
            "steps": [s.to_dict() for s in b.steps],
            "weekdays": sorted(b.weekdays),
            "time_start": b.time_start.isoformat() if b.time_start else None,
            "time_end": b.time_end.isoformat() if b.time_end else None,
            "occurrences": b.occurrences,
        }
        for b in existing
    ]
    return json.dumps(rendered, indent=1)


def build_prompt(summary: str, existing: list[Behaviour], template: str | None = None) -> str:
    """Prompt for the conversation agent: refine candidates, propose new ones.

    ``template`` (from the integration options) replaces the built-in
    wording; it must contain ``{summary}`` and may use ``{known}`` and
    ``{cap}``. Safety does not depend on it: the reply parser enforces the
    candidate-only protocol no matter what the prompt asked for.
    """
    return (template or PROMPT_TEMPLATE).format(
        summary=summary, known=_render_existing(existing), cap=MAX_PROPOSALS_PER_RUN
    )


# -- reply parsing ------------------------------------------------------------


@dataclass(slots=True)
class AnalysisResult:
    """Validated LLM reply: candidates updated in place, plus brand-new ones.

    ``refined`` holds the *existing* Behaviour objects that were updated (the
    engine already owns them); ``new`` holds fresh candidates to add.
    ``dropped`` holds ``(name, reason)`` for proposals the guardrails rejected
    — the analysis log shows them, because a run that silently eats everything
    the model proposed is indistinguishable from a model that proposed
    nothing (which is exactly how weeks of "no new convincing patterns"
    hid a working LLM).
    """

    refined: list[Behaviour] = field(default_factory=list)
    new: list[Behaviour] = field(default_factory=list)
    dropped: list[tuple[str, str]] = field(default_factory=list)


def _apply_update(target: Behaviour, revised: Behaviour) -> None:
    """Copy the refined definition onto an existing candidate.

    The target keeps its identity and everything the user owns: id, status,
    actions, occurrences, confidence, cooldown — and the name once the user
    has renamed it.
    """
    target.steps = revised.steps
    target.weekdays = revised.weekdays
    target.time_start = revised.time_start
    target.time_end = revised.time_end
    if not target.custom_name and revised.name:
        target.name = revised.name
    if revised.description:
        target.description = revised.description


def parse_analysis_reply(text: str, existing: list[Behaviour] | None = None) -> AnalysisResult:
    """Turn an LLM reply into refined candidates and validated new ones.

    Guardrails: updates may only touch CANDIDATE behaviours (confirmed and
    dismissed are hard-fixed), refined definitions may not collide with any
    other behaviour's signature, and new proposals are validated exactly like
    before. A legacy plain-array reply is treated as new-only. Malformed
    replies yield an empty result.
    """
    payload = extract_reply_json(text)
    if isinstance(payload, list):
        payload = {"new": payload}
    if isinstance(payload, dict) and "new" not in payload and "updates" not in payload:
        legacy = payload.get("behaviours")
        if isinstance(legacy, list):
            payload = {"new": legacy}
    if not isinstance(payload, dict):
        return AnalysisResult()

    existing = existing or []
    by_id = {b.id: b for b in existing}
    seen = {behaviour_signature(b) for b in existing}

    refined: list[Behaviour] = []
    for item in payload.get("updates") or []:
        if not isinstance(item, dict):
            continue
        target = by_id.get(str(item.get("id", "")))
        if target is None or target.status is not BehaviourStatus.CANDIDATE:
            continue
        revised = _to_behaviour(item)
        if revised is None:
            continue
        revised_sig = behaviour_signature(revised)
        if revised_sig != behaviour_signature(target) and revised_sig in seen:
            continue  # the refinement would collide with another behaviour
        seen.add(revised_sig)
        _apply_update(target, revised)
        refined.append(target)
        if len(refined) >= MAX_UPDATES_PER_RUN:
            break

    proposals: list[Behaviour] = []
    dropped: list[tuple[str, str]] = []
    dismissed_steps = {
        tuple((s.entity_id, s.to_state) for s in b.steps): b.name
        for b in existing
        if b.status is BehaviourStatus.DISMISSED
    }
    for item in payload.get("new") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip() or "<unnamed>"
        steps_raw = item.get("steps")
        if not isinstance(steps_raw, list) or len(steps_raw) < 2:
            dropped.append((name, "needs 2 or more steps in a stable order"))
            continue
        behaviour = _to_behaviour(item)
        if behaviour is None:
            dropped.append((name, "did not validate (bad entity ids, states or fields)"))
            continue
        step_key = tuple((s.entity_id, s.to_state) for s in behaviour.steps)
        if step_key in dismissed_steps:
            # The signature check only catches exact copies; the model happily
            # "restates" a dismissed pattern with shifted times or weekdays.
            dropped.append((name, f"restates the dismissed '{dismissed_steps[step_key]}'"))
            continue
        signature = behaviour.id.removeprefix(PROPOSAL_PREFIX)
        if signature in seen:
            dropped.append((name, "duplicates another behaviour in this household"))
            continue
        seen.add(signature)
        proposals.append(behaviour)
        if len(proposals) >= MAX_PROPOSALS_PER_RUN:
            break
    return AnalysisResult(refined=refined, new=proposals, dropped=dropped)


# -- proposal validation ---------------------------------------------------------


def _signature(steps: list[BehaviourStep], weekdays: set[int], time_start, time_end) -> str:
    parts = (
        ";".join(f"{s.entity_id}={s.to_state}" for s in steps),
        ",".join(str(d) for d in sorted(weekdays)),
        time_start.isoformat() if time_start else "",
        time_end.isoformat() if time_end else "",
    )
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:8]


def behaviour_signature(behaviour: Behaviour) -> str:
    return _signature(behaviour.steps, behaviour.weekdays, behaviour.time_start, behaviour.time_end)


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
        steps.append(BehaviourStep(entity_id, to_state, max(5, min(gap, 24 * 3600))))

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
        # (confirm via phil9000.confirm_behaviour, actions via the panel/YAML).
        actions=[],
        weekdays=weekdays,
        time_start=time_start,
        time_end=time_end,
        status=BehaviourStatus.CANDIDATE,
    )


def _bracketed(text: str, opener: str) -> str | None:
    """The widest span from the first opener to the last matching closer."""
    closer = "]" if opener == "[" else "}"
    start, end = text.find(opener), text.rfind(closer)
    if start == -1 or end <= start:
        return None
    return text[start : end + 1]


def extract_reply_json(text: str):
    """Best-effort extraction of the reply JSON (object form or legacy array).

    Tries the whole text, then a fenced block, and inside each the array
    span before the object span (so a legacy ``[{...}]`` reply surrounded by
    prose is not mistaken for a single object). Returns None when the reply
    contains no JSON at all — the analysis pass treats that as an agent
    failure rather than "nothing found" (a chatty or error reply means the
    model or max-tokens settings are wrong, and that must be visible).
    """
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    candidates = [text.strip()]
    if fenced:
        candidates.append(fenced.group(1).strip())
    for candidate in candidates:
        for blob in (candidate, _bracketed(candidate, "["), _bracketed(candidate, "{")):
            if not blob:
                continue
            try:
                return json.loads(blob)
            except (ValueError, TypeError):
                continue
    return None
