/**
 * PHIL9000 sidebar panel — recent activity feed, learned behaviours with
 * confidence and evidence; confirm / dismiss / reopen, rename, edit actions,
 * and an "Analyse now" button that runs the LLM pattern analysis.
 *
 * Plain JS web component, no build step and no framework. Data flows over
 * the Home Assistant websocket API (phil9000/behaviours, phil9000/activity,
 * phil9000/subscribe_activity, phil9000/analysis_log,
 * phil9000/subscribe_analysis_log, phil9000/run_analysis, phil9000/set_status,
 * phil9000/set_name, phil9000/set_actions, phil9000/monitoring,
 * phil9000/set_observed, phil9000/set_domains, phil9000/mute_entity,
 * phil9000/unmute_entity, phil9000/record_status, phil9000/record_mode,
 * phil9000/record_remove, phil9000/record_notes, phil9000/record_clear,
 * phil9000/record_propose, phil9000/record_refine, phil9000/record_save,
 * phil9000/subscribe_recording); the panel refreshes on
 * phil9000_behaviour_detected.
 */

const STATUS_RANK = { confirmed: 0, candidate: 1, dismissed: 2 };
const WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
// Rows kept client-side: matches the backend feed cap. The visible box
// shows ~25 rows and scrolls; the buffer keeps the full window (a week,
// capped) available without re-fetching.
const FEED_MAX = 1000;

const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (ch) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch],
  );

const weekdaySummary = (days) => {
  if (!days || days.length === 0 || days.length === 7) return "every day";
  return days.map((d) => WEEKDAY_NAMES[d - 1] || d).join(", ");
};

const timeWindow = (b) => {
  if (!b.time_start || !b.time_end) return "any time";
  return `${b.time_start.slice(0, 5)}–${b.time_end.slice(0, 5)}`;
};

// "*" is the any-change to_state (input_button helpers, change-only sensors).
const stateLabel = (state) => (state === "*" ? "any change" : esc(state));

const stepSummary = (b) =>
  b.steps
    .map(
      (s, i) =>
        `${i ? " → " : ""}${esc(s.entity_id)} = ${stateLabel(s.to_state)}` +
        (i && s.max_gap_seconds ? ` (≤${Math.round(s.max_gap_seconds / 60)}m)` : ""),
    )
    .join("");

const actionLabel = (a) =>
  `${esc(a.domain)}.${esc(a.service)}${a.entity_id ? ` → ${esc(a.entity_id)}` : ""}` +
  (a.data && Object.keys(a.data).length ? `  ${esc(JSON.stringify(a.data))}` : "");

const formatWhen = (iso) => {
  const date = new Date(iso);
  return isNaN(date) ? iso : date.toLocaleString();
};

const formatClock = (iso) => {
  const date = new Date(iso);
  if (isNaN(date)) return iso;
  const today = new Date().toDateString() === date.toDateString();
  return today ? date.toLocaleTimeString() : date.toLocaleString();
};

