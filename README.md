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

- A **PHIL9000 sidebar panel** shows a live **Recent activity** feed (the newest 25 real
  state changes on observed entities, read from Home Assistant's recorder — HA's single
  central activity log), plus learned behaviours with confidence, steps, schedule and
  last-fired evidence. From the panel you can confirm, dismiss, reopen, **rename**, edit
  actions, and press **Analyse now** to run the LLM pass immediately (websocket
  commands `phil9000/behaviours`, `phil9000/activity`, `phil9000/subscribe_activity`,
  `phil9000/run_analysis`, `phil9000/set_status`, `phil9000/set_name`,
  `phil9000/set_actions`).

- **Noisy entity? Mute it.** Every activity row has a ⊘ button: the entity is added to
  the integration's exclusion list (persisted), stops being observed immediately — no
  feed rows, no behaviour steps, no LLM digest lines — and the change applies *in
  place*, without a config-entry reload, so the live feed keeps streaming. Use
  **Pause** next to the feed title when rows scroll past too fast: live rows buffer
  (the button counts them) until you hit **Resume**.

- **Domains and Entities panels**, side by side to the right of the activity feed:
  a **domain subscription checklist** (tick whole entity types on/off, live, no reload)
  and a searchable list of *every* entity with a checkbox (tick to observe: unmute or
  add explicitly; untick to mute). `added` and `muted` chips show why each entity is in
  its current state. Observation is managed **entirely from the panel** — the
  integration settings only hold the analysis/notify options.
  (`phil9000/monitoring`, `phil9000/set_observed`, `phil9000/set_domains`,
  `phil9000/mute_entity`, `phil9000/unmute_entity`)

- When a candidate pattern is first seen you also get a nudge (via the `notify` entity
  from the options, otherwise a persistent notification) with the pattern and its id.

- You can also confirm or dismiss from *Developer Tools → Actions* (or a script):
  `phil9000.confirm_behaviour` / `phil9000.dismiss_behaviour` with `behaviour_id`.

- Confirmed behaviours fire the `phil9000_behaviour_detected` event and execute their
  actions (HA service calls defined in the YAML) — deterministic Python, no LLM.

- **Optional pattern discovery**: enable *nightly pattern analysis* in the integration
  options (off by default), press **Analyse now**, or call `phil9000.run_analysis`.
  PHIL9000 summarises the rolling recorder history and the current behaviour list and
  asks the household's Assist conversation agent to do two things: *refine* existing
  candidates (tighter windows, corrected steps, sharper descriptions) and propose new
  patterns. Everything is schema-validated; proposals arrive as candidates with **no
  actions**.

- **Analysis log** under the activity feed: every LLM run reports itself — who
  triggered it (panel button, service, nightly schedule), how much history it
  collected, what it refined or proposed, how long it took, and why it failed when
  it can't run. Rows stream in live while a run is in flight and the last 50 lines
  survive restarts (`.phil9000/analysis_log.yaml`).
  (`phil9000/analysis_log`, `phil9000/subscribe_analysis_log`)

- **Accepted behaviours are hard-fixed**: the LLM may only refine candidates. Once you
  confirm (or dismiss) a behaviour, analysis passes leave it alone; genuinely different
  variants come back as new candidates. Names you set in the panel are also kept
  (`custom_name` in the YAML).

- Anything you confirm, dismiss, rename or learn survives restarts.

## Testing what PHIL9000 sees

To verify the integration is actually getting inputs:

1. Enable debug logging and reload PHIL9000:

   ```yaml
   logger:
     logs:
       custom_components.phil9000: debug
   ```

   Startup logs `PHIL9000 observing N entities (M domains), K behaviours loaded`;
   N should match what you configured (noise entities — disabled, hidden,
   diagnostic/config-category — are skipped for whole-domain subscriptions).

2. Open the **PHIL9000 panel**. Toggle a sensor (or use *Developer Tools → States →
   Set State*) and the change should appear in **Recent activity** within a second,
   as `entity  old → new` with a local timestamp.

3. What counts as an activity: a state change on an observed entity (not excluded).
   Attribute-only changes are ignored — they can never match a behaviour step and
   would flood the feed. Removing an entity is ignored.

4. Complete a candidate's step sequence and `occurrences` increments; a confirmed
   behaviour fires `phil9000_behaviour_detected` (watch it under *Developer Tools →
   Events → Listen*) and runs its actions.

If the feed stays empty: the panel shows a hint. Common causes are the recorder being
unavailable, its retention purged (default ~10 days), or a `recorder: exclude:` filter
hiding the observed entities from history.

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
    custom_name: false           # true once you rename it; LLM refinements keep the name
```

## Gotchas

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
