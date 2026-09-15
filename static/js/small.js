// Aba "Começar pequeno": plano de escalada e ranking de moedas medido para a Binance Spot.

import { api } from "./api.js";
import { esc, fmtAgo, fmtCompact, fmtNum, fmtPct, fmtPrice } from "./format.js";
import { toast } from "./notify.js";

const $ = (id) => document.getElementById(id);

const CATEGORY = {
  OPERAVEL: ["PODE OPERAR", "tone-buy"],
  OBSERVAR: ["OBSERVAR", "tone-prepare"],
  EVITAR: ["EVITAR", "tone-sell"],
};
const DECISION = { SUBIR: "tone-buy", MANTER: "tone-wait", DESCER: "tone-sell" };
const money = (v) => `R$ ${fmtNum(v, 2)}`;

let pollTimer = null;
let handlers = { onOpen: () => {}, onSettingsChanged: () => {} };

export function setupSmall(options) {
  handlers = { ...handlers, ...options };
  $("btn-screener-refresh").addEventListener("click", () => loadScreener(true));
  $("screener-grid").addEventListener("click", (e) => {
    const card = e.target.closest("[data-open]");
    if (card) handlers.onOpen(card.dataset.open, card.dataset.tf);
  });
  $("scale-actions").addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-level]");
    if (!btn) return;
    const level = Number(btn.dataset.level);
    const label = btn.dataset.label;
    const ok = window.confirm(level === 0
      ? "Iniciar o treino sem dinheiro? O capital em ⚙ passa a R$ 50 (risco 3%) e a contagem do plano começa agora."
      : `Mudar para o nível ${level} (${label})? O capital em ⚙ passa a ser ${label} e a contagem do nível recomeça.`);
    if (!ok) return;
    btn.disabled = true;
    try {
      renderScaling(await api.setScaleLevel(level));
      handlers.onSettingsChanged();
      toast("Plano de escalada", `Nível ${level} ativo · capital ${label}.`, "success", 4000);
      loadScreener(false);
    } catch (err) {
      toast("Não foi possível mudar o nível", err.message, "danger");
    } finally {
      btn.disabled = false;
    }
  });
}

export function loadSmall() {
  api.scaling().then(renderScaling).catch((err) => toast("Erro no plano de escalada", err.message, "danger"));
  loadScreener(false);
}

export function stopSmall() {
  clearTimeout(pollTimer);
}

async function loadScreener(refresh) {
  clearTimeout(pollTimer);
  try {
    const data = await api.screener(refresh);
    renderScreener(data);
    if (data.running && document.querySelector('[data-view="pequeno"].active')) {
      pollTimer = setTimeout(() => loadScreener(false), 3000);
    }
  } catch (err) {
    $("screener-status").innerHTML = `<p class="muted">Ranking indisponível: ${esc(err.message)}</p>`;
  }
}

// ------------------------------------------------------------------ plano de escalada

function renderScaling(s) {
  $("small-capital").textContent = s.level === 0 ? `Treino · simula ${money(s.capital)}` : `Capital ${money(s.capital)}`;
  const decision = $("scale-decision");
  decision.textContent = s.decision;
  decision.className = `chip dot-pill ${DECISION[s.decision] || "tone-wait"}`;

  $("scale-ladder").innerHTML = s.ladder.map((step) => `
    <li class="${step.level === s.level ? "current" : step.level < s.level ? "done" : ""}">
      <span class="lvl">${step.level === 0 ? "0" : step.level}</span><span class="cap">${esc(step.label)}</span>
    </li>`).join("");

  $("scale-text").textContent = s.text;
  $("scale-checks").innerHTML = s.checks.map((c) => `
    <li class="${c.ok ? "ok" : "no"}"><i>${c.ok ? "✓" : "✕"}</i><span>${esc(c.label)}</span><b>${esc(c.value)}</b></li>`).join("")
    + s.retreat_reasons.map((r) => `<li class="warn"><i>!</i><span>${esc(r)}</span></li>`).join("");
  $("scale-source").innerHTML = `Contando ${esc(s.source)}${s.since ? ` desde ${esc(new Date(s.since).toLocaleDateString("pt-BR"))}` : ""}. `
    + `Perda máxima por operação neste nível: <b>${money(s.max_loss_per_trade)}</b> (${s.risk_pct}% do capital). `
    + (s.risk_pct > 1 ? "Com pouco dinheiro a ordem usa o saldo todo, senão fica abaixo do mínimo da Binance; o stop limita a perda." : "");

  const st = s.stats;
  $("scale-kpis").innerHTML = `
    <div class="card kpi"><span>Operações</span><b>${st.trades}</b></div>
    <div class="card kpi"><span>Acerto</span><b>${st.win_rate === null ? "—" : `${fmtNum(st.win_rate, 0)}%`}</b></div>
    <div class="card kpi"><span>Resultado</span><b class="${st.total_r >= 0 ? "up" : "down"}">${st.total_r > 0 ? "+" : ""}${fmtNum(st.total_r, 1)}R</b></div>`;

  const actions = [];
  const next = s.ladder[s.level + 1];
  const prev = s.ladder[s.level - 1];
  if (!s.started) {
    $("scale-text").textContent = "Plano ainda não iniciado. Comece pelo treino: o AURUM passa a calcular as boletas "
      + "com R$ 50 e confere sozinho cada sinal de cripto, sem você arriscar nada.";
    $("scale-actions").innerHTML = `<button class="btn btn-gold" data-level="0" data-label="treino">Começar o plano (treino com R$ 50)</button>`;
    return;
  }
  if (next) {
    const label = next.level === 1 ? "R$ 50" : next.label;
    actions.push(`<button class="btn ${s.decision === "SUBIR" ? "btn-gold" : "btn-ghost"}" data-level="${next.level}" data-label="${esc(label)}"
      ${s.decision === "SUBIR" ? "" : 'title="As condições ainda não foram cumpridas"'}>Subir para ${esc(next.level === 1 ? "dinheiro real · R$ 50" : next.label)}</button>`);
  }
  if (prev) {
    actions.push(`<button class="btn ${s.decision === "DESCER" ? "btn-danger" : "btn-ghost"}" data-level="${prev.level}" data-label="${esc(prev.level === 0 ? "treino" : prev.label)}">Voltar para ${esc(prev.level === 0 ? "o treino" : prev.label)}</button>`);
  }
  $("scale-actions").innerHTML = actions.join("")
    + (s.decision !== "SUBIR" && next ? '<p class="hint">Subir antes de cumprir as condições vai contra o plano. A decisão é sua.</p>' : "");
}