const STYLE = `
  <style>
    :host { display: block; padding: 16px; color: var(--primary-text-color);
            font-family: Roboto, sans-serif; }
    header { display: flex; align-items: baseline; gap: 12px; margin-bottom: 4px;
             flex-wrap: wrap; }
    header h1 { font-size: 20px; font-weight: 500; margin: 0; }
    .meta { color: var(--secondary-text-color); font-size: 12px; margin: 0 0 16px; }
    .meta code { font-size: 11px; }
    button { border: none; border-radius: 6px; padding: 6px 14px; cursor: pointer;
             font-size: 13px; background: var(--primary-color); color: #fff; }
    button.secondary { background: var(--secondary-background-color);
             color: var(--primary-text-color); border: 1px solid var(--divider-color); }
    button.danger { background: #c0392b; }
    button:disabled { opacity: .5; cursor: default; }
    .card { background: var(--card-background-color); border-radius: 10px;
            padding: 14px 16px; margin-bottom: 12px;
            box-shadow: 0 1px 3px rgba(0,0,0,.12); }
    .card h3 { margin: 0 0 2px; font-size: 16px; font-weight: 500; }
    .pill { display: inline-block; font-size: 11px; padding: 2px 8px;
            border-radius: 10px; margin-left: 8px; vertical-align: middle; }
    .pill.confirmed { background: rgba(46,125,50,.15); color: #2e7d32; }
    .pill.candidate { background: rgba(239,108,0,.15); color: #ef6c00; }
    .pill.dismissed { background: rgba(120,120,120,.18); color: #757575; }
    .id { font-family: monospace; font-size: 11px; color: var(--secondary-text-color); }
    .conf { display: flex; align-items: center; gap: 8px; margin: 8px 0 4px;
            font-size: 12px; color: var(--secondary-text-color); }
    .bar { flex: 0 0 120px; height: 6px; border-radius: 3px;
           background: var(--divider-color); overflow: hidden; }
    .bar i { display: block; height: 100%; background: var(--primary-color); }
    .steps { font-size: 13px; margin: 6px 0; }
    .lines { font-size: 12px; color: var(--secondary-text-color); margin: 4px 0; }
    .actions-list { font-size: 12px; margin: 6px 0; padding-left: 0; list-style: none; }
    .actions-list li { font-family: monospace; margin: 2px 0; }
    .row { display: flex; gap: 8px; margin-top: 10px; flex-wrap: wrap; }
    .editor { margin-top: 10px; border-top: 1px solid var(--divider-color);
              padding-top: 10px; }
    .editor .action-row { display: grid; gap: 6px; margin-bottom: 6px;
              grid-template-columns: 1fr 1fr 1.4fr 2fr auto; }
    .editor input { padding: 6px 8px; border: 1px solid var(--divider-color);
              border-radius: 6px; background: var(--card-background-color);
              color: var(--primary-text-color); font-size: 12px; min-width: 0; }
    .error { background: rgba(192,57,43,.12); color: #c0392b; padding: 10px 14px;
             border-radius: 8px; margin-bottom: 12px; font-size: 13px; }
    .note { background: rgba(25,118,210,.10); color: #1976d2; padding: 10px 14px;
            border-radius: 8px; margin-bottom: 12px; font-size: 13px; }
    .warn { background: rgba(230,126,34,.12); color: #e67e22; padding: 10px 14px;
            border-radius: 8px; margin-bottom: 12px; font-size: 13px; }
    .running { background: rgba(96,125,139,.12); color: #607d8b; padding: 10px 14px;
               border-radius: 8px; margin-bottom: 12px; font-size: 13px; }
    .running::before { content: "◌ "; display: inline-block;
               animation: phil-spin 1.2s linear infinite; }
    @keyframes phil-spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
    .section { font-size: 14px; font-weight: 500; margin: 18px 0 8px;
               color: var(--secondary-text-color); text-transform: uppercase;
               letter-spacing: .05em; }
    .act-list { list-style: none; margin: 0; padding: 0; font-size: 13px;
                /* Full-size table: always 25 rows tall (the feed's cap),
                   stable whether the day was quiet or busy. A wrapped row
                   (long entity id) would exceed the budget, so it scrolls. */
                height: calc(25 * 23px); box-sizing: border-box; overflow-y: auto; }
    .act-list li { display: flex; gap: 10px; padding: 3px 0; align-items: baseline;
                   border-bottom: 1px solid var(--divider-color); }
    .act-list li:last-child { border-bottom: none; }
    .act-list .empty { border-bottom: none; }
    .act-time { font-family: monospace; font-size: 12px; color: var(--secondary-text-color);
                flex: 0 0 86px; }
    .act-entity { font-family: monospace; font-size: 12px; overflow-wrap: anywhere; }
    .act-change { color: var(--secondary-text-color); }
    .log-list { list-style: none; margin: 0; padding: 0; font-size: 13px; }
    .log-list li { display: flex; gap: 10px; padding: 3px 0; align-items: baseline;
                   border-bottom: 1px solid var(--divider-color); }
    .log-list li:last-child { border-bottom: none; }
    .log-list .log-msg { flex: 1 1 auto; overflow-wrap: anywhere; }
    .log-list .log-error .log-msg { color: var(--error-color, #db4437); }
    .log-list .empty { border-bottom: none; }
    .section-head { display: flex; align-items: baseline; gap: 12px; }
    .section-head .section { margin-bottom: 0; }
    .section-head button { font-size: 12px; padding: 3px 12px; }
    .section-head input { font-size: 12px; padding: 3px 8px; width: 230px;
      margin-right: -4px; }
    .top-grid { display: flex; gap: 24px; align-items: flex-start; flex-wrap: wrap; }
    #activity-col { flex: 3 1 430px; min-width: 320px; }
    #manage-col { flex: 2 1 340px; min-width: 300px; }
    .mute-btn { flex: 0 0 auto; margin-left: auto; padding: 0 9px; font-size: 13px;
                line-height: 1.5; opacity: .65; }
    .mute-btn:hover { opacity: 1; }
    .domain-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
                   gap: 0 12px; }
    .domain-grid .ent-row { font-size: 12px; padding: 2px 0; }
    .entity-search { width: 100%; box-sizing: border-box; padding: 7px 10px;
                     border: 1px solid var(--divider-color); border-radius: 8px;
                     background: var(--card-background-color);
                     color: var(--primary-text-color); font-size: 13px;
                     margin: 8px 0; }
    .ent-list { max-height: 340px; overflow-y: auto; border-top: 1px solid
                var(--divider-color); }
    .ent-row { display: flex; gap: 8px; align-items: center; font-size: 13px;
               padding: 3px 2px; cursor: pointer; }
    .ent-row span { font-family: monospace; overflow-wrap: anywhere; }
    .ent-row input[type="checkbox"] { flex: 0 0 auto; cursor: pointer; }
    .mini-chip { font-size: 10px; padding: 1px 6px; border-radius: 8px;
                 background: var(--divider-color); color: var(--secondary-text-color); }
    .mini-chip.muted { background: rgba(192,57,43,.14); color: #c0392b; }
    .name-input { font-size: 16px; padding: 4px 8px; width: 300px; max-width: 70%;
                  border: 1px solid var(--divider-color); border-radius: 6px;
                  background: var(--card-background-color);
                  color: var(--primary-text-color); }
    .empty { color: var(--secondary-text-color); font-size: 14px; }
    button.armed { background: #c0392b; animation: phil-pulse 1.6s ease-in-out infinite; }
    @keyframes phil-pulse { 50% { opacity: .65; } }
    .rec-card { background: var(--card-background-color); border-radius: 10px;
                padding: 12px 16px; margin-bottom: 12px;
                box-shadow: 0 1px 3px rgba(0,0,0,.12); }
    .rec-card h4 { margin: 0 0 2px; font-size: 15px; font-weight: 500; }
    .rec-heading { font-size: 12px; font-weight: 500; text-transform: uppercase;
                   letter-spacing: .05em; color: var(--secondary-text-color);
                   margin: 10px 0 4px; }
    .rec-list { list-style: none; margin: 0; padding: 0; font-size: 13px;
                max-height: 190px; overflow-y: auto; }
    .rec-list li { display: flex; gap: 10px; padding: 3px 0;
                   align-items: baseline; border-bottom: 1px solid var(--divider-color); }
    .rec-list li:last-child { border-bottom: none; }
    .rec-hint { font-size: 12px; color: var(--secondary-text-color); }
    .rec-notes { width: 100%; box-sizing: border-box; min-height: 54px;
                 padding: 7px 10px; border: 1px solid var(--divider-color);
                 border-radius: 8px; background: var(--card-background-color);
                 color: var(--primary-text-color); font-size: 13px; margin: 4px 0;
                 font-family: inherit; }
    .proposal { border: 1px dashed var(--divider-color); border-radius: 8px;
                padding: 10px 12px; margin: 10px 0; font-size: 13px; }
    .refine-row { display: flex; gap: 8px; margin-top: 8px; }
    .refine-row input { flex: 1; padding: 6px 8px; border: 1px solid var(--divider-color);
                border-radius: 6px; background: var(--card-background-color);
                color: var(--primary-text-color); font-size: 12px; min-width: 0; }
    .rec-mark { flex: 0 0 16px; font-size: 12px; }
    .rec-mark.trigger { color: #c0392b; }
    .rec-mark.action { color: #e67e22; }
  </style>`;

