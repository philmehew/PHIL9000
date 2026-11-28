"""Recorded teaching sessions: user-demonstrated triggers and actions.

Pure Python — no Home Assistant imports (guarded by tests/test_purity.py).

The Record flow: the user arms **trigger** capture and performs (or waits for)
the events that mark a situation, then arms **action** capture and performs the
change the automation should later replay. Recorded items are reviewed and
pruned in the panel; the LLM turns the demonstration (plus the user's notes)
into a full automation proposal, which the user refines by chat and finally
creates as a CONFIRMED behaviour — the rule was taught explicitly, so it skips
the candidate phase (unlike LLM-discovered patterns from analyser.py).

This module owns the session model, the proposal prompts and their guardrails;
capturing events and calling the agent are Home Assistant concerns (see
__init__.py and analysis.py).
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, time
from typing import Any

from .analyser import extract_reply_json
from .models import Behaviour, BehaviourAction, BehaviourStatus, BehaviourStep
from .subscriptions import is_valid_entity_id

MODES = frozenset({"trigger", "action"})
# Per-mode capture caps: a chatty sensor in trigger mode must not flood the
# session (or the recording file); the oldest captures drop first.
MAX_ITEMS_PER_MODE = 100
# Refinement chat kept on the session (oldest trimmed) — enough context for
# "actually, make that weekdays only" without re-sending a running dialogue.
MAX_CONVERSATION_TURNS = 12
BEHAVIOUR_ID_PREFIX = "rec-"

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(slots=True)
class RecordedItem:
    """One captured state change, tagged to the trigger or action bucket."""

    entity_id: str
    from_state: str | None
    to_state: str
    at: datetime
    item_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "entity_id": self.entity_id,
            "from_state": self.from_state,
            "to_state": self.to_state,
            "at": self.at.astimezone(UTC).isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RecordedItem:
        return cls(
            entity_id=str(data["entity_id"]),
            from_state=data.get("from_state"),
            to_state=str(data["to_state"]),
            at=datetime.fromisoformat(str(data["at"])),
            item_id=str(data.get("item_id") or uuid.uuid4().hex[:8]),
        )


@dataclass(slots=True)
class RecordingSession:
    """The single household recording session (mode + captures + notes + proposal)."""

    mode: str | None = None  # None | "trigger" | "action"
    triggers: list[RecordedItem] = field(default_factory=list)
    actions: list[RecordedItem] = field(default_factory=list)
    notes: str = ""
    proposal: dict[str, Any] | None = None
    conversation: list[dict[str, str]] = field(default_factory=list)
    started_at: datetime | None = None

    # -- capture ----------------------------------------------------------

    def set_mode(self, mode: str | None) -> None:
        """Arm a capture mode ("trigger"/"action") or disarm with None/"off"."""
        if mode == "off":
            mode = None
        if mode is not None and mode not in MODES:
            raise ValueError(f"unknown recording mode {mode!r}")
        self.mode = mode
        if mode is not None and self.started_at is None:
            self.started_at = datetime.now(UTC)

    def capture(
        self,
        kind: str,
        entity_id: str,
        from_state: str | None,
        to_state: str,
        at: datetime,
    ) -> RecordedItem:
        """Append a captured row to its bucket (capped, oldest dropped)."""
        item = RecordedItem(entity_id=entity_id, from_state=from_state, to_state=to_state, at=at)
        bucket = self.triggers if kind == "trigger" else self.actions
        bucket.append(item)
        if len(bucket) > MAX_ITEMS_PER_MODE:
            del bucket[:-MAX_ITEMS_PER_MODE]
        return item

    def remove(self, kind: str, item_id: str) -> bool:
        """Delete one captured item; False when the id is unknown."""
        bucket = self.triggers if kind == "trigger" else self.actions
        for index, item in enumerate(bucket):
            if item.item_id == item_id:
                del bucket[index]
                return True
        return False

    def add_turn(self, role: str, text: str) -> None:
        self.conversation.append({"role": role, "text": text})
        if len(self.conversation) > MAX_CONVERSATION_TURNS:
            del self.conversation[:-MAX_CONVERSATION_TURNS]

    def clear(self) -> None:
        """Discard everything (called after the behaviour is created)."""
        self.mode = None
        self.triggers = []
        self.actions = []
        self.notes = ""
        self.proposal = None
        self.conversation = []
        self.started_at = None

    def is_empty(self) -> bool:
        """True when there is nothing to show or act on."""
        return not (
            self.mode
            or self.triggers
            or self.actions
            or self.notes.strip()
            or self.proposal
            or self.started_at
        )

    # -- serialisation ------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "triggers": [i.to_dict() for i in self.triggers],
            "actions": [i.to_dict() for i in self.actions],
            "notes": self.notes,
            "proposal": self.proposal,
            "conversation": list(self.conversation),
            "started_at": self.started_at.isoformat() if self.started_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RecordingSession:
        """Lenient load: unknown fields ignored, broken items skipped."""
        session = cls()
        mode = data.get("mode")
        if mode in MODES:
            session.mode = str(mode)
        started = data.get("started_at")
        if isinstance(started, str):
            try:
                session.started_at = datetime.fromisoformat(started)
            except ValueError:
                session.started_at = None
        session.notes = str(data.get("notes") or "")
        if isinstance(data.get("proposal"), dict):
            session.proposal = data["proposal"]
        conversation = data.get("conversation")
        if isinstance(conversation, list):
            session.conversation = [
                turn
                for turn in conversation
                if isinstance(turn, dict) and turn.get("role") in ("user", "assistant")
            ][-MAX_CONVERSATION_TURNS:]
        for key, bucket in (("triggers", session.triggers), ("actions", session.actions)):
            raw = data.get(key)
            if not isinstance(raw, list):
                continue
            for item in raw[:MAX_ITEMS_PER_MODE]:
                if not isinstance(item, dict) or not item.get("entity_id") or not item.get("at"):
                    continue
                try:
                    bucket.append(RecordedItem.from_dict(item))
                except (TypeError, ValueError):
                    continue  # skip unreadable rows rather than lose the session
        return session


# -- prompt building ---------------------------------------------------------


def _format_items(items: list[RecordedItem]) -> str:
    """One line per capture with UTC wall time and the gap since the previous."""
    if not items:
        return "(nothing recorded)"
    lines: list[str] = []
    previous: datetime | None = None
    for item in items:
        moment = item.at.astimezone(UTC)
        gap = f" (+{round((item.at - previous).total_seconds())}s)" if previous else ""
        lines.append(
            f"- {moment:%a %H:%M}{gap}: {item.entity_id} {item.from_state or '—'} -> "
            f"{item.to_state}"
        )
        previous = item.at
    return "\n".join(lines)


def _format_known(known: list[Behaviour]) -> str:
    if not known:
        return "(none)"
    return "\n".join(f"- {b.name} [{b.status.value}]" for b in known)


RECORD_REPLY_SCHEMA: dict[str, Any] = {
    # Documents the reply contract the prompt asks for and the parser
    # enforces — same treatment as analyser.ANALYSIS_REPLY_SCHEMA: handed to
    # ai_task structured output only once Home Assistant's serializer bug is
    # fixed (see analysis._async_call_ai_task_agent).
    "type": "object",
    "properties": {
        "name": {"type": "string", "minLength": 1},
        "description": {"type": "string"},
        "steps": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "entity_id": {"type": "string", "minLength": 3},
                    "to_state": {"type": "string", "minLength": 1},
                    "max_gap_seconds": {"type": "integer", "minimum": 5, "maximum": 86400},
                },
                "required": ["entity_id", "to_state"],
            },
        },
        "weekdays": {"type": "array", "items": {"type": "integer", "minimum": 1, "maximum": 7}},
        "time_start": {"type": "string", "description": "HH:MM UTC"},
        "time_end": {"type": "string", "description": "HH:MM UTC"},
        "cooldown_seconds": {"type": "integer", "minimum": 30, "maximum": 86400},
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "domain": {"type": "string", "minLength": 1},
                    "service": {"type": "string", "minLength": 1},
                    "entity_id": {"type": "string"},
                    "data": {"type": "object"},
                },
                "required": ["domain", "service"],
            },
        },
        "reply": {"type": "string", "description": "one or two sentences to the user"},
    },
    "required": ["name", "steps", "actions"],
}

RECORD_PROMPT_TEMPLATE = """You are a smart-home automation designer. The user taught PHIL9000 \
a rule by demonstration: they recorded the household events that mark the situation (the \
TRIGGERS), then performed the changes the automation should later replay (the ACTIONS). \
Turn this one demonstration into the automation the user most likely means.

