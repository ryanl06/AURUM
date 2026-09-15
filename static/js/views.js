// Telas secundárias: favoritos (lateral), radar, histórico e operações.

import { api } from "./api.js";
import { esc, fmtDateTime, fmtNum, fmtPct, fmtPrice, sparkline, TONE_COLOR } from "./format.js";

const $ = (id) => document.getElementById(id);

const ACTION_LABEL = {
  ENTRAR_AGORA: "ENTRAR AGORA", PREPARE_SE: "PREPARE-SE", AGUARDE: "AGUARDE", SAIR_AGORA: "SAIR AGORA",
  MANTENHA: "MANTENHA", MERCADO_FECHADO: "FECHADO", PRECO: "PREÇO",
};
const ACTION_TONE = {
  ENTRAR_AGORA: "buy", PREPARE_SE: "prepare", AGUARDE: "wait", SAIR_AGORA: "exit", MANTENHA: "hold", MERCADO_FECHADO: "wait",
};
const PRIORITY = { ENTRAR_AGORA: 0, SAIR_AGORA: 1, PREPARE_SE: 2, MANTENHA: 3, AGUARDE: 4, MERCADO_FECHADO: 5 };

function toneFor(item) {
  if (item.action === "ENTRAR_AGORA") return item.side === "VENDA" ? "sell" : "buy";
  return item.tone || ACTION_TONE[item.action] || "wait";
}

export function renderWatchlist(favorites, radarItems, activeSymbol) {
  const bySymbol = Object.fromEntries((radarItems || []).map((r) => [r.symbol, r]));
  $("watchlist").innerHTML = favorites.map((f) => {
    const r = bySymbol[f.symbol];
    const tone = r && !r.error ? toneFor(r) : "wait";
    const color = r?.change_pct >= 0 ? TONE_COLOR.buy : TONE_COLOR.sell;
    return `
      <li class="watch-item ${f.symbol === activeSymbol ? "active" : ""}" data-symbol="${esc(f.symbol)}" title="${esc(r?.headline || f.name)}">
        <div><div class="watch-sym">${esc(f.symbol.replace(".SA", ""))}</div><div class="watch-name">${esc(r?.name || f.name)}</div></div>
        <div><div class="watch-price">${r && !r.error ? fmtPrice(r.price, r.decimals) : "…"}</div>
          <div class="watch-row2">${r && !r.error ? `<span class="mono small ${r.change_pct >= 0 ? "up" : "down"}">${fmtPct(r.change_pct)}</span>` : ""}</div></div>
        ${r && !r.error ? `<div class="watch-spark">${sparkline(r.spark, { width: 200, height: 22, color, fill: false })}</div>
          <div style="grid-column:1/-1"><span class="dot-pill tone-${tone}">${esc(r.title)}${r.side && r.action !== "AGUARDE" ? ` · ${r.side}` : ""}</span></div>` : ""}
        <button class="watch-remove" data-remove="${esc(f.symbol)}" title="Remover dos favoritos" aria-label="Remover">✕</button>
      </li>`;
  }).join("") || '<li class="muted small" style="padding:8px">Nenhum favorito. Clique na ☆ ao lado do nome do ativo.</li>';
}

