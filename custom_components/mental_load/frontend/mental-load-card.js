/**
 * Mental Load Analyzer Custom Lovelace Card (v1.3.0)
 * Responsive multi-device card with interactive 7-day selector, 5-factor breakdown,
 * checkable invisible head-tasks, Google Calendar sync, iOS iCal link, and 1-click feedback.
 */
class MentalLoadCard extends HTMLElement {
  constructor() {
    super();
    this._selectedDate = null;
    this._optimisticDone = new Map();
    this._toastMsg = "";
  }

  static getStubConfig() {
    return {
      today_entity: "sensor.mental_load_today",
      tomorrow_entity: "sensor.mental_load_tomorrow",
      week_entity: "sensor.mental_load_week_average",
      title: "Mental Load & Kopfarbeit",
      show_factors: true,
      show_recommendations: true,
    };
  }

  setConfig(config) {
    this._config = {
      today_entity: "sensor.mental_load_today",
      tomorrow_entity: "sensor.mental_load_tomorrow",
      week_entity: "sensor.mental_load_week_average",
      title: "Mental Load & Kopfarbeit",
      show_factors: true,
      show_recommendations: true,
      ...config,
    };
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  _levelColor(level) {
    switch (level) {
      case "critical": return "#ef4444";
      case "high": return "#f97316";
      case "medium": return "#eab308";
      default: return "#22c55e";
    }
  }

  _weekdayShort(dateStr) {
    try {
      const d = new Date(`${dateStr}T12:00:00`);
      return d.toLocaleDateString("de-DE", { weekday: "short" });
    } catch {
      return "";
    }
  }

  _showToast(msg) {
    this._toastMsg = msg;
    this._render();
    setTimeout(() => {
      this._toastMsg = "";
      this._render();
    }, 4000);
  }

  _toggleChecklistItem(itemUid, nextDone) {
    if (!this._hass) return;
    this._optimisticDone.set(itemUid, nextDone);
    this._render();
    this._hass.callService("mental_load", "toggle_checklist_item", {
      item_uid: itemUid,
      completed: nextDone,
    });
  }

  _sendFeedback(adjustment) {
    if (!this._hass) return;
    const payload = { adjustment };
    if (this._selectedDate) payload.date = this._selectedDate;
    this._hass.callService("mental_load", "submit_feedback", payload);
    this._showToast("Feedback gespeichert – KI passt Bewertung an");
  }

  _pushHiddenTasks() {
    if (!this._hass) return;
    const payload = {};
    if (this._selectedDate) payload.date = this._selectedDate;
    this._hass.callService("mental_load", "push_hidden_tasks", payload);
    this._showToast("Aufgaben an To-Do-Liste gesendet");
  }

  _syncExternalCalendar() {
    if (!this._hass) return;
    this._hass.callService("mental_load", "sync_external_calendar", {});
    this._showToast("Synchronisation mit Ziel-Kalender gestartet");
  }

  _refresh() {
    if (!this._hass) return;
    this._hass.callService("mental_load", "refresh", { clear_cache: true });
    this._showToast("Kalender wird mit KI neu analysiert...");
  }

  async _copyIcalLink(icalPath) {
    if (!icalPath) return;
    const proto = window.location.protocol === "https:" ? "webcal:" : "http:";
    const url = `${proto}//${window.location.host}${icalPath}`;
    try {
      await navigator.clipboard.writeText(url);
      this._showToast(`iOS Kalender-Link kopiert: ${url}`);
    } catch {
      this._showToast(`iOS Abo-URL: ${url}`);
    }
  }

  _getDayPayload() {
    const today = this._hass.states[this._config.today_entity];
    const week = this._hass.states[this._config.week_entity];
    const weekAttrs = (week && week.attributes) || {};
    const todayAttrs = (today && today.attributes) || {};

    const dailyScores = weekAttrs.daily_scores || [];
    const daysDetail = weekAttrs.days_detail || {};
    const todayDate = todayAttrs.date || (dailyScores[0] && dailyScores[0].date) || null;

    if (!this._selectedDate || (!daysDetail[this._selectedDate] && this._selectedDate !== todayDate)) {
      this._selectedDate = todayDate;
    }

    const activeDay = (this._selectedDate && daysDetail[this._selectedDate]) || {
      date: todayDate || "",
      score: today ? Number(today.state) || 0 : 0,
      level: todayAttrs.level || "low",
      badge_summary: todayAttrs.badge_summary || "Keine Daten",
      reasoning: todayAttrs.reasoning || "",
      factor_breakdown: todayAttrs.factor_breakdown || {},
      hidden_tasks: todayAttrs.hidden_tasks || [],
      recommendations: todayAttrs.recommendations || [],
      provider_used: todayAttrs.provider_used || "-",
    };

    const serverCompleted = new Set(
      weekAttrs.completed_item_uids || todayAttrs.completed_item_uids || []
    );
    const isCompleted = (uid) =>
      this._optimisticDone.has(uid) ? this._optimisticDone.get(uid) : serverCompleted.has(uid);

    return {
      activeDay,
      dailyScores,
      icalPath: weekAttrs.ical_path || "",
      isCompleted,
    };
  }

  _render() {
    if (!this._hass || !this._config) return;
    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
    }

    const { activeDay, dailyScores, icalPath, isCompleted } = this._getDayPayload();
    const score = Number(activeDay.score) || 0;
    const level = activeDay.level || "low";
    const breakdown = activeDay.factor_breakdown || {};
    const hiddenTasks = activeDay.hidden_tasks || [];
    const recommendations = activeDay.recommendations || [];

    const factorLabels = [
      ["pacing_context", "Taktung & Wechsel"],
      ["social_battery", "Soziale Batterie"],
      ["mobility", "Ortswechsel"],
      ["ha_context", "HA Live-Kontext"],
      ["hidden_work", "Kopfarbeit"],
    ];

    let totalSteps = 0;
    let doneSteps = 0;
    hiddenTasks.forEach((t) => {
      const steps = t.checklist && t.checklist.length ? t.checklist : [t.title];
      steps.forEach((_, idx) => {
        totalSteps += 1;
        if (isCompleted(`${t.uid}:step:${idx}`)) doneSteps += 1;
      });
    });

    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; container-type: inline-size; }
        ha-card { padding: 16px; border-radius: 16px; overflow: hidden; }
        .header { display: flex; justify-content: space-between; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; }
        .title { font-size: 1.12rem; font-weight: 700; display: flex; align-items: center; gap: 8px; }
        .top-actions { display: flex; gap: 6px; flex-wrap: wrap; }
        .hero { display: flex; gap: 14px; align-items: center; margin-bottom: 12px; }
        .score-ring {
          min-width: 64px; height: 64px; border-radius: 16px; display: flex; flex-direction: column;
          align-items: center; justify-content: center; color: #fff; font-weight: 800; font-size: 1.28rem;
          background: ${this._levelColor(level)}; box-shadow: 0 4px 12px rgba(0,0,0,0.18);
        }
        .score-sub { font-size: 0.68rem; font-weight: 600; opacity: 0.92; text-transform: uppercase; }
        .hero-text { flex: 1; min-width: 0; }
        .badge-title { font-weight: 700; font-size: 0.98rem; line-height: 1.3; margin-bottom: 4px; }
        .muted { font-size: 0.84rem; color: var(--secondary-text-color); line-height: 1.4; }
        .heatmap {
          display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: 6px;
          margin: 14px 0; overflow-x: auto;
        }
        .day-cell {
          text-align: center; padding: 8px 4px; border-radius: 10px; cursor: pointer;
          background: var(--secondary-background-color); border: 2px solid transparent;
          border-bottom: 3px solid var(--divider-color); transition: transform 0.12s ease, border-color 0.12s ease;
        }
        .day-cell:hover { transform: translateY(-1px); }
        .day-cell.active { border-color: var(--primary-color); background: color-mix(in srgb, var(--primary-color) 12%, var(--secondary-background-color)); }
        .day-name { font-size: 0.7rem; color: var(--secondary-text-color); text-transform: uppercase; }
        .day-date { font-size: 0.72rem; margin-bottom: 2px; }
        .day-score { font-size: 0.9rem; font-weight: 700; }
        .factors { display: grid; gap: 6px; margin: 12px 0; padding: 10px 12px; border-radius: 12px; background: var(--secondary-background-color); }
        .factor-row { display: grid; grid-template-columns: 128px 1fr 38px; gap: 8px; align-items: center; font-size: 0.8rem; }
        .bar-bg { height: 7px; border-radius: 99px; background: rgba(128,128,128,0.22); overflow: hidden; }
        .bar-fill { height: 100%; background: var(--primary-color); border-radius: 99px; }
        .section-head { display: flex; justify-content: space-between; align-items: center; margin: 14px 0 8px; font-weight: 700; font-size: 0.92rem; }
        .pill { font-size: 0.74rem; padding: 2px 8px; border-radius: 99px; background: var(--secondary-background-color); color: var(--secondary-text-color); font-weight: 600; }
        .tasks-grid { display: grid; gap: 10px; }
        .task-card {
          padding: 10px 12px; border-radius: 12px; background: var(--secondary-background-color);
          border-left: 4px solid ${this._levelColor(level)};
        }
        .task-top { display: flex; justify-content: space-between; align-items: flex-start; gap: 8px; margin-bottom: 6px; }
        .task-title { font-weight: 700; font-size: 0.9rem; }
        .task-meta { font-size: 0.76rem; color: var(--secondary-text-color); margin-bottom: 6px; }
        .checklist { display: grid; gap: 5px; }
        .check-row {
          display: flex; align-items: flex-start; gap: 8px; cursor: pointer; font-size: 0.84rem;
          padding: 4px 6px; border-radius: 8px; user-select: none;
        }
        .check-row:hover { background: rgba(128,128,128,0.1); }
        .check-row input { width: 17px; height: 17px; margin: 1px 0 0; accent-color: var(--primary-color); cursor: pointer; flex-shrink: 0; }
        .check-row.done span { text-decoration: line-through; opacity: 0.58; }
        .recs { margin-top: 10px; padding: 10px 12px; border-radius: 10px; background: rgba(59,130,246,0.08); font-size: 0.82rem; }
        .recs ul { margin: 4px 0 0 18px; padding: 0; }
        .actions { display: flex; flex-wrap: wrap; gap: 7px; margin-top: 14px; padding-top: 10px; border-top: 1px solid var(--divider-color); }
        button {
          border: 1px solid var(--divider-color); border-radius: 10px; padding: 7px 11px;
          background: var(--card-background-color); color: var(--primary-text-color); cursor: pointer;
          font-size: 0.8rem; font-weight: 600; transition: background 0.12s ease;
        }
        button:hover { background: var(--secondary-background-color); }
        .toast { margin-top: 10px; padding: 8px 10px; border-radius: 8px; background: rgba(34,197,94,0.15); font-size: 0.8rem; font-weight: 600; word-break: break-all; }
        @container (max-width: 440px) {
          .heatmap { grid-template-columns: repeat(4, minmax(0, 1fr)); }
          .factor-row { grid-template-columns: 108px 1fr 34px; font-size: 0.76rem; }
        }
      </style>
      <ha-card>
        <div class="header">
          <div class="title">🧠 ${this._config.title}</div>
          <div class="top-actions">
            ${icalPath ? `<button id="btn-ical" title="iOS / Mac Kalender-Abo kopieren">🍏 iOS Abo</button>` : ""}
            <button id="btn-cal-sync" title="In separaten Google Kalender synchronisieren">📅 Google Sync</button>
            <button id="btn-refresh" title="Kalender neu analysieren">↻</button>
          </div>
        </div>

