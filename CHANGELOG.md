# Changelog

## v0.4.1 — 2026-11-22

### Mute noisy entities, right from the feed

- **⊘ on every activity row** mutes that entity: it lands in the integration's
  exclusion list (persisted, also visible in the options flow) and stops being
  observed immediately — no more feed rows, behaviour steps or LLM digest lines
  for it. Exactly the "I don't need every voltage change on the heater" case.

- **Monitoring section** in the panel: the watched domains, extra entities and
  a live count, plus every **muted entity with an Unmute button**. Add or
  remove whole domains still happens in the integration options.

- Mutes (and any entity-selection change from the options flow) apply **in
  place**: the config entry is no longer reloaded for data-only changes, so
  open panels keep their live subscription streaming through a mute. Options
  changes still reload, since they can alter the nightly analysis schedule.

- New websocket commands: `phil9000/monitoring`, `phil9000/mute_entity`,
  `phil9000/unmute_entity`; exclusion ids are validated by a pure
  `is_valid_entity_id` helper; `phil9000/behaviours` and `phil9000/activity`
  now read the runtime's live subscription model.

## v0.4.0 — 2026-11-22

### See what PHIL9000 sees, and refine until accepted

- **Recent activity feed** in the panel: the newest 25 real state changes on
  observed entities, read from Home Assistant's recorder (the single central
  activity log) and live-appended while the panel is open. Attribute-only state
  changes are no longer observed at all — they can't match a step and used to
  flood chatty sensors into everything.

- **Analyse now button** in the panel runs the LLM pattern analysis on demand
  (same path as `phil9000.run_analysis`), with a result banner and a
  one-run-at-a-time guard.

- **Refinement protocol**: the analyser now receives the current behaviour list
  (with ids and occurrence counts) and replies `{"updates": [...], "new": [...]}`
  — candidates get their gaps, windows and descriptions firmed up as evidence
  accumulates, instead of only new patterns being possible.

- **Accepted behaviours are hard-fixed**: only candidates can be refined. The
  parser drops any update naming a confirmed or dismissed behaviour, and a
  refinement that would collide with another behaviour's signature. Variants of
  confirmed patterns come back as new candidates.

- **Rename in the panel** (`phil9000/set_name`): user-given names set
  `custom_name`, which LLM refinements then preserve. The YAML schema accepts
  `custom_name` (defaults to false).

- New websocket commands: `phil9000/activity`, `phil9000/subscribe_activity`,
  `phil9000/run_analysis`, `phil9000/set_name`. New pure module `activity.py`
  holds the feed-merging rules; `README.md` gained a "Testing what PHIL9000
  sees" walkthrough.

## v0.3.2 — 2026-11-22

- Brand icon refined: the eye now has a round silver bezel (was a copper
  oval) and its reflections sit across the upper third of the lens, as on
  the real HAL dome.

- Sidebar panel icon changed from `mdi:eye` to `mdi:eye-circle`.

- Fixed the hi-res brand image filename: Home Assistant looks for
  `brand/icon@2x.png`.

## v0.3.1 — 2026-11-22

- Added brand images (`brand/icon.png`, `brand/icon@2x.png`): a HAL 9000
  panel with a green eye, shown on the Home Assistant integration cards
  (HA ≥ 2026.3 picks these up automatically).

- Sidebar panel icon changed from `mdi:brain` to `mdi:eye`, echoing the logo.

## v0.3.0 — 2026-11-22

### Observing by entity type

- Setup is now a short checklist of entity *types* (domains) with sensible
  defaults pre-ticked (binary sensors, device trackers, people, lights,
  switches) instead of an unworkable entity dropdown.

- Whole-domain subscriptions cover entities added later, and skip hidden,
  disabled and diagnostic/config entities automatically.

- The integration's options refine a domain subscription per device: *also
  observe these individual entities* and *never observe these entities*.

- The sidebar panel count and LLM history analysis expand domain
  subscriptions to the concrete entities observed right now.

- Existing per-entity selections keep working (fully backwards compatible);
  they appear in the options' individual-entity field.

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