class Phil9000Panel extends HTMLElement {
  constructor() {
    super();
    // Shadow DOM: the panel's styles (button, .card, .error ...) would
    // otherwise leak into the whole Home Assistant document, and HA's own
    // styles could bleed in. Custom properties still inherit through the
    // shadow boundary, so HA themes keep working.
    this.shadow = this.attachShadow({ mode: "open" });
    this._initialized = false;
  }

  set hass(hass) {
    this._hass = hass;
    if (this._initialized) return;
    this._initialized = true;
    this._behaviours = null;
    this._meta = { path: "", observed_entities: [] };
    this._error = null;
    this._busy = false;
    this._editing = null; // behaviour_id while its action editor is open
    this._draft = []; // actions being edited (copy of stored actions)
    this._renaming = null; // behaviour_id while its name input is open
    this._activity = [];
    this._activityFilter = ""; // live filter over the loaded feed rows
    this._activityHint = "";
    this._paused = false; // live rows buffer while the user inspects the feed
    this._pending = [];
    this._analysing = false;
    this._analysisNote = null;
    this._noteKind = "note"; // banner class for the note: "note" or "warn"
    this._analysisLog = null; // entries from phil9000/analysis_log (newest first)
    this._monitoring = null;
    this._entityFilter = "";
    // Recording (teach-by-demonstration): session snapshot, in-flight LLM
    // proposal flag, and the text the user has typed into the notes/refine
    // boxes but not yet sent (so live pushes don't wipe it).
    this._recording = null;
    this._proposing = false;
    this._notesDraft = null;
    this._refineDraft = "";
    // Confirmed behaviours execute service calls, so amending them (and
    // subscriptions/analysis) is admin-only on the backend; reflect that.
    this._readOnly = hass.user && hass.user.is_admin === false;

    this.shadow.innerHTML = `${STYLE}<header><h1>PHIL9000</h1>
      <button data-act="analyse" ${this._analysing || this._readOnly ? "disabled" : ""}>Analyse now</button>
      <button data-act="record-arm" data-mode="trigger" ${this._readOnly ? "disabled" : ""}
        title="Capture the state changes that should trigger the automation">● Record trigger</button>
      <button data-act="record-arm" data-mode="action" ${this._readOnly ? "disabled" : ""}
        title="Capture the changes the automation should replay">● Record action</button>
      <button class="secondary" data-act="record-arm" data-mode="off" ${this._readOnly ? "disabled" : ""}
        title="Stop capturing">Stop</button></header>
      <p class="meta"></p><div id="banner"></div>
      <div class="top-grid">
        <div id="activity-col">
          <div class="section-head"><h2 class="section">Recent activity</h2>
            <input class="activity-search" type="search" placeholder="Filter — entity or state…" />
            <button class="secondary" data-act="pause">Pause</button></div>
          <div id="recording"></div>
          <div id="activity"><div class="card"><ul class="act-list">
            <li class="empty">Loading…</li>
          </ul></div></div>
          <h2 class="section">Analysis log</h2>
          <div class="card"><ul class="log-list" id="analysis-log">
            <li class="empty">Loading…</li>
          </ul></div>
        </div>
        <div id="manage-col">
          <h2 class="section">Domains</h2>
          <div class="card"><div class="domain-grid" id="domain-list">
            <p class="empty">Loading…</p>
          </div></div>
          <h2 class="section">Entities</h2>
          <div class="card">
            <input class="entity-search" placeholder="Search — tick to observe, untick to mute…" />
            <div class="ent-list" id="entity-list"><p class="empty">Loading…</p></div>
          </div>
        </div>
      </div>
      <h2 class="section">Behaviours</h2><div id="body"><p class="empty">Loading…</p></div>`;
    this.shadow.addEventListener("click", (ev) => this._onClick(ev));
    this.shadow.addEventListener("input", (ev) => {
      if (ev.target.classList.contains("entity-search")) {
        this._entityFilter = ev.target.value.trim().toLowerCase();
        this._renderEntityList();
      }
      if (ev.target.classList.contains("activity-search")) {
        this._activityFilter = ev.target.value.trim().toLowerCase();
        this._renderActivity();
      }
      if (ev.target.id === "rec-notes") this._notesDraft = ev.target.value;
      if (ev.target.classList.contains("refine-input")) this._refineDraft = ev.target.value;
    });
    this.shadow.addEventListener("change", (ev) => {
      const box = ev.target.closest("input[data-act='set-observed']");
      if (box) {
        this._setObserved(box.dataset.entity, box.checked);
        return;
      }
      if (ev.target.closest("input[data-act='set-domain']")) this._setDomains();
    });
    this.shadow.addEventListener("keydown", (ev) => {
      if (ev.target.classList.contains("name-input")) {
        if (ev.key === "Enter") ev.target.closest(".card").querySelector('[data-act="save-name"]').click();
        if (ev.key === "Escape") {
          this._renaming = null;
          this._render();
        }
      }
      if (ev.target.classList.contains("refine-input") && ev.key === "Enter") {
        const btn = this.shadow.querySelector('[data-act="rec-refine"]');
        if (btn && !btn.disabled) btn.click();
      }
    });

    // HA's frontend keeps one websocket connection, so subscriptions made
    // here must be dropped when the panel unmounts — otherwise every visit
    // to the panel adds another set of live callbacks (and one more
    // phil9000/behaviours fetch per event) that never stop firing.
    this._unsubscribes = [];
    this._unsubscribes.push(
      hass.connection.subscribeEvents(
        () => this._refresh(),
        "phil9000_behaviour_detected",
      ),
    );
    const track = (promise) => {
      promise.catch(() => {}); // a failed subscribe must not leak as unhandled
      this._unsubscribes.push(promise);
    };
    track(
      hass.connection.subscribeMessage(
        (row) => {
          if (this._paused) {
            this._pending.unshift(row);
            this._pending = this._pending.slice(0, FEED_MAX);
            this._updatePauseBtn();
            return;
          }
          this._activity.unshift(row);
          this._activity = this._activity.slice(0, FEED_MAX);
          this._activityHint = "";
          this._renderActivity();
        },
        { type: "phil9000/subscribe_activity" },
      ),
    );
    track(
      hass.connection.subscribeMessage(
        (entry) => {
          this._analysisLog = [entry, ...(this._analysisLog || [])].slice(0, 50);
          // Every entry carries the post-event run state, so a run started
          // elsewhere (service, nightly, another tab) disables the button and
          // shows the running status here too.
          this._analysing = !!entry.running;
          this._renderAnalysisLog();
          this._renderBanner();
          this._updateAnalyseBtn();
        },
        { type: "phil9000/subscribe_analysis_log" },
      ),
    );
    track(
      hass.connection.subscribeMessage(
        (session) => {
          // One full recording snapshot per change (mode, capture, removal,
          // notes, proposal) — also delivered when another panel acts.
          this._recording = session;
          this._renderRecording();
          this._renderActivity();
        },
        { type: "phil9000/subscribe_recording" },
      ),
    );
    this._refresh();
    this._reloadActivity();
    this._loadAnalysisLog();
    this._loadMonitoring();
    this._loadRecording();
  }