Recorded TRIGGERS, in the order they happened (times UTC; "+Ns" is the gap since the \
previous trigger):
{triggers}

Recorded ACTIONS — state changes the user caused, which the automation must reproduce:
{actions}

User notes about what they meant:
{notes}

Behaviours this household already knows (name [status]) — do not duplicate one:
{known}

Decide the sensible generalisation:
- "steps" are the triggers: use ONLY the recorded trigger entities with their recorded \
target states, in the order that best detects the situation.
- "max_gap_seconds": the observed gap with room to breathe (usually 2-4x, minimum 60).
- "weekdays" (ISO 1=Mon..7=Sun) and "time_start"/"time_end" ("HH:MM" UTC): set them only \
when the demo or notes clearly imply a schedule (an evening demo with lights-on suggests \
a night window); leave weekdays empty and times unset when the rule should work any time. \
The user's notes outrank your guesses.
- "actions": one entry per recorded action, expressed as the Home Assistant service call \
that produces that state (a light going off to on is light.turn_on), enriched with "data" \
when the notes ask for it (brightness, volume, message...). Add an entity that was not \
recorded only when a note explicitly asks for it.
- "cooldown_seconds": how close two runs may be (default 3600).

Reply with ONLY a JSON object (no prose, no code fences):
{{"name": "short human name", "description": "one sentence of what the automation does", \
"steps": [{{"entity_id": "...", "to_state": "...", "max_gap_seconds": 600}}], \
"weekdays": [], "time_start": "HH:MM", "time_end": "HH:MM", "cooldown_seconds": 3600, \
"actions": [{{"domain": "light", "service": "turn_on", "entity_id": "light.example", \
"data": {{}}}}], "reply": "one or two sentences to the user: what you built, and anything \
the notes asked for that you could not express"}}
"""

RECORD_REFINE_TEMPLATE = """You are a smart-home automation designer. The user recorded a \
demonstration (TRIGGERS + ACTIONS) and you previously proposed this automation:
{proposal}

