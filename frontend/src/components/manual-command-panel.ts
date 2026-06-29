import { LitElement, html } from "lit";
import { customElement, state } from "lit/decorators.js";
import { postJson } from "../api";

@customElement("manual-command-panel")
export class ManualCommandPanel extends LitElement {
  createRenderRoot() {
    return this;
  }

  @state() private raw = '{\n  "type": "Replay",\n  "fields": {}\n}';
  @state() private message = "";

  render() {
    return html`
      <article>
        <header><strong>Manual Commands</strong></header>
        ${this.message ? html`<p><mark>${this.message}</mark></p>` : null}
        <div class="grid">
          <button @click=${() => this.send({ type: "Replay", fields: {} })}>Replay</button>
          <button @click=${() => this.send({ type: "HaltTx", fields: { auto_tx_only: false } })}>Halt TX</button>
          <button @click=${() => this.send({ type: "Clear", fields: { window: 2 } })}>Clear</button>
        </div>
        <textarea rows="12" .value=${this.raw} @input=${this.onInput}></textarea>
        <button @click=${this.sendRaw}>Send JSON</button>
      </article>
    `;
  }

  private onInput(event: Event) {
    this.raw = (event.target as HTMLTextAreaElement).value;
  }

  private async sendRaw() {
    try {
      await this.send(JSON.parse(this.raw));
    } catch (error) {
      this.flash(String(error));
    }
  }

  private async send(body: unknown) {
    try {
      await postJson("/api/debug/send", body);
      this.flash("sent");
    } catch (error) {
      this.flash(String(error));
    }
  }

  private flash(message: string) {
    this.message = message;
    window.setTimeout(() => (this.message = ""), 2500);
  }
}