  disconnectedCallback() {
    (this._unsubscribes || []).forEach((promise) => {
      promise.then((unsub) => unsub && unsub()).catch(() => {});
    });
    this._unsubscribes = [];
  }

  async _reloadActivity(replace) {
    let fetched = [];
    try {
      const result = await this._hass.connection.sendMessagePromise({
        type: "phil9000/activity",
      });
      fetched = result.items || [];
      this._activityHint = result.hint || "";
    } catch (err) {
      this._activityHint = err.message || String(err);
    }
    if (replace) {
      // The observed set just changed (domains/entities): show exactly the
      // new window, dropping rows for entities no longer observed. Live
      // rows arrive via the subscription as before.
      this._activity = fetched.slice(0, FEED_MAX);
      this._pending = [];
      this._updatePauseBtn();
      this._renderActivity();
      return;
    }
    // Rows pushed live since subscribing may not be committed to the
    // recorder yet (it writes asynchronously): merge them in instead of
    // overwriting, deduped on (at, entity), newest first, capped.
    const seen = new Set(fetched.map((r) => `${r.at}|${r.entity_id}`));
    const liveOnly = (this._activity || []).filter(
      (r) => !seen.has(`${r.at}|${r.entity_id}`),
    );
    this._activity = [...fetched, ...liveOnly]
      .sort((a, b) => (a.at < b.at ? 1 : a.at > b.at ? -1 : 0))
      .slice(0, FEED_MAX);
    this._renderActivity();
  }

  _renderActivity() {
    const el = this.shadow.querySelector("#activity");
    if (!el) return;
    const q = this._activityFilter;
    // Each whitespace-separated term must appear in the entity ID or either
    // state — so "appletv playing" finds apple_tv rows that changed to/from
    // playing, and a lone "appletv" behaves as a plain entity filter.
    const rows = q
      ? this._activity.filter((r) =>
          q.split(/\s+/).every((t) =>
            [r.entity_id, r.old_state ?? "", r.new_state ?? ""]
              .join(" ")
              .toLowerCase()
              .includes(t),
          ),
        )
      : this._activity;
    if (rows.length === 0) {
      // The card frame is always present — an empty feed should look like
      // a card with a hint in it, not a collapsed hole in the layout.
      const message = q
        ? `No events for “${esc(q)}” in the last ${this._activity.length} feed rows.`
        : this._activityHint ||
          "No state changes observed yet. Trigger a sensor (or use Developer Tools → " +
            "Set State) and it should appear here within a second.";
      el.innerHTML = `<div class="card"><ul class="act-list">
        <li class="empty">${esc(message)}</li></ul></div>`;
      return;
    }
    // Mark rows the recording has captured (● trigger, ⚡ action) so the
    // demonstration is visible in the context of the raw feed.
    const marks = new Map();
    if (this._recording) {
      for (const i of this._recording.triggers || []) marks.set(`${i.at}|${i.entity_id}`, "trigger");
      for (const i of this._recording.actions || []) marks.set(`${i.at}|${i.entity_id}`, "action");
    }
    el.innerHTML = `<div class="card"><ul class="act-list">${rows
      .map((row) => {
        const mark = marks.get(`${row.at}|${row.entity_id}`);
        return `<li>
          ${mark ? `<span class="rec-mark ${mark}" title="captured as a recorded ${mark}">${mark === "trigger" ? "●" : "⚡"}</span>` : '<span class="rec-mark"></span>'}
          <span class="act-time">${esc(formatClock(row.at))}</span>
          <span class="act-entity">${esc(row.entity_id)}</span>
          <span class="act-change">${
            row.event
              ? `🔘 fired <b>${esc(row.new_state)}</b>`
              : `${esc(row.old_state ?? "—")} → <b>${esc(row.new_state)}</b>`
          }</span>
          <button class="secondary mute-btn" data-act="mute" data-entity="${esc(row.entity_id)}"
            ${this._readOnly ? "disabled" : ""}
            title="Mute ${esc(row.entity_id)} — stop observing it (revert in the Entities panel)">⊘</button>
        </li>`;
      })
      .join("")}</ul></div>`;
  }

  async _loadAnalysisLog() {
    try {
      const result = await this._hass.connection.sendMessagePromise({
        type: "phil9000/analysis_log",
      });
      this._analysisLog = result.entries || [];
      // A run already in flight (service/nightly/another tab) shows its
      // status from the moment the panel opens.
      this._analysing = !!result.running;
    } catch (err) {
      this._analysisLog = [];
    }
    this._renderAnalysisLog();
    this._renderBanner();
    this._updateAnalyseBtn();
  }

