// Formatação de números, datas e pequenos componentes em SVG.

const NBSP = " ";

export function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]);
}

export function fmtNum(value, decimals = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  return Number(value).toLocaleString("pt-BR", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

export function fmtPrice(value, decimals = 2) {
  return fmtNum(value, decimals);
}

export function fmtPct(value, decimals = 2) {
  if (value === null || value === undefined) return "—";
  const sign = value > 0 ? "+" : value < 0 ? "−" : "";
  return `${sign}${fmtNum(Math.abs(value), decimals)}%`;
}

export function fmtCompact(value) {
  if (value === null || value === undefined) return "—";
  return Number(value).toLocaleString("pt-BR", { notation: "compact", maximumFractionDigits: 1 });
}

export function fmtTime(iso, withSeconds = false) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit", second: withSeconds ? "2-digit" : undefined });
}

export function fmtDateTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return `${d.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" })}${NBSP}${d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" })}`;
}

export function fmtCountdown(ms) {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return "--:--";
  const total = Math.max(0, Math.round(ms / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const pad = (n) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
}

export function fmtAgo(iso) {
  if (!iso) return "—";
  const secs = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (secs < 5) return "agora";
  if (secs < 60) return `há ${secs}s`;
  if (secs < 3600) return `há ${Math.floor(secs / 60)} min`;
  return `há ${Math.floor(secs / 3600)} h`;
}

export const KIND_LABEL = {
  b3: "Ação B3", b3fut: "Futuro B3", crypto: "Cripto", forex: "Forex", futures: "Futuro", index: "Índice", us: "Ação EUA",
  equity: "Ação", etf: "ETF", cryptocurrency: "Cripto", currency: "Moeda", future: "Futuro",
};

export const TONE_COLOR = {
  buy: "#16c784", sell: "#f0454f", exit: "#f0454f", prepare: "#f5a524", hold: "#4c9dff", wait: "#7d8aa3",
};

export function sparkline(values, { width = 120, height = 32, color = "#f0b90b", fill = true } = {}) {
  const pts = (values || []).filter((v) => Number.isFinite(v));
  if (pts.length < 2) return "";
  const min = Math.min(...pts);
  const max = Math.max(...pts);
  const span = max - min || 1;
  const step = width / (pts.length - 1);
  const coords = pts.map((v, i) => [i * step, height - 2 - ((v - min) / span) * (height - 4)]);
  const path = coords.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join("");
  const id = `g${Math.random().toString(36).slice(2, 8)}`;
  const area = fill
    ? `<defs><linearGradient id="${id}" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="${color}" stop-opacity=".28"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient></defs>
       <path d="${path}L${width},${height}L0,${height}Z" fill="url(#${id})" stroke="none"/>`
    : "";
  return `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none">${area}<path d="${path}" stroke="${color}" stroke-width="1.6" fill="none" vector-effect="non-scaling-stroke"/></svg>`;
}
