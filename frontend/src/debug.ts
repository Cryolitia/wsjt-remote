import { LitElement, html } from "lit";
import { customElement, state } from "lit/decorators.js";
import { apiUrl, wsUrl } from "./api";
import "./components/backend-settings";
import "./components/debug-stream";
import "./components/manual-command-panel";
import type { DebugEvent } from "./types";

@customElement("wsjtx-debug")
class WSJTXDebug extends LitElement {
  createRenderRoot() {
    return this;
  }

  @state() private events: DebugEvent[] = [];
  private ws?: WebSocket;

  connectedCallback() {
    super.connectedCallback();
    this.loadEvents();
    this.connect();
  }

  disconnectedCallback() {
    this.ws?.close();
    super.disconnectedCallback();
  }

  render() {
    return html`
      <nav>
        <ul><li><strong>WSJT-X Debug</strong></li></ul>
        <ul><li><a href="/">Remote</a></li></ul>
      </nav>
      <backend-settings @backend-change=${this.reconnect}></backend-settings>
      <div class="grid">
        <debug-stream .events=${this.events}></debug-stream>
        <manual-command-panel></manual-command-panel>
      </div>
    `;
  }

  private async loadEvents() {
    const response = await fetch(apiUrl("/api/debug/events"));
    this.events = await response.json();
  }

  private connect() {
    this.ws?.close();
    this.ws = new WebSocket(wsUrl("/ws"));
    this.ws.onmessage = (event) => {
      const payload = JSON.parse(event.data);
      if (payload.event === "debug") this.events = [...this.events, payload.data].slice(-500);
    };
    this.ws.onclose = () => window.setTimeout(() => this.connect(), 2000);
  }

  private reconnect() {
    this.loadEvents();
    this.connect();
  }
}
