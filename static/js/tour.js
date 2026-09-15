// Tour guiado: destaca cada parte do painel e explica em uma frase o que fazer com ela.

import { esc } from "./format.js";

const STEPS = [
  { sel: ".search", title: "1. Escolha o ativo", text: "Digite WDO, WIN, EURUSD, PETR4, BTC… e aperte Enter. Atalho: tecla /." },
  { sel: "#tf-switch", title: "2. Tempo gráfico", text: "Cada botão muda o tamanho do candle. No histórico, o 1h foi o mais confiável; 5m e 15m exigem mais cuidado." },
  { sel: "#signal-card", title: "3. O sinal", text: "ENTRAR AGORA, PREPARE-SE, AGUARDE, SAIR AGORA, MANTENHA ou PAUSA. Só entre quando disser SIM em “É o momento certo?”, dentro da janela." },
  { sel: "#context-chips", title: "4. Contexto", text: "Confiabilidade do ativo, tendência do tempo maior, sessão e notícias. Confiabilidade BAIXA = prefira não operar aqui." },
  { sel: "#risk-strip", title: "5. Seus limites de hoje", text: "Operações, resultado em R e perdas seguidas. Ao bater um limite o AURUM entra em PAUSA até amanhã." },
  { sel: ".plan-card", title: "6. Plano", text: "Entrada, stop, alvos e tamanho pelo seu risco. Nunca entre sem o stop." },
  { sel: "#ticket-card", title: "7. Ordem para a XP", text: "Boleta pronta: código do contrato, quantidade, stop e alvo no tick. Digite o preço atual do XP Unity para ajustar, copie e envie na XP." },
  { sel: ".xp-card", title: "8. Operar pela XP", text: "Passo a passo para enviar a ordem no XP Unity. O AURUM analisa e avisa; a ordem é sempre sua." },
  { sel: ".chart-card", title: "9. Gráfico", text: "Candles, médias, VWAP, suporte/resistência, RSI e MACD. As setas mostram onde as regras dispararam no passado." },
  { sel: ".forecast-card", title: "10. Previsão e agenda", text: "Probabilidade histórica, leituras do mercado, próximos cruzamentos e as notícias que podem mexer no preço." },
  { sel: ".sidebar", title: "11. Favoritos e análise contínua", text: "O scanner analisa seus favoritos a cada fechamento de candle e avisa na tela, com som e no Telegram." },
  { sel: ".tabs", title: "12. Abas", text: "Radar (todos os favoritos), Histórico, Operações, Desempenho real dos sinais, Regras e este Tutorial." },
  { sel: "#btn-settings", title: "13. Configurações", text: "Capital, risco por operação, limites do dia, proteção de notícias, MT5 e Telegram. Pronto: bons treinos no simulador!" },
];

let index = 0;
let hole = null;
let pop = null;
let onKey = null;

function visible(el) {
  const r = el.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
}

function place() {
  const step = STEPS[index];
  const el = document.querySelector(step.sel);
  if (!el || !visible(el)) { next(1); return; }
  el.scrollIntoView({ behavior: "smooth", block: "center", inline: "nearest" });
  setTimeout(() => {
    const r = el.getBoundingClientRect();
    const pad = 6;
    Object.assign(hole.style, { top: `${r.top - pad}px`, left: `${r.left - pad}px`, width: `${r.width + pad * 2}px`, height: `${Math.min(r.height, innerHeight - 40) + pad * 2}px` });
    pop.innerHTML = `
      <div class="tp-step">PASSO ${index + 1} DE ${STEPS.length}</div>
      <h4>${esc(step.title.replace(/^\d+\.\s*/, ""))}</h4>
      <p>${esc(step.text)}</p>
      <div class="tp-foot">
        <div class="tp-dots">${STEPS.map((_, i) => `<i class="${i === index ? "on" : ""}"></i>`).join("")}</div>
        <div>
          <button class="btn btn-ghost btn-xs" data-tour-act="close">Sair</button>
          ${index > 0 ? '<button class="btn btn-ghost btn-xs" data-tour-act="prev">Voltar</button>' : ""}
          <button class="btn btn-gold btn-xs" data-tour-act="next">${index === STEPS.length - 1 ? "Concluir" : "Próximo"}</button>
        </div>
      </div>`;
    const popH = pop.offsetHeight;
    const popW = pop.offsetWidth;
    let top = r.bottom + 14;
    if (top + popH > innerHeight - 12) top = Math.max(12, r.top - popH - 14);
    if (top < 12) top = Math.min(innerHeight - popH - 12, Math.max(12, r.top + 12));
    const left = Math.min(Math.max(12, r.left), innerWidth - popW - 12);
    Object.assign(pop.style, { top: `${top}px`, left: `${left}px` });
  }, 380);
}

function next(delta) {
  index += delta;
  if (index < 0) index = 0;
  if (index >= STEPS.length) { stopTour(); return; }
  place();
}

export function stopTour() {
  hole?.remove();
  pop?.remove();
  hole = pop = null;
  if (onKey) document.removeEventListener("keydown", onKey);
  window.removeEventListener("resize", place);
}

export function startTour(goToPanel) {
  stopTour();
  goToPanel();
  index = 0;
  hole = Object.assign(document.createElement("div"), { className: "tour-hole" });
  pop = Object.assign(document.createElement("div"), { className: "tour-pop" });
  document.body.append(hole, pop);
  pop.addEventListener("click", (e) => {
    const act = e.target.closest("[data-tour-act]")?.dataset.tourAct;
    if (act === "close") stopTour();
    if (act === "next") next(1);
    if (act === "prev") next(-1);
  });
  onKey = (e) => {
    if (e.key === "Escape") stopTour();
    if (e.key === "ArrowRight") next(1);
    if (e.key === "ArrowLeft") next(-1);
  };
  document.addEventListener("keydown", onKey);
  window.addEventListener("resize", place);
  setTimeout(place, 250);
}
