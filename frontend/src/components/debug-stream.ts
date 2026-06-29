import { LitElement, html } from "lit";
import { customElement, property } from "lit/decorators.js";
import type { DebugEvent } from "../types";

@customElement("debug-stream")
export class DebugStream extends LitElement {
  createRenderRoot() {
    return this;
  }

  @property({ type: Array }) events: DebugEvent[] = [];

  render() {
    return html`
      <article>
        <header><strong>Raw Stream</strong></header>
        <table>
          <thead><tr><th>Time</th><th>Dir</th><th>Type</th><th>Remote</th><th>Size</th><th>Details</th></tr></thead>
          <tbody>
            ${this.events.slice().reverse().map((event) => {
              const msg = event.message as { type?: string; id?: string } | null;
              return html`
                <tr>
                  <td>${event.time}</td>
                  <td>${event.direction}</td>
                  <td>${event.error || msg?.type || "?"}</td>
                  <td>${event.remote}</td>
                  <td>${event.size}</td>
                  <td>
                    <details>
                      <summary>open</summary>
                      <pre>${event.hex}</pre>
                      <pre>${JSON.stringify(event.message, null, 2)}</pre>
                    </details>
                  </td>
                </tr>
              `;
            })}
          </tbody>
        </table>
      </article>
    `;
  }
}