  _renderAnalysisLog() {
    const el = this.shadow.querySelector("#analysis-log");
    if (!el) return;
    const entries = this._analysisLog || [];
    if (entries.length === 0) {
      el.innerHTML =
        `<li class="empty">No analysis runs yet — press “Analyse now” above, ` +
        `or wait for the nightly run.</li>`;
      return;
    }
    el.innerHTML = entries
      .map(
        (e) => `<li class="log-${e.level === "error" ? "error" : "info"}">
          <span class="act-time">${esc(formatClock(e.at))}</span>
          <span class="log-msg">${esc(e.message)}</span>
        </li>`,
      )
      .join("");
  }

  _togglePause() {
    this._paused = !this._paused;
    if (!this._paused && this._pending.length) {
      this._activity = [...this._pending, ...this._activity].slice(0, FEED_MAX);
      this._pending = [];
      this._renderActivity();
    }
    this._updatePauseBtn();
  }

  _updatePauseBtn() {
    const btn = this.shadow.querySelector('[data-act="pause"]');
    if (!btn) return;
    btn.textContent = this._paused
      ? `Resume${this._pending.length ? ` (${this._pending.length})` : ""}`
      : "Pause";
  }

  // -- recording (teach a rule by demonstration) -------------------------

  async _loadRecording() {
    try {
      this._recording = await this._hass.connection.sendMessagePromise({
        type: "phil9000/record_status",
      });
    } catch (err) {
      /* keep whatever snapshot we have */
    }
    this._renderRecording();
  }

  async _recordSend(payload) {
    this._busy = true;
    this._renderBanner();
    this._renderRecording();
    try {
      const result = await this._hass.connection.sendMessagePromise(payload);
      if (result && Array.isArray(result.triggers)) this._recording = result;
      this._error = null;
    } catch (err) {
      this._error = err.message || String(err);
    }
    this._busy = false;
    this._renderBanner();
    this._renderRecording();
  }

  _recordArm(mode) {
    // Pressing the armed mode again disarms it.
    const current = this._recording ? this._recording.mode : null;
    return this._recordSend({
      type: "phil9000/record_mode",
      mode: current === mode ? "off" : mode,
    });
  }

  _recordNotes() {
    const el = this.shadow.querySelector("#rec-notes");
    this._notesDraft = null;
    return this._recordSend({ type: "phil9000/record_notes", notes: el ? el.value : "" });
  }

  async _recordPropose(message) {
    this._proposing = true;
    this._error = null;
    this._renderRecording();
    try {
      await this._hass.connection.sendMessagePromise(
        message
          ? { type: "phil9000/record_refine", message }
          : { type: "phil9000/record_propose" },
      );
      if (message) this._refineDraft = "";
    } catch (err) {
      this._error = err.message || String(err);
    }
    this._proposing = false;
    await this._loadRecording();
    this._renderBanner();
  }

  async _recordSave(direct) {
    this._busy = true;
    this._error = null;
    this._renderBanner();
    this._renderRecording();
    try {
      const res = await this._hass.connection.sendMessagePromise({
        type: "phil9000/record_save",
        direct,
      });
      this._recording = await this._hass.connection.sendMessagePromise({
        type: "phil9000/record_status",
      });
      this._notesDraft = null;
      this._refineDraft = "";
      this._noteKind = "note";
      this._analysisNote = `Automation created from the recording (${res.behaviour_id}) — confirm, rename or edit its actions in the Behaviours list below.`;
    } catch (err) {
      this._error = err.message || String(err);
    }
    this._busy = false;
    this._render();
  }

  _recordClear() {
    this._notesDraft = null;
    this._refineDraft = "";
    return this._recordSend({ type: "phil9000/record_clear" });
  }

  _renderRecording() {
    const el = this.shadow.querySelector("#recording");
    if (!el) return;
    const s = this._recording;
    const mode = s ? s.mode : null;
    this.shadow.querySelectorAll("[data-act='record-arm']").forEach((btn) => {
      btn.classList.toggle("armed", !!mode && btn.dataset.mode === mode);
    });
    const empty =
      !s ||
      (!mode &&
        !(s.triggers || []).length &&
        !(s.actions || []).length &&
        !s.notes &&
        !s.proposal);
    if (empty) {
      el.innerHTML = "";
      return;
    }
    const locked = this._readOnly || this._busy;
    const itemRow = (i, kind) => `<li>
      <span class="act-time">${esc(formatClock(i.at))}</span>
      <span class="act-entity">${esc(i.entity_id)}</span>
      <span class="act-change">${esc(i.from_state ?? "—")} → <b>${esc(i.to_state)}</b></span>
      <button class="danger mute-btn" data-act="rec-remove" data-kind="${kind}"
        data-item="${esc(i.item_id)}" ${locked ? "disabled" : ""}
        title="Remove this recorded ${kind} — it will not be part of the automation">×</button>
    </li>`;
    const list = (items, kind) =>
      (items || []).length
        ? `<ul class="rec-list">${items.map((i) => itemRow(i, kind)).join("")}</ul>`
        : `<div class="rec-hint">nothing captured yet</div>`;
    const notes = this._notesDraft !== null ? this._notesDraft : s.notes || "";
    let html = `<div class="rec-card">`;
    if (mode === "trigger")
      html += `<div class="running">Recording triggers — go and do the thing that should set the automation off.</div>`;
    if (mode === "action")
      html += `<div class="running">Recording actions — now change what the automation should replay later.</div>`;
    html += `<div class="rec-heading">Recorded triggers</div>${list(s.triggers, "trigger")}`;
    html += `<div class="rec-heading">Recorded actions</div>${list(s.actions, "action")}`;
    html += `<div class="rec-heading">Notes for the proposal</div>
      <textarea id="rec-notes" class="rec-notes" ${locked ? "disabled" : ""}
        placeholder="What should the LLM know? e.g. weekdays only; dim the strip to 30% warm…">${esc(notes)}</textarea>
      <div class="row"><button class="secondary" data-act="rec-notes-save" ${locked ? "disabled" : ""}>Save notes</button></div>`;
    if (s.proposal) html += this._proposalCard(s.proposal);
    const busy = locked || this._proposing;
    html += `<div class="row">
      <button data-act="rec-propose" ${busy ? "disabled" : ""}>✨ Propose automation</button>
      <button class="secondary" data-act="rec-save-direct" ${busy ? "disabled" : ""}
        title="Skip the LLM: triggers as recorded, on/off actions mapped mechanically">Save as-is (no LLM)</button>
      <button class="danger" data-act="rec-clear" ${busy ? "disabled" : ""}>Discard</button>
    </div>`;
    if (this._proposing)
      html += `<div class="running">Asking the model — this can take a minute; progress appears in the Analysis log.</div>`;
    html += `</div>`;
    el.innerHTML = html;
  }

