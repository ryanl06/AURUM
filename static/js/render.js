// Renderização do painel principal a partir do payload de /api/analysis.

import { esc, fmtAgo, fmtCompact, fmtCountdown, fmtNum, fmtPct, fmtPrice, KIND_LABEL } from "./format.js";

const $ = (id) => document.getElementById(id);
const RING = 326.7;
let lastPrice = null;
let lastSymbol = null;

export function renderPanel(a, { isFavorite }) {
  renderHeader(a, isFavorite);
  renderSignal(a);
  renderPlan(a);
  renderForecast(a);
  renderSetups(a);
  renderIndicators(a);
  renderHours(a);
  renderBacktest(a);
  renderTicket(a);
  renderRisk(a);
  renderNews(a);
  $("chart-tf").textContent = `· ${a.timeframe.label}`;
}

function renderRisk(a) {
  const r = a.context?.risk;
  const strip = $("risk-strip");
  if (!r) { strip.innerHTML = ""; return; }
  const bar = (value, limit) => (limit > 0 ? `<span class="rs-bar"><i style="width:${Math.min(100, (value / limit) * 100)}%"></i></span>` : "");
  const lossUsed = Math.max(0, -r.day_r);
  strip.className = `risk-strip ${r.blocked ? "blocked" : ""}`;
  strip.innerHTML = `
    <span class="rs-title">${r.blocked ? "⛔ Pausa — limite atingido" : "Hoje"}</span>
    <span class="rs-item">Operações <b>${r.trades_today}/${r.limits.max_trades || "∞"}</b>${bar(r.trades_today, r.limits.max_trades)}</span>
    <span class="rs-item">Resultado <b class="${r.day_r >= 0 ? "up" : "down"}">${r.day_r > 0 ? "+" : ""}${fmtNum(r.day_r, 1)}R</b>
      <span class="muted">(limite −${fmtNum(r.limits.max_loss_r, 1)}R)</span>${bar(lossUsed, r.limits.max_loss_r)}</span>
    <span class="rs-item">Perdas seguidas <b>${r.losing_streak}/${r.limits.max_streak || "∞"}</b></span>
    ${r.enabled ? "" : '<span class="rs-item muted">proteção desligada</span>'}
    <button class="link-btn rs-link" data-open-settings>ajustar limites</button>`;
}

function renderNews(a) {
  const n = a.context?.news;
  $("news-currencies").textContent = n?.currencies?.length ? `· ${n.currencies.join(", ")}` : "";
  if (!n || !n.available) {
    $("news-list").innerHTML = '<li class="empty">Agenda indisponível agora (sem internet?). Confira as notícias por fora.</li>';
    return;
  }
  // Até 6 eventos, garantindo que os de alto impacto apareçam mesmo depois de vários de impacto médio.
  const high = n.upcoming.filter((e) => e.impact === "High");
  const items = [...high.slice(0, 6), ...n.upcoming.filter((e) => e.impact !== "High")]
    .slice(0, 6).sort((x, y) => x.minutes - y.minutes);
  $("news-list").innerHTML = items.length ? items.map((e) => {
    const blocking = n.blocking && n.blocking.iso === e.iso && n.blocking.title === e.title;
    const when = e.minutes >= 0 ? (e.minutes < 90 ? `em ${e.minutes} min` : e.time.slice(0, 5)) : `há ${-e.minutes} min`;
    return `<li class="news-item ${e.impact === "High" ? "high" : ""} ${blocking ? "blocking" : ""}" title="${esc(e.original)}${e.forecast ? ` · previsão ${esc(e.forecast)}` : ""}${e.previous ? ` · anterior ${esc(e.previous)}` : ""}">
      <i class="imp"></i><span class="t">${esc(e.title)}<small>${esc(e.country)} · impacto ${e.impact === "High" ? "ALTO" : "médio"}${blocking ? " · entradas em espera" : ""}</small></span>
      <span class="w">${esc(e.time.slice(-5))}<small>${esc(when)}</small></span></li>`;
  }).join("") : '<li class="empty">Nenhuma notícia relevante nas próximas 48 horas.</li>';
}

// Preço real digitado pelo usuário (XP Unity), guardado por código de ativo por até 15 minutos.
const realPrices = new Map();

function parseBr(text) {
  const clean = String(text || "").trim().replace(/\s/g, "");
  if (!clean) return null;
  const normalized = clean.includes(",") ? clean.replace(/\./g, "").replace(",", ".") : clean;
  const value = Number(normalized);
  return Number.isFinite(value) && value > 0 ? value : null;
}

function roundTick(price, tick, mode = "nearest") {
  const steps = price / tick;
  const n = mode === "down" ? Math.floor(steps + 1e-9) : mode === "up" ? Math.ceil(steps - 1e-9) : Math.round(steps);
  return Number((n * tick).toFixed(6));
}

/** Desloca a boleta para o preço real da XP mantendo as distâncias (em pontos) do plano. */
export function adjustTicket(t, realPrice) {
  const long = t.mode === "exit" ? t.side === "VENDA" : t.side === "COMPRA";
  const shift = realPrice - t.entry;
  const away = long ? "down" : "up";
  const entry = roundTick(realPrice, t.tick);
  const stop = roundTick(t.stop + shift, t.tick, away);
  const target1 = roundTick(t.target1 + shift, t.tick, away);
  const target2 = roundTick(t.target2 + shift, t.tick, away);
  const stopLimit = roundTick(long ? stop - 2 * t.tick : stop + 2 * t.tick, t.tick);
  const stopPoints = Math.abs(entry - stop);
  const targetPoints = Math.abs(target2 - entry);
  return {
    ...t, entry, stop, target1, target2, stop_limit: stopLimit, stop_points: stopPoints, target_points: targetPoints,
    risk_money: Number((t.quantity * stopPoints * t.point_value).toFixed(2)),
    reward_money: Number((t.quantity * targetPoints * t.point_value).toFixed(2)),
    approximate: false, adjusted: true,
    warnings: t.warnings.filter((w) => !w.includes("aproximados")),
  };
}

