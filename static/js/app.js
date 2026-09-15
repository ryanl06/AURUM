// AURUM — orquestração do painel: estado, rotas, atualização contínua e eventos.

import { api, OfflineError, onConnectionChange } from "./api.js";
import { MarketChart } from "./chart.js";
import { esc, fmtTime, KIND_LABEL } from "./format.js";
import { browserNotify, flashScreen, playTone, requestNotifyPermission, toast, unlockAudio } from "./notify.js";
import { LiveFeed } from "./live.js";
import { renderError, renderLivePrice, renderLoading, renderMt5, renderPanel, setRealPrice, tickSignal, ticketText } from "./render.js";
import { startTour } from "./tour.js";
import { setupTutorial } from "./tutorial.js";
import { loadHistory, loadOperations, loadPerformance, renderRadar, renderWatchlist } from "./views.js";

const $ = (id) => document.getElementById(id);
const POLL_MS = { "1m": 10000, "5m": 15000, "15m": 20000, "1h": 30000, "1d": 60000 };
const TF_SHORT = { "1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h", "1d": "1D" };
const LEVEL_COLOR = { success: "#16c784", danger: "#f0454f", warning: "#f5a524", info: "#f0b90b" };

const store = {
  get(key, fallback) {
    try { return JSON.parse(localStorage.getItem(`aurum.${key}`)) ?? fallback; } catch { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem(`aurum.${key}`, JSON.stringify(value)); } catch { /* armazenamento indisponível */ }
  },
};

const state = {
  symbol: store.get("symbol", "WDOFUT"),
  tf: store.get("tf", "1h"),
  mode: store.get("mode", "pro"),
  sound: store.get("sound", true),
  notify: store.get("notify", false),
  tab: "painel",
  meta: null,
  analysis: null,
  favorites: [],
  radar: [],
  lastAlertId: null,
  pollTimer: null,
  seq: 0,
  refreshedFor: null,
  positionSide: "COMPRA",
};

const chart = new MarketChart($("chart"), $("chart-legend"));
chart.setStyle(store.get("chartStyle", "candles"));
let liveKey = null;
let reloadAfterClose = null;

const live = new LiveFeed({
  onCandle(candle) {
    const a = state.analysis;
    if (!a || `${a.symbol}|${a.timeframe.key}` !== liveKey) return;
    const kind = chart.liveUpdate(candle);
    if (!kind) return;
    renderLivePrice(a, chart.lastPrice());
    if (kind === "new") {
      // Fechou um candle: recalcula sinais, previsão e plano logo em seguida.
      live.lastBarTime = candle.time;
      clearTimeout(reloadAfterClose);
      reloadAfterClose = setTimeout(() => loadAnalysis({ force: true }), 2500);
    }
  },
  onStatus(status) {
    const badge = $("live-status");
    badge.className = `live-badge ${status.mode}`;
    badge.querySelector("span").textContent = status.text;
  },
});

function syncLiveFeed(data) {
  const key = `${data.symbol}|${data.timeframe.key}`;
  const bars = data.chart.candles;
  const lastBar = bars.length ? bars[bars.length - 1].time : null;
  if (key !== liveKey) {
    liveKey = key;
    live.start(data, lastBar);
  } else {
    live.updateContext(data, Math.max(lastBar || 0, live.lastBarTime || 0));
  }
}

// ------------------------------------------------------------------ rotas (#/SIMBOLO/TEMPO)

function parseHash() {
  const [, symbol, tf] = location.hash.split("/");
  return { symbol: symbol ? decodeURIComponent(symbol) : null, tf: tf && POLL_MS[tf] ? tf : null };
}

function writeHash() {
  const target = `#/${encodeURIComponent(state.symbol)}/${state.tf}`;
  if (location.hash !== target) history.replaceState(null, "", target);
}

window.addEventListener("hashchange", () => {
  const { symbol, tf } = parseHash();
  if (symbol && (symbol !== state.symbol || (tf && tf !== state.tf))) {
    selectAsset(symbol, tf || state.tf);
    switchTab("painel");
  }
});

// ------------------------------------------------------------------ análise

