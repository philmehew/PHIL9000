# HAL1976

A Home Assistant integration that learns a household's behaviour by deriving patterns from
sensor data — observe first, ask questions, and (once you confirm) act with pure Python.

Status: **Milestone 2 complete** — domain package + detection engine are tested, and the
Home Assistant integration scaffold (config flow, event subscription, YAML persistence,
`hal1976_behaviour_detected`) works end-to-end. See `~/.opencode/plan/hal1976-mvp.md`
(approved plan); `PLAN.md` is the earlier draft.

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
