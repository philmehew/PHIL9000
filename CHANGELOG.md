# Changelog

## v0.2.0 — 2026-11-22

### Entity selection

- Setup and options now use Home Assistant's searchable entity picker (type to
  filter, selected entities shown as chips) instead of an unsearchable list of
  every entity in the system.

- Candidates are prefiltered to observation-worthy domains (presence, activity
  and controllable-device signals); hidden, disabled and diagnostic/config
  entities (firmware sensors, config buttons, ...) are left out.

- Entities already selected are always kept visible in the options picker, even
  if they would no longer pass the filter.

## v0.1.0 — 2026-11-21

Initial MVP release.

### Learning loop

- Subscribes to user-selected entities and matches a live stream of state changes
  against known behaviours with a deterministic, pure-Python detection engine
  (sequential steps, max gaps, weekday and time-window constraints, per-behaviour
  cooldowns).

- Candidates are observed, tallied and nudged to you — they never fire events or
  actions. Confidence grows with occurrences (`1 - 1/(n+1)`); nothing auto-promotes.

### Acting

- Confirmed behaviours fire `phil9000_behaviour_detected` and execute their actions
  as ordered Home Assistant service calls (failures are isolated and logged).

- `phil9000.confirm_behaviour` / `phil9000.dismiss_behaviour` services with
  immediate YAML persistence.

### Discovery (optional, off by default)

- `phil9000.run_analysis` and a nightly job summarise recorder history and ask the
  household's Assist conversation agent for recurring patterns.

- Proposals are schema-validated, arrive as action-free candidates, never re-propose
  known or dismissed patterns, and are idempotent across runs.

### Interface

- Sidebar panel (plain JS, no build step): behaviours with confidence, steps,
  schedule and last-fired evidence; confirm / dismiss / reopen; inline action editor.

- Websocket commands: `phil9000/behaviours`, `phil9000/set_status`, `phil9000/set_actions`.

- Config flow for entity selection; options flow for entities, conversation agent,
  notify entity, default cooldown, and analysis settings.

### Storage & robustness

- Behaviours persist as user-editable YAML under `<config>/phil9000/behaviours.yaml`
  (atomic writes). Corrupt files trigger setup-retry instead of silent data loss.

- Core domain logic is importable and testable without Home Assistant, enforced by
  a purity guard test.

### Known limitations

- All times are UTC (detection windows included).

- Richer history visualisation and multi-user disambiguation are not in the MVP.