async function loadAnalysis({ force = false, announce = false } = {}) {
  const seq = ++state.seq;
  clearTimeout(state.pollTimer);
  try {
    const data = await api.analysis(state.symbol, state.tf, force);
    if (seq !== state.seq) return;
    const changedSymbol = data.symbol !== state.symbol;
    state.symbol = data.symbol;
    state.analysis = data;
    store.set("symbol", state.symbol);
    writeHash();
    renderError(null);
    renderPanel(data, { isFavorite: state.favorites.some((f) => f.symbol === data.symbol) });
    chart.update(data);
    syncLiveFeed(data);
    if (chart.lastPrice() !== null && chart.lastPrice() !== data.price) renderLivePrice(data, chart.lastPrice());
    tickSignal(data);
    highlightWatchlist();
    if (announce || changedSymbol) {
      toast(`Agora monitorando: ${data.symbol}`, `${data.asset.name} · ${data.timeframe.label} · ${data.signal.title}`, "info", 4000);
    }
    if (data.alerts?.length) pollAlerts();
  } catch (err) {
    if (seq !== state.seq || err instanceof OfflineError) return;  // desligado: a tela de reconexão já explica
    renderError(err.message);
    if (!state.analysis || state.analysis.symbol !== state.symbol) {
      $("signal-title").textContent = "SEM DADOS";
      $("signal-headline").textContent = err.message;
    }
  } finally {
    if (seq === state.seq) state.pollTimer = setTimeout(() => loadAnalysis(), POLL_MS[state.tf]);
  }
}

// ------------------------------------------------------------------ servidor desligado / religado

let healthTimer = null;

onConnectionChange((online) => {
  $("offline").hidden = online;
  if (!online) {
    clearInterval(healthTimer);
    healthTimer = setInterval(() => api.health().catch(() => {}), 4000);
    return;
  }
  clearInterval(healthTimer);
  toast("AURUM conectado", "O servidor voltou. Atualizando a análise.", "success", 3000);
  if (!state.meta) api.meta().then((meta) => { state.meta = meta; renderTimeframes(); }).catch(() => {});
  loadFavorites();
  loadAnalysis({ force: true });
  loadRadar();
  refreshScanner();
});

function selectAsset(symbol, tf = state.tf) {
  state.symbol = symbol.trim().toUpperCase();
  state.tf = tf;
  store.set("tf", tf);
  state.refreshedFor = null;
  renderTimeframes();
  renderLoading(state.symbol);
  loadAnalysis({ announce: true });
}

// ------------------------------------------------------------------ relógio e contagem regressiva

function tick() {
  $("clock").textContent = new Date().toLocaleTimeString("pt-BR");
  const a = state.analysis;
  if (!a) return;
  tickSignal(a);
  const tfMs = a.candle.seconds * 1000;
  const closeAt = new Date(a.candle.close_at).getTime();
  const clockAt = closeAt <= Date.now() && tfMs < 86400000 ? Math.ceil(Date.now() / tfMs) * tfMs : closeAt;
  chart.setClock(a.market.open ? clockAt - Date.now() : null, a.market.open);
  // Quando o candle fecha, busca de novo logo em seguida para confirmar sinais.
  if (a.market.open && Date.now() > closeAt + 4000 && state.refreshedFor !== a.candle.close_at) {
    state.refreshedFor = a.candle.close_at;
    loadAnalysis({ force: true });
  }
}

// ------------------------------------------------------------------ alertas

async function pollAlerts() {
  try {
    if (state.lastAlertId === null) {
      const latest = await api.alerts(0, 1);
      state.lastAlertId = latest[0]?.id ?? 0;
      return;
    }
    const fresh = await api.alerts(state.lastAlertId, 20);
    if (!fresh.length) return;
    state.lastAlertId = Math.max(...fresh.map((a) => a.id));
    for (const alert of fresh.reverse()) {
      toast(alert.title, alert.message, alert.level, alert.level === "danger" ? 15000 : 9000);
      if (state.sound) playTone(alert.level);
      if (state.notify) browserNotify(alert.title, alert.message);
      if (alert.symbol === state.symbol) flashScreen(LEVEL_COLOR[alert.level] || LEVEL_COLOR.info);
    }
    $("alert-dot").hidden = false;
    if (state.tab === "historico") refreshHistory();
  } catch {
    /* sem alertas nesta rodada */
  }
}

