// Feed ao vivo do gráfico.
// Cripto: WebSocket público da Binance (cada negociação move o candle).
// Demais ativos: busca o candle em formação a cada poucos segundos (dados de 1 minuto).

import { api } from "./api.js";

const POLL_OPEN_MS = 4000;
const POLL_CLOSED_MS = 60000;
const WS_URLS = ["wss://stream.binance.com:9443/ws/", "wss://data-stream.binance.vision/ws/"];

export class LiveFeed {
  constructor({ onCandle, onStatus }) {
    this.onCandle = onCandle;
    this.onStatus = onStatus;
    this.session = 0;
    this.timer = null;
    this.ws = null;
  }

  start(analysis, lastBarTime) {
    this.stop();
    const session = ++this.session;
    this.symbol = analysis.symbol;
    this.tf = analysis.timeframe.key;
    this.marketOpen = analysis.market.open;
    this.lastBarTime = lastBarTime;
    this.wsIndex = 0;
    this.wsFailures = 0;
    this._poll(session, true);
  }

  updateContext(analysis, lastBarTime) {
    this.marketOpen = analysis.market.open;
    this.lastBarTime = lastBarTime;
  }

  stop() {
    this.session += 1;
    clearTimeout(this.timer);
    if (this.ws) {
      this.ws.onclose = null;
      this.ws.close();
      this.ws = null;
    }
  }

  async _poll(session, first = false) {
    if (session !== this.session) return;
    try {
      const data = await api.live(this.symbol, this.tf, this.lastBarTime);
      if (session !== this.session) return;
      this._emit(data.candle, data.candle.source);
      if (first && data.binance_stream) {
        this._openSocket(session, data.binance_stream);
        return;  // o WebSocket assume a partir daqui
      }
    } catch {
      this.onStatus?.({ mode: "erro", text: "Sem atualização ao vivo agora" });
    }
    if (session !== this.session) return;
    const wait = this.marketOpen ? POLL_OPEN_MS : POLL_CLOSED_MS;
    this.onStatus?.({ mode: this.marketOpen ? "polling" : "fechado",
      text: this.marketOpen ? `Ao vivo · atualiza a cada ${POLL_OPEN_MS / 1000}s` : "Mercado fechado · gráfico parado até a abertura" });
    this.timer = setTimeout(() => this._poll(session), document.hidden ? Math.max(wait, 15000) : wait);
  }

  _openSocket(session, stream) {
    if (session !== this.session) return;
    let ws;
    try {
      ws = new WebSocket(WS_URLS[this.wsIndex % WS_URLS.length] + stream);
    } catch {
      this._fallback(session);
      return;
    }
    this.ws = ws;
    let pending = null;
    let scheduled = false;
    ws.onopen = () => {
      this.wsFailures = 0;
      this.onStatus?.({ mode: "stream", text: "Ao vivo · tempo real (Binance)" });
    };
    ws.onmessage = (event) => {
      const k = JSON.parse(event.data).k;
      if (!k) return;
      pending = {
        time: Math.floor(k.t / 1000), open: +k.o, high: +k.h, low: +k.l, close: +k.c, volume: +k.v, price: +k.c,
      };
      if (!scheduled) {  // no máximo uma atualização por quadro de tela
        scheduled = true;
        requestAnimationFrame(() => {
          scheduled = false;
          if (session === this.session && pending) this._emit(pending, "Binance (tempo real)");
        });
      }
    };
    ws.onclose = () => {
      if (session !== this.session) return;
      this.wsFailures += 1;
      this.wsIndex += 1;
      if (this.wsFailures >= 4) {
        this._fallback(session);
        return;
      }
      this.onStatus?.({ mode: "reconnect", text: "Reconectando ao tempo real…" });
      this.timer = setTimeout(() => this._openSocket(session, stream), 1000 * 2 ** this.wsFailures);
    };
    ws.onerror = () => ws.close();
  }

  _fallback(session) {
    this.ws = null;
    this.timer = setTimeout(() => this._poll(session), POLL_OPEN_MS);
  }

  _emit(candle, source) {
    this.onCandle?.(candle, source);
  }
}
