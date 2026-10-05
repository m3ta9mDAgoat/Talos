const API_BASE = import.meta.env.VITE_API_URL ?? (import.meta.env.DEV ? 'http://localhost:8000' : '');

async function errorMessage(response: Response): Promise<string> {
  const text = await response.text();
  const contentType = response.headers.get('content-type') ?? '';
  if (contentType.includes('application/json')) {
    try {
      const data = JSON.parse(text);
      const detail = data?.detail ?? data?.error ?? data?.message;
      if (detail) return `HTTP ${response.status}: ${String(detail)}`;
    } catch { /* fall through */ }
  }
  const trimmed = text.trim();
  if (/^<!doctype html/i.test(trimmed) || /^<html/i.test(trimmed)) {
    return `HTTP ${response.status}: hosting proxy/server temporarily unavailable. Talos can retry safely.`;
  }
  return `HTTP ${response.status}: ${trimmed.slice(0, 500) || response.statusText}`;
}

export async function apiGet<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, { cache: 'no-store' });
  if (!response.ok) throw new Error(await errorMessage(response));
  return response.json() as Promise<T>;
}

export async function apiPost<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(await errorMessage(response));
  return response.json() as Promise<T>;
}
