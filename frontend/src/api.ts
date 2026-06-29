const KEY = "wsjtx_remote_backend_url";

export function getBackendUrl(): string {
  return localStorage.getItem(KEY) || defaultBackendUrl();
}

function defaultBackendUrl(): string {
  if (window.location.port === "5173") {
    return `${window.location.protocol}//${window.location.hostname}:8080`;
  }
  return window.location.origin;
}

export function setBackendUrl(value: string): void {
  const trimmed = value.trim().replace(/\/$/, "");
  if (trimmed) {
    localStorage.setItem(KEY, trimmed);
  }
}

export function resetBackendUrl(): void {
  localStorage.removeItem(KEY);
}

export function apiUrl(path: string): string {
  return `${getBackendUrl()}${path.startsWith("/") ? path : `/${path}`}`;
}

export function wsUrl(path: string): string {
  const url = new URL(apiUrl(path));
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}

export async function postJson(path: string, body: unknown = {}): Promise<unknown> {
  const response = await fetch(apiUrl(path), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = typeof data === "object" && data && "error" in data ? String((data as { error: unknown }).error) : response.statusText;
    throw new Error(message);
  }
  return data;
}
