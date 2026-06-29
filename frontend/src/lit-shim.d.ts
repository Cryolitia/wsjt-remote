declare module "lit" {
  export class LitElement extends HTMLElement {
    connectedCallback(): void;
    disconnectedCallback(): void;
    requestUpdate(name?: PropertyKey, oldValue?: unknown): void;
  }

  export function html(strings: TemplateStringsArray, ...values: unknown[]): unknown;
}

declare module "lit/decorators.js" {
  export function customElement(tagName: string): ClassDecorator;
  export function property(options?: Record<string, unknown>): PropertyDecorator;
  export function state(options?: Record<string, unknown>): PropertyDecorator;
  export function query(selector: string): PropertyDecorator;
}