export function renderRadar(items) {
  const sorted = [...items].sort((a, b) => {
    if (a.error || b.error) return a.error ? 1 : -1;
    return (PRIORITY[a.action] ?? 9) - (PRIORITY[b.action] ?? 9) || Math.abs(b.score ?? 0) - Math.abs(a.score ?? 0);
  });
  $("radar-grid").innerHTML = sorted.map((r) => {
    if (r.error) {
      return `<article class="card radar-card error" data-symbol="${esc(r.symbol)}"><div class="radar-sym">${esc(r.symbol)}</div><div class="muted small">${esc(r.error)}</div></article>`;
    }
    const tone = toneFor(r);
    const color = r.change_pct >= 0 ? TONE_COLOR.buy : TONE_COLOR.sell;
    return `
      <article class="card radar-card" data-symbol="${esc(r.symbol)}">
        <div class="radar-top">
          <div><div class="radar-sym">${esc(r.symbol)}</div><div class="radar-name">${esc(r.name)}</div></div>
          <div><div class="radar-price">${fmtPrice(r.price, r.decimals)}</div><div class="mono small ${r.change_pct >= 0 ? "up" : "down"}" style="text-align:right">${fmtPct(r.change_pct)}</div></div>
        </div>
        <div class="radar-spark">${sparkline(r.spark, { width: 280, height: 46, color })}</div>
        <div class="radar-signal">
          <span class="radar-action" style="color:${TONE_COLOR[tone]}">${esc(r.title)}</span>
          <span class="dot-pill tone-${r.trend === "ALTA" ? "buy" : r.trend === "BAIXA" ? "sell" : "wait"}">${esc(r.trend)}</span>
        </div>
        <div class="radar-headline">${esc(r.headline)}</div>
        <div class="radar-foot">
          <span>Força <b>${r.score > 0 ? "+" : ""}${fmtNum(r.score, 0)}</b></span>
          <span>RSI <b>${fmtNum(r.rsi, 0)}</b></span>
          <span title="Confiabilidade medida fora da amostra">Confiab. <b class="${r.reliability === "ALTA" ? "up" : r.reliability === "BAIXA" ? "down" : ""}">${esc(r.reliability || "—")}</b></span>
          <span>${r.market_open ? "● aberto" : "○ fechado"}</span>
        </div>
      </article>`;
  }).join("") || '<p class="muted">Adicione ativos aos favoritos para ver o radar.</p>';
}

export async function loadHistory({ symbol, tf, currentOnly, onlySignals }) {
  const params = { limit: 300, only_signals: onlySignals || undefined };
  if (currentOnly) params.symbol = symbol;
  const [rows, alerts] = await Promise.all([api.history(params), api.alerts(0, 60)]);
  $("hist-csv").href = api.csvUrl(currentOnly ? { symbol } : {});
  $("hist-report").href = api.reportUrl(symbol, tf);

  $("history-table").innerHTML = `
    <thead><tr><th>Data</th><th>Ativo</th><th>Tempo</th><th>Sinal</th><th class="num">Preço</th><th class="num">Força</th><th class="num">RSI</th><th>Previsão</th><th>Resumo</th></tr></thead>
    <tbody>${rows.length ? rows.map((h) => {
      const tone = h.action === "ENTRAR_AGORA" ? (h.side === "VENDA" ? "sell" : "buy") : ACTION_TONE[h.action] || "wait";
      return `<tr>
        <td class="mono">${fmtDateTime(h.ts)}</td><td class="mono">${esc(h.symbol)}</td><td class="mono">${esc(h.timeframe)}</td>
        <td><span class="dot-pill tone-${tone}">${esc(ACTION_LABEL[h.action] || h.action)}${h.side && h.action !== "AGUARDE" ? ` · ${esc(h.side)}` : ""}</span></td>
        <td class="num">${fmtNum(h.price, h.price < 10 ? 5 : 2)}</td>
        <td class="num ${h.score >= 0 ? "up" : "down"}">${h.score > 0 ? "+" : ""}${fmtNum(h.score, 0)}</td>
        <td class="num">${fmtNum(h.rsi, 0)}</td><td>${esc(h.forecast || "")}</td><td class="wrap">${esc(h.headline || "")}</td></tr>`;
    }).join("") : '<tr class="empty-row"><td colspan="9">Ainda sem histórico. O scanner salva uma análise a cada ciclo — volte em alguns minutos.</td></tr>'}</tbody>`;

  $("alert-list").innerHTML = alerts.length
    ? alerts.map((al) => `<li class="alert-item ${esc(al.level)}"><div class="t">${esc(al.title)}</div><div class="m">${esc(al.message)}</div><div class="d">${fmtDateTime(al.ts)} · ${esc(al.timeframe)}</div></li>`).join("")
    : '<li class="muted small">Nenhum alerta ainda.</li>';
}

const STATUS_LABEL = { open: ["EM ANDAMENTO", "tone-hold"], win: ["GANHOU", "tone-buy"], loss: ["PERDEU", "tone-sell"] };

