import { LitElement, html } from "lit";
import { customElement, property } from "lit/decorators.js";
import type { Remote, Status } from "../types";

@customElement("status-panel")
export class StatusPanel extends LitElement {
  createRenderRoot() {
    return this;
  }

  @property({ type: Object }) remote?: Remote;
  @property({ type: Object }) status?: Status;

  render() {
    const remote = this.remote;
    const status = this.status || {};
    const mhz = status.dial_frequency ? (status.dial_frequency / 1_000_000).toFixed(6) : "-";
    return html`
      <article>
        <header>
          <strong>WSJT-X</strong>
          ${remote?.connected ? html`<mark>Connected</mark>` : html`<mark>Disconnected</mark>`}
        </header>
        <div class="grid">
          <div><small>ID</small><br>${remote?.id || "-"}</div>
          <div><small>Version</small><br>${remote?.version || "-"}</div>
          <div><small>Schema</small><br>${remote?.schema || "-"}</div>
          <div><small>Frequency</small><br>${mhz} MHz</div>
          <div><small>Mode</small><br>${status.mode || "-"}</div>
          <div><small>Tx</small><br>${status.transmitting ? "Transmitting" : status.tx_enabled ? "Enabled" : "Idle"}</div>
          <div><small>DE</small><br>${status.de_call || "-"} ${status.de_grid || ""}</div>
          <div><small>DX</small><br>${status.dx_call || "-"} ${status.dx_grid || ""}</div>
        </div>
        <footer><small>Last transmitted</small><br>${status.tx_message || "-"}</footer>
      </article>
    `;
  }
}