// ------------------------------------------------------------------ favoritos e radar

async function loadFavorites() {
  try {
    state.favorites = await api.favorites();
    renderWatchlist(state.favorites, state.radar, state.symbol);
  } catch (err) {
    toast("Não foi possível carregar os favoritos", err.message, "danger");
  }
}

async function loadRadar() {
  try {
    const data = await api.radar(state.tf);
    state.radar = data.items;
    renderWatchlist(state.favorites, state.radar, state.symbol);
    if (state.tab === "radar") renderRadar(state.radar);
  } catch {
    /* tenta de novo no próximo ciclo */
  }
}

function highlightWatchlist() {
  document.querySelectorAll(".watch-item").forEach((el) => el.classList.toggle("active", el.dataset.symbol === state.symbol));
}

async function toggleFavorite(symbol) {
  const isFav = state.favorites.some((f) => f.symbol === symbol);
  try {
    state.favorites = isFav ? await api.removeFavorite(symbol) : await api.addFavorite(symbol);
    toast(isFav ? "Removido dos favoritos" : "Adicionado aos favoritos", symbol, "info", 3000);
    renderWatchlist(state.favorites, state.radar, state.symbol);
    if (state.analysis?.symbol === symbol) $("btn-fav").classList.toggle("on", !isFav);
    if (!isFav) loadRadar();
    if (state.tab === "radar") renderRadar(state.radar.filter((r) => state.favorites.some((f) => f.symbol === r.symbol)));
  } catch (err) {
    toast("Não foi possível alterar os favoritos", err.message, "danger");
  }
}

// ------------------------------------------------------------------ scanner

async function refreshScanner() {
  try {
    const s = await api.scanner();
    const parts = [];
    if (s.running) parts.push("Analisando agora…");
    else if (s.last_run) parts.push(`Última: ${fmtTime(s.last_run)} (${s.last_count} ativos)`);
    if (s.next_run && !s.running) parts.push(`Próxima: ${fmtTime(s.next_run)}`);
    parts.push(`${s.targets.length} ativos monitorados`);
    if (s.errors.length) parts.push(`${s.errors.length} com erro`);
    $("scanner-meta").innerHTML = parts.map(esc).join("<br>");
  } catch {
    $("scanner-meta").textContent = "Servidor indisponível.";
  }
}

// ------------------------------------------------------------------ busca

let searchTimer = null;
let searchIndex = -1;

function setupSearch() {
  const input = $("search-input");
  const box = $("search-results");

  const close = () => { box.hidden = true; searchIndex = -1; };
  const go = (symbol) => {
    close();
    input.value = "";
    input.blur();
    switchTab("painel");
    selectAsset(symbol);
  };

  input.addEventListener("input", () => {
    clearTimeout(searchTimer);
    const q = input.value.trim();
    searchTimer = setTimeout(async () => {
      try {
        const { resolved, results } = await api.search(q);
        if (input.value.trim() !== q) return;
        const items = results.slice(0, 8);
        const hint = resolved && q ? `<div class="search-hint">Enter para analisar <b class="mono">${esc(resolved)}</b></div>` : '<div class="search-hint">Populares</div>';
        box.innerHTML = hint + items.map((r, i) => `
          <button class="search-item" data-symbol="${esc(r.symbol)}" data-i="${i}">
            <span class="sym">${esc(r.symbol)}</span><span class="nm">${esc(r.name)}</span>
            <span class="kd">${esc(KIND_LABEL[r.kind] || r.exchange || r.kind || "")}</span>
          </button>`).join("");
        searchIndex = -1;
        box.hidden = false;
      } catch {
        close();
      }
    }, 180);
  });
  input.addEventListener("focus", () => input.dispatchEvent(new Event("input")));
  input.addEventListener("keydown", (e) => {
    const items = [...box.querySelectorAll(".search-item")];
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!items.length) return;
      searchIndex = (searchIndex + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
      items.forEach((el, i) => el.classList.toggle("active", i === searchIndex));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const picked = items[searchIndex]?.dataset.symbol || input.value.trim();
      if (picked) go(picked);
    } else if (e.key === "Escape") {
      close();
      input.blur();
    }
  });
  box.addEventListener("mousedown", (e) => {
    const item = e.target.closest(".search-item");
    if (item) {
      e.preventDefault();
      go(item.dataset.symbol);
    }
  });
  input.addEventListener("blur", () => setTimeout(close, 120));
}