export async function loadPerformance({ symbol, currentOnly }) {
  const data = await api.signals(currentOnly ? { symbol } : {});
  const s = data.summary;
  const pf = s.profit_factor;
  $("perf-kpis").innerHTML = `
    <div class="card kpi"><span>Sinais emitidos</span><b>${s.total}</b></div>
    <div class="card kpi"><span>Conferidos</span><b>${s.closed}</b></div>
    <div class="card kpi"><span>Acerto real</span><b>${s.win_rate === null ? "—" : `${fmtNum(s.win_rate, 0)}%`}</b></div>
    <div class="card kpi" title="Soma dos resultados em múltiplos do risco. Fator de lucro: ${pf === null ? "—" : fmtNum(pf, 2)}"><span>Resultado</span><b class="${s.total_r >= 0 ? "up" : "down"}">${s.total_r > 0 ? "+" : ""}${fmtNum(s.total_r, 1)}R</b></div>`;

  $("perf-table").innerHTML = `
    <thead><tr><th>Emitido</th><th>Ativo</th><th>Tempo</th><th>Lado</th><th>Regra</th><th class="num">Entrada</th><th class="num">Stop</th><th class="num">Alvo</th><th class="num">Conf.</th><th>Situação</th><th class="num">Resultado</th></tr></thead>
    <tbody>${data.items.length ? data.items.map((r) => {
      const [label, cls] = STATUS_LABEL[r.status] || [r.status, "tone-wait"];
      const d = r.entry < 10 ? 5 : 2;
      return `<tr>
        <td class="mono">${fmtDateTime(r.ts)}</td><td class="mono">${esc(r.symbol)}</td><td class="mono">${esc(r.timeframe)}</td>
        <td><span class="dot-pill ${r.side === "COMPRA" ? "tone-buy" : "tone-sell"}">${esc(r.side)}</span></td>
        <td>${esc(r.setup || "—")}</td>
        <td class="num">${fmtNum(r.entry, d)}</td><td class="num">${fmtNum(r.stop, d)}</td><td class="num">${fmtNum(r.target2, d)}</td>
        <td class="num">${r.confidence === null ? "—" : `${fmtNum(r.confidence, 0)}%`}</td>
        <td><span class="dot-pill ${cls}">${label}</span></td>
        <td class="num ${r.result_r > 0 ? "up" : r.result_r < 0 ? "down" : ""}">${r.result_r === null ? "—" : `${r.result_r > 0 ? "+" : ""}${fmtNum(r.result_r, 2)}R`}</td></tr>`;
    }).join("") : '<tr class="empty-row"><td colspan="11">Nenhum ENTRAR AGORA emitido ainda. Com o AURUM aberto, cada sinal novo aparece aqui e é conferido automaticamente.</td></tr>'}</tbody>`;

  $("perf-assets").innerHTML = `
    <thead><tr><th>Ativo · tempo</th><th class="num">Sinais</th><th class="num">Acerto</th><th class="num">Resultado</th></tr></thead>
    <tbody>${data.by_asset.length ? data.by_asset.map((x) => `<tr><td class="mono">${esc(x.key)}</td><td class="num">${x.signals}</td>
      <td class="num">${fmtNum(x.win_rate, 0)}%</td><td class="num ${x.total_r >= 0 ? "up" : "down"}">${x.total_r > 0 ? "+" : ""}${fmtNum(x.total_r, 1)}R</td></tr>`).join("")
      : '<tr class="empty-row"><td colspan="4">Sem sinais conferidos ainda.</td></tr>'}</tbody>`;
}

