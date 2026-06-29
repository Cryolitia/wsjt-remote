import { LitElement, html } from "lit";
import { customElement, property, state } from "lit/decorators.js";
import { postJson } from "../api";
import type { Decode, Status, WatchedCall } from "../types";

type WatchedActivity = Decode & { watchCall: string };

@customElement("activity-board")
export class ActivityBoard extends LitElement {
  createRenderRoot() {
    return this;
  }

  @property({ type: Array }) decodes: Decode[] = [];
  @property({ type: Object }) status: Status = {};
  @state() private watched = new Map<string, WatchedCall>();
  @state() private watchedActivities: WatchedActivity[] = [];
  @state() private message = "";
  @state() private limit = 50;
  private watchedActivityKeys = new Set<string>();

  render() {
    return html`
      ${this.message ? html`<p><mark>${this.message}</mark></p>` : null}
      <div class="grid">
        <article>
          <header>
            <nav>
              <ul><li><strong>All Activity</strong></li></ul>
              <ul>
                <li>
                  <select aria-label="Activity limit" .value=${String(this.limit)} @change=${this.onLimitChange}>
                    <option value="50">50</option>
                    <option value="100">100</option>
                    <option value="200">200</option>
                  </select>
                </li>
              </ul>
            </nav>
          </header>
          <table>
            <thead><tr><th>Sig</th><th>DT</th><th>Freq</th><th>Message</th><th></th></tr></thead>
            <tbody>${this.renderDecodeRows()}</tbody>
          </table>
        </article>
        <article>
          <header>
            <nav>
              <ul><li><strong>Watch List</strong></li></ul>
              <ul><li><small>${Array.from(this.watched.keys()).join(", ")}</small></li></ul>
            </nav>
          </header>
          <table>
            <thead><tr><th>Call</th><th>Sig</th><th>DT</th><th>Freq</th><th>Message</th><th></th></tr></thead>
            <tbody>${this.renderWatchedRows()}</tbody>
          </table>
        </article>
      </div>
    `;
  }

  private renderDecodeRows() {
    const rows = [];
    let lastSlot = "";
    for (const decode of this.decodes.slice(-this.limit).reverse()) {
      const slot = timeSlot(decode.time);
      if (slot !== lastSlot) {
        rows.push(html`<tr><th colspan="5"><small><strong>${slot}</strong></small></th></tr>`);
        lastSlot = slot;
      }
      rows.push(html`
        <tr @dblclick=${() => this.watch(decode)}>
          <td><small><strong>${decode.snr}</strong></small></td>
          <td><small>${formatDt(decode.delta_time)}</small></td>
          <td><small>${decode.delta_frequency}</small></td>
          <td><small>${decode.message}</small></td>
          <td><small><a href="#" @click=${(event: Event) => this.replyAndWatchFromLink(event, decode)}>Reply</a></small></td>
        </tr>
      `);
    }
    return rows;
  }

  private renderWatchedRows() {
    const rows = [];
    let lastSlot = "";
    for (const item of this.watchedActivities.slice(-this.limit).reverse()) {
      const slot = timeSlot(item.time);
      if (slot !== lastSlot) {
        rows.push(html`<tr><th colspan="6"><small><strong>${slot}</strong></small></th></tr>`);
        lastSlot = slot;
      }
      rows.push(html`
        <tr>
          <td><small><strong>${item.watchCall}</strong></small></td>
          <td><small>${item.snr}</small></td>
          <td><small>${formatDt(item.delta_time)}</small></td>
          <td><small>${item.delta_frequency}</small></td>
          <td><small>${item.message}</small></td>
          <td>
            <small>
              ${item.index >= 0 && item.id !== "local" ? html`<a href="#" @click=${(event: Event) => this.replyFromLink(event, item.index)}>Reply</a> · ` : null}
              ${item.id !== "local" ? html`<a href="#" @click=${(event: Event) => this.unwatchFromLink(event, item.watchCall)}>Remove</a>` : null}
            </small>
          </td>
        </tr>
      `);
    }
    return rows;
  }

  private onLimitChange(event: Event) {
    this.limit = Number((event.target as HTMLSelectElement).value);
  }

