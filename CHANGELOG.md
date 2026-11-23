# Changelog

## v0.5.3 — 2026-11-23

### Full-size, scrollable activity history

- The Recent activity feed now covers the **last 3 hours**, capped at
  **1000 rows** — not just the newest 25 events. The box keeps its fixed
  ~25-row visible height and **scrolls** through the rest, and the buffer
  survives Pause up to 1000 rows. Re-opening the panel shows the full
  window again, read from the recorder.
- Data budget: the 3-hour window keeps the single recorder query trivial;
  1000 rows ≈ 150 KB of JSON over the websocket — cheap on anything that
  runs Home Assistant. Anything older lives in HA's Logbook.

## v0.5.2 — 2026-11-23

### Panel polish

- The **Recent activity** table is always full size — 25 rows tall (the
  feed's cap) — instead of shrinking to the number of events. The layout is
  stable whether the day was quiet or busy; a wrapped row (long entity id)
  scrolls rather than stretching the card.

## v0.5.1 — 2026-11-23

### Panel polish + custom LLM prompt

- The **Recent activity** card is always rendered — while loading and when
  no events have been observed yet — with the hint ("No state changes
  observed yet…", or a recorder error) shown *inside* the card, instead of
  the area collapsing to a bare line of text.

- **Custom LLM prompt** (integration settings, just under the conversation
  agent): a multi-line field pre-filled with the built-in template. Tweak
  the wording, add household context, or change the output style — it must
  contain `{summary}` (where the entity history goes) and may use `{known}`
  and `{cap}`; stray braces and missing placeholders are rejected with a
  form error; clearing the field restores the built-in template. Safety is
  unchanged: the reply parser enforces the candidate-only protocol
  regardless of what the prompt says.

## v0.5.0 — 2026-11-23

### Review backlog: engine efficiency, debounced saves, shadow-DOM panel

- **The detection engine pre-filters events.** It now keeps an index of the
  entities appearing in any behaviour's steps; events for everything else
  (most of a whole-domain subscription) are skipped before the engine or
  the occurrence bookkeeping runs — no semantics change, but O(behaviours)
  work only happens for relevant events. The index rebuilds after LLM
  refinements so new entities are covered immediately.
- **behaviours.yaml writes are debounced** (30s): a noisy candidate used to
  rewrite the file on every match. Explicit user actions still save
  immediately; at worst 30s of occurrence counters are lost on a hard crash.
- **A breaking event can restart a sequence**: an event that arrives with
  the wrong state for the next step used to just cancel the match, unlike
  the gap-timeout branch which retries the same event against the first
  step. Both paths now behave the same.
- **The activity feed keeps live rows on first load**: rows pushed between
  subscribing and the recorder's (asynchronous) commit were silently
  overwritten by the initial fetch; they're now merged in, deduped and
  re-sorted newest-first.
- **The "Skipped: analysis already in progress" log line is persisted**
  like every other log line, instead of living only until the next run.
- **`Observation.attributes` is gone**: the full attribute dict was copied
  per event and shipped in every detection event payload, but nothing ever
  read it.
- **The panel renders in a shadow root**: its styles (button, .card, …)
  previously leaked into the whole Home Assistant document — and HA's could
  bleed in. CSS is now fully encapsulated; HA theme variables still inherit
  through the shadow boundary.

## v0.4.9 — 2026-11-23

### Admin-only amendments

- Confirmed behaviours run service calls with the integration's authority,
  so anything that **amends** PHIL9000 now requires an **admin** user:
  confirming/dismissing/renaming, editing actions, subscriptions and
  per-entity mutes, and running the LLM analysis (websocket commands reject
  with `unauthorized`; the services raise `Unauthorized`).
- Reads stay open to everyone — the panel, activity feed, analysis log and
  monitoring view work for non-admin users, who now see a **read-only**
  panel: change buttons and checkboxes are disabled and the header notes
  the admin requirement.
- Automations calling the services without a user (internal calls) are
  unaffected.

## v0.4.8 — 2026-11-23

### Code review round: efficiency, leak and dead-code fixes

- **Recorder reads are batched**: the activity feed and the analysis run
  queried HA's history once *per entity* — with whole-domain subscriptions
  that meant hundreds of database round-trips per panel load. Both now make
  a single query for the whole observed list.
- **Panel subscriptions no longer leak**: HA keeps one websocket connection,
  so every visit to the sidebar panel used to add another set of live
  subscriptions that kept firing on the detached panel forever. Subscriptions
  are now dropped in `disconnectedCallback`.
- **The action editor survives background refreshes**: a behaviour detected
  mid-editing used to re-render the card list and wipe whatever you were
  typing. Editor inputs are now harvested before any re-render, and the
  rename box keeps its typed text.
- **Hung agent calls time out** after 10 minutes instead of holding the
  in-flight guard until Home Assistant restarts.
- **The options update listener reloads via a fire-and-forget task** per
  HA's documented pattern, instead of awaiting a reload from inside the
  listener.
- **14 copies of the websocket "not loaded" guard** collapsed into one
  `_require()` helper; the dead legacy `parse_proposals()` API and the
  unused `CONF_ENTITY_IDS` constant are removed (tests now exercise the
  same pipeline through `parse_analysis_reply`).

## v0.4.7 — 2026-11-23

### Panel inset

- The panel content no longer sits flush against the sidebar edge. The
  intended 16 px padding was declared with `:host`, which only matches
  inside a shadow root — the panel renders in the light DOM, so it never
  applied. Styled by element tag instead; the whole panel now has the
  usual Home Assistant inset.

## v0.4.6 — 2026-11-22

### Analysis log panel

- A new **Analysis log** card sits under Recent activity and narrates every
  LLM run: `Run started (manual/nightly/service)` → `Collected 312 state
  changes from 14 entities, 7-day window, 3 known behaviours` →
  `Finished in 8s — refined Dad clocks off; proposed Evening handoff`, or
  why a run failed (`Run failed: no recorded history…`, shown in red). Rows
  stream in live while a run is in flight, and the last 50 lines persist
  across restarts in `.phil9000/analysis_log.yaml` — so you can see what
  the nightly run did this morning.
- New websocket commands: `phil9000/analysis_log` (fetch, newest first) and
  `phil9000/subscribe_analysis_log` (live pushes).

## v0.4.5 — 2026-11-22

### Compact domain checklist with hover hints

- Domain names in the panel's Domains card drop their bracketed examples
  ("Binary sensors (motion, doors, presence)" → **Binary sensors**), which
  was breaking the grid layout. The examples survive as a **hover tooltip**
  on each row. New pure helper `domain_label_parts()` splits label from
  hint; `phil9000/monitoring`'s `domain_options` now carry both fields.
  The setup wizard's checklist keeps the full labels — it has the room.

## v0.4.4 — 2026-11-22

### Side-by-side management panels; settings-only integration form

- The panel now lays out in two columns: **Recent activity** on the left,
  with **Domains** and **Entities** cards stacked to its right (they drop
  underneath on narrow screens). The "Monitoring" section heading is gone —
  the two cards are self-explanatory.

- The integration settings (options flow) no longer show the domain
  checklist: observation is managed **entirely** from the panel, and the
  form now holds only the analysis/notify/cooldown settings. Saving it
  cannot touch subscription data at all.

## v0.4.3 — 2026-11-22

### Entity management fully in the panel

- The Monitoring section gained the **domain subscription checklist** (tick
  whole domains on/off, live, no reload) above the searchable entity list.
  The old domain chips row is gone — the checklist is the display now.
  New `phil9000/set_domains` command.

- The integration options form dropped its "extra entities" and "never
  observe" fields — per-entity management belongs in the panel now. The
  form keeps the domain checklist and the analysis/notify settings, and
  saving it **preserves** explicit entity ids and mutes made from the
  panel instead of overwriting them.

## v0.4.2 — 2026-11-22

### Pause the feed, manage entities with checkboxes

- **Pause / Resume** beside the Recent activity title: while paused, live
  rows buffer instead of scrolling the list out from under you (the button
  shows how many are waiting); rows stay clickable, so muting a chatty
  entity mid-stream finally works. Resume prepends the buffered rows.

- **Entity browser** in the Monitoring section: a search box plus a
  scrollable checkbox list of every entity — tick to observe (unmute, or
  add as an explicit entity if its domain isn't subscribed), untick to mute.
  `added`/`muted` chips explain each state. New `phil9000/set_observed`
  command; unobserved entities untick to nothing (no phantom mutes).

- The integration options' extra-entity and exclusion fields are now
  **searchable multi-select lists** (with free-text entry for ids that have
  no state yet) instead of an entity dropdown that only ever picked one.

- The Recent activity card is capped at half the panel width so long entity
  ids stop pushing the ⊘ button around on wide screens.

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