  _proposalCard(p) {
    const steps = (p.steps || [])
      .map(
        (s, i) =>
          `${i ? " → " : ""}${esc(s.entity_id)} = ${stateLabel(s.to_state)}` +
          (i ? ` (≤${Math.round((s.max_gap_seconds || 300) / 60)}m)` : ""),
      )
      .join("");
    const actions = p.actions || [];
    const window =
      p.time_start && p.time_end ? `${p.time_start}–${p.time_end} UTC` : "any time";
    const busy = this._readOnly || this._busy || this._proposing;
    return `<div class="proposal">
      <div class="rec-heading">Proposed automation</div>
      <h4>${esc(p.name)}</h4>
      ${p.description ? `<div class="lines">${esc(p.description)}</div>` : ""}
      <div class="steps">${steps}</div>
      <div class="lines">${weekdaySummary(p.weekdays)} · ${window} ·
        cooldown ${Math.round((p.cooldown_seconds || 3600) / 60)} min</div>
      ${actions.length ? `<ul class="actions-list">${actions.map((a) => `<li>${actionLabel(a)}</li>`).join("")}</ul>` : `<div class="lines">no actions proposed</div>`}
      ${p.reply ? `<div class="lines">“${esc(p.reply)}”</div>` : ""}
      <div class="refine-row">
        <input class="refine-input" value="${esc(this._refineDraft)}" ${busy ? "disabled" : ""}
          placeholder="tell me to change it — e.g. only on weekdays, and add 5 minutes…" />
        <button data-act="rec-refine" ${busy ? "disabled" : ""}>Refine</button>
      </div>
      <div class="row">
        <button data-act="rec-save" ${busy ? "disabled" : ""}>✔ Create automation</button>
      </div>
    </div>`;
  }

  async _loadMonitoring() {
    try {
      this._monitoring = await this._hass.connection.sendMessagePromise({
        type: "phil9000/monitoring",
      });
    } catch (err) {
      this._error = err.message || String(err);
      this._monitoring = null;
      this._render();
    }
    this._renderMonitoring();
  }

  _renderMonitoring() {
    const domainList = this.shadow.querySelector("#domain-list");
    if (!domainList) return;
    const m = this._monitoring;
    if (!m) {
      domainList.innerHTML = "";
      const entityList = this.shadow.querySelector("#entity-list");
      if (entityList) entityList.innerHTML = "";
      return;
    }
    const checked = new Set(m.domains || []);
    domainList.innerHTML = (m.domain_options || [])
      .map(
        (d) => `<label class="ent-row" ${d.hint ? `title="${esc(d.hint)}"` : ""}>
          <input type="checkbox" data-act="set-domain" data-domain="${esc(d.value)}"
            ${this._readOnly ? "disabled" : ""}
            ${checked.has(d.value) ? "checked" : ""} />
          <span>${esc(d.label)}</span>
        </label>`,
      )
      .join("");
    this._renderEntityList();
  }

  async _setDomains() {
    const domains = [...this.shadow.querySelectorAll("input[data-act='set-domain']:checked")].map(
      (box) => box.dataset.domain,
    );
    try {
      await this._hass.connection.sendMessagePromise({
        type: "phil9000/set_domains",
        domains,
      });
    } catch (err) {
      this._error = err.message || String(err);
      this._render();
    }
    this._loadMonitoring();
    this._reloadActivity(true);
  }

  _renderEntityList() {
    const el = this.shadow.querySelector("#entity-list");
    if (!el || !this._monitoring) return;
    const m = this._monitoring;
    const observed = new Set(m.observed || []);
    const explicit = new Set(m.entities || []);
    const muted = new Set(m.muted || []);
    const all = Object.keys(this._hass.states).sort();
    const matches = this._entityFilter
      ? all.filter((e) => e.includes(this._entityFilter))
      : all;
    const shown = matches.slice(0, 300);
    el.innerHTML =
      shown
        .map(
          (e) => `<label class="ent-row">
            <input type="checkbox" data-act="set-observed" data-entity="${esc(e)}"
              ${this._readOnly ? "disabled" : ""}
              ${observed.has(e) ? "checked" : ""} />
            <span>${esc(e)}</span>
            ${explicit.has(e) ? '<code class="mini-chip">added</code>' : ""}
            ${muted.has(e) ? '<code class="mini-chip muted">muted</code>' : ""}
          </label>`,
        )
        .join("") +
      (matches.length > shown.length
        ? `<div class="lines">+${matches.length - shown.length} more — keep typing</div>`
        : "");
  }

  async _setObserved(entityId, observed) {
    try {
      await this._hass.connection.sendMessagePromise({
        type: "phil9000/set_observed",
        entity_id: entityId,
        observed,
      });
      if (!observed) {
        // The backend stopped observing it; clear its rows from feed + buffer.
        this._activity = this._activity.filter((r) => r.entity_id !== entityId);
        this._pending = this._pending.filter((r) => r.entity_id !== entityId);
        this._renderActivity();
      }
    } catch (err) {
      this._error = err.message || String(err);
      this._render();
    }
    this._loadMonitoring();
    this._reloadActivity(true);
  }

