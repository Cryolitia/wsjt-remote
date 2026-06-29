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
            <thead><tr><th>Sig</th><th>DT</th><th>Freq</th><th>Message</th><th>DXCC</th><th></th></tr></thead>
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
            <thead><tr><th>Call</th><th>Sig</th><th>DT</th><th>Freq</th><th>Message</th><th>DXCC</th><th></th></tr></thead>
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
      const highlightClass = activityHighlightClass(decode);
      const fullRowHighlight = shouldHighlightFullRow(decode);
      if (slot !== lastSlot) {
        rows.push(html`<tr><th colspan="6"><small><strong>${slot}</strong></small></th></tr>`);
        lastSlot = slot;
      }
      rows.push(html`
        <tr class=${fullRowHighlight ? highlightClass : ""} @dblclick=${() => this.watch(decode)}>
          <td><small><strong>${decode.snr}</strong></small></td>
          <td><small>${formatDt(decode.delta_time)}</small></td>
          <td><small>${decode.delta_frequency}</small></td>
          <td><small class=${activityMessageClass(decode, this.status.de_call)}>${decode.message}</small></td>
          <td class=${fullRowHighlight ? "" : highlightClass}><small>${formatDxcc(decode)}</small></td>
          <td><small><a href="#" @click=${(event: Event) => this.replyAndWatchFromLink(event, decode)}>Reply</a></small></td>
        </tr>
      `);
    }
    return rows;
  }

  private renderWatchedRows() {
    const rows = [];
    let lastSlot = "";
    const activities = this.watchedActivities
      .map((item, order) => ({ item, order }))
      .sort((left, right) => activityTimeMs(left.item) - activityTimeMs(right.item) || left.order - right.order)
      .slice(-this.limit)
      .reverse();
    for (const { item } of activities) {
      const slot = timeSlot(item.time);
      const highlightClass = activityHighlightClass(item);
      const fullRowHighlight = shouldHighlightFullRow(item);
      if (slot !== lastSlot) {
        rows.push(html`<tr><th colspan="7"><small><strong>${slot}</strong></small></th></tr>`);
        lastSlot = slot;
      }
      rows.push(html`
        <tr class=${fullRowHighlight ? highlightClass : ""}>
          <td><small><strong>${item.watchCall}</strong></small></td>
          <td><small>${item.snr}</small></td>
          <td><small>${formatDt(item.delta_time)}</small></td>
          <td><small>${item.delta_frequency}</small></td>
          <td><small class=${activityMessageClass(item, this.status.de_call)}>${item.message}</small></td>
          <td class=${fullRowHighlight ? "" : highlightClass}><small>${formatDxcc(item)}</small></td>
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
      for (const decode of this.decodes) {
        this.watchIfCallingOwn(decode);
        this.matchWatched(decode);
      }
    }
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
    this.addWatch(callsign, decode);
  }

  private watchIfCallingOwn(decode: Decode) {
    if (decode.id === "local" || !isCallingOwnCall(decode.message, this.status.de_call)) return;
    const now = new Date().toISOString();
    const callsign = extractCallsign(decode.message, this.status.de_call || "");
    if (!callsign) return;
    const existing = this.watched.get(callsign);
    if (existing?.lastDecode === decode) return;
    this.addWatch(callsign, decode, now);
  }

  private addWatch(callsign: string, decode: Decode, now = new Date().toISOString()) {
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
    return words.find((word, index) => index > 0 && isCall(word) && word !== own) || "";
  }
  const calls = words.filter(isCall);
  if (calls.length >= 2) return calls[1] !== own ? calls[1] : "";
  return calls.find((word) => word !== own) || "";
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

function formatDxcc(decode: Decode): string {
  return decode.dxcc_label || decode.dxcc_entity || "-";
}

function activityTimeMs(decode: Decode): number {
  const timestamp = Date.parse(decode.received_at);
  return Number.isFinite(timestamp) ? timestamp : 0;
}

function activityHighlightClass(decode: Decode): string {
  if (decode.id === "local") return "activity-row--tx";
  if (decode.dxcc_entity && decode.worked_dxcc === false) return "activity-row--new-dxcc";
  if (decode.dxcc_entity && decode.worked_dxcc === true && decode.worked_dxcc_band === false) return "activity-row--band-dxcc";
  if (decode.worked_grid4 && decode.worked_grid === false) return "activity-row--new-grid";
  if (decode.worked_grid4 && decode.worked_grid === true && decode.worked_grid_band === false) return "activity-row--band-grid";
  if (decode.worked_call === false) return "activity-row--new-call";
  if (decode.worked_call === true && decode.worked_call_band === false) return "activity-row--band-call";
  return "";
}

function shouldHighlightFullRow(decode: Decode): boolean {
  if (decode.id === "local") return true;
  const words = decode.message.toUpperCase().split(/\s+/).filter(Boolean);
  return words.some((word) => word === "CQ" || word === "73" || word === "RRR" || word === "RR73");
}

function activityMessageClass(decode: Decode, ownCall?: string): string {
  const classes = [];
  if (decode.dxcc_call && decode.dxcc_call !== ownCall?.toUpperCase() && decode.worked_call_band) classes.push("activity-message--worked-call");
  if (isCallingOwnCall(decode.message, ownCall)) classes.push("activity-message--calling-own");
  return classes.join(" ");
}

function isCallingOwnCall(message: string, ownCall?: string): boolean {
  const own = ownCall?.toUpperCase();
  if (!own) return false;
  const words = message.toUpperCase().split(/\s+/).filter(Boolean);
  return words[0] === own;
}
