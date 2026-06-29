import { LitElement, html } from "lit";
import { customElement, query, state } from "lit/decorators.js";
import { postJson, wsUrl } from "./api";
import "./components/backend-settings";
import "./components/status-panel";
import "./components/activity-board";
import type { Decode, Snapshot, Status } from "./types";
import type { ActivityBoard } from "./components/activity-board";

@customElement("wsjtx-app")
class WSJTXApp extends LitElement {
  createRenderRoot() {
    return this;
  }

  @state() private snapshot: Snapshot = { remote: { connected: false, id: "", host: "", port: 0, schema: 3, version: "", revision: "", last_seen: "" }, status: {}, decodes: [] };
  @state() private actionNotice = "";
  @state() private wsNotice = "";
  private ws?: WebSocket;
  private reconnectTimer?: number;
  private lastTransmitMessage = "";
  @query("activity-board") private activityBoard?: ActivityBoard;

  connectedCallback() {
    super.connectedCallback();
    this.connect();
  }

  disconnectedCallback() {
    if (this.reconnectTimer) window.clearTimeout(this.reconnectTimer);
    this.ws?.close();
    super.disconnectedCallback();
  }

  render() {
    return html`
      <nav>
        <ul><li><strong>WSJT-X Remote</strong></li></ul>
        <ul><li><a href="/debug">Debug</a></li></ul>
      </nav>
      <backend-settings @backend-change=${this.reconnect}></backend-settings>
      <status-panel .remote=${this.snapshot.remote} .status=${this.snapshot.status}></status-panel>
      ${this.wsNotice ? html`<p><mark>${this.wsNotice}</mark></p>` : null}
      ${this.actionNotice ? html`<p><mark>${this.actionNotice}</mark></p>` : null}
      <article>
        <fieldset role="group">
          <button @click=${this.cq}>CQ</button>
          <button class="contrast" @click=${this.halt}>Halt TX</button>
          <button class="secondary" @click=${this.clear}>Clear</button>
        </fieldset>
      </article>
      <activity-board .decodes=${this.snapshot.decodes} .status=${this.snapshot.status as Status}></activity-board>
    `;
  }

  private connect() {
    if (this.reconnectTimer) window.clearTimeout(this.reconnectTimer);
    if (this.ws) {
      this.ws.onclose = null;
      this.ws.onerror = null;
      this.ws.close();
    }

    const ws = new WebSocket(wsUrl("/ws"));
    this.ws = ws;
    ws.onopen = () => {
      if (this.ws === ws) this.wsNotice = "";
    };
    ws.onmessage = (event) => this.handleMessage(JSON.parse(event.data));
    ws.onclose = () => {
      if (this.ws !== ws) return;
      this.wsNotice = "WebSocket disconnected, reconnecting...";
      this.reconnectTimer = window.setTimeout(() => this.connect(), 2000);
    };
    ws.onerror = () => {
      this.wsNotice = "WebSocket disconnected, reconnecting...";
    };
  }

  private reconnect() {
    this.connect();
  }

  private handleMessage(payload: { event: string; data: unknown }) {
    if (payload.event === "state") {
      this.snapshot = payload.data as Snapshot;
      this.trackTransmit(this.snapshot.status);
    }
    if (payload.event === "status") {
      const status = payload.data as Status;
      this.snapshot = { ...this.snapshot, status };
      this.trackTransmit(status);
    }
    if (payload.event === "decode") this.snapshot = { ...this.snapshot, decodes: [...this.snapshot.decodes, payload.data as Decode].slice(-500) };
    if (payload.event === "clear") this.snapshot = { ...this.snapshot, decodes: [] };
    if (payload.event === "logged_adif") {
      const data = payload.data as { call?: string };
      this.activityBoard?.handleLoggedAdif(data.call || "");
    }
  }

  private async cq() {
    const call = String(this.snapshot.status.de_call || "").trim();
    const grid = String(this.snapshot.status.de_grid || "").trim().slice(0, 4);
    const text = call && grid ? `CQ ${call} ${grid}` : "CQ";
    const idle = !this.snapshot.status.tx_enabled && !this.snapshot.status.transmitting;
    const message = idle
      ? "CQ set to Tx5; Enable Tx triggered"
      : "CQ set to Tx5; Enable Tx already active";
    await this.action(async () => {
      await postJson("/api/cq");
      if (idle) await postJson("/api/alt-n");
    }, message, text);
  }
  private async halt() { await this.action(() => postJson("/api/halt-tx", { auto_tx_only: false }), "Halt sent"); }
  private async clear() { await this.action(() => postJson("/api/clear", { window: 2 }), "Clear sent"); }

  private async action(fn: () => Promise<unknown>, message: string, txMessage = "") {
    try {
      await fn();
      if (txMessage) this.activityBoard?.handleTransmit(txMessage);
      this.flash(message);
    } catch (error) {
      this.flash(String(error));
    }
  }

  private trackTransmit(status: Status) {
    const message = String(status.tx_message || "").trim();
    if (!status.transmitting) {
      this.lastTransmitMessage = "";
      return;
    }
    if (!message || message === this.lastTransmitMessage) return;
    this.lastTransmitMessage = message;
    this.activityBoard?.handleTransmit(message);
  }

  private flash(message: string) {
    this.actionNotice = message;
    window.setTimeout(() => (this.actionNotice = ""), 2500);
  }
}