        <div class="hero">
          <div class="score-ring">
            <span>${score}%</span>
            <span class="score-sub">${activeDay.date ? activeDay.date.slice(5) : ""}</span>
          </div>
          <div class="hero-text">
            <div class="badge-title">${activeDay.badge_summary || "Keine Daten"}</div>
            <div class="muted">${activeDay.reasoning || ""}</div>
          </div>
        </div>

        ${dailyScores.length ? `
          <div class="heatmap">
            ${dailyScores.map((d) => {
              const isSelected = d.date === activeDay.date;
              return `
                <div class="day-cell ${isSelected ? "active" : ""}" data-date="${d.date}" style="border-bottom-color: ${this._levelColor(d.level)}">
                  <div class="day-name">${this._weekdayShort(d.date)}</div>
                  <div class="day-date">${d.date.slice(8)}.${d.date.slice(5, 7)}.</div>
                  <div class="day-score">${d.score}%</div>
                </div>
              `;
            }).join("")}
          </div>
        ` : ""}

        ${this._config.show_factors ? `
          <div class="factors">
            ${factorLabels.map(([k, label]) => {
              const val = breakdown[k] ?? 0;
              return `
                <div class="factor-row">
                  <span>${label}</span>
                  <div class="bar-bg"><div class="bar-fill" style="width:${val}%"></div></div>
                  <span>${val}%</span>
                </div>
              `;
            }).join("")}
          </div>
        ` : ""}

