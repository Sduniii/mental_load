/**
 * Mental Load Analyzer Custom Lovelace Card
 * Displays 7-day heatmap, 5-factor breakdown, invisible head-tasks checklist,
 * and 1-click calibration feedback.
 */
class MentalLoadCard extends HTMLElement {
  static getStubConfig() {
    return {
      today_entity: "sensor.mental_load_today",
      tomorrow_entity: "sensor.mental_load_tomorrow",
      week_entity: "sensor.mental_load_week_average",
      title: "Mental Load & Kopfarbeit"
    };
  }

  setConfig(config) {
    this._config = {
      today_entity: "sensor.mental_load_today",
      tomorrow_entity: "sensor.mental_load_tomorrow",
      week_entity: "sensor.mental_load_week_average",
      title: "Mental Load & Kopfarbeit",
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

  _sendFeedback(adjustment) {
    if (!this._hass) return;
    this._hass.callService("mental_load", "submit_feedback", { adjustment });
  }

  _pushHiddenTasks() {
    if (!this._hass) return;
    this._hass.callService("mental_load", "push_hidden_tasks", {});
  }

  _refresh() {
    if (!this._hass) return;
    this._hass.callService("mental_load", "refresh", { clear_cache: true });
  }

  _render() {
    if (!this._hass || !this._config) return;
    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
    }

    const today = this._hass.states[this._config.today_entity];
    const tomorrow = this._hass.states[this._config.tomorrow_entity];
    const week = this._hass.states[this._config.week_entity];

    const score = today ? Number(today.state) || 0 : 0;
    const attrs = today ? today.attributes || {} : {};
    const level = attrs.level || "low";
    const breakdown = attrs.factor_breakdown || {};
    const hiddenTasks = attrs.hidden_tasks || [];
    const dailyScores = (week && week.attributes && week.attributes.daily_scores) || [];
    const tomorrowScore = tomorrow ? tomorrow.state : "?";

    const factorLabels = [
      ["pacing_context", "Taktung & Wechsel"],
      ["social_battery", "Soziale Batterie"],
      ["mobility", "Ortswechsel"],
      ["ha_context", "HA Live-Kontext"],
      ["hidden_work", "Kopfarbeit"],
    ];

    this.shadowRoot.innerHTML = `
      <style>
        ha-card { padding: 16px; }
        .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
        .title { font-size: 1.15rem; font-weight: 600; }
        .badge-row { display: flex; gap: 12px; align-items: center; margin-bottom: 14px; }
        .score-pill {
          padding: 8px 14px; border-radius: 12px; font-weight: 700; font-size: 1.2rem;
          color: #fff; background: ${this._levelColor(level)};
        }
        .sub { font-size: 0.88rem; color: var(--secondary-text-color); }
        .heatmap { display: grid; grid-template-columns: repeat(auto-fit, minmax(46px, 1fr)); gap: 6px; margin: 12px 0; }
        .day-cell {
          text-align: center; padding: 6px 4px; border-radius: 8px;
          background: var(--secondary-background-color); font-size: 0.78rem;
          border-bottom: 3px solid var(--divider-color);
        }
        .factors { display: grid; gap: 6px; margin: 12px 0; }
        .factor-row { display: grid; grid-template-columns: 140px 1fr 38px; gap: 8px; align-items: center; font-size: 0.82rem; }
        .bar-bg { height: 7px; border-radius: 4px; background: var(--secondary-background-color); overflow: hidden; }
        .bar-fill { height: 100%; background: var(--primary-color); }
        .tasks { margin-top: 12px; border-top: 1px solid var(--divider-color); padding-top: 10px; }
        .task-item { margin-bottom: 8px; padding: 8px; border-radius: 8px; background: var(--secondary-background-color); font-size: 0.85rem; }
        .checklist { margin: 4px 0 0 14px; padding: 0; font-size: 0.8rem; color: var(--secondary-text-color); }
        .actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
        button {
          border: 1px solid var(--divider-color); border-radius: 8px; padding: 6px 10px;
          background: var(--card-background-color); color: var(--primary-text-color); cursor: pointer; font-size: 0.8rem;
        }
        button:hover { background: var(--secondary-background-color); }
      </style>
      <ha-card>
        <div class="header">
          <div class="title">${this._config.title}</div>
          <button id="btn-refresh">↻ Neu analysieren</button>
        </div>
        <div class="badge-row">
          <div class="score-pill">${score}%</div>
          <div>
            <div><strong>${attrs.badge_summary || "Keine Daten"}</strong></div>
            <div class="sub">Morgen: ${tomorrowScore}% · Quelle: ${attrs.provider_used || "-"}</div>
          </div>
        </div>
        <div class="sub">${attrs.reasoning || ""}</div>

        ${dailyScores.length ? `
          <div class="heatmap">
            ${dailyScores.map(d => `
              <div class="day-cell" style="border-bottom-color: ${this._levelColor(d.level)}">
                <div>${d.date.slice(5)}</div>
                <strong>${d.score}%</strong>
              </div>
            `).join("")}
          </div>
        ` : ""}

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

        <div class="tasks">
          <strong>🧠 Unsichtbare Kopf-Aufgaben heute (${hiddenTasks.length})</strong>
          ${hiddenTasks.length === 0 ? `<div class="sub">Keine versteckten Aufgaben erkannt.</div>` : hiddenTasks.map(t => `
            <div class="task-item">
              <div><strong>${t.start ? t.start.slice(11, 16) : ""}</strong> ${t.title} (+${t.load_contribution}%)</div>
              ${(t.checklist && t.checklist.length) ? `
                <ul class="checklist">
                  ${t.checklist.map(c => `<li>${c}</li>`).join("")}
                </ul>
              ` : ""}
            </div>
          `).join("")}
        </div>

        <div class="actions">
          <button id="fb-lower">😌 Entspannter</button>
          <button id="fb-ok">🎯 Passt</button>
          <button id="fb-higher">🤯 Stressiger</button>
          <button id="btn-todo">📝 In To-Do pushen</button>
        </div>
      </ha-card>
    `;

    this.shadowRoot.getElementById("btn-refresh")?.addEventListener("click", () => this._refresh());
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
  description: "7-Tage Mental-Load-Heatmap, 5-Faktoren-Analyse & unsichtbare Kopf-Aufgaben",
});