  async _analyse() {
    this._analysing = true;
    this._analysisNote = null;
    this._error = null;
    this._render();
    try {
      const res = await this._hass.connection.sendMessagePromise({
        type: "phil9000/run_analysis",
      });
      const parts = [];
      if (res.refined && res.refined.length) parts.push(`Refined: ${res.refined.join(", ")}`);
      if (res.new && res.new.length) parts.push(`New: ${res.new.join(", ")}`);
      this._noteKind = "note";
      this._analysisNote = parts.length
        ? parts.join(" · ")
        : "No new convincing patterns this time.";
    } catch (err) {
      const msg = err.message || String(err);
      if (msg.includes("already in progress")) {
        // A skip is not an outcome: say so plainly and in a distinct colour,
        // pointing at the run that IS going.
        this._noteKind = "warn";
        this._analysisNote =
          "Skipped — another analysis run is already in progress; " +
          "watch the Analysis log below for its result.";
      } else {
        this._noteKind = "note";
        this._analysisNote = `Analysis failed: ${msg}`;
      }
    }
    this._analysing = false;
    await this._refresh();
  }

  async _refresh() {
    try {
      const result = await this._hass.connection.sendMessagePromise({
        type: "phil9000/behaviours",
      });
      this._behaviours = result.behaviours;
      this._meta = { path: result.path, observed_entities: result.observed_entities };
      this._error = null;
    } catch (err) {
      this._error = err.message || String(err);
    }
    this._render();
  }

  _send(payload) {
    this._busy = true;
    this._render();
    return this._hass.connection
      .sendMessagePromise(payload)
      .catch((err) => {
        this._error = err.message || String(err);
      })
      .finally(() => {
        this._busy = false;
        this._refresh();
      });
  }

  _collectDraft() {
    // Pull current input values into the draft before mutating it.
    if (!this._editing) return;
    this.shadow.querySelectorAll(".action-row").forEach((row) => {
      const idx = Number(row.dataset.idx);
      this._draft[idx] = {
        domain: row.querySelector('[data-field="domain"]').value.trim(),
        service: row.querySelector('[data-field="service"]').value.trim(),
        entity_id: row.querySelector('[data-field="entity_id"]').value.trim(),
        dataRaw: row.querySelector('[data-field="data"]').value,
      };
    });
  }

  async _saveDraft() {
    this._collectDraft();
    const actions = [];
    for (const a of this._draft) {
      if (!a.domain || !a.service) continue;
      let data = {};
      const raw =
        a.dataRaw !== undefined
          ? a.dataRaw
          : a.data && Object.keys(a.data).length
            ? JSON.stringify(a.data)
            : "";
      if (raw.trim()) {
        try {
          data = JSON.parse(raw);
        } catch (err) {
          this._error = `Action ${actions.length + 1}: data is not valid JSON (${err.message})`;
          this._render();
          return;
        }
      }
      actions.push({ domain: a.domain, service: a.service, entity_id: a.entity_id, data });
    }
    this._editing = null;
    await this._send({
      type: "phil9000/set_actions",
      behaviour_id: this._saveTarget,
      actions,
    });
  }

  async _onClick(ev) {
    const btn = ev.target.closest("[data-act]");
    if (!btn || this._busy) return;
    const act = btn.dataset.act;
    const id = btn.dataset.id;
    if (act === "analyse") return this._analyse();
    if (act === "pause") return this._togglePause();
    if (act === "mute") return this._setObserved(btn.dataset.entity, false);
    if (act === "record-arm") return this._recordArm(btn.dataset.mode);
    if (act === "rec-remove")
      return this._recordSend({
        type: "phil9000/record_remove",
        kind: btn.dataset.kind,
        item_id: btn.dataset.item,
      });
    if (act === "rec-notes-save") return this._recordNotes();
    if (act === "rec-propose") return this._recordPropose(null);
    if (act === "rec-refine") {
      const input = this.shadow.querySelector(".refine-input");
      const message = input ? input.value.trim() : "";
      if (!message) return undefined;
      return this._recordPropose(message);
    }
    if (act === "rec-save") return this._recordSave(false);
    if (act === "rec-save-direct") return this._recordSave(true);
    if (act === "rec-clear") {
      if (!window.confirm("Discard the recorded demonstration?")) return undefined;
      return this._recordClear();
    }
    if (act === "rename") {
      this._renaming = id;
      return this._render();
    }
    if (act === "cancel-name") {
      this._renaming = null;
      return this._render();
    }
    if (act === "save-name") {
      const input = this.shadow.querySelector(".name-input");
      const name = input ? input.value.trim() : "";
      if (!name) return undefined;
      this._renaming = null;
      return this._send({ type: "phil9000/set_name", behaviour_id: id, name });
    }
    if (act === "confirm") return this._send({ type: "phil9000/set_status", behaviour_id: id, status: "confirmed" });
    if (act === "dismiss") return this._send({ type: "phil9000/set_status", behaviour_id: id, status: "dismissed" });
    if (act === "reopen") return this._send({ type: "phil9000/set_status", behaviour_id: id, status: "candidate" });
    if (act === "edit-actions") {
      const behaviour = this._behaviours.find((b) => b.id === id);
      this._editing = id;
      this._draft = JSON.parse(JSON.stringify(behaviour.actions || []));
      return this._render();
    }
    if (act === "add-action") {
      this._collectDraft();
      this._draft.push({ domain: "", service: "", entity_id: "", data: {} });
      return this._render();
    }
    if (act === "remove-action") {
      this._collectDraft();
      this._draft.splice(Number(btn.dataset.idx), 1);
      return this._render();
    }
    if (act === "save-actions") {
      this._saveTarget = id;
      return this._saveDraft();
    }
    if (act === "cancel-actions") {
      this._editing = null;
      return this._render();
    }
    return undefined;
  }

  // Banner: errors (red), the live run status (grey, spinner), and the note
  // from the last analysis (blue) or a skip warning (amber). The running line
  // appears whether the run was started here or elsewhere.
  _renderBanner() {
    const banner = this.shadow.querySelector("#banner");
    if (!banner) return;
    banner.innerHTML =
      (this._error ? `<div class="error">${esc(this._error)}</div>` : "") +
      (this._analysing
        ? `<div class="running">Analysis running… progress appears in the Analysis log below.</div>`
        : "") +
      (this._analysisNote
        ? `<div class="${this._noteKind === "warn" ? "warn" : "note"}">${esc(this._analysisNote)}</div>`
        : "");
  }