// ------------------------------------------------------------------ abas, modo e tempo gráfico

function switchTab(tab) {
  state.tab = tab;
  document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.dataset.view === tab));
  if (tab === "radar") { renderRadar(state.radar); loadRadar(); }
  if (tab === "historico") { $("alert-dot").hidden = true; refreshHistory(); }
  if (tab === "operacoes") refreshOperations();
  if (tab === "desempenho") refreshPerformance();
  if (tab === "tutorial") store.set("tutorialSeen", true);
  document.querySelector(".main").scrollTo(0, 0);
}

function refreshHistory() {
  loadHistory({
    symbol: state.symbol, tf: state.tf,
    currentOnly: $("hist-current").checked, onlySignals: $("hist-signals").checked,
  }).catch((err) => toast("Erro ao carregar histórico", err.message, "danger"));
}

function refreshOperations() {
  loadOperations().catch((err) => toast("Erro ao carregar operações", err.message, "danger"));
}

function refreshPerformance() {
  loadPerformance({ symbol: state.symbol, currentOnly: $("perf-current").checked })
    .catch((err) => toast("Erro ao carregar desempenho", err.message, "danger"));
}

function renderTimeframes() {
  const tfs = state.meta?.timeframes || Object.keys(POLL_MS).map((key) => ({ key, label: key }));
  $("tf-switch").innerHTML = tfs.map((t) => `<button data-tf="${t.key}" class="${t.key === state.tf ? "active" : ""}" title="${esc(t.label)}">${TF_SHORT[t.key] || t.key}</button>`).join("");
}

function applyMode() {
  document.body.dataset.mode = state.mode;
  document.querySelectorAll("#mode-switch button").forEach((b) => b.classList.toggle("active", b.dataset.mode === state.mode));
  if (state.analysis) renderPanel(state.analysis, { isFavorite: state.favorites.some((f) => f.symbol === state.symbol) });
}

function applyToggles() {
  $("btn-sound").classList.toggle("on", state.sound);
  $("btn-notify").classList.toggle("on", state.notify);
}

// ------------------------------------------------------------------ posição (registrar / encerrar)

function openPositionModal() {
  const a = state.analysis;
  if (!a) return;
  if (a.position) {
    closePosition(a.position.id, a.price, a.decimals);
    return;
  }
  const form = $("position-form");
  const plan = a.plan;
  const view = a.ticketView || a.ticket;
  const ticket = view?.available && view.mode === "entry" ? view : null;
  state.positionSide = a.signal.side || plan.side;
  syncSideToggle();
  // Com boleta da XP, usa os valores já arredondados ao tick e a quantidade em contratos/ações.
  form.entry_price.value = ticket ? ticket.entry : a.price;
  form.quantity.value = ticket ? ticket.quantity : plan.pips?.lots ?? plan.quantity ?? "";
  form.stop_price.value = ticket ? ticket.stop : plan.stop ?? "";
  form.target_price.value = ticket ? ticket.target2 : plan.target2 ?? "";
  form.notes.value = a.signal.setup ? `Sinal: ${a.signal.setup}` : "";
  form.querySelectorAll("#pre-trade-checklist input").forEach((c) => { c.checked = false; });
  $("position-error").hidden = true;
  const pause = $("position-pause");
  const risk = a.context?.risk;
  pause.hidden = !(risk?.blocked || a.signal.guard === "news");
  pause.textContent = risk?.blocked
    ? `Atenção: seu limite do dia já foi atingido (${risk.reasons.join(" ")}) Registrar outra entrada vai contra o seu plano de risco.`
    : a.signal.guard === "news" ? "Atenção: há notícia de alto impacto agora. O preço pode saltar e passar do stop." : "";
  $("position-modal").showModal();
}

