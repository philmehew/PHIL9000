# HAL1976

A Home Assistant integration that learns a household's behaviour by deriving patterns from
sensor data — observe first, ask questions, and (once you confirm) act with pure Python.

Status: **MVP in progress** — see `PLAN.md` and `~/.opencode/plan/hal1976-mvp.md` (approved).

## Layout

- `hal1976/` — core domain package (no Home Assistant imports): behaviour models,
  the deterministic detection engine, YAML persistence for learned behaviours.
- `tests/` — unit tests with synthetic event streams.
- `custom_components/hal1976/` — Home Assistant integration adapter (Milestone 2).

## Quick start

```bash
uv sync
uv run pytest        # run the test suite
uv run python main.py  # replay a synthetic evening and watch detection fire
```

## Philosophy

- **Observe first.** HAL1976 proposes patterns; it never acts autonomously by default.
- **LLM for discovery, Python for execution.** Pattern *detection* and action execution
  are deterministic Python; the LLM (via Home Assistant Assist) only proposes candidates.
- **You stay in the loop.** Every learned behaviour can be inspected, corrected, confirmed,
  or dismissed.
