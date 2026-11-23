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
 * phil9000/unmute_entity); the panel refreshes on
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

const stepSummary = (b) =>
  b.steps
    .map(
      (s, i) =>
        `${i ? " → " : ""}${esc(s.entity_id)} = ${esc(s.to_state)}` +
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
    header { display: flex; align-items: baseline; gap: 12px; margin-bottom: 4px; }
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
    .section-head input { font-size: 12px; padding: 3px 8px; width: 160px;
      margin-right: -4px; }
    .feed-status { font-size: 12px; color: var(--secondary-text-color);
                   margin-left: auto; text-align: right; }
    .feed-status.error { color: var(--error-color, #db4437); }
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
    this._fetchInfo = null; // last recorder fetch result: row count, window
    this._paused = false; // live rows buffer while the user inspects the feed
    this._pending = [];
    this._analysing = false;
    this._analysisNote = null;
    this._analysisLog = null; // entries from phil9000/analysis_log (newest first)
    this._monitoring = null;
    this._entityFilter = "";
    // Confirmed behaviours execute service calls, so amending them (and
    // subscriptions/analysis) is admin-only on the backend; reflect that.
    this._readOnly = hass.user && hass.user.is_admin === false;

    this.shadow.innerHTML = `${STYLE}<header><h1>PHIL9000</h1>
      <button data-act="analyse" ${this._analysing || this._readOnly ? "disabled" : ""}>Analyse now</button></header>
      <p class="meta"></p><div id="banner"></div>
      <div class="top-grid">
        <div id="activity-col">
          <div class="section-head"><h2 class="section">Recent activity</h2>
            <input class="activity-search" type="search" placeholder="Filter — entity or state…" />
            <button class="secondary" data-act="pause">Pause</button>
            <span class="feed-status" id="feed-status"></span></div>
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
          this._renderAnalysisLog();
        },
        { type: "phil9000/subscribe_analysis_log" },
      ),
    );
    this._refresh();
    this._reloadActivity();
    this._loadAnalysisLog();
    this._loadMonitoring();
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
      this._fetchInfo = {
        count: fetched.length,
        observed: result.observed,
        entities: result.observed_entities,
        hours: result.window_hours,
        limit: result.limit,
        error: "",
      };
    } catch (err) {
      this._activityHint = err.message || String(err);
      this._fetchInfo = { error: this._activityHint };
    }
    if (replace) {
      // The observed set just changed (domains/entities): show exactly the
      // new window, dropping rows for entities no longer observed. Live
      // rows arrive via the subscription as before.
      this._activity = fetched.slice(0, FEED_MAX);
      this._pending = [];
      this._updatePauseBtn();
      this._renderActivity();
      this._renderFeedStatus();
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
    this._renderFeedStatus();
  }

  _renderFeedStatus() {
    const el = this.shadow.querySelector("#feed-status");
    if (!el || !this._fetchInfo) return;
    const info = this._fetchInfo;
    if (info.error) {
      el.classList.add("error");
      el.textContent = `history query failed: ${info.error}`;
      return;
    }
    el.classList.remove("error");
    const win =
      info.hours >= 24 ? `${Math.round(info.hours / 24)}d` : `${info.hours}h`;
    let text =
      `history: ${info.count} of ${info.observed} observed entities, ` +
      `last ${win} (cap ${info.limit})`;
    if (info.entities && info.entities.length) text += ` · ${info.entities.join(", ")}`;
    el.textContent = text;
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
    el.innerHTML = `<div class="card"><ul class="act-list">${rows
      .map(
        (row) => `<li>
          <span class="act-time">${esc(formatClock(row.at))}</span>
          <span class="act-entity">${esc(row.entity_id)}</span>
          <span class="act-change">${esc(row.old_state ?? "—")} → <b>${esc(row.new_state)}</b></span>
          <button class="secondary mute-btn" data-act="mute" data-entity="${esc(row.entity_id)}"
            ${this._readOnly ? "disabled" : ""}
            title="Mute ${esc(row.entity_id)} — stop observing it (revert in the Entities panel)">⊘</button>
        </li>`,
      )
      .join("")}</ul></div>`;
  }

  async _loadAnalysisLog() {
    try {
      const result = await this._hass.connection.sendMessagePromise({
        type: "phil9000/analysis_log",
      });
      this._analysisLog = result.entries || [];
    } catch (err) {
      this._analysisLog = [];
    }
    this._renderAnalysisLog();
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
      this._analysisNote = parts.length
        ? parts.join(" · ")
        : "No new convincing patterns this time.";
    } catch (err) {
      this._analysisNote = `Analysis failed: ${err.message || String(err)}`;
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

  _render() {
    const banner = this.shadow.querySelector("#banner");
    banner.innerHTML =
      (this._error ? `<div class="error">${esc(this._error)}</div>` : "") +
      (this._analysisNote ? `<div class="note">${esc(this._analysisNote)}</div>` : "");
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