function syncSideToggle() {
  document.querySelectorAll("#pos-side button").forEach((b) => b.classList.toggle("active", b.dataset.side === state.positionSide));
}

function recalcPlanForSide() {
  const a = state.analysis;
  const form = $("position-form");
  const entry = Number(form.entry_price.value);
  if (!a || !entry) return;
  const dist = (a.plan.stop_pct / 100) * entry;
  const long = state.positionSide === "COMPRA";
  const d = a.decimals;
  form.stop_price.value = (long ? entry - dist : entry + dist).toFixed(d);
  form.target_price.value = (long ? entry + dist * a.plan.reward_ratio : entry - dist * a.plan.reward_ratio).toFixed(d);
}

async function submitPosition(e) {
  e.preventDefault();
  const form = $("position-form");
  const num = (v) => (v === "" ? null : Number(v));
  const body = {
    symbol: state.symbol,
    timeframe: state.tf,
    side: state.positionSide,
    entry_price: num(form.entry_price.value),
    quantity: num(form.quantity.value),
    stop_price: num(form.stop_price.value),
    target_price: num(form.target_price.value),
    notes: form.notes.value || null,
  };
  try {
    await api.openPosition(body);
    $("position-modal").close();
    toast("Operação registrada", `${body.side} ${state.symbol} a ${body.entry_price}. O AURUM vai avisar a hora de sair.`, "success");
    loadAnalysis({ force: true });
  } catch (err) {
    $("position-error").textContent = err.message;
    $("position-error").hidden = false;
  }
}

async function closePosition(id, price, decimals = 2) {
  const shown = price !== undefined && price !== "" ? Number(price).toFixed(decimals) : "preço atual";
  if (!confirm(`Encerrar a operação agora em ${shown}?`)) return;
  try {
    const pos = await api.closePosition(id, price === "" || price === undefined ? null : Number(price));
    toast("Operação encerrada", `Resultado: ${pos.pnl_pct > 0 ? "+" : ""}${pos.pnl_pct?.toFixed(2)}%`, pos.pnl_pct >= 0 ? "success" : "danger");
    loadAnalysis({ force: true });
    if (state.tab === "operacoes") refreshOperations();
  } catch (err) {
    toast("Não foi possível encerrar", err.message, "danger");
  }
}

// ------------------------------------------------------------------ configurações

async function openSettings() {
  const form = $("settings-form");
  $("scan-tf-select").innerHTML = (state.meta?.timeframes || []).map((t) => `<option value="${t.key}">${esc(t.label)}</option>`).join("");
  try {
    const s = await api.settings();
    for (const [key, value] of Object.entries(s)) {
      const field = form.elements[key];
      if (!field) continue;
      if (field.type === "checkbox") field.checked = value === "1";
      else field.value = value;
    }
  } catch (err) {
    toast("Erro ao carregar configurações", err.message, "danger");
  }
  $("settings-error").hidden = true;
  form.querySelectorAll(".tg-feedback").forEach((el) => el.remove());
  renderTelegramStatus(form);
  $("settings-modal").showModal();
}

function renderTelegramStatus(form) {
  const on = form.elements.telegram_enabled.checked && form.elements.telegram_chat_id.value.trim();
  const status = $("tg-status");
  status.textContent = on ? "LIGADO" : "DESLIGADO";
  status.classList.toggle("on", Boolean(on));
}

async function telegramAction(button, kind) {
  const form = $("settings-form");
  const body = { token: form.elements.telegram_token.value, chat_id: form.elements.telegram_chat_id.value };
  let feedback = button.parentElement.querySelector(".tg-feedback");
  if (!feedback) {
    feedback = document.createElement("span");
    button.parentElement.append(feedback);
  }
  button.disabled = true;
  feedback.className = "tg-feedback";
  feedback.textContent = kind === "detect" ? "Procurando sua mensagem…" : "Enviando…";
  const res = await (kind === "detect" ? api.telegramDetect(body) : api.telegramTest(body))
    .catch((err) => ({ ok: false, detail: err.message }));
  button.disabled = false;
  feedback.className = `tg-feedback ${res.ok ? "ok" : "err"}`;
  feedback.textContent = res.ok && kind === "test" ? "Mensagem enviada — confira o Telegram." : res.detail;
  if (res.ok && kind === "detect") {
    form.elements.telegram_chat_id.value = res.chat_id;
    form.elements.telegram_enabled.checked = true;
    renderTelegramStatus(form);
  }
}

