# HAL1976

A Home Assistant integration that learns a household's behaviour by deriving patterns from
sensor data — observe first, ask questions, and (once you confirm) act with pure Python.

Status: **Milestone 5 complete** — the full loop works: observed events feed the pure
detection engine, confirmed behaviours fire `hal1976_behaviour_detected` **and execute
their service-call actions**, candidates nudge you, a **sidebar panel** shows learned
behaviours with confidence, evidence, confirm/dismiss/reopen and action editing, and an
optional **Assist-backed analyser** proposes new candidates from recorder history
(schema-validated, always inert until confirmed). Everything persists to YAML.
See `~/.opencode/plan/hal1976-mvp.md` (approved plan); `PLAN.md` is the earlier draft.

## Layout

Everything ships inside the integration folder, because that is all HACS copies to the
user's `config/`:

- `custom_components/hal1976/` — the integration.
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

Learned behaviours persist as **YAML** in `<HA config>/hal1976/behaviours.yaml` —
user-editable and versionable. Observation data itself stays in HA's recorder.

## Installing in Home Assistant

1. Add this repository as a HACS custom repository (type: *Integration*), or copy
   `custom_components/hal1976/` into your HA config directory.
2. Restart Home Assistant, then add HAL1976 under *Settings → Devices & Services* and
   pick the entities to observe.

## Using it

- HAL1976 observes the entities you selected and tallies matches. **Candidates never act.**
- A **HAL1976 sidebar panel** lists learned behaviours with confidence, steps, schedule
  and last-fired evidence, and lets you confirm, dismiss, reopen, and edit each
  behaviour's actions (talks to the `hal1976/behaviours`, `hal1976/set_status` and
  `hal1976/set_actions` websocket commands).
- When a candidate pattern is first seen you also get a nudge (via the `notify` entity
  from the options, otherwise a persistent notification) with the pattern and its id.
- You can also confirm or dismiss from *Developer Tools → Actions* (or a script):
  `hal1976.confirm_behaviour` / `hal1976.dismiss_behaviour` with `behaviour_id`.
- Confirmed behaviours fire the `hal1976_behaviour_detected` event and execute their
  actions (HA service calls defined in the YAML) — deterministic Python, no LLM.
- **Optional pattern discovery**: enable *nightly pattern analysis* in the integration
  options (off by default) and/or call `hal1976.run_analysis` on demand. HAL1976
  summarises the recent recorder history and asks the household's Assist conversation
  agent for recurring patterns. Proposals are schema-validated, arrive as candidates
  with **no actions**, never re-propose known patterns, and nothing executes until you
  confirm them — the LLM only ever discovers; detection and action stay pure Python.
- Anything you confirm, dismiss or learn survives restarts.

## Development

```bash
uv sync
uv run pytest        # full suite: pure core + integration smoke tests
uv run python main.py  # replay a synthetic evening and watch detection fire
```

## Philosophy

- **Observe first.** HAL1976 proposes patterns; it never acts autonomously by default.
- **LLM for discovery, Python for execution.** Pattern *detection* and action execution
  are deterministic Python; the LLM (via Home Assistant Assist) only proposes candidates.
- **You stay in the loop.** Every learned behaviour can be inspected, corrected, confirmed,
  or dismissed.