export async function loadOperations() {
  const positions = await api.positions();
  const open = positions.filter((p) => p.status === "open");
  const closed = positions.filter((p) => p.status === "closed");

  const live = await Promise.all(open.map((p) => api.analysis(p.symbol, p.timeframe || "15m").catch(() => null)));
  $("ops-open").innerHTML = `
    <thead><tr><th>Ativo</th><th>Lado</th><th>Aberta em</th><th class="num">Entrada</th><th class="num">Stop</th><th class="num">Agora</th><th class="num">Resultado</th><th>Sinal</th><th></th></tr></thead>
    <tbody>${open.length ? open.map((p, i) => {
      const a = live[i];
      const d = a?.decimals ?? 2;
      const pnl = a?.position?.pnl_pct;
      return `<tr>
        <td class="mono"><a href="#/${encodeURIComponent(p.symbol)}/${p.timeframe || "15m"}" class="link-btn">${esc(p.symbol)}</a></td>
        <td><span class="dot-pill ${p.side === "COMPRA" ? "tone-buy" : "tone-sell"}">${esc(p.side)}</span></td>
        <td class="mono">${fmtDateTime(p.opened_at)}</td>
        <td class="num">${fmtPrice(p.entry_price, d)}</td><td class="num">${p.stop_price ? fmtPrice(p.stop_price, d) : "3%"}</td>
        <td class="num">${a ? fmtPrice(a.price, d) : "—"}</td>
        <td class="num ${pnl >= 0 ? "up" : "down"}">${pnl === undefined ? "—" : fmtPct(pnl)}</td>
        <td>${a ? `<span class="dot-pill tone-${a.signal.tone}">${esc(a.signal.title)}</span>` : "—"}</td>
        <td><button class="btn btn-danger btn-xs" data-close="${p.id}" data-price="${a?.price ?? ""}">Encerrar</button></td></tr>`;
    }).join("") : '<tr class="empty-row"><td colspan="9">Nenhuma operação aberta. No painel, clique em “Já entrei nesta operação”.</td></tr>'}</tbody>`;

  $("ops-closed").innerHTML = `
    <thead><tr><th>Ativo</th><th>Lado</th><th>Abertura</th><th>Fechamento</th><th class="num">Entrada</th><th class="num">Saída</th><th class="num">Resultado</th><th class="num">R</th><th class="num">R$</th><th></th></tr></thead>
    <tbody>${closed.length ? closed.map((p) => `<tr>
        <td class="mono">${esc(p.symbol)}</td><td><span class="dot-pill ${p.side === "COMPRA" ? "tone-buy" : "tone-sell"}">${esc(p.side)}</span></td>
        <td class="mono">${fmtDateTime(p.opened_at)}</td><td class="mono">${fmtDateTime(p.closed_at)}</td>
        <td class="num">${fmtNum(p.entry_price, p.entry_price < 10 ? 5 : 2)}</td><td class="num">${fmtNum(p.exit_price, p.exit_price < 10 ? 5 : 2)}</td>
        <td class="num ${p.pnl_pct >= 0 ? "up" : "down"}">${fmtPct(p.pnl_pct)}</td>
        <td class="num ${(p.result_r ?? 0) >= 0 ? "up" : "down"}">${p.result_r === null || p.result_r === undefined ? "—" : `${p.result_r > 0 ? "+" : ""}${fmtNum(p.result_r, 2)}R`}</td>
        <td class="num ${(p.pnl_money ?? 0) >= 0 ? "up" : "down"}">${p.pnl_money === null || p.pnl_money === undefined ? "—" : fmtNum(p.pnl_money, 2)}</td>
        <td><button class="btn btn-ghost btn-xs" data-delete="${p.id}">Remover</button></td></tr>`).join("")
      : '<tr class="empty-row"><td colspan="10">Nenhuma operação encerrada ainda.</td></tr>'}</tbody>`;

  const wins = closed.filter((p) => p.pnl_pct > 0).length;
  const totalR = closed.reduce((sum, p) => sum + (p.result_r || 0), 0);
  const money = closed.reduce((sum, p) => sum + (p.pnl_money || 0), 0);
  $("ops-kpis").innerHTML = `
    <div class="card kpi"><span>Abertas</span><b>${open.length}</b></div>
    <div class="card kpi"><span>Encerradas</span><b>${closed.length}</b></div>
    <div class="card kpi"><span>Taxa de acerto</span><b>${closed.length ? `${fmtNum((wins / closed.length) * 100, 0)}%` : "—"}</b></div>
    <div class="card kpi" title="Resultado em R$: ${fmtNum(money, 2)}"><span>Resultado em R</span><b class="${totalR >= 0 ? "up" : "down"}">${closed.length ? `${totalR > 0 ? "+" : ""}${fmtNum(totalR, 1)}R` : "—"}</b></div>`;
}