The demonstration again, for reference (times UTC):
{triggers}

Actions the user performed:
{actions}

User notes:
{notes}

Conversation so far:
{conversation}

The user now says:
{message}

Apply the request and return the FULL revised automation — same shape as before, steps \
may still only use the recorded trigger entities. The user's latest message outranks \
everything. Reply with ONLY a JSON object (no prose, no code fences), including a "reply" \
field: one or two sentences telling the user what you changed.
"""


def build_recording_prompt(
    session: RecordingSession,
    *,
    message: str | None = None,
    known: list[Behaviour] | None = None,
) -> str:
    """Prompt for a first proposal (message=None) or a refinement turn."""
    parts = {
        "triggers": _format_items(session.triggers),
        "actions": _format_items(session.actions),
        "notes": session.notes.strip() or "(none)",
        "known": _format_known(list(known or ())),
    }
    if message is None:
        return RECORD_PROMPT_TEMPLATE.format(**parts)
    conversation = (
        "\n".join(f"{turn['role']}: {turn['text']}" for turn in session.conversation) or "(none)"
    )
    return RECORD_REFINE_TEMPLATE.format(
        proposal=json.dumps(session.proposal or {}, indent=1),
        conversation=conversation,
        message=message.strip(),
        **parts,
    )


# -- reply parsing -------------------------------------------------------------


@dataclass(slots=True)
class ProposalResult:
    """A validated proposal, plus why parts of the reply were dropped.

    ``proposal`` is None when nothing usable survived validation (the caller
    reports that to the user — guardrail drops are never silent).
    """

    proposal: dict[str, Any] | None = None
    dropped: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _parse_hhmm(value: object) -> time | None:
    if not isinstance(value, str):
        return None
    try:
        return time.fromisoformat(value.strip())
    except ValueError:
        return None


def _clamp_int(value: object, *, default: int, low: int, high: int) -> int:
    try:
        number = int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return default
    return max(low, min(number, high))


def parse_automation_reply(text: str, session: RecordingSession) -> ProposalResult | None:
    """Validate the LLM's automation proposal against what was recorded.

    Guardrails: steps may only use entities the user actually recorded as
    triggers (hallucinated steps are dropped with a reason), actions must be
    well-formed service calls, and time/weekday/gap/cooldown values are
    clamped. Returns None when the reply carries no JSON at all (an agent
    failure, not an empty proposal).
    """
    payload = extract_reply_json(text)
    if not isinstance(payload, dict):
        return None
    result = ProposalResult()

    trigger_states: dict[str, list[str]] = {}
    for item in session.triggers:
        trigger_states.setdefault(item.entity_id, []).append(item.to_state)

    steps: list[dict[str, Any]] = []
    raw_steps = payload.get("steps")
    for step in raw_steps if isinstance(raw_steps, list) else []:
        if not isinstance(step, dict):
            result.dropped.append(("a step", "was not an object"))
            continue
        entity_id = str(step.get("entity_id", "")).strip().lower()
        to_state = step.get("to_state")
        if not is_valid_entity_id(entity_id):
            result.dropped.append((f"step {entity_id or '?'}", "malformed entity id"))
            continue
        if entity_id not in trigger_states:
            result.dropped.append(
                (f"step {entity_id}", "entity was not recorded as a trigger — refusing it")
            )
            continue
        if not isinstance(to_state, str) or not to_state:
            result.dropped.append((f"step {entity_id}", "missing to_state"))
            continue
        if to_state not in trigger_states[entity_id]:
            result.warnings.append(
                f"step {entity_id}={to_state} differs from the recorded state(s) "
                f"{'/'.join(trigger_states[entity_id])} — kept as proposed"
            )
        steps.append(
            {
                "entity_id": entity_id,
                "to_state": to_state,
                "max_gap_seconds": _clamp_int(
                    step.get("max_gap_seconds"), default=300, low=5, high=86400
                ),
            }
        )

    action_entities = {item.entity_id for item in session.actions}
    actions: list[dict[str, Any]] = []
    raw_actions = payload.get("actions")
    for action in raw_actions if isinstance(raw_actions, list) else []:
        if not isinstance(action, dict):
            result.dropped.append(("an action", "was not an object"))
            continue
        domain = str(action.get("domain", "")).strip().lower()
        service = str(action.get("service", "")).strip().lower()
        entity_id = str(action.get("entity_id", "")).strip().lower()
        label = f"action {domain}.{service}"
        if not _NAME_RE.match(domain) or not _NAME_RE.match(service):
            result.dropped.append((label or "an action", "domain/service must be lowercase names"))
            continue
        if entity_id and not is_valid_entity_id(entity_id):
            result.dropped.append((label, f"malformed entity id {entity_id}"))
            continue
        data = action.get("data")
        if data is None:
            data = {}
        if not isinstance(data, dict):
            result.dropped.append((label, "data must be an object"))
            continue
        if entity_id and entity_id not in action_entities:
            # Legitimate when the notes asked for it (e.g. "also lock the door"),
            # surprising otherwise — visible either way.
            result.warnings.append(f"{label} targets {entity_id}, which was not recorded")
        actions.append({"domain": domain, "service": service, "entity_id": entity_id, "data": data})

    time_start = _parse_hhmm(payload.get("time_start"))
    time_end = _parse_hhmm(payload.get("time_end"))
    if time_start is None or time_end is None:
        time_start = time_end = None

    weekdays: list[int] = []
    raw_days = payload.get("weekdays")
    for day in raw_days if isinstance(raw_days, list) else []:
        try:
            number = int(day)  # type: ignore[call-overload]
        except (TypeError, ValueError):
            continue
        if 1 <= number <= 7 and number not in weekdays:
            weekdays.append(number)

    if not steps:
        # Without a usable step the proposal can never detect anything: hand
        # the drops back so the caller can tell the model (and user) why.
        result.dropped.append(("<whole proposal>", "no valid steps from the recorded triggers"))
        return result

    result.proposal = {
        "name": (str(payload.get("name", "")).strip() or "Recorded routine")[:120],
        "description": str(payload.get("description", "")).strip()[:500],
        "steps": steps,
        "weekdays": sorted(weekdays),
        "time_start": time_start.isoformat(timespec="minutes") if time_start else None,
        "time_end": time_end.isoformat(timespec="minutes") if time_end else None,
        "cooldown_seconds": _clamp_int(
            payload.get("cooldown_seconds"), default=3600, low=30, high=86400
        ),
        "actions": actions,
        "reply": str(payload.get("reply", "")).strip()[:500],
    }
    return result


# -- creating the behaviour ------------------------------------------------------


def proposal_to_behaviour(proposal: dict[str, Any]) -> Behaviour:
    """Build the confirmed behaviour the user creates from the proposal."""
    steps = [
        BehaviourStep(
            entity_id=step["entity_id"],
            to_state=step["to_state"],
            max_gap_seconds=_clamp_int(step.get("max_gap_seconds"), default=300, low=5, high=86400),
        )
        for step in proposal.get("steps", [])
    ]
    actions = [
        BehaviourAction(
            domain=action["domain"],
            service=action["service"],
            entity_id=action.get("entity_id", ""),
            data=dict(action.get("data") or {}),
        )
        for action in proposal.get("actions", [])
    ]
    return Behaviour(
        id=f"{BEHAVIOUR_ID_PREFIX}{uuid.uuid4().hex[:12]}",
        name=str(proposal.get("name") or "Recorded routine")[:120],
        description=str(proposal.get("description") or ""),
        steps=steps,
        actions=actions,
        weekdays=set(int(day) for day in proposal.get("weekdays") or []),
        time_start=_parse_hhmm(proposal.get("time_start")),
        time_end=_parse_hhmm(proposal.get("time_end")),
        confidence=1.0,
        status=BehaviourStatus.CONFIRMED,
        cooldown_seconds=_clamp_int(
            proposal.get("cooldown_seconds"), default=3600, low=30, high=86400
        ),
    )


# States whose replay needs a per-domain service name (everything else maps to
# turn_on/turn_off). The direct (no-LLM) save handles only what can be derived
# mechanically; anything fancier is the Propose button's job.
_DOMAIN_STATE_SERVICES: dict[str, dict[str, str]] = {
    "cover": {"open": "open_cover", "closed": "close_cover"},
    "lock": {"locked": "lock", "unlocked": "unlock"},
    "media_player": {
        "playing": "media_play",
        "paused": "media_pause",
        "idle": "media_pause",
        "standby": "turn_standby",
    },
}


def state_to_action(entity_id: str, to_state: str) -> BehaviourAction | None:
    """Best mechanical service call to bring entity_id to to_state (None if none)."""
    domain = entity_id.partition(".")[0]
    service = _DOMAIN_STATE_SERVICES.get(domain, {}).get(to_state)
    if service is None:
        service = {"on": "turn_on", "off": "turn_off"}.get(to_state)
    if service is None:
        return None
    return BehaviourAction(domain=domain, service=service, entity_id=entity_id)


def direct_behaviour(session: RecordingSession) -> Behaviour:
    """No-LLM conversion: recorded triggers→steps (observed gaps), actions→services.

    Raises ValueError naming what cannot be replayed mechanically, so the
    panel can point the user at Propose instead.
    """
    if not session.triggers:
        raise ValueError("record at least one trigger first")
    problems = [
        f"{item.entity_id} → {item.to_state!r}"
        for item in session.actions
        if state_to_action(item.entity_id, item.to_state) is None
    ]
    if problems:
        raise ValueError(
            "cannot replay "
            + ", ".join(problems)
            + " without the LLM — press Propose automation instead"
        )
    if not session.actions:
        raise ValueError("record at least one action first (or use Propose)")

    steps: list[BehaviourStep] = []
    previous: datetime | None = None
    for item in session.triggers:
        if previous is None:
            gap = 300  # the engine ignores the first step's gap
        else:
            observed = round((item.at - previous).total_seconds())
            gap = max(60, min(observed * 2, 3600))  # the observed rhythm, with slack
        steps.append(
            BehaviourStep(entity_id=item.entity_id, to_state=item.to_state, max_gap_seconds=gap)
        )
        previous = item.at

    actions = [state_to_action(item.entity_id, item.to_state) for item in session.actions]
    trail = " → ".join(f"{s.entity_id}={s.to_state}" for s in steps)
    notes = session.notes.strip()
    return Behaviour(
        id=f"{BEHAVIOUR_ID_PREFIX}{uuid.uuid4().hex[:12]}",
        name="Recorded routine",
        description=(f"Recorded: {trail}" + (f". Notes: {notes}" if notes else ""))[:500],
        steps=steps,
        actions=[action for action in actions if action is not None],
        confidence=1.0,
        status=BehaviourStatus.CONFIRMED,
    )