async function saveSettings(e) {
  e.preventDefault();
  if (e.submitter?.value === "cancel") { $("settings-modal").close(); return; }
  const form = $("settings-form");
  const values = {};
  for (const field of form.elements) {
    if (!field.name) continue;
    values[field.name] = field.type === "checkbox" ? (field.checked ? "1" : "0") : field.value;
  }
  try {
    await api.saveSettings(values);
    $("settings-modal").close();
    toast("Configurações salvas", "Análise recalculada com as novas regras.", "success", 3500);
    loadAnalysis({ force: true });
    refreshScanner();
  } catch (err) {
    $("settings-error").textContent = err.message;
    $("settings-error").hidden = false;
  }
}

// ------------------------------------------------------------------ eventos

function bindEvents() {
  $("tf-switch").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-tf]");
    if (btn && btn.dataset.tf !== state.tf) { selectAsset(state.symbol, btn.dataset.tf); loadRadar(); }
  });
  $("mode-switch").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-mode]");
    if (!btn) return;
    state.mode = btn.dataset.mode;
    store.set("mode", state.mode);
    applyMode();
  });
  document.querySelector(".tabs").addEventListener("click", (e) => {
    const btn = e.target.closest(".tab");
    if (btn) switchTab(btn.dataset.tab);
  });
  document.querySelector("[data-tab-go]").addEventListener("click", () => switchTab("radar"));
  document.querySelector("[data-action=home]").addEventListener("click", (e) => { e.preventDefault(); switchTab("painel"); });

  $("watchlist").addEventListener("click", (e) => {
    const remove = e.target.closest("[data-remove]");
    if (remove) { e.stopPropagation(); toggleFavorite(remove.dataset.remove); return; }
    const item = e.target.closest(".watch-item");
    if (item) { switchTab("painel"); if (item.dataset.symbol !== state.symbol) selectAsset(item.dataset.symbol); }
  });
  $("radar-grid").addEventListener("click", (e) => {
    const card = e.target.closest(".radar-card");
    if (card) { switchTab("painel"); selectAsset(card.dataset.symbol); }
  });
  $("fav-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const value = $("fav-input").value.trim();
    if (!value) return;
    const btn = e.submitter;
    btn.disabled = true;
    try {
      state.favorites = await api.addFavorite(value);
      $("fav-input").value = "";
      toast("Adicionado ao radar", value.toUpperCase(), "success", 3000);
      renderWatchlist(state.favorites, state.radar, state.symbol);
      await loadRadar();
      renderRadar(state.radar);
    } catch (err) {
      toast("Ativo não encontrado", err.message, "danger");
    } finally {
      btn.disabled = false;
    }
  });

  $("btn-fav").addEventListener("click", () => toggleFavorite(state.symbol));
  $("btn-open-position").addEventListener("click", openPositionModal);
  $("pos-side").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-side]");
    if (!btn) return;
    state.positionSide = btn.dataset.side;
    syncSideToggle();
    recalcPlanForSide();
  });
  $("position-form").addEventListener("submit", (e) => {
    if (e.submitter?.value === "cancel") { e.preventDefault(); $("position-modal").close(); return; }
    submitPosition(e);
  });
  $("ops-open").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-close]");
    if (btn) closePosition(Number(btn.dataset.close), btn.dataset.price, 5);
  });
  $("ops-closed").addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-delete]");
    if (!btn || !confirm("Remover este registro do histórico de operações?")) return;
    await api.deletePosition(Number(btn.dataset.delete));
    refreshOperations();
  });

  $("btn-settings").addEventListener("click", openSettings);
  $("settings-form").addEventListener("submit", saveSettings);
  $("settings-form").addEventListener("change", (e) => {
    if (e.target.name === "telegram_enabled") renderTelegramStatus($("settings-form"));
  });
  $("btn-telegram-detect").addEventListener("click", (e) => telegramAction(e.currentTarget, "detect"));
  $("btn-telegram-test").addEventListener("click", (e) => telegramAction(e.currentTarget, "test"));

  $("btn-sound").addEventListener("click", () => {
    state.sound = !state.sound;
    store.set("sound", state.sound);
    applyToggles();
    if (state.sound) { unlockAudio(); playTone("success"); }
  });
  $("btn-notify").addEventListener("click", async () => {
    if (!state.notify) {
      const granted = await requestNotifyPermission();
      if (!granted) { toast("Notificações bloqueadas", "Permita notificações para este site no navegador.", "warning"); return; }
    }
    state.notify = !state.notify;
    store.set("notify", state.notify);
    applyToggles();
    toast(state.notify ? "Notificações ligadas" : "Notificações desligadas", state.notify ? "Você será avisado mesmo com a aba em segundo plano." : "", "info", 3000);
  });

  $("btn-scan-now").addEventListener("click", async () => {
    await api.scanNow();
    toast("Scanner acionado", "Analisando todos os favoritos agora.", "info", 3000);
    setTimeout(refreshScanner, 800);
  });
  $("perf-current").addEventListener("change", refreshPerformance);
  document.addEventListener("change", (e) => {
    if (e.target.id === "ticket-real-price") setRealPrice(state.analysis, e.target.value);
  });
  document.addEventListener("keydown", (e) => {
    if (e.target.id === "ticket-real-price" && e.key === "Enter") { e.preventDefault(); e.target.blur(); }
  });
  $("btn-copy-ticket").addEventListener("click", async () => {
    const view = state.analysis?.ticketView || state.analysis?.ticket;
    const text = view?.available ? ticketText(view) : null;
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      toast("Ordem copiada", "Cole no bloco de notas ou confira enquanto preenche a boleta da XP.", "success", 3500);
    } catch {
      const area = Object.assign(document.createElement("textarea"), { value: text });
      document.body.append(area);
      area.select();
      document.execCommand("copy");
      area.remove();
      toast("Ordem copiada", "", "success", 2500);
    }
  });
  $("btn-start-tour").addEventListener("click", () => {
    store.set("tutorialSeen", true);
    startTour(() => switchTab("painel"));
  });
  document.addEventListener("click", async (e) => {
    if (e.target.closest("[data-open-settings]")) { openSettings(); return; }
    if (e.target.closest("[data-start-tour]")) { startTour(() => switchTab("painel")); return; }
    const open = e.target.closest("[data-open-symbol]");
    if (open) { switchTab("painel"); selectAsset(open.dataset.openSymbol); return; }
    if (e.target.closest("#btn-mt5-reconnect")) {
      const btn = e.target.closest("#btn-mt5-reconnect");
      btn.disabled = true;
      btn.textContent = "Conectando…";
      try {
        const status = await api.mt5Reconnect();
        renderMt5(status);
        toast(status.connected ? "MetaTrader 5 conectado" : "MetaTrader 5 não encontrado", status.connected ? "Cotações em tempo real ativas." : status.error || "", status.connected ? "success" : "warning");
        if (status.connected) loadAnalysis({ force: true });
      } catch (err) {
        toast("Falha ao conectar", err.message, "danger");
      }
    }
  });
  $("btn-b3-pack").addEventListener("click", () => addPack("b3", "WDO e WIN adicionados", "Mini dólar e mini índice agora estão no radar."));
  $("btn-forex-pack").addEventListener("click", () => addPack("forex", "Pares forex adicionados", "EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD, USDCHF, NZDUSD e ouro no radar."));

  async function addPack(kind, title, message) {
    const btn = $(`btn-${kind}-pack`);
    btn.disabled = true;
    try {
      state.favorites = await (kind === "b3" ? api.b3Pack() : api.forexPack());
      toast(title, message, "success", 4000);
      renderWatchlist(state.favorites, state.radar, state.symbol);
      await loadRadar();
      renderRadar(state.radar);
    } catch (err) {
      toast("Não foi possível adicionar", err.message, "danger");
    } finally {
      btn.disabled = false;
    }
  }
  $("hist-current").addEventListener("change", refreshHistory);
  $("hist-signals").addEventListener("change", refreshHistory);

  const applyChartStyle = (style) => {
    chart.setStyle(style);
    store.set("chartStyle", style);
    document.querySelectorAll("#chart-style button").forEach((b) => b.classList.toggle("active", b.dataset.style === style));
  };
  applyChartStyle(store.get("chartStyle", "candles"));
  $("chart-style").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-style]");
    if (btn) applyChartStyle(btn.dataset.style);
  });
  const toggleFullscreen = (force) => {
    const card = $("chart-card");
    const on = force ?? !card.classList.contains("fullscreen");
    card.classList.toggle("fullscreen", on);
    document.body.style.overflow = on ? "hidden" : "";
  };
  $("btn-chart-fullscreen").addEventListener("click", () => toggleFullscreen());
  document.addEventListener("keydown", (e) => {
    const typing = ["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement?.tagName) || document.querySelector("dialog[open]");
    if (typing) return;
    if (e.key.toLowerCase() === "f") toggleFullscreen();
    if (e.key === "Escape" && $("chart-card").classList.contains("fullscreen")) toggleFullscreen(false);
  });
  $("chart-toggles").addEventListener("change", (e) => {
    const input = e.target.closest("input[data-layer]");
    if (input) chart.setLayer(input.dataset.layer, input.checked);
  });

  document.addEventListener("keydown", (e) => {
    const typing = ["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement?.tagName) || document.querySelector("dialog[open]");
    if (typing) return;
    if (e.key === "/") { e.preventDefault(); $("search-input").focus(); }
    const tfKeys = { 1: "1m", 2: "5m", 3: "15m", 4: "1h", 5: "1d" };
    if (tfKeys[e.key]) selectAsset(state.symbol, tfKeys[e.key]);
    if (e.key.toLowerCase() === "s") { state.mode = state.mode === "pro" ? "simple" : "pro"; store.set("mode", state.mode); applyMode(); }
  });
  document.addEventListener("pointerdown", unlockAudio, { once: true });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) loadAnalysis(); });
}

