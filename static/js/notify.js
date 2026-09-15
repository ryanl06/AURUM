// Toasts, sons e notificações do navegador.

import { esc } from "./format.js";

const toastsEl = () => document.getElementById("toasts");
let audioCtx = null;

export function toast(title, message = "", level = "info", timeout = 7000) {
  const el = document.createElement("div");
  el.className = `toast ${level}`;
  el.innerHTML = `<div><div class="t">${esc(title)}</div>${message ? `<div class="m">${esc(message)}</div>` : ""}</div>
    <button aria-label="Fechar">✕</button>`;
  const close = () => {
    el.classList.add("leaving");
    setTimeout(() => el.remove(), 250);
  };
  el.querySelector("button").addEventListener("click", close);
  toastsEl().prepend(el);
  while (toastsEl().children.length > 5) toastsEl().lastElementChild.remove();
  if (timeout) setTimeout(close, timeout);
}

export function unlockAudio() {
  if (!audioCtx) {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (Ctx) audioCtx = new Ctx();
  }
  if (audioCtx?.state === "suspended") audioCtx.resume();
}

// Sons sintetizados: subida = compra, descida = venda/saída, bipe duplo = prepare-se.
export function playTone(kind) {
  unlockAudio();
  if (!audioCtx) return;
  const patterns = {
    success: [[660, 0], [880, 0.14]],
    danger: [[740, 0], [520, 0.16], [392, 0.32]],
    warning: [[700, 0], [700, 0.18]],
    info: [[600, 0]],
  };
  const now = audioCtx.currentTime;
  for (const [freq, at] of patterns[kind] || patterns.info) {
    const osc = audioCtx.createOscillator();
    const gain = audioCtx.createGain();
    osc.type = "sine";
    osc.frequency.value = freq;
    gain.gain.setValueAtTime(0.0001, now + at);
    gain.gain.exponentialRampToValueAtTime(0.18, now + at + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, now + at + 0.22);
    osc.connect(gain).connect(audioCtx.destination);
    osc.start(now + at);
    osc.stop(now + at + 0.25);
  }
}

export function browserNotify(title, body) {
  if (!("Notification" in window) || Notification.permission !== "granted") return;
  try {
    const n = new Notification(title, { body, tag: title, icon: document.querySelector("link[rel=icon]")?.href });
    n.onclick = () => window.focus();
  } catch {
    /* alguns navegadores bloqueiam notificações fora de HTTPS */
  }
}

export async function requestNotifyPermission() {
  if (!("Notification" in window)) return false;
  if (Notification.permission === "granted") return true;
  return (await Notification.requestPermission()) === "granted";
}

export function flashScreen(color) {
  const main = document.querySelector(".main");
  main.style.setProperty("--flash", color);
  main.classList.remove("flash-screen");
  void main.offsetWidth;
  main.classList.add("flash-screen");
}
