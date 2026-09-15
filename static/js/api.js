// Cliente da API do AURUM.

export class OfflineError extends Error {}

const TIMEOUT_MS = 60000;
const listeners = new Set();
let online = true;

export function onConnectionChange(fn) {
  listeners.add(fn);
}

function setOnline(value) {
  if (value === online) return;
  online = value;
  listeners.forEach((fn) => fn(value));
}

async function request(method, url, body) {
  const options = { method, headers: { "X-AURUM": "1" } };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  options.signal = controller.signal;
  let response;
  try {
    response = await fetch(url, options);
  } catch (err) {
    if (err.name === "AbortError") throw new Error("O servidor demorou demais para responder. Tentando de novo…");
    setOnline(false);
    throw new OfflineError("O AURUM está desligado neste computador.");
  } finally {
    clearTimeout(timer);
  }
  setOnline(true);
  let data = null;
  try {
    data = await response.json();
  } catch {
    /* resposta sem JSON */
  }
  if (!response.ok) {
    const detail = data?.detail;
    const message = typeof detail === "string"
      ? detail
      : Array.isArray(detail) ? detail.map((d) => d.msg).join("; ") : `Erro ${response.status}`;
    throw new Error(message);
  }
  return data;
}

const qs = (params) => new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "")).toString();

export const api = {
  health: () => request("GET", "/api/health"),
  meta: () => request("GET", "/api/meta"),
  search: (q) => request("GET", `/api/search?${qs({ q })}`),
  analysis: (symbol, tf, force = false) => request("GET", `/api/analysis?${qs({ symbol, tf, force: force || undefined })}`),
  live: (symbol, tf, barStart) => request("GET", `/api/live?${qs({ symbol, tf, bar_start: barStart })}`),
  radar: (tf) => request("GET", `/api/radar?${qs({ tf })}`),
  favorites: () => request("GET", "/api/favorites"),
  addFavorite: (symbol) => request("POST", "/api/favorites", { symbol }),
  forexPack: () => request("POST", "/api/favorites/forex-pack"),
  b3Pack: () => request("POST", "/api/favorites/b3-pack"),
  signals: (params) => request("GET", `/api/signals?${qs(params || {})}`),
  removeFavorite: (symbol) => request("DELETE", `/api/favorites/${encodeURIComponent(symbol)}`),
  history: (params) => request("GET", `/api/history?${qs(params)}`),
  alerts: (sinceId = 0, limit = 50) => request("GET", `/api/alerts?${qs({ since_id: sinceId, limit })}`),
  positions: (status) => request("GET", `/api/positions?${qs({ status })}`),
  openPosition: (data) => request("POST", "/api/positions", data),
  closePosition: (id, exitPrice) => request("POST", `/api/positions/${id}/close`, { exit_price: exitPrice ?? null }),
  deletePosition: (id) => request("DELETE", `/api/positions/${id}`),
  settings: () => request("GET", "/api/settings"),
  saveSettings: (values) => request("PUT", "/api/settings", values),
  telegramTest: (body) => request("POST", "/api/telegram/test", body),
  telegramDetect: (body) => request("POST", "/api/telegram/detect", body),
  scanner: () => request("GET", "/api/scanner"),
  mt5Status: () => request("GET", "/api/mt5/status"),
  mt5Reconnect: () => request("POST", "/api/mt5/reconnect"),
  scanNow: () => request("POST", "/api/scanner/run"),
  csvUrl: (params) => `/api/history.csv?${qs(params)}`,
  reportUrl: (symbol, tf) => `/api/report.txt?${qs({ symbol, tf })}`,
};
