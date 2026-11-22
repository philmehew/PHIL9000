/**
 * PHIL9000 sidebar panel — lists learned behaviours with confidence and
 * evidence; confirm / dismiss / reopen and edit a behaviour's actions.
 *
 * Plain JS web component, no build step and no framework. Data flows over
 * the Home Assistant websocket API (phil9000/behaviours, phil9000/set_status,
 * phil9000/set_actions); the panel refreshes on phil9000_behaviour_detected.
 */

const STATUS_RANK = { confirmed: 0, candidate: 1, dismissed: 2 };
const WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

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
    .empty { color: var(--secondary-text-color); font-size: 14px; }
  </style>`;

class Phil9000Panel extends HTMLElement {
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

    this.innerHTML = `${STYLE}<header><h1>PHIL9000</h1>
      <button class="secondary" data-act="refresh">Refresh</button></header>
      <p class="meta"></p><div id="banner"></div><div id="body"><p class="empty">Loading…</p></div>`;
    this.addEventListener("click", (ev) => this._onClick(ev));

    hass.connection.subscribeEvents(
      () => this._refresh(),
      "phil9000_behaviour_detected",
    );
    this._refresh();
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
    this.querySelectorAll(".action-row").forEach((row) => {
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
    if (act === "refresh") return this._refresh();
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
    const banner = this.querySelector("#banner");
    banner.innerHTML = this._error ? `<div class="error">${esc(this._error)}</div>` : "";
    const meta = this.querySelector(".meta");
    const entities = (this._meta.observed_entities || []).length;
    meta.innerHTML =
      `Observing <code>${entities}</code> entities · behaviours stored in <code>${esc(this._meta.path)}</code>`;

    const body = this.querySelector("#body");
    if (!this._behaviours) return;
    if (this._behaviours.length === 0) {
      body.innerHTML = `<p class="empty">No behaviours learned yet. PHIL9000 is observing your
        entities and will suggest patterns as it notices them. You can also add
        candidate behaviours directly to the YAML file above.</p>`;
      return;
    }
    const sorted = [...this._behaviours].sort(
      (a, b) =>
        STATUS_RANK[a.status] - STATUS_RANK[b.status] ||
        b.confidence - a.confidence ||
        b.occurrences - a.occurrences,
    );
    body.innerHTML = sorted.map((b) => this._card(b)).join("");
  }

  _card(b) {
    const pct = Math.round(b.confidence * 100);
    const parts = [
      `<div class="card">`,
      `<h3>${esc(b.name)}<span class="pill ${b.status}">${b.status}</span></h3>`,
      `<div class="id">${esc(b.id)}</div>`,
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
    if (b.status !== "confirmed")
      buttons.push(`<button data-act="confirm" data-id="${esc(b.id)}" ${this._busy ? "disabled" : ""}>Confirm</button>`);
    if (b.status !== "dismissed")
      buttons.push(`<button class="danger" data-act="dismiss" data-id="${esc(b.id)}" ${this._busy ? "disabled" : ""}>Dismiss</button>`);
    if (b.status === "dismissed")
      buttons.push(`<button class="secondary" data-act="reopen" data-id="${esc(b.id)}" ${this._busy ? "disabled" : ""}>Reopen</button>`);
    buttons.push(`<button class="secondary" data-act="edit-actions" data-id="${esc(b.id)}" ${this._busy || this._editing ? "disabled" : ""}>Edit actions</button>`);
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