// ------------------------------------------------------------------ ranking de moedas

function renderScreener(data) {
  const status = $("screener-status");
  if (data.running) {
    const { done, total } = data.progress;
    const pct = total ? Math.round((done / total) * 100) : 0;
    status.innerHTML = `<div class="screener-progress"><span>Analisando as moedas mais negociadas da Binance… ${done}/${total || "?"}</span>
      <div class="bar"><i style="width:${pct}%"></i></div></div>`;
  } else if (data.error) {
    status.innerHTML = `<p class="muted">Última atualização falhou: ${esc(data.error)}</p>`;
  } else if (data.finished_at) {
    const cap = data.capital_brl !== null && data.capital_brl !== undefined ? ` · capital considerado ${money(data.capital_brl)}` : "";
    status.innerHTML = `<p class="muted small">Atualizado ${esc(fmtAgo(data.finished_at))}${cap} · dólar a ${money(data.brl_per_usd || 0)}. Recalcula sozinho a cada 6 horas.</p>`;
  } else {
    status.innerHTML = "";
  }

  const items = data.items || [];
  if (!items.length) {
    $("screener-grid").innerHTML = data.running ? '<p class="muted">Primeira análise leva cerca de 1 a 2 minutos.</p>'
      : '<p class="muted">Clique em “Atualizar ranking”.</p>';
    return;
  }
  $("screener-grid").innerHTML = items.map((it, i) => card(it, i + 1)).join("");
}

function card(it, rank) {
  const [label, tone] = CATEGORY[it.category] || ["—", "tone-wait"];
  const b = it.best;
  const signal = b && b.action !== "AGUARDE" && b.action !== "MERCADO_FECHADO"
    ? `<span class="dot-pill ${b.action === "ENTRAR_AGORA" ? "tone-buy" : "tone-prepare"}">${esc(b.title)}</span>` : "";
  const metrics = b ? `
    <div class="scr-metrics">
      <span title="Tempo gráfico com melhor resultado">Gráfico <b>${esc(b.label)}</b></span>
      <span title="Confiabilidade fora da amostra">Confiab. <b class="${b.reliability === "ALTA" ? "up" : b.reliability === "BAIXA" ? "down" : ""}">${esc(b.reliability)}</b></span>
      <span title="Fator de lucro no período recente (acima de 1 = ganhou)">Fator <b>${b.profit_factor === null ? "—" : fmtNum(b.profit_factor, 2)}</b></span>
      <span title="Resultado somado em múltiplos do risco no período recente">Result. <b class="${b.total_r >= 0 ? "up" : "down"}">${b.total_r > 0 ? "+" : ""}${fmtNum(b.total_r, 1)}R</b></span>
      <span title="Sinais medidos no período recente">Sinais <b>${b.trades}</b></span>
      <span title="Capital mínimo para a entrada e a proteção OCO passarem no mínimo da Binance">Mínimo <b class="${it.fits_capital === false ? "down" : ""}">${b.min_capital_brl ? money(b.min_capital_brl) : "—"}</b></span>
    </div>` : "";
  return `
    <article class="card radar-card scr-card cat-${esc(it.category.toLowerCase())}" data-open="${esc(it.symbol)}" data-tf="${esc(b?.timeframe || "1h")}"
      title="Abrir ${esc(it.base)} no painel">
      <div class="radar-top">
        <div><div class="radar-sym"><span class="scr-rank">#${rank}</span> ${esc(it.base)}<small>/USDT</small></div>
          <div class="radar-name">Volume 24h US$ ${esc(fmtCompact(it.volume_usd))}${it.spread_pct !== null ? ` · spread ${fmtNum(it.spread_pct, 3)}%` : ""}</div></div>
        <div><div class="radar-price">${fmtPrice(it.price, b?.decimals ?? (it.price < 1 ? 6 : 2))}</div>
          <div class="mono small ${it.change_pct >= 0 ? "up" : "down"}" style="text-align:right">${fmtPct(it.change_pct)}</div></div>
      </div>
      <div class="scr-grade">
        <span class="dot-pill ${tone}">${label}</span>${signal}
        <div class="scr-score" title="Nota de 0 a 100"><div class="bar"><i style="width:${it.score}%"></i></div><b>${it.score}</b></div>
      </div>
      ${metrics}
      <ul class="scr-reasons">${it.reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>
    </article>`;
}