        <div class="section-head">
          <span>📋 Unsichtbare Kopf-Aufgaben (${hiddenTasks.length})</span>
          <span class="pill">${doneSteps}/${totalSteps} erledigt</span>
        </div>

        <div class="tasks-grid">
          ${hiddenTasks.length === 0 ? `<div class="muted">Keine versteckten Aufgaben an diesem Tag.</div>` : hiddenTasks.map((t) => {
            const steps = (t.checklist && t.checklist.length) ? t.checklist : [t.title];
            const startStr = t.start ? t.start.slice(11, 16) : "";
            const endStr = t.end ? t.end.slice(11, 16) : "";
            return `
              <div class="task-card">
                <div class="task-top">
                  <div class="task-title">${t.title}</div>
                  <span class="pill">${startStr}${endStr ? `–${endStr}` : ""} · +${t.load_contribution}%</span>
                </div>
                ${t.parent_event_summary ? `<div class="task-meta">Termin: ${t.parent_event_summary}</div>` : ""}
                <div class="checklist">
                  ${steps.map((step, idx) => {
                    const itemUid = `${t.uid}:step:${idx}`;
                    const checked = isCompleted(itemUid);
                    return `
                      <label class="check-row ${checked ? "done" : ""}">
                        <input type="checkbox" data-uid="${itemUid}" ${checked ? "checked" : ""} />
                        <span>${step}</span>
                      </label>
                    `;
                  }).join("")}
                </div>
              </div>
            `;
          }).join("")}
        </div>