  private replyFromLink(event: Event, index: number) {
    event.preventDefault();
    this.reply(index);
  }

  private unwatchFromLink(event: Event, callsign: string) {
    event.preventDefault();
    this.unwatch(callsign);
  }

  updated(changed: Map<string, unknown>) {
    if (changed.has("decodes")) {
      for (const decode of this.decodes) this.matchWatched(decode);
    }
  }

  handleLoggedAdif(call: string) {
    if (!call) return;
    this.unwatch(call.toUpperCase());
  }

  handleTransmit(message: string) {
    const now = new Date().toISOString();
    this.addWatchedActivity("TX", {
      index: 0,
      id: "local",
      received_at: now,
      new: true,
      time: now.slice(11, 23),
      snr: 0,
      delta_time: 0,
      delta_frequency: 0,
      mode: String(this.status.mode || ""),
      message,
      low_confidence: false,
      off_air: false,
    });
  }

  private watch(decode: Decode) {
    const callsign = extractCallsign(decode.message, this.status.de_call || "");
    if (!callsign) {
      this.flash("无法从消息中识别呼号");
      return;
    }
    const now = new Date().toISOString();
    const existing = this.watched.get(callsign);
    this.watched.set(callsign, {
      callsign,
      grid: extractGrid(decode.message),
      firstSeen: existing?.firstSeen || now,
      lastSeen: now,
      lastDecode: decode,
    });
    this.addWatchedActivity(callsign, decode);
    this.watched = new Map(this.watched);
  }

  private matchWatched(decode: Decode) {
    for (const [call, item] of this.watched) {
      if (decode.message.toUpperCase().includes(call)) {
        this.watched.set(call, { ...item, lastSeen: new Date().toISOString(), lastDecode: decode });
        this.addWatchedActivity(call, decode);
      }
    }
    this.watched = new Map(this.watched);
  }

  private unwatch(callsign: string) {
    const call = callsign.toUpperCase();
    this.watched.delete(call);
    this.watchedActivities = this.watchedActivities.filter((item) => item.watchCall !== call);
    for (const key of Array.from(this.watchedActivityKeys)) {
      if (key.startsWith(`${call}:`)) this.watchedActivityKeys.delete(key);
    }
    this.watched = new Map(this.watched);
  }

  private addWatchedActivity(call: string, decode: Decode) {
    const key = `${call}:${decode.index}:${decode.received_at}:${decode.message}`;
    if (this.watchedActivityKeys.has(key)) return;
    this.watchedActivityKeys.add(key);
    this.watchedActivities = [...this.watchedActivities, { ...decode, watchCall: call }].slice(-500);
  }

  private async reply(index: number) {
    try {
      await postJson("/api/reply", { decode_index: index });
      this.flash("Reply sent");
    } catch (error) {
      this.flash(String(error));
    }
  }

  private async replyAndWatch(decode: Decode) {
    this.watch(decode);
    await this.reply(decode.index);
  }

  private async replyAndWatchFromLink(event: Event, decode: Decode) {
    event.preventDefault();
    await this.replyAndWatch(decode);
  }

  private flash(message: string) {
    this.message = message;
    window.setTimeout(() => (this.message = ""), 2500);
  }
}

function extractCallsign(message: string, ownCall: string): string {
  const words = message.toUpperCase().split(/\s+/).filter(Boolean);
  const own = ownCall.toUpperCase();
  if (words[0] === "CQ") {
    return words.find((word, index) => index > 0 && isCall(word)) || "";
  }
  return words.find((word) => isCall(word) && word !== own) || "";
}

function extractGrid(message: string): string | undefined {
  return message.toUpperCase().split(/\s+/).find(isGrid);
}

function isCall(word: string): boolean {
  return !isGrid(word) && /^[A-Z0-9/]{3,12}$/.test(word) && /\d/.test(word) && /[A-Z]/.test(word);
}

function isGrid(word: string): boolean {
  return /^[A-R]{2}\d{2}([A-X]{2})?$/.test(word);
}

function timeSlot(time: string): string {
  return time.split(".")[0] || "unknown";
}

function formatDt(value: number): string {
  return value.toFixed(1);
}