const isCrypto = (t) => t.kind === "crypto";
const moneyOf = (t, v) => `${t.currency_symbol || "R$"} ${fmtNum(v, 2)}`;
const distUnit = (t) => (t.kind === "b3fut" ? "pts" : isCrypto(t) ? t.currency : "R$/ação");

function quantityText(t) {
  if (isCrypto(t)) return `${fmtNum(t.quantity, t.qty_decimals)} ${t.unit}`;
  return `${fmtNum(t.quantity, 0)} ${t.quantity === 1 ? t.unit : t.unit === "ação" ? "ações" : "contratos"}`;
}

/** Valor de um campo da boleta: literal ou lido da boleta (já ajustada ao preço real, se houver). */
function xpValue(t, field) {
  if (field.value !== undefined) return field.value;
  if (field.key === "code") return t.code;
  if (field.key === "quantity") return isCrypto(t) ? fmtNum(t.quantity, t.qty_decimals) : fmtNum(t.quantity, 0);
  return fmtPrice(t[field.key], t.decimals);
}

export function ticketText(t) {
  const d = t.decimals;
  const pts = distUnit(t);
  const units = quantityText(t);
  const money = (v) => moneyOf(t, v);
  if (t.mode === "exit" || isCrypto(t)) return t.text;
  return [
    "AURUM → ORDEM PARA A XP",
    `Ativo: ${t.code} · ${t.name}${t.expiry_label ? ` (vence ${t.expiry_label})` : ""}`,
    `Operação: ${t.side} ${units}${t.lot_note ? ` · ${t.lot_note}` : ""}`,
    `Entrada: ${t.order_type.toLowerCase()} (~${fmtPrice(t.entry, d)})${t.window ? ` · ${t.window}` : ""}${t.adjusted ? " · ajustado ao preço da XP" : ""}`,
    `Stop loss: disparo ${fmtPrice(t.stop, d)} · limite ${fmtPrice(t.stop_limit, d)} (${fmtPrice(t.stop_points, d)} ${pts} = ${money(t.risk_money)})`,
    `Alvo 1: ${fmtPrice(t.target1, d)} · Alvo 2 (stop gain): ${fmtPrice(t.target2, d)} (+${money(t.reward_money)})`,
    "",
    ...(t.xp_orders || []).flatMap((o) => [`${o.title}:`, ...o.fields.map((f) => `  ${f.label}: ${xpValue(t, f)}`), ""]),
    ...t.warnings.map((w) => `Atenção: ${w}`),
    "Isto não prevê resultados. Use stop loss.",
  ].join("\n");
}

export function setRealPrice(a, text) {
  const code = a?.ticket?.code;
  if (!code) return;
  const price = parseBr(text);
  if (price === null) realPrices.delete(code);
  else realPrices.set(code, { price, at: Date.now() });
  if (document.activeElement?.id === "ticket-real-price") document.activeElement.blur();
  renderTicket(a);
}

