# HAL1976

A Home Assistant integration that learns a household's behaviour by deriving patterns from
sensor data — observe first, ask questions, and (once you confirm) act with pure Python.

Status: **Milestone 3 complete** — the full loop works: observed events feed the pure
detection engine, confirmed behaviours fire `hal1976_behaviour_detected` **and execute
their service-call actions**, candidates nudge you to confirm or dismiss them, and
everything persists to YAML. See `~/.opencode/plan/hal1976-mvp.md` (approved plan);
`PLAN.md` is the earlier draft.

## Layout

Everything ships inside the integration folder, because that is all HACS copies to the
user's `config/`:

- `custom_components/hal1976/` — the integration.
  - Pure core, **no Home Assistant imports** (guarded by `tests/test_purity.py`):
    `models.py`, `detect.py`, `yaml_store.py`, `seed.py`, `const.py`.
  - Thin HA adapter (HA imports are function-local): `__init__.py`, `config_flow.py`,
    `manifest.json`, `strings.json`, `translations/`.
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
- When a candidate pattern is first seen you get a nudge (via the `notify` entity from
  the options, otherwise a persistent notification) with the pattern and its id.
- Confirm or dismiss from *Developer Tools → Actions* (or a script):
  `hal1976.confirm_behaviour` / `hal1976.dismiss_behaviour` with `behaviour_id`.
- Confirmed behaviours fire the `hal1976_behaviour_detected` event and execute their
  actions (HA service calls defined in the YAML) — deterministic Python, no LLM.
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
