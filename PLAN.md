# HAL1976 — MVP Plan

## Goal of the MVP

Ship a working Home Assistant integration that: **observes entity events, detects a simple but real
behaviour pattern, asks the user about it, and — once confirmed — detects that pattern reliably and
executes a user-chosen action, using pure Python (no LLM at detection time).**

An LLM is used only in an optional, periodic "analysis" step that reviews collected observations and
proposes candidate patterns for the user to confirm. The MVP can run end-to-end without the LLM
(manual pattern definition), so the core loop is testable and deterministic.

## Architecture overview

```
Home Assistant event bus / state machine
        │  (subscribe to state_changed)
        ▼
┌─────────────────────┐     ┌──────────────────────────┐
│  Observation store  │     │  Pattern definitions     │
│  (rolling history   │     │  (confirmed + candidate, │
│   of entity events) │     │  persisted in HA storage)│
└─────────┬───────────┘     └──────────┬───────────────┘
          │                            │
          ▼                            ▼
   ┌──────────────────────────────────────────┐
   │  Detection engine (pure Python)           │
   │  matches live event stream against        │
   │  confirmed patterns → fires BehaviourDetected │
   └──────────────┬───────────────────────────┘
                  │
        ┌─────────┴──────────┐
        ▼                    ▼
 ┌──────────────┐    ┌─────────────────┐
 │ LLM analyser  │    │ Action executor  │
 │ (periodic,    │    │ (calls services, │
 │  optional;    │    │  e.g. TTS welcome│
 │  proposes      │    │  message, turn  │
 │  candidates)   │    │  off desk plugs) │
   └──────────────┘    └─────────────────┘
```

## Component breakdown (MVP scope)

### 1. Home Assistant integration scaffold — `custom_components/hal1976/`
- `manifest.json` (HACS installable), `config_flow.py` (minimal setup), `__init__.py`
  (async setup entry), `const.py`.
- Config flow asks: which entities to observe (multi-select), and optionally an LLM endpoint/key
  for the analyser (approach decided at review — see open questions).
- Persistence via HA storage helpers (`Store`) — learned patterns must survive restarts.

### 2. Observation store
- Subscribes to `state_changed` events for the user-selected entities.
- Stores a rolling window of observations (entity_id, old/new state, timestamp, attributes) —
  default 30 days, capped.
- Key API: query by entity and time range. This is the training data for the LLM analyser and
  the context for explaining patterns to the user.

### 3. Behaviour model (core domain)
- `Pattern`: id, name, human description, list of `PatternStep` (entity_id, state condition, max
  gap to next step), day-of-week/time-window constraints, confidence, status
  (`candidate | confirmed | dismissed`).
- Plain dataclasses + JSON serialisation; UI-agnostic per AGENTS.md.
- Seed example for testing: "End of work day" (office presence off → kitchen presence on within
  5 min, weekdays, 16:00–19:00).

### 4. Detection engine (pure Python)
- On each observed event, match against **confirmed** patterns only (candidates never execute).
- Sequential-step matcher with time gaps and window constraints — deterministic, unit-testable.
- On match: fire `hal1976_behaviour_detected` event on the HA event bus, then trigger the
  pattern's configured actions.
- Debounce/dedupe: a confirmed pattern doesn't re-fire within a cooldown period.

### 5. Actions & suggestions
- MVP actions: call any HA service (user picks entity + service), e.g. `tts.speak` welcome
  message, `switch.turn_off` on desk plugs.
- Suggestion flow: candidate patterns surface as persistent notifications / events; user confirms
  via the integration's options flow or a service call in MVP (proper UI later).

### 6. LLM analyser (optional module, periodic)
- Nightly (or on-demand) job: summarises the observation store and proposes candidate patterns as
  `Pattern` data — never executes anything.
- Guardrails: candidates always require user confirmation; LLM output schema-validated.
- Runs behind a config flag; MVP is fully functional without it.

## Repo layout

```
custom_components/hal1976/     # HA integration (manifest, config_flow, __init__, storage, event hooks)
hal1976/                       # installable python package: domain models + engine
  models.py                    # Pattern, PatternStep, Observation, BehaviourStatus
  store.py                     # observation store (pure, no HA deps)
  detect.py                    # detection engine (pure, no HA deps)
  analyser.py                  # LLM analyser (optional)
  actions.py                   # action spec + executor (HA-facing side kept thin)
tests/                         # unit tests for store, models, detect, analyser (mocked LLM)
main.py / pyproject.toml       # uv-managed dev workflow
```

Key decision: **domain logic lives outside the integration folder with no HA imports**, so it is
trivially testable and could later run standalone. The integration is a thin adapter.

## Milestones

1. **M1 — Domain + tests:** models, observation store, detection engine, seed "end of work day"
   pattern, full unit test suite with synthetic event streams. *No HA code.*
2. **M2 — HA scaffold:** custom integration, config flow (entity selection), `state_changed`
   subscription → observation store, persistence, HACS meta files (`hacs.json`, README).
3. **M3 — Loop closure:** BehaviourDetected event fires on a real match; action executor calls
   services; confirm/dismiss via options flow or service; persistent-notification suggestions.
4. **M4 — LLM analyser:** periodic candidate-pattern proposal with schema validation and manual
   confirmation gate; config flag; tests with a mocked LLM.
5. **M5 — MVP polish:** cooldowns, robustness, logging, docs, HACS release v0.1.0.

## Out of scope for MVP (parked)

- Rich frontend / Lovelace card (the data/API surface is designed so it can come later).
- Autonomy: patterns never auto-promote from candidate to confirmed.
- Multi-user/household-member disambiguation.
- Complex actions (scripts, sequences with conditions) beyond a single service call.
- Statistical pattern mining beyond the LLM's proposals.

## Open questions for review

1. LLM access: use HA's built-in `conversation` integration / Assist, or a direct API key
   (OpenAI/Anthropic) in config?
2. Is a single service-call action enough for MVP, or do you want simple action sequences from
   day one?
3. Should observation storage use HA's recorder/history (read-only dependency) or HAL1976's own
   event-bus subscription? Plan assumes the latter (self-contained, works without recorder).
4. Confirmation UX: are persistent notifications + options flow acceptable for MVP, or is a basic
   Lovelace card a must-have earlier?