  _updateAnalyseBtn() {
    const btn = this.shadow.querySelector('[data-act="analyse"]');
    if (btn) btn.disabled = this._analysing || this._busy || this._readOnly;
  }

  _render() {
    this._renderBanner();
    const analyseBtn = this.shadow.querySelector('[data-act="analyse"]');
    if (analyseBtn) analyseBtn.disabled = this._analysing || this._busy || this._readOnly;
    const meta = this.shadow.querySelector(".meta");
    const entities = (this._meta.observed_entities || []).length;
    meta.innerHTML =
      `Observing <code>${entities}</code> entities · behaviours stored in <code>${esc(this._meta.path)}</code>` +
      (this._readOnly ? ` · <b>read-only</b> — sign in as an admin to make changes` : "");

    const body = this.shadow.querySelector("#body");
    if (!this._behaviours) return;
    if (this._behaviours.length === 0) {
      body.innerHTML = `<p class="empty">No behaviours learned yet. PHIL9000 is observing your
        entities and will suggest patterns as it learns. You can also add
        candidate behaviours directly to the YAML file above.</p>`;
      return;
    }
    // A background refresh (live event, another command) must not wipe what
    // the user is typing: harvest the editor inputs first, and restore the
    // rename box's typed text after the re-render.
    if (this._editing) this._collectDraft();
    const openNameInput = this.shadow.querySelector(".name-input");
    const typedName = this._renaming && openNameInput ? openNameInput.value : null;
    const sorted = [...this._behaviours].sort(
      (a, b) =>
        STATUS_RANK[a.status] - STATUS_RANK[b.status] ||
        b.confidence - a.confidence ||
        b.occurrences - a.occurrences,
    );
    body.innerHTML = sorted.map((b) => this._card(b)).join("");
    if (this._renaming) {
      const input = body.querySelector(".name-input");
      if (input) {
        if (typedName !== null) input.value = typedName;
        input.focus();
        input.select();
      }
    }
  }

  _card(b) {
    const pct = Math.round(b.confidence * 100);
    const parts = [
      `<div class="card">`,
      this._renaming === b.id
        ? `<h3><input class="name-input" value="${esc(b.name)}" /></h3>`
        : `<h3>${esc(b.name)}<span class="pill ${b.status}">${b.status}</span></h3>`,
      `<div class="id">${esc(b.id)}${b.custom_name ? " · named by you" : ""}</div>`,
      b.description ? `<div class="lines">${esc(b.description)}</div>` : "",
      `<div class="conf"><span class="bar"><i style="width:${pct}%"></i></span>
         ${pct}% confidence · seen ${b.occurrences} time${b.occurrences === 1 ? "" : "s"}</div>`,
      `<div class="steps">${stepSummary(b)}</div>`,
      `<div class="lines">${weekdaySummary(b.weekdays)} · ${timeWindow(b)} ·
         cooldown ${Math.round(b.cooldown_seconds / 60)} min</div>`,
    ];

    if (b.last_detection) {
      parts.push(
        `<div class="lines">last fired ${esc(formatWhen(b.last_detection.at))}</div>`,
      );
    }

    if (this._editing === b.id) {
      parts.push(this._actionEditor(b));
    } else {
      const actions = b.actions || [];
      parts.push(
        actions.length
          ? `<ul class="actions-list">${actions.map((a) => `<li>${actionLabel(a)}</li>`).join("")}</ul>`
          : `<div class="lines">no actions defined</div>`,
      );
    }

    const buttons = [];
    const locked = this._busy || this._readOnly;
    const editing = this._busy || this._editing || this._readOnly;
    if (this._renaming === b.id) {
      buttons.push(`<button data-act="save-name" data-id="${esc(b.id)}" ${locked ? "disabled" : ""}>Save name</button>`);
      buttons.push(`<button class="secondary" data-act="cancel-name">Cancel</button>`);
    } else {
      buttons.push(`<button class="secondary" data-act="rename" data-id="${esc(b.id)}" ${editing ? "disabled" : ""}>Rename</button>`);
    }
    if (b.status !== "confirmed")
      buttons.push(`<button data-act="confirm" data-id="${esc(b.id)}" ${locked ? "disabled" : ""}>Confirm</button>`);
    if (b.status !== "dismissed")
      buttons.push(`<button class="danger" data-act="dismiss" data-id="${esc(b.id)}" ${locked ? "disabled" : ""}>Dismiss</button>`);
    if (b.status === "dismissed")
      buttons.push(`<button class="secondary" data-act="reopen" data-id="${esc(b.id)}" ${locked ? "disabled" : ""}>Reopen</button>`);
    buttons.push(`<button class="secondary" data-act="edit-actions" data-id="${esc(b.id)}" ${editing ? "disabled" : ""}>Edit actions</button>`);
    parts.push(`<div class="row">${buttons.join("")}</div></div>`);
    return parts.join("");
  }

  _actionEditor(b) {
    const rows = this._draft
      .map(
        (a, i) => `<div class="action-row" data-idx="${i}">
          <input data-field="domain" value="${esc(a.domain)}" placeholder="domain" />
          <input data-field="service" value="${esc(a.service)}" placeholder="service" />
          <input data-field="entity_id" value="${esc(a.entity_id)}" placeholder="target entity" />
          <input data-field="data" value="${esc(a.data && Object.keys(a.data).length ? JSON.stringify(a.data) : "")}" placeholder='data JSON, e.g. {"message": "hi"}' />
          <button class="danger" data-act="remove-action" data-idx="${i}">×</button>
        </div>`,
      )
      .join("");
    return `<div class="editor">
      <div class="lines">Actions run in order when this behaviour is detected.</div>
      ${rows}
      <div class="row">
        <button class="secondary" data-act="add-action">Add action</button>
        <button data-act="save-actions" data-id="${esc(b.id)}">Save actions</button>
        <button class="secondary" data-act="cancel-actions">Cancel</button>
      </div>
    </div>`;
  }
}

if (!customElements.get("phil9000-panel")) {
  customElements.define("phil9000-panel", Phil9000Panel);
}
