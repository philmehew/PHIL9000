# AGENTS.md — HAL3000

## What HAL3000 is

HAL3000 is a Home Assistant integration (HACS/Marketplace installable) that learns a household's
behaviour by deriving patterns from Home Assistant sensor and entity data — presence sensors,
device state changes, time-of-day rhythms, etc.

**Example pattern:** an office presence sensor stops seeing a user at ~17:30; a couple of minutes
later the kitchen presence sensor sees them. That's likely "end of the work day". HAL3000 could
then suggest (or, once confirmed, perform) a courteous "welcome home" action and check the office
desk is powered down.

## Core philosophy

1. **Observe first.** The initial purpose is to observe, identify patterns, and ask the user
   questions about what those patterns are. Do not act autonomously by default.
2. **LLM for discovery, Python for execution.** LLM involvement is limited to periodic
   parsing/analysis of data to identify and firm up behavioural patterns. Detecting a learned
   pattern and executing its action must be pure Python logic (criteria matching → action) with
   no LLM in the loop. Keep this separation architectural, not incidental.
3. **User in the loop.** HAL3000 offers information on what it has learned and makes suggestions
   for what it should do. Users must be able to understand learned behaviours (and the entities
   involved), offer corrections, and define/adjust actions for when behaviours are detected.
4. **MVP before frontend.** No concrete frontend exists yet. Design behaviours, patterns, and
   suggestions as data/models first, so any UI ( Lovelace card, config flow panel, etc.) can be
   layered on later. Consider a minimal service/API surface for inspecting and editing learned
   behaviours from the start.

## Domain vocabulary (working)

- **Event / observation:** a timestamped entity state change (e.g. `binary_sensor.office_presence`
  → `off` at 17:30).
- **Behaviour (pattern):** a named, recurring sequence/correlation of observations with typical
  timing and confidence (e.g. "end of work day": office presence off → kitchen presence on, within
  ~2–5 min, weekdays ~17:00–18:00).
- **Suggestion:** HAL3000's proposal to the user: "I've noticed X — is this the end of your work
  day? Would you like me to do Y when it happens?"
- **Confirmed behaviour / action:** a behaviour the user has confirmed, plus the action(s) to
  execute when it is detected. Executed via pure Python matching, no LLM.

## Technical constraints

- Python 3.13, managed with `uv` (`pyproject.toml`, `.python-version`).
- Target: Home Assistant custom integration structure (`custom_components/hal3000/`), installable
  via HACS/Marketplace.
- Persistence must survive restarts (Home Assistant storage helpers or similar) — learned patterns
  are valuable and must not be lost.
- Sensor/state history access via Home Assistant APIs (event bus, state machine, recorder/history).
- Tests for pattern detection and matching logic — this is the core value and must be deterministic.

## Repo status

Brand-new project (empty scaffold, no commits yet). See `PLAN.md` / current plan for the MVP scope.