function renderTicket(a) {
  if (document.activeElement?.id === "ticket-real-price") return;  // não apaga o que o usuário está digitando
  const original = a.ticket;
  let t = original;
  const body = $("ticket-body");
  const copy = $("btn-copy-ticket");
  const mode = $("ticket-mode");
  if (!t || !t.available) {
    copy.hidden = true;
    mode.textContent = "indisponível";
    mode.className = "chip";
    body.innerHTML = `<div class="ticket-empty"><span>${esc(t?.reason || "Sem boleta para este ativo.")}</span>
      ${t?.suggest ? `<button class="btn btn-ghost btn-xs" data-open-symbol="${esc(t.suggest)}">Analisar ${esc(t.suggest.replace(".SA", "").replace("FUT", ""))}</button>` : ""}</div>`;
    return;
  }
  copy.hidden = false;
  const saved = realPrices.get(t.code);
  if (saved && Date.now() - saved.at < 15 * 60 * 1000 && t.mode === "entry") t = adjustTicket(t, saved.price);
  else realPrices.delete(t.code);
  a.ticketView = t;  // o botão "Copiar ordem" usa a versão ajustada
  const live = ["ENTRAR_AGORA", "SAIR_AGORA"].includes(a.signal.action);
  mode.textContent = t.mode === "exit" ? "SAÍDA" : live ? "ENVIAR AGORA" : "SIMULAÇÃO";
  mode.className = `chip ${t.mode === "exit" ? "tone-sell" : live ? "tone-buy" : ""}`;
  const d = t.decimals;
  const pts = distUnit(t);
  const where = t.exchange || "XP";
  $("ticket-title").textContent = `Ordem para a ${where}`;
  const showAdjust = t.mode === "entry" && (original.approximate || a.market.delayed || t.adjusted);
  body.innerHTML = `
    ${showAdjust ? `
    <div class="tk-adjust">
      <label for="ticket-real-price"><b>Preço agora no XP Unity</b><small>${t.adjusted
        ? `Boleta ajustada ao preço que você informou (${fmtPrice(saved.price, d)}). Apague para voltar à referência.`
        : "Digite o último preço que aparece na XP: entrada, stop e alvos se ajustam mantendo as distâncias do plano."}</small></label>
      <input id="ticket-real-price" type="text" inputmode="decimal" autocomplete="off" placeholder="${fmtPrice(original.entry, d)}"
             value="${t.adjusted ? fmtPrice(saved.price, d) : ""}" data-code="${esc(t.code)}">
    </div>` : ""}
    <div class="ticket-grid">
      <div class="tk wide"><span>${isCrypto(t) ? "Par na Binance" : "Ativo (código na XP)"}</span><b>${esc(t.code)}</b><small>${esc(t.name)}${t.expiry_label ? ` · vence ${esc(t.expiry_label)}` : ""}</small></div>
      <div class="tk ${["COMPRA", "COMPRAR"].includes(t.side) ? "side-buy" : "side-sell"}"><span>Operação</span><b>${esc(t.side)}</b><small>${esc(t.action)}</small></div>
      <div class="tk"><span>Quantidade</span><b>${esc(quantityText(t))}</b><small>${esc(t.lot_note || (isCrypto(t) ? `~${moneyOf(t, t.notional)} · taxas ~${moneyOf(t, t.fees_money)}` : t.order_type))}</small></div>
      ${t.mode === "exit" ? "" : `
      <div class="tk"><span>Entrada</span><b>${fmtPrice(t.entry, d)}</b><small>${esc(t.order_type)}</small></div>
      <div class="tk stop"><span>Stop · disparo</span><b>${fmtPrice(t.stop, d)}</b><small>limite ${fmtPrice(t.stop_limit, d)} · ${fmtPrice(t.stop_points, d)} ${pts}</small></div>
      <div class="tk target"><span>Alvo 1</span><b>${fmtPrice(t.target1, d)}</b><small>realização parcial</small></div>
      <div class="tk target"><span>Alvo 2 · stop gain</span><b>${fmtPrice(t.target2, d)}</b><small>${fmtPrice(t.target_points, d)} ${pts}</small></div>
      <div class="tk"><span>Risco</span><b class="down">${moneyOf(t, t.risk_money)}</b><small>se bater o stop</small></div>
      <div class="tk"><span>Ganho no alvo 2</span><b class="up">${moneyOf(t, t.reward_money)}</b><small>antes de custos</small></div>
      <div class="tk wide"><span>Janela</span><b>${esc(t.window || "—")}</b><small>tick ${fmtNum(t.tick, t.tick < 1 ? 2 : 0)} · ${t.kind === "b3fut" ? `R$ ${fmtNum(t.point_value, 2)} por ponto` : isCrypto(t) ? `validade ${esc(t.validity)}` : "R$ 0,01 por ação"}</small></div>`}
    </div>
    ${t.warnings.length ? `<ul class="ticket-warn">${t.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>` : ""}
    ${(t.xp_orders || []).length ? `<div class="xp-orders">${t.xp_orders.map((o) => `
      <div class="xp-order">
        <div class="xp-order-title">${esc(o.title)}</div>
        ${o.fields.map((f) => `<div class="xp-field"><span>${esc(f.label)}</span><b>${esc(xpValue(t, f))}</b></div>`).join("")}
        ${o.note ? `<small>${esc(o.note)}</small>` : ""}
      </div>`).join("")}</div>` : ""}
    <details class="ticket-steps"><summary>Como enviar na ${esc(where)}</summary><ol>${t.steps.map((s) => `<li>${esc(s)}</li>`).join("")}</ol></details>`;
}

export function renderMt5(status) {
  const chip = $("mt5-chip");
  const body = $("mt5-body");
  if (!status) return;
  if (!status.enabled) {
    chip.textContent = "XP Unity";
    chip.className = "chip tone-buy";
    body.innerHTML = `
      <p>Você envia as ordens no <b>XP Unity</b> (home broker gratuito da XP) e o AURUM cuida da análise, do risco e da boleta.</p>
      <ol>
        <li>Abra o <a href="https://www.xpi.com.br/home-broker/" target="_blank" rel="noopener">XP Unity</a> e deixe o ativo aberto (ex.: o código <b>WDO</b> do vencimento mostrado na boleta).</li>
        <li>Quando o AURUM mostrar <b>ENTRAR AGORA</b>, digite no campo <b>“Preço agora no XP Unity”</b> o último preço da XP.</li>
        <li>Copie a ordem e preencha a boleta: lado, quantidade, stop (disparo e limite) e alvo.</li>
        <li>Clique em <b>Já entrei</b> aqui para o AURUM acompanhar a saída.</li>
      </ol>
      <p class="small muted">Os preços do WDO/WIN no AURUM vêm de referências públicas (dólar à vista e Ibovespa) e as ações da B3 têm atraso de ~15 min. Direção e horário do sinal valem; o preço exato é sempre o da XP — por isso o campo de ajuste. Prefira os tempos gráficos de 15m ou 1h.</p>`;
    return;
  }
  const on = status.connected && status.enabled;
  chip.textContent = on ? "MT5 conectado" : status.enabled ? "MT5 desconectado" : "MT5 desligado";
  chip.className = `chip ${on ? "tone-buy" : ""}`;
  const term = status.terminal || {};
  body.innerHTML = on
    ? `<div class="xp-status on"><i></i><div><b>${esc(term.company || "MetaTrader 5")}</b><br><span class="small">${esc(term.server || "")} · cotações em tempo real ativas</span></div></div>
       <p>WDO, WIN e ações da B3 agora vêm direto do seu MetaTrader 5. O AURUM só lê preços — as ordens continuam com você.</p>
       <button class="btn btn-ghost btn-xs" id="btn-mt5-reconnect">Reconectar</button>`
    : `<div class="xp-status"><i></i><div><b>Usando fontes públicas</b><br><span class="small">${esc(status.enabled ? (status.error || "MetaTrader 5 não encontrado.") : "Ligue “Usar MetaTrader 5” em ⚙.")}</span></div></div>
       <ol>
         <li>Faça login no <a href="https://www.xpi.com.br/" target="_blank" rel="noopener">Portal XP</a> e vá em <b>Minha Conta → Ferramentas e Serviços → Assinaturas de plataformas e serviços</b> → contrate o <b>MetaTrader 5 Simulado</b> (treino) e/ou <b>MetaTrader 5</b> (real). Ele não aparece na busca da página pública de Plataformas — só dentro da área logada.</li>
         <li>Login, senha e servidor chegam no seu e-mail. Baixe o instalador pelo link da própria página de assinatura.</li>
         <li>Abra o MT5 e entre com servidor <code>XPMT5-Demo</code> (simulado) ou <code>XPMT5-PRD</code> (real).</li>
         <li>Na conta real, defina o <b>limite de garantia</b> do MT5 no Portal XP.</li>
         ${status.installed ? "" : "<li>Feche o AURUM e abra de novo o <code>INICIAR_AURUM.bat</code> (ele instala o conector).</li>"}
         <li>Com o MT5 aberto e logado, clique em <b>Reconectar</b>.</li>
       </ol>
       <p class="small"><a href="https://atendimento.xpi.com.br/artigo/1476-como-realizarcontratacao-e-instalacao-do-metatrader-5" target="_blank" rel="noopener">Guia oficial da XP</a> ·
         <a href="https://atendimento.xpi.com.br/categoria/plataformas/metatrader-5" target="_blank" rel="noopener">Central de ajuda do MT5</a></p>
       <button class="btn btn-gold btn-xs" id="btn-mt5-reconnect">Reconectar</button>`;
}

function renderHeader(a, isFavorite) {
  $("asset-name").textContent = a.asset.name;
  $("asset-symbol").textContent = a.symbol;
  $("asset-kind").textContent = KIND_LABEL[a.asset.kind] || a.asset.kind;
  const market = $("market-status");
  market.className = `chip market ${a.market.open ? "open" : "closed"}`;
  market.querySelector("span").textContent = `${a.market.open ? "Mercado aberto" : "Mercado fechado"}${a.market.next_change_label ? ` · ${a.market.next_change_label}` : ""}`;
  market.title = `${a.market.label}${a.market.note ? ` — ${a.market.note}` : ""}`;

  const priceEl = $("price");
  priceEl.textContent = fmtPrice(a.price, a.decimals);
  priceEl.dataset.live = a.price;
  if (lastSymbol === a.symbol && lastPrice !== null && a.price !== lastPrice) {
    priceEl.classList.remove("flash-up", "flash-down");
    void priceEl.offsetWidth;
    priceEl.classList.add(a.price > lastPrice ? "flash-up" : "flash-down");
    setTimeout(() => priceEl.classList.remove("flash-up", "flash-down"), 900);
  }
  lastPrice = a.price;
  lastSymbol = a.symbol;

  const change = $("change");
  change.textContent = `${fmtPct(a.change_pct)} ${a.timeframe.key === "1d" ? "no último dia" : "hoje"}`;
  change.className = `change mono ${a.change_pct >= 0 ? "up" : "down"}`;
  const trend = $("trend-pill");
  trend.className = `trend-pill ${a.trend.direction}`;
  trend.textContent = `TENDÊNCIA ${a.trend.direction}`;
  trend.title = a.trend.text;

  const source = $("data-source");
  const realtime = (a.data_source || "").includes("tempo real");
  const approx = (a.data_source || "").includes("aproximada");
  const viaMt5 = (a.data_source || "").startsWith("MetaTrader");
  source.textContent = realtime ? (viaMt5 ? "● MT5 tempo real" : "● Tempo real")
    : approx ? "Referência aproximada" : a.market.delayed ? "Yahoo · atraso ~15 min" : "Yahoo Finance";
  source.className = `chip ${realtime ? "tone-buy" : approx ? "tone-prepare" : ""}`;
  source.title = `Fonte dos preços: ${a.data_source}`;

  const star = $("btn-fav");
  star.classList.toggle("on", isFavorite);
  star.title = isFavorite ? "Remover dos favoritos" : "Adicionar aos favoritos";
  document.title = `${a.signal.title} · ${a.symbol} ${fmtPrice(a.price, a.decimals)} — AURUM`;
}

function renderSignal(a) {
  const s = a.signal;
  const card = $("signal-card");
  card.dataset.tone = s.tone;
  $("signal-title").textContent = s.title;
  $("signal-headline").textContent = s.headline;
  $("signal-explain").textContent = document.body.dataset.mode === "simple" ? s.simple : s.explanation;
  const warnings = s.warnings || [];
  $("signal-warnings").hidden = !warnings.length;
  $("signal-warnings").innerHTML = warnings.map((w) => `<li>${esc(w)}</li>`).join("");
  $("signal-setup").textContent = s.side ? `${s.side === "COMPRA" ? "COMPRA ↑" : "VENDA ↓"}${s.setup ? ` · ${s.setup}` : ""}` : (s.setup || "");

  const moment = $("moment-pill");
  moment.textContent = s.right_moment ? "SIM" : "NÃO";
  moment.className = `moment-pill ${s.right_moment ? "yes" : "no"}`;

  $("signal-window").textContent = s.window?.label || (a.market.open ? "Sem entrada agora" : "Mercado fechado");
  $("signal-horizon").textContent = s.horizon?.label || (s.action === "MANTENHA" ? "Até sinal de saída" : "—");

  const conf = s.confidence;
  const hasConf = conf !== null && conf !== undefined;
  const ringValue = hasConf ? conf : Math.round(Math.abs(a.score ?? 0));
  $("confidence-value").textContent = hasConf ? `${conf}%` : `${ringValue}`;
  $("confidence-label").textContent = hasConf ? "chance histórica" : "força atual";
  card.querySelector(".ring-fill").style.strokeDashoffset = RING * (1 - Math.min(100, ringValue) / 100);
  const info = s.confidence_info;
  $("confidence-caption").innerHTML = hasConf && info
    ? `alvo antes do stop · empate <b>${fmtNum(info.breakeven, 0)}%</b><br>vantagem <b class="${info.edge === "positiva" ? "up" : info.edge === "negativa" ? "down" : ""}">${esc(info.edge)}</b> · ${info.trades} sinais`
    : "0 = equilíbrio · 100 = domínio total";

  renderContextChips(a);

  $("pressure-buy").style.width = `${a.pressure.buy}%`;
  $("pressure-buy-v").textContent = `${a.pressure.buy}%`;
  $("pressure-sell-v").textContent = `${a.pressure.sell}%`;

  const btn = $("btn-open-position");
  btn.textContent = a.position ? "Encerrar minha operação" : "Já entrei nesta operação";
  btn.className = a.position ? "btn btn-danger" : "btn btn-gold";
}

const REL_CLASS = { ALTA: "good", "MÉDIA": "mid", BAIXA: "bad", INDEFINIDA: "" };

function renderContextChips(a) {
  const c = a.context || {};
  const chips = [];
  if (c.reliability) {
    chips.push(`<span class="ctx ${REL_CLASS[c.reliability.level] || ""}" title="${esc(c.reliability.short)}">Confiabilidade <b>${esc(c.reliability.level)}</b></span>`);
  }
  if (c.htf) {
    const cls = c.htf.bias === 1 ? "good" : c.htf.bias === -1 ? "bad" : "";
    chips.push(`<span class="ctx ${cls}" title="Tendência do tempo gráfico maior${c.htf.filter ? " (filtro ligado)" : ""}">Tempo maior ${esc(c.htf.timeframe)} <b>${esc(c.htf.text)}</b></span>`);
  }
  if (c.session) {
    chips.push(`<span class="ctx ${c.session.active ? "good" : "mid"}" title="${esc(c.session.label)}">Sessão Londres/NY <b>${c.session.active ? "ABERTA" : "FECHADA"}</b></span>`);
  }
  const soon = c.news?.upcoming?.find((e) => e.impact === "High" && e.minutes >= -30 && e.minutes <= 60);
  if (soon) {
    chips.push(`<span class="ctx bad" title="${esc(soon.original)}">Notícia <b>${soon.minutes >= 0 ? `em ${soon.minutes} min` : "agora"}</b></span>`);
  }
  if (c.risk?.blocked) chips.push('<span class="ctx bad">Limite do dia <b>ATINGIDO</b></span>');
  if (a.plan?.pips) {
    chips.push(`<span class="ctx info" title="Stop do plano em pips">Stop <b>${fmtNum(a.plan.pips.stop, 1)} pips</b></span>`);
  }
  $("context-chips").innerHTML = chips.join("");
}

export function tickSignal(a) {
  if (!a) return;
  const now = Date.now();
  const open = a.market.open;
  const tfMs = a.candle.seconds * 1000;
  let closeAt = new Date(a.candle.close_at).getTime();
  let start = new Date(a.candle.start).getTime();
  if (closeAt <= now && tfMs < 86400000) {
    // Dados com atraso (ex.: B3): usa o candle do relógio real.
    closeAt = Math.ceil(now / tfMs) * tfMs;
    start = closeAt - tfMs;
  }
  const remaining = closeAt - now;
  $("candle-countdown").textContent = open ? fmtCountdown(remaining) : "—";
  const pct = open ? Math.min(100, Math.max(0, ((now - start) / (closeAt - start)) * 100)) : 0;
  $("candle-progress").style.width = `${pct}%`;

  const upd = $("updated-label");
  upd.textContent = `Última análise ${new Date(a.generated_at).toLocaleTimeString("pt-BR")} · dados ${fmtAgo(a.fetched_at)}`;
  upd.className = `updated ${a.stale ? "stale" : "live"}`;
  if (a.stale) upd.textContent += " · sem candles novos";
}

function renderPlan(a) {
  const p = a.plan;
  const d = a.decimals;
  const long = p.side === "COMPRA";
  const planNote = p.from_position ? " · sua operação" : a.signal.action === "ENTRAR_AGORA" ? "" : " · simulação";
  $("plan-side").textContent = `${long ? "COMPRA ↑" : "VENDA ↓"}${planNote}`;
  $("plan-side").className = `chip ${long ? "tone-buy" : "tone-sell"}`;
  const rows = [
    ["target", "ALVO 2", p.target2, `${long ? "+" : "−"}${fmtNum(p.target2_pct)}%`],
    ["target", "ALVO 1", p.target1, `${long ? "+" : "−"}${fmtNum(p.target1_pct)}%`],
    ["entry", "ENTRADA", p.entry, `R:R 1:${fmtNum(p.reward_ratio, 1)}`],
    ["stop", "STOP", p.stop, `${long ? "−" : "+"}${fmtNum(p.stop_pct)}%`],
  ];
  $("plan-ladder").innerHTML = rows.map(([cls, label, value, pct]) => `
    <div class="ladder-row ${cls}"><span class="lbl">${label}</span><span class="val mono">${fmtPrice(value, d)}</span>
    <span class="pct mono ${cls === "stop" ? "down" : cls === "target" ? "up" : "muted"}">${pct}</span></div>`).join("");
  $("plan-size").innerHTML = `
    <div class="mini-stat"><span>Quantidade</span><b>${fmtNum(p.quantity, p.quantity < 10 ? 4 : 0)}</b></div>
    <div class="mini-stat"><span>Valor</span><b>${fmtCompact(p.notional)}</b></div>
    <div class="mini-stat" title="Quanto você perde se o stop for atingido"><span>Risco</span><b class="down">${fmtNum(p.risk_amount)}</b></div>`;
  $("plan-max-stop").textContent = fmtNum(p.max_stop_pct, 1);
  $("plan-atr").textContent = fmtNum(p.atr_mult, 1);

  const pips = p.pips;
  $("plan-pips").hidden = !pips;
  if (pips) {
    $("plan-pips").innerHTML = `
      <div class="mini-stat" title="Distância do stop em pips"><span>Stop</span><b>${fmtNum(pips.stop, 1)} pips</b></div>
      <div class="mini-stat" title="Distância do alvo 2 em pips"><span>Alvo 2</span><b>${fmtNum(pips.target2, 1)} pips</b></div>
      <div class="mini-stat" title="Tamanho para arriscar ${fmtNum(p.risk_pct, 1)}% do capital. Valor do pip por ${esc(pips.unit)}: ${fmtNum(pips.pip_value_per_lot, 2)} USD${pips.approx ? " (aproximado)" : ""}">
        <span>Lotes</span><b>${pips.lots === null ? "—" : fmtNum(pips.lots, 2)}${pips.approx ? "*" : ""}</b></div>`;
  }

  const lv = a.context?.levels;
  const rowsLv = [];
  if (lv) {
    for (const r of lv.resistances.slice(0, 2).reverse()) {
      rowsLv.push(`<div class="level-row res"><span class="tag">RESISTÊNCIA</span><span class="mono">${fmtPrice(r.price, d)}</span><span>${esc(r.strength)} · ${fmtPct(r.distance_pct)}</span></div>`);
    }
    for (const s of lv.supports.slice(0, 2)) {
      rowsLv.push(`<div class="level-row sup"><span class="tag">SUPORTE</span><span class="mono">${fmtPrice(s.price, d)}</span><span>${esc(s.strength)} · ${fmtPct(s.distance_pct)}</span></div>`);
    }
  }
  $("plan-levels").innerHTML = rowsLv.join("");
}

function renderForecast(a) {
  const f = a.forecast;
  const head = $("forecast-headline");
  head.textContent = f.headline;
  head.className = `forecast-headline ${f.direction}`;
  const score = a.score ?? 0;
  $("score-thumb").style.left = `${50 + score / 2}%`;
  $("score-chip").textContent = `força ${score > 0 ? "+" : ""}${fmtNum(score, 0)}`;
  const prob = f.probability;
  $("forecast-probability").hidden = !prob;
  if (prob) {
    $("forecast-probability").innerHTML = `
      <div class="prob-top"><span>Histórico: ${prob.horizon} candles depois</span><span class="prob-edge ${prob.edge}">vantagem ${esc(prob.edge)}</span></div>
      <div class="prob-bar"><span style="width:${prob.up_rate}%"></span></div>
      <div class="prob-vals"><span class="up">↑ subiu ${fmtNum(prob.up_rate, 0)}%</span><span class="down">caiu ${fmtNum(prob.down_rate, 0)}% ↓</span></div>
      <div class="prob-text">${esc(prob.text)}</div>`;
  }
  const cons = f.consensus;
  $("forecast-consensus").hidden = !cons;
  if (cons) {
    const dir = (d) => (d === 1 ? "up" : d === -1 ? "down" : "flat");
    $("forecast-consensus").innerHTML = `
      <div class="cons-head"><span class="eyebrow">Consenso das leituras</span><b class="${cons.tone}">${esc(cons.label)}</b></div>
      <div class="cons-votes">${cons.votes.map((v) => `
        <div class="cons-vote ${dir(v.direction)}" title="${esc(v.detail)}">
          <i>${v.direction === 1 ? "▲" : v.direction === -1 ? "▼" : "•"}</i><span>${esc(v.name)}</span></div>`).join("")}</div>
      <div class="cons-foot">${cons.up} de ${cons.total} para alta · ${cons.down} de ${cons.total} para baixa</div>`;
  }
  const an = f.analog;
  $("forecast-analog").hidden = !an;
  if (an) {
    const v = an.validation || {};
    $("forecast-analog").innerHTML = `
      <div class="prob-top"><span>Padrões semelhantes · ${an.neighbors} casos · semelhança ${esc(an.similarity)}</span>
        <span class="prob-edge ${an.edge === "possível" ? "forte" : ""}" title="Medido fora da amostra: ${v.tested || 0} previsões">direção: ${esc(an.edge === "possível" ? "vantagem possível" : "sem vantagem")}</span></div>
      <div class="analog-range">
        <div><small>faixa provável em ${an.range.bars} candles</small><b>${fmtPrice(an.range.low, a.decimals)} — ${fmtPrice(an.range.high, a.decimals)}</b></div>
        <div><small>cobriu no teste</small><b>${v.range_coverage ?? "—"}%</b></div>
      </div>
      <div class="prob-text">${esc(an.direction_text)}</div>`;
  }
  $("forecast-reads").innerHTML = f.reads.length
    ? f.reads.map((r) => `<li class="${r.tone}">${esc(r.text)}</li>`).join("")
    : '<li class="empty">Sem leituras fortes agora — mercado sem exageros.</li>';

  const etaItems = [];
  for (const setup of a.setups) {
    if (setup.status === "QUASE") {
      etaItems.push(`<li class="eta ${setup.side === "COMPRA" ? "bull" : "bear"}"><span class="t">${esc(setup.name)} pode disparar</span>
        <span class="w">~${setup.eta_at}<small>${setup.eta} candle${setup.eta > 1 ? "s" : ""}</small></span></li>`);
    }
  }
  for (const e of f.etas) {
    etaItems.push(`<li class="eta ${e.tone}"><span class="t">${esc(e.label)}</span><span class="w">~${e.at}<small>${e.candles} candle${e.candles > 1 ? "s" : ""}</small></span></li>`);
  }
  $("forecast-etas").innerHTML = etaItems.length
    ? etaItems.slice(0, 5).join("")
    : '<li class="empty">Nenhum cruzamento previsto para os próximos candles.</li>';

  const mini = $("position-mini");
  if (a.position) {
    const p = a.position;
    const rules = a.signal.exit_rules || [];
    mini.hidden = false;
    mini.innerHTML = `
      <div class="row"><span class="eyebrow">Sua operação · ${esc(p.side)}</span><span class="pnl ${p.pnl_pct >= 0 ? "up" : "down"}">${fmtPct(p.pnl_pct)}</span></div>
      <div class="row small muted"><span>Entrada ${fmtPrice(p.entry_price, a.decimals)}</span><span>Agora ${fmtPrice(p.current_price, a.decimals)}</span></div>
      <ul>${rules.map((r) => `<li class="${r.ok ? "hit" : ""}"><b>${r.ok ? "● " : "○ "}${esc(r.name)}</b><span>${esc(r.text)}</span></li>`).join("")}</ul>`;
  } else {
    mini.hidden = true;
  }
}

function renderSetups(a) {
  const html = a.setups.map((s) => {
    const hot = ["DISPAROU", "FORMANDO", "QUASE"].includes(s.status);
    const bt = s.backtest;
    const btText = bt && bt.trades
      ? `Histórico: <b>${fmtNum(bt.win_rate, 0)}%</b> em ${bt.trades} · fator <b class="${(bt.profit_factor ?? 0) >= 1 ? "up" : "down"}">${bt.profit_factor === null ? "—" : fmtNum(bt.profit_factor, 2)}</b>`
      : "Sem ocorrências no histórico";
    return `
      <div class="setup ${s.side === "VENDA" ? "sell" : ""} ${hot ? "hot" : ""} ${s.blocked ? "blocked" : ""}"
           title="${s.blocked ? "Bloqueada: perdeu dinheiro no histórico deste ativo e tempo gráfico" : ""}">
        <div class="setup-head">
          <span class="setup-name">${esc(s.name)}</span>
          ${s.status ? `<span class="setup-status s-${s.status.replace(/\s/g, "")}">${esc(s.status)}</span>` : `<span class="muted small mono">${s.met}/${s.total}</span>`}
        </div>
        <div class="setup-desc">${esc(s.description)}</div>
        <div class="setup-progress">${s.conditions.map((c) => `<i class="${c.ok ? "on" : ""}"></i>`).join("")}</div>
        <ul class="conds">${s.conditions.map((c) => `<li class="${c.ok ? "ok" : ""}">${esc(c.label)}</li>`).join("")}</ul>
        <div class="setup-foot">
          <span>${btText}</span>
          ${s.eta ? `<span class="setup-eta">~${s.eta_at}</span>` : s.last_signal ? `<span>Último: ${esc(s.last_signal)}</span>` : ""}
        </div>
      </div>`;
  }).join("");
  $("setups").innerHTML = html;
}

function gauge(pos, cls = "") {
  const pct = Math.max(0, Math.min(100, pos));
  return `<div class="gauge ${cls}"><i style="left:${pct}%"></i></div>`;
}

function renderIndicators(a) {
  const i = a.indicators;
  const d = a.decimals;
  const items = [
    { name: "RSI (14)", val: fmtNum(i.rsi.value, 1), text: i.rsi.text, tone: i.rsi.tone, extra: gauge(i.rsi.value ?? 50) },
    { name: "MACD (12, 26, 9)", val: `${i.macd.arrow === "up" ? "▲" : "▼"} ${fmtNum(i.macd.hist, Math.min(d + 2, 6))}`, text: i.macd.text, tone: i.macd.tone },
    { name: "Médias MME9 / MME21", val: `${fmtPrice(i.ema.ema9, d)} / ${fmtPrice(i.ema.ema21, d)}`, text: i.ema.text, tone: i.ema.tone },
    { name: "Bollinger (20, 2)", val: `${fmtPrice(i.bollinger.lower, d)} – ${fmtPrice(i.bollinger.upper, d)}`, text: i.bollinger.text, tone: i.bollinger.tone, extra: gauge((i.bollinger.pctb ?? 0.5) * 100, "bb") },
    { name: "ATR (volatilidade)", val: `${fmtNum(i.atr.pct, 2)}%`, text: i.atr.text, tone: i.atr.tone },
    { name: "ADX (força da tendência)", val: fmtNum(i.adx.value, 1), text: i.adx.text, tone: i.adx.tone, extra: gauge(((i.adx.value ?? 0) / 50) * 100, "adx") },
    { name: "Volume", val: i.volume.available ? `${fmtNum(i.volume.ratio, 2)}x média` : "n/d", text: i.volume.text, tone: i.volume.tone },
  ];
  if (i.vwap) items.push({ name: "VWAP do dia", val: fmtPrice(i.vwap.value, d), text: i.vwap.text, tone: i.vwap.tone });
  $("indicators").innerHTML = items.map((it) => `
    <div class="ind">
      <div class="ind-top"><span class="ind-name">${it.name}</span><span class="ind-val">${esc(it.val)}</span></div>
      ${it.extra || ""}
      <div class="ind-text ${it.tone}">${esc(it.text)}</div>
    </div>`).join("");
}

function renderHours(a) {
  const h = a.hours;
  $("hours-unit").textContent = h.unit === "hora" ? "por hora do dia (horário local)" : "por dia da semana";
  $("hours-best").innerHTML = h.best.length
    ? `<span class="muted small">Mais movimento:</span>${h.best.map((b) => `<span class="chip">${esc(b)}</span>`).join("")}`
    : '<span class="muted small">Histórico insuficiente.</span>';
  const now = new Date();
  const currentKey = h.unit === "hora" ? now.getHours() : (now.getDay() + 6) % 7;
  $("hours").innerHTML = h.buckets.map((b) => `
    <div class="hour ${h.best.includes(b.label) ? "top" : ""} ${b.key === currentKey ? "now" : ""}">
      <i style="height:${Math.max(3, b.intensity * 100)}%"></i><span>${esc(b.label.replace("h", ""))}</span>
      <div class="tip">${esc(b.label)} · variação média ${fmtNum(b.range_pct, 2)}% · ${fmtNum(b.up_rate, 0)}% candles de alta</div>
    </div>`).join("");
}

function renderBacktest(a) {
  const bt = a.backtest;
  const o = bt.overall;
  $("bt-period").textContent = `${bt.period.from} a ${bt.period.to} · ${bt.candles} candles`;
  const pf = o.profit_factor;
  $("bt-stats").innerHTML = `
    <div class="mini-stat"><span>Sinais</span><b>${o.trades}</b></div>
    <div class="mini-stat"><span>Acerto</span><b class="${(o.win_rate ?? 0) >= 100 / (1 + bt.reward_ratio) ? "up" : "down"}">${o.win_rate === null ? "—" : `${fmtNum(o.win_rate, 0)}%`}</b></div>
    <div class="mini-stat" title="Ganho total ÷ perda total. Acima de 1 = ganhou mais do que perdeu."><span>Fator de lucro</span><b class="${pf >= 1 ? "up" : "down"}">${pf === null ? "—" : fmtNum(pf, 2)}</b></div>
    <div class="mini-stat" title="Resultado somado em múltiplos do risco (R)"><span>Resultado</span><b class="${o.total_r >= 0 ? "up" : "down"}">${o.total_r > 0 ? "+" : ""}${fmtNum(o.total_r, 1)}R</b></div>`;
  const rows = bt.setups.map((s) => `
    <tr><td><span class="dot-pill ${s.side === "COMPRA" ? "tone-buy" : "tone-sell"}">${esc(s.name)}</span></td>
      <td class="num">${s.trades}</td>
      <td class="num"><span class="bar-cell"><i style="width:${s.win_rate ?? 0}%"></i></span>${s.win_rate === null ? "—" : `${fmtNum(s.win_rate, 0)}%`}</td>
      <td class="num ${s.profit_factor >= 1 ? "up" : s.profit_factor === null ? "" : "down"}">${s.profit_factor === null ? "—" : fmtNum(s.profit_factor, 2)}</td>
      <td class="num">${s.direction_rate === null ? "—" : `${fmtNum(s.direction_rate, 0)}%`}</td></tr>`).join("");
  $("bt-table").innerHTML = `<thead><tr><th>Regra</th><th class="num">Sinais</th>
    <th class="num" title="Operações que bateram o alvo antes do stop">Acerto</th>
    <th class="num" title="Ganho total ÷ perda total">Fator</th>
    <th class="num" title="Preço foi na direção do sinal ${bt.direction_bars} candles depois">Direção ${bt.direction_bars}c</th></tr></thead><tbody>${rows}</tbody>`;
  const breakeven = 100 / (1 + bt.reward_ratio);
  const rel = a.context?.reliability;
  const pfText = (x) => (x === null || x === undefined ? "—" : fmtNum(x, 2));
  $("bt-oos").innerHTML = `
    <div class="oos-box" title="Todas as regras, período recente (últimos 30% do histórico)"><span>Recente · todas</span>
      <b class="${(bt.recent_all.profit_factor ?? 0) >= 1 ? "up" : "down"}">fator ${pfText(bt.recent_all.profit_factor)}</b><small>${bt.recent_all.trades} sinais</small></div>
    <div class="oos-box" title="Filtro decidido só com o período antigo e medido no recente"><span>Recente · filtradas</span>
      <b class="${(bt.recent_filtered.profit_factor ?? 0) >= 1 ? "up" : "down"}">fator ${pfText(bt.recent_filtered.profit_factor)}</b><small>${bt.recent_filtered.trades} sinais · fora da amostra</small></div>
    <div class="oos-box"><span>Confiabilidade</span><b class="${rel?.level === "ALTA" ? "up" : rel?.level === "BAIXA" ? "down" : ""}">${esc(rel?.level || "—")}</b><small>custo ${fmtNum(bt.cost_pct, 3)}% por operação</small></div>`;
  $("bt-hint").innerHTML = `Entrada no candle seguinte ao sinal, stop pelo ATR, alvo de ${fmtNum(bt.reward_ratio, 1)}x o risco (limite de ${bt.horizon} candles) e custos descontados. Acertar mais de <b>${fmtNum(breakeven, 0)}%</b> já empata. Período recente começa em ${esc(bt.split_date || "—")}. Passado não garante futuro.`;
}

/** Preço do topo acompanhando o gráfico ao vivo (entre uma análise completa e outra). */
export function renderLivePrice(a, price) {
  if (!a || price === null || price === undefined) return;
  const el = $("price");
  const previous = Number(el.dataset.live || a.price);
  el.textContent = fmtPrice(price, a.decimals);
  el.dataset.live = price;
  if (price !== previous) {
    el.classList.remove("tick-up", "tick-down");
    void el.offsetWidth;
    el.classList.add(price > previous ? "tick-up" : "tick-down");
    clearTimeout(el._tick);
    el._tick = setTimeout(() => el.classList.remove("tick-up", "tick-down"), 700);
  }
  if (a.change_pct !== null && a.change_pct !== undefined && a.price) {
    const reference = a.price / (1 + a.change_pct / 100);
    const pct = (price / reference - 1) * 100;
    const change = $("change");
    change.textContent = `${fmtPct(pct)} ${a.timeframe.key === "1d" ? "no último dia" : "hoje"}`;
    change.className = `change mono ${pct >= 0 ? "up" : "down"}`;
  }
  document.title = `${a.signal.title} · ${a.symbol} ${fmtPrice(price, a.decimals)} — AURUM`;
}

export function renderError(message) {
  const el = $("load-error");
  el.hidden = !message;
  el.textContent = message || "";
}

export function renderLoading(symbol) {
  const card = $("signal-card");
  card.dataset.tone = "wait";
  $("signal-title").textContent = "ANALISANDO";
  $("signal-headline").textContent = `Buscando dados de ${symbol}…`;
  $("signal-explain").textContent = "";
}
