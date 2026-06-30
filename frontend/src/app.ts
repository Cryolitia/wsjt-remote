import { LitElement, html } from "lit";
import { customElement, property, query, state } from "lit/decorators.js";
import { postJson, wsUrl } from "./api";
import "./components/backend-settings";
import "./components/status-panel";
import "./components/activity-board";
import type { Decode, Snapshot, Status } from "./types";
import type { ActivityBoard } from "./components/activity-board";

@customElement("utc-clock")
class UtcClock extends LitElement {
  createRenderRoot() {
    return this;
  }

  @state() private now = new Date();
  @property({ type: String }) serverTime = "";
  private timer?: number;
  private serverOffsetMs = 0;

  connectedCallback() {
    super.connectedCallback();
    this.tick();
    this.timer = window.setInterval(() => this.tick(), 1000);
  }

  disconnectedCallback() {
    if (this.timer) window.clearInterval(this.timer);
    super.disconnectedCallback();
  }

  render() {
    const iso = this.now.toISOString().replace(/\.\d{3}Z$/, "Z");
    return html`
      <section class="utc-clock" aria-label="Current UTC time">
        <time datetime=${iso}>${iso}</time>
      </section>
    `;
  }

  private tick() {
    this.now = new Date(Date.now() + this.serverOffsetMs);
  }

  updated(changed: Map<string, unknown>) {
    if (!changed.has("serverTime")) return;
    const serverMs = Date.parse(this.serverTime);
    if (!Number.isFinite(serverMs)) return;
    this.serverOffsetMs = serverMs - Date.now();
    this.tick();
  }
}

@customElement("ft8-progress")
class Ft8Progress extends LitElement {
  createRenderRoot() {
    return this;
  }

  @property({ type: String }) serverTime = "";
  @property({ type: Boolean }) transmitting = false;
  @state() private progress = 0;
  private timer?: number;
  private serverOffsetMs = 0;

  connectedCallback() {
    super.connectedCallback();
    this.tick();
    this.timer = window.setInterval(() => this.tick(), 200);
  }

  disconnectedCallback() {
    if (this.timer) window.clearInterval(this.timer);
    super.disconnectedCallback();
  }

  render() {
    return html`
      <div class=${this.transmitting ? "ft8-progress ft8-progress--tx" : "ft8-progress"} aria-label="FT8 period progress">
        <div class="ft8-progress__bar" style=${`width: ${this.progress}%`}></div>
      </div>
    `;
  }

  updated(changed: Map<string, unknown>) {
    if (!changed.has("serverTime")) return;
    const serverMs = Date.parse(this.serverTime);
    if (!Number.isFinite(serverMs)) return;
    this.serverOffsetMs = serverMs - Date.now();
    this.tick();
  }

  private tick() {
    const periodMs = 15_000;
    const elapsed = (Date.now() + this.serverOffsetMs) % periodMs;
    this.progress = (elapsed / periodMs) * 100;
  }
}

@customElement("wsjtx-app")
class WSJTXApp extends LitElement {
  createRenderRoot() {
    return this;
  }

  @state() private snapshot: Snapshot = { remote: { connected: false, id: "", host: "", port: 0, schema: 3, version: "", revision: "", last_seen: "" }, server_time: "", status: {}, decodes: [] };
  @state() private actionNotice = "";
  @state() private wsNotice = "";
  private ws?: WebSocket;
  private reconnectTimer?: number;
  private wasTransmitting = false;
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
    const txIdle = !this.snapshot.status.tx_enabled && !this.snapshot.status.transmitting;
    return html`
      <utc-clock .serverTime=${this.snapshot.server_time || ""}></utc-clock>
      <nav>
        <ul><li><strong>WSJT-X Remote</strong></li></ul>
        <ul><li><a href="/debug">Debug</a></li></ul>
      </nav>
      <backend-settings @backend-change=${this.reconnect}></backend-settings>
      <status-panel .remote=${this.snapshot.remote} .status=${this.snapshot.status}></status-panel>
      ${this.wsNotice ? html`<p><mark>${this.wsNotice}</mark></p>` : null}
      ${this.actionNotice ? html`<div class="toast-stack"><div class="toast">${this.actionNotice}</div></div>` : null}
      <article>
        <fieldset role="group">
          <button @click=${this.cq}>CQ</button>
          <button class=${txIdle ? "secondary" : "contrast"} @click=${this.halt}>Halt TX</button>
          <button class="secondary" @click=${this.clear}>Clear</button>
        </fieldset>
      </article>
      <activity-board .decodes=${this.snapshot.decodes} .status=${this.snapshot.status as Status}></activity-board>
      <ft8-progress .serverTime=${this.snapshot.server_time || ""} .transmitting=${Boolean(this.snapshot.status.transmitting)}></ft8-progress>
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
    if (payload.event === "clear") {
      this.snapshot = { ...this.snapshot, decodes: [] };
      this.activityBoard?.clearMessages();
    }
  }

  private async cq() {
    const call = String(this.snapshot.status.de_call || "").trim();
    const grid = String(this.snapshot.status.de_grid || "").trim().slice(0, 4);
    const idle = !this.snapshot.status.tx_enabled && !this.snapshot.status.transmitting;
    const message = idle
      ? "CQ set to Tx5; Enable Tx triggered"
      : "CQ set to Tx5; Enable Tx already active";
    await this.action(async () => {
      await postJson("/api/cq");
      if (idle) await postJson("/api/alt-n");
    }, message);
  }
  private async halt() { await this.action(() => postJson("/api/halt-tx", { auto_tx_only: false }), "Halt sent"); }
  private async clear() {
    await this.action(async () => {
      await postJson("/api/clear", { window: 2 });
      this.snapshot = { ...this.snapshot, decodes: [] };
      this.activityBoard?.clearMessages();
    }, "Clear sent");
  }

  private async action(fn: () => Promise<unknown>, message: string) {
    try {
      await fn();
      this.flash(message);
    } catch (error) {
      this.flash(String(error));
    }
  }

  private trackTransmit(status: Status) {
    const transmitting = Boolean(status.transmitting);
    if (!transmitting) {
      this.wasTransmitting = false;
      return;
    }
    if (this.wasTransmitting) return;
    this.wasTransmitting = true;
    const message = transmitMessage(status);
    if (!message) return;
    this.activityBoard?.handleTransmit(message);
  }

  private flash(message: string) {
    this.actionNotice = message;
    window.setTimeout(() => (this.actionNotice = ""), 2500);
  }
}

function transmitMessage(status: Status): string {
  const explicit = String(status.tx_message || "").trim();
  if (explicit) return explicit;
  const ownCall = String(status.de_call || "").trim().toUpperCase();
  const ownGrid = String(status.de_grid || "").trim().toUpperCase().slice(0, 4);
  const dxCall = String(status.dx_call || "").trim().toUpperCase();
  if (!dxCall) return ownCall && ownGrid ? `CQ ${ownCall} ${ownGrid}` : "CQ";
  return `${dxCall} ${ownCall || "UNKNOWN"} UNKNOWN`;
}
