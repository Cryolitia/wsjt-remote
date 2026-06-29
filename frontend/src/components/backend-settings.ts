import { LitElement, html } from "lit";
import { customElement, state } from "lit/decorators.js";
import { getBackendUrl, resetBackendUrl, setBackendUrl } from "../api";

@customElement("backend-settings")
export class BackendSettings extends LitElement {
  createRenderRoot() {
    return this;
  }

  @state() private value = getBackendUrl();

  render() {
    return html`
      <details>
        <summary>后端地址</summary>
        <fieldset role="group">
          <input .value=${this.value} @input=${this.onInput} placeholder="http://127.0.0.1:8080">
          <button @click=${this.save}>保存</button>
          <button class="secondary" @click=${this.reset}>重置</button>
        </fieldset>
      </details>
    `;
  }

  private onInput(event: Event) {
    this.value = (event.target as HTMLInputElement).value;
  }

  private save() {
    setBackendUrl(this.value);
    this.dispatchEvent(new CustomEvent("backend-change", { bubbles: true, composed: true }));
  }

  private reset() {
    resetBackendUrl();
    this.value = getBackendUrl();
    this.dispatchEvent(new CustomEvent("backend-change", { bubbles: true, composed: true }));
  }
}