// ------------------------------------------------------------------ início

function registerServiceWorker() {
  if (!("serviceWorker" in navigator)) return;
  navigator.serviceWorker.register("/sw.js").catch(() => { /* navegador sem suporte: segue sem modo app */ });
}

async function boot() {
  registerServiceWorker();
  const fromHash = parseHash();
  if (fromHash.symbol) state.symbol = fromHash.symbol;
  if (fromHash.tf) state.tf = fromHash.tf;

  applyMode();
  applyToggles();
  renderTimeframes();
  bindEvents();
  setupSearch();
  setupTutorial();
  if (!store.get("tutorialSeen", false)) {
    setTimeout(() => toast("Novo por aqui?", "Abra a aba Tutorial (no topo) e clique em “Fazer o tour guiado” para conhecer a tela em 2 minutos.", "info", 12000), 2500);
  }

  try {
    state.meta = await api.meta();
    $("disclaimer-text").textContent = state.meta.disclaimer;
    renderTimeframes();
  } catch (err) {
    renderError(err.message);
  }

  renderLoading(state.symbol);
  await loadFavorites();
  loadAnalysis();
  loadRadar();
  pollAlerts();
  refreshScanner();

  setInterval(tick, 1000);
  setInterval(pollAlerts, 5000);
  setInterval(loadRadar, 60000);
  setInterval(refreshScanner, 15000);
  const refreshMt5 = () => api.mt5Status().then(renderMt5).catch(() => {});
  refreshMt5();
  setInterval(refreshMt5, 30000);
  setInterval(() => { if (state.tab === "historico") refreshHistory(); }, 60000);
  setInterval(() => { if (state.tab === "desempenho") refreshPerformance(); }, 60000);
}

boot();
