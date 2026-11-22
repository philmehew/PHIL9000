# PHIL9000

A Home Assistant integration that learns a household's behaviour by deriving patterns from
sensor data — observe first, ask questions, and (once you confirm) act with pure Python.

Status: **v0.1.0 (MVP) released** — all five functional milestones are done: observed
events feed the pure detection engine, confirmed behaviours fire
`phil9000_behaviour_detected` **and execute their service-call actions**, candidates nudge
you, a sidebar panel handles review and action editing, and an optional Assist-backed
analyser proposes new candidates from recorder history (schema-validated, inert until
confirmed). See `~/.opencode/plan/phil9000-mvp.md` (approved plan); `PLAN.md` is the
earlier draft.

## Layout

Everything ships inside the integration folder, because that is all HACS copies to the
user's `config/`:

- `custom_components/phil9000/` — the integration.
  - Pure core, **no Home Assistant imports** (guarded by `tests/test_purity.py`):
    `models.py`, `detect.py`, `yaml_store.py`, `seed.py`, `const.py`.
  - Thin HA adapter (HA imports are function-local): `__init__.py`, `config_flow.py`,
    `websocket_api.py`, `analysis.py` + `notifier.py` (recorder/agent plumbing for the
    analyser), `frontend/panel.js` (plain-JS sidebar panel), `manifest.json`,
    `strings.json`, `services.yaml`, `translations/`.
  - `analyser.py` is also pure: history summaries, the discovery prompt, and
    schema-validation of LLM proposals into inert candidates.
- `tests/` — pure unit tests (run without Home Assistant) plus adapter smoke tests
  using `pytest-homeassistant-custom-component` (dev-only dependency).
- `main.py` — standalone demo: replays a synthetic evening through the detection engine.

Learned behaviours persist as **YAML** in `<HA config>/phil9000/behaviours.yaml` —
user-editable and versionable. Observation data itself stays in HA's recorder.

## Installing in Home Assistant

1. Add this repository as a HACS custom repository (type: *Integration*), or copy
   `custom_components/phil9000/` into your HA config directory.
2. Restart Home Assistant, then add PHIL9000 under *Settings → Devices & Services* and
   pick the entities to observe.

## Using it

- PHIL9000 observes the entities you selected and tallies matches. **Candidates never act.**
- A **PHIL9000 sidebar panel** lists learned behaviours with confidence, steps, schedule
  and last-fired evidence, and lets you confirm, dismiss, reopen, and edit each
  behaviour's actions (talks to the `phil9000/behaviours`, `phil9000/set_status` and
  `phil9000/set_actions` websocket commands).
- When a candidate pattern is first seen you also get a nudge (via the `notify` entity
  from the options, otherwise a persistent notification) with the pattern and its id.
- You can also confirm or dismiss from *Developer Tools → Actions* (or a script):
  `phil9000.confirm_behaviour` / `phil9000.dismiss_behaviour` with `behaviour_id`.
- Confirmed behaviours fire the `phil9000_behaviour_detected` event and execute their
  actions (HA service calls defined in the YAML) — deterministic Python, no LLM.
- **Optional pattern discovery**: enable *nightly pattern analysis* in the integration
  options (off by default) and/or call `phil9000.run_analysis` on demand. PHIL9000
  summarises the recent recorder history and asks the household's Assist conversation
  agent for recurring patterns. Proposals are schema-validated, arrive as candidates
  with **no actions**, never re-propose known patterns, and nothing executes until you
  confirm them — the LLM only ever discovers; detection and action stay pure Python.
- Anything you confirm, dismiss or learn survives restarts.

## Example behaviour

The seed pattern, in full, as it lives in `behaviours.yaml`. Add your own candidates by
hand (status `candidate` = observe and tally only), or let the analyser propose them:

```yaml
version: 1
behaviours:
  - id: end_of_work_day
    name: End of work day
    description: You left the office and appeared in the kitchen shortly after.
    steps:
      - entity_id: binary_sensor.office_presence
        to_state: "off"
      - entity_id: binary_sensor.kitchen_presence
        to_state: "on"
        max_gap_seconds: 300     # this step within 5 min of the previous one
    actions:                     # run in order, only when status is confirmed
      - domain: tts
        service: speak
        entity_id: tts.home_assistant_cloud
        data:
          message: Welcome home — the office is all powered down.
    weekdays: [1, 2, 3, 4, 5]    # ISO numbers, 1 = Monday; omit for every day
    time_start: "16:00:00"       # window for the FIRST step (see UTC note)
    time_end: "19:00:00"
    status: candidate            # candidate | confirmed | dismissed
    cooldown_seconds: 3600       # minimum seconds between firings
    confidence: 0.0              # managed by PHIL9000 (1 - 1/(occurrences+1))
    occurrences: 0               # managed by PHIL9000
```

## Gotchas (v0.1.0)

- **Times are UTC everywhere.** State timestamps, detection windows and the analyser
  prompt all use UTC. A `time_start: "17:30"` that means "5:30pm local" needs converting
  for your timezone (local-time windows are a planned follow-up).
- A corrupt `behaviours.yaml` puts the integration into setup-retry (visible in
  *Settings → Devices & Services*) rather than silently starting empty — fix or restore
  the file, then retry.
- The entity pickers only list entities that currently exist in HA.
- Suggestions nudge once per candidate per HA run; the panel and YAML remain the
  long-term record.

## Development

```bash
uv sync
uv run pytest        # full suite: pure core + HA-harness tests
uv run ruff check .  # lint
uv run python main.py  # replay a synthetic evening and watch detection fire
```

## Philosophy

- **Observe first.** PHIL9000 proposes patterns; it never acts autonomously by default.
- **LLM for discovery, Python for execution.** Pattern *detection* and action execution
  are deterministic Python; the LLM (via Home Assistant Assist) only proposes candidates.
- **You stay in the loop.** Every learned behaviour can be inspected, corrected, confirmed,
  or dismissed.