        ${(this._config.show_recommendations && recommendations.length) ? `
          <div class="recs">
            <strong>💡 KI-Empfehlungen</strong>
            <ul>${recommendations.map((r) => `<li>${r}</li>`).join("")}</ul>
          </div>
        ` : ""}

        <div class="actions">
          <button id="fb-lower">😌 Entspannter</button>
          <button id="fb-ok">🎯 Passt</button>
          <button id="fb-higher">🤯 Stressiger</button>
          <button id="btn-todo">📝 In To-Do pushen</button>
        </div>

        ${this._toastMsg ? `<div class="toast">${this._toastMsg}</div>` : ""}
      </ha-card>
    `;

    this.shadowRoot.querySelectorAll(".day-cell").forEach((el) => {
      el.addEventListener("click", () => {
        this._selectedDate = el.getAttribute("data-date");
        this._render();
      });
    });

    this.shadowRoot.querySelectorAll("input[type='checkbox'][data-uid]").forEach((cb) => {
      cb.addEventListener("change", (ev) => {
        const uid = ev.target.getAttribute("data-uid");
        this._toggleChecklistItem(uid, Boolean(ev.target.checked));
      });
    });

    this.shadowRoot.getElementById("btn-refresh")?.addEventListener("click", () => this._refresh());
    this.shadowRoot.getElementById("btn-cal-sync")?.addEventListener("click", () => this._syncExternalCalendar());
    this.shadowRoot.getElementById("btn-ical")?.addEventListener("click", () => this._copyIcalLink(icalPath));
    this.shadowRoot.getElementById("fb-lower")?.addEventListener("click", () => this._sendFeedback("lower"));
    this.shadowRoot.getElementById("fb-ok")?.addEventListener("click", () => this._sendFeedback("accurate"));
    this.shadowRoot.getElementById("fb-higher")?.addEventListener("click", () => this._sendFeedback("higher"));
    this.shadowRoot.getElementById("btn-todo")?.addEventListener("click", () => this._pushHiddenTasks());
  }
}

customElements.define("mental-load-card", MentalLoadCard);
window.customCards = window.customCards || [];
window.customCards.push({
  type: "mental-load-card",
  name: "Mental Load & Kopfarbeit",
  description: "Dynamische 7-Tage Mental-Load-Karte mit abhakbaren Kopf-Aufgaben, Google-Sync & iOS iCal-Abo",
});
