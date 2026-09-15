// Gráfico ao vivo: candles (normal, Heikin-Ashi ou linha), médias, Bollinger, VWAP, volume, RSI, MACD,
// suporte/resistência, sinais históricos, linhas do plano, cone de projeção e relógio do candle na escala de preço.

import { fmtCountdown, fmtPrice } from "./format.js";

const LWC = window.LightweightCharts;
const COLORS = {
  bull: "#16c784", bear: "#f0454f", gold: "#f0b90b", blue: "#4c9dff", violet: "#8b6cff",
  grid: "rgba(255,255,255,0.035)", text: "#6b7588", border: "rgba(255,255,255,0.08)",
};
const LIVE_MERGE_WINDOW = 90_000;  // ms: por quanto tempo o preço ao vivo tem prioridade sobre a análise

export class MarketChart {
  constructor(container, legendEl) {
    this.container = container;
    this.legendEl = legendEl;
    this.key = null;
    this.decimals = 2;
    this.style = "candles";
    this.layers = { ema: true, bb: true, vwap: true, levels: true, markers: true, plan: true, projection: true };
    this.priceLines = [];
    this.lastPayload = null;
    this.bars = [];
    this.liveBar = null;

    if (!LWC) {
      container.innerHTML = '<div class="error-banner">Não foi possível carregar a biblioteca do gráfico (sem internet?). O restante do painel funciona normalmente.</div>';
      return;
    }

    this.chart = LWC.createChart(container, {
      autoSize: true,
      layout: {
        background: { type: "solid", color: "transparent" },
        textColor: COLORS.text,
        fontFamily: "JetBrains Mono, Consolas, monospace",
        fontSize: 11,
        attributionLogo: false,
        panes: { separatorColor: "rgba(255,255,255,0.07)", separatorHoverColor: "rgba(240,185,11,0.25)", enableResize: true },
      },
      grid: { vertLines: { color: COLORS.grid }, horzLines: { color: COLORS.grid } },
      crosshair: {
        mode: LWC.CrosshairMode.Normal,
        vertLine: { color: "rgba(240,185,11,0.35)", labelBackgroundColor: "#2a3140" },
        horzLine: { color: "rgba(240,185,11,0.35)", labelBackgroundColor: "#2a3140" },
      },
      rightPriceScale: { borderColor: COLORS.border },
      timeScale: {
        borderColor: COLORS.border, timeVisible: true, secondsVisible: false, rightOffset: 12,
        tickMarkFormatter: (time, type) => {
          const d = new Date(time * 1000);
          if (type <= 2) return d.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" });
          return d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
        },
      },
      localization: {
        locale: "pt-BR",
        timeFormatter: (time) => new Date(time * 1000).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }),
        priceFormatter: (p) => fmtPrice(p, this.decimals),
      },
    });

    const line = (color, width = 1.5, extra = {}, pane = 0) => this.chart.addSeries(LWC.LineSeries, {
      color, lineWidth: width, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, ...extra,
    }, pane);

    this.candles = this.chart.addSeries(LWC.CandlestickSeries, {
      upColor: COLORS.bull, downColor: COLORS.bear, borderVisible: false,
      wickUpColor: COLORS.bull, wickDownColor: COLORS.bear, priceLineColor: COLORS.gold,
    }, 0);
    this.closeLine = this.chart.addSeries(LWC.AreaSeries, {
      lineColor: COLORS.gold, topColor: "rgba(240,185,11,0.22)", bottomColor: "rgba(240,185,11,0.0)", lineWidth: 2,
      priceLineColor: COLORS.gold, visible: false,
    }, 0);
    this.volume = this.chart.addSeries(LWC.HistogramSeries, {
      priceScaleId: "vol", priceFormat: { type: "volume" }, lastValueVisible: false, priceLineVisible: false,
    }, 0);
    this.volume.priceScale().applyOptions({ scaleMargins: { top: 0.84, bottom: 0 }, visible: false });

    this.bbUpper = line("rgba(139,108,255,0.55)", 1, { lineStyle: LWC.LineStyle.Dotted });
    this.bbMid = line("rgba(139,108,255,0.25)", 1, { lineStyle: LWC.LineStyle.Dotted });
    this.bbLower = line("rgba(139,108,255,0.55)", 1, { lineStyle: LWC.LineStyle.Dotted });
    this.vwap = line("rgba(38,198,218,0.8)", 1.4, { lineStyle: LWC.LineStyle.LargeDashed });
    this.ema21 = line(COLORS.blue, 1.6);
    this.ema9 = line(COLORS.gold, 1.6);
    this.projHigh = line("rgba(240,185,11,0.45)", 1, { lineStyle: LWC.LineStyle.Dashed });
    this.projMid = line("rgba(240,185,11,0.85)", 1.5, { lineStyle: LWC.LineStyle.Dotted });
    this.projLow = line("rgba(240,185,11,0.45)", 1, { lineStyle: LWC.LineStyle.Dashed });

    this.rsi = line("#c9a0ff", 1.5, { lastValueVisible: true, priceFormat: { type: "price", precision: 1, minMove: 0.1 } }, 1);
    for (const [price, color] of [[70, COLORS.bear], [50, "rgba(255,255,255,0.15)"], [30, COLORS.bull]]) {
      this.rsi.createPriceLine({ price, color, lineWidth: 1, lineStyle: LWC.LineStyle.Dashed, axisLabelVisible: price !== 50, title: "" });
    }

    this.macdHist = this.chart.addSeries(LWC.HistogramSeries, { priceLineVisible: false, lastValueVisible: false }, 2);
    this.macd = line(COLORS.blue, 1.4, { lastValueVisible: false }, 2);
    this.macdSignal = line(COLORS.gold, 1.2, { lastValueVisible: false }, 2);

    this.markers = LWC.createSeriesMarkers(this.candles, []);
    this._sizePanes();
    this.chart.subscribeCrosshairMove((param) => this._legend(param));

    this.clock = Object.assign(document.createElement("div"), { className: "chart-clock" });
    this.pulse = Object.assign(document.createElement("div"), { className: "chart-pulse" });
    container.append(this.clock, this.pulse);
    this.chart.timeScale().subscribeVisibleLogicalRangeChange(() => this._placeOverlays());
  }

  _sizePanes() {
    const panes = this.chart.panes();
    if (panes.length >= 3) {
      panes[0].setStretchFactor(5);
      panes[1].setStretchFactor(1.3);
      panes[2].setStretchFactor(1.3);
    }
  }

  // ---------------------------------------------------------------- camadas e estilo

  setLayer(name, visible) {
    this.layers[name] = visible;
    if (!this.chart) return;
    this.ema9.applyOptions({ visible: this.layers.ema });
    this.ema21.applyOptions({ visible: this.layers.ema });
    for (const s of [this.bbUpper, this.bbMid, this.bbLower]) s.applyOptions({ visible: this.layers.bb });
    this.vwap.applyOptions({ visible: this.layers.vwap });
    for (const s of [this.projHigh, this.projMid, this.projLow]) s.applyOptions({ visible: this.layers.projection });
    if (this.lastPayload) this._decorate(this.lastPayload);
  }

  setStyle(style) {
    this.style = style;
    if (!this.chart) return;
    this.candles.applyOptions({ visible: style !== "line" });
    this.closeLine.applyOptions({ visible: style === "line" });
    this._renderPrice(true);
    this._placeOverlays();
  }

  _renderPrice(full) {
    if (!this.bars.length) return;
    if (this.style === "line") {
      const data = this.bars.map((b) => ({ time: b.time, value: b.close }));
      if (full) this.closeLine.setData(data); else this.closeLine.update(data[data.length - 1]);
    } else if (this.style === "ha") {
      const ha = heikinAshi(this.bars);
      if (full) this.candles.setData(ha); else this.candles.update(ha[ha.length - 1]);
    } else if (full) {
      this.candles.setData(this.bars);
    } else {
      this.candles.update(this.bars[this.bars.length - 1]);
    }
  }

  // ---------------------------------------------------------------- dados da análise

  update(analysis) {
    if (!this.chart) return;
    const { chart: c, decimals, symbol, timeframe } = analysis;
    this.decimals = decimals;
    const key = `${symbol}|${timeframe.key}`;
    const fresh = key !== this.key;
    if (fresh) this.liveBar = null;
    this.key = key;
    this.seconds = timeframe.seconds;

    const format = { type: "price", precision: decimals, minMove: 1 / 10 ** decimals };
    for (const s of [this.candles, this.closeLine, this.ema9, this.ema21, this.bbUpper, this.bbMid, this.bbLower,
      this.vwap, this.projHigh, this.projMid, this.projLow]) s.applyOptions({ priceFormat: format });
    const macdPrecision = Math.min(decimals + 3, 8);
    for (const s of [this.macd, this.macdSignal, this.macdHist]) {
      s.applyOptions({ priceFormat: { type: "price", precision: macdPrecision, minMove: 1 / 10 ** macdPrecision } });
    }

    this.bars = c.candles.map((b) => ({ ...b }));
    this._mergeLive();

    const vol = analysis.volume_available
      ? c.volume.map((v) => ({ time: v.time, value: v.value, color: v.up ? "rgba(22,199,132,0.28)" : "rgba(240,69,79,0.28)" }))
      : [];
    const hist = c.macd_hist.map((p, i, arr) => {
      const prev = i ? arr[i - 1].value : 0;
      const color = p.value >= 0
        ? (p.value >= prev ? "rgba(22,199,132,0.75)" : "rgba(22,199,132,0.35)")
        : (p.value <= prev ? "rgba(240,69,79,0.75)" : "rgba(240,69,79,0.35)");
      return { ...p, color };
    });

    if (this.style === "candles") sync(this.candles, this.bars, fresh);
    else this._renderPrice(true);
    const sets = [
      [this.volume, vol], [this.ema9, c.ema9], [this.ema21, c.ema21],
      [this.bbUpper, c.bb_upper], [this.bbMid, c.bb_mid], [this.bbLower, c.bb_lower], [this.vwap, c.vwap || []],
      [this.rsi, c.rsi], [this.macd, c.macd], [this.macdSignal, c.macd_signal], [this.macdHist, hist],
    ];
    for (const [series, data] of sets) sync(series, data, fresh);

    const proj = this.layers.projection ? c.projection : null;
    this.projHigh.setData(proj ? proj.p90 : []);
    this.projMid.setData(proj ? proj.median : []);
    this.projLow.setData(proj ? proj.p10 : []);

    if (fresh) {
      // Espaçamento fixo mostra os candles recentes com leitura confortável em qualquer largura.
      this.chart.timeScale().applyOptions({ barSpacing: 7 });
      this.chart.timeScale().scrollToRealTime();
    }
    this.lastPayload = analysis;
    this._decorate(analysis);
    this._legend(null);
    this._placeOverlays();
  }

  /** Mantém o candle ao vivo quando chega uma análise mais antiga que o último preço recebido. */
  _mergeLive() {
    const live = this.liveBar;
    if (!live || Date.now() - live.receivedAt > LIVE_MERGE_WINDOW || !this.bars.length) return;
    const last = this.bars[this.bars.length - 1];
    if (live.time === last.time) {
      last.high = Math.max(last.high, live.high);
      last.low = Math.min(last.low, live.low);
      last.close = live.close;
    } else if (live.time > last.time) {
      this.bars.push({ time: live.time, open: live.open, high: live.high, low: live.low, close: live.close });
    }
  }

  // ---------------------------------------------------------------- ao vivo

  /** Atualiza o candle em formação. Retorna "same", "new" ou null (ignorado). */
  liveUpdate(candle) {
    if (!this.chart || !this.bars.length) return null;
    const d = this.decimals;
    const round = (v) => Number(Number(v).toFixed(d));
    const last = this.bars[this.bars.length - 1];
    if (candle.time < last.time) return null;
    let kind;
    if (candle.time === last.time) {
      last.high = round(Math.max(last.high, candle.high));
      last.low = round(Math.min(last.low, candle.low));
      last.close = round(candle.close);
      kind = "same";
    } else {
      this.bars.push({ time: candle.time, open: round(candle.open), high: round(candle.high), low: round(candle.low), close: round(candle.close) });
      if (this.bars.length > 1500) this.bars.shift();
      kind = "new";
    }
    const bar = this.bars[this.bars.length - 1];
    this.liveBar = { ...bar, receivedAt: Date.now() };
    this._renderPrice(false);
    this.candles._last = bar.time;
    this.candles._n = this.bars.length;
    if (candle.volume !== undefined && this.lastPayload?.volume_available) {
      this.volume.update({ time: bar.time, value: candle.volume, color: bar.close >= bar.open ? "rgba(22,199,132,0.28)" : "rgba(240,69,79,0.28)" });
    }
    this._legend(null);
    this._placeOverlays();
    this.pulse.classList.remove("beat");
    void this.pulse.offsetWidth;
    this.pulse.classList.add("beat");
    return kind;
  }

  lastPrice() {
    return this.bars.length ? this.bars[this.bars.length - 1].close : null;
  }

  /** Relógio do candle logo abaixo da etiqueta de preço, como nas plataformas profissionais. */
  setClock(msRemaining, open) {
    if (!this.clock) return;
    this.clock.textContent = open && msRemaining !== null ? fmtCountdown(msRemaining) : "";
    this.clock.hidden = !open || msRemaining === null;
    this._placeOverlays();
  }

  _placeOverlays() {
    if (!this.chart || !this.bars.length) return;
    const price = this.lastPrice();
    const series = this.style === "line" ? this.closeLine : this.candles;
    const y = series.priceToCoordinate(price);
    const scaleWidth = this.chart.priceScale("right").width();
    if (y === null || y === undefined) {
      this.clock.style.display = "none";
      this.pulse.style.display = "none";
      return;
    }
    this.clock.style.display = this.clock.hidden ? "none" : "block";
    this.clock.style.top = `${y + 11}px`;
    this.clock.style.width = `${scaleWidth}px`;
    const x = this.chart.timeScale().timeToCoordinate(this.bars[this.bars.length - 1].time);
    if (x === null || x === undefined) {
      this.pulse.style.display = "none";
    } else {
      this.pulse.style.display = "block";
      this.pulse.style.left = `${x}px`;
      this.pulse.style.top = `${y}px`;
    }
  }

  // ---------------------------------------------------------------- decorações

  _decorate(analysis) {
    const markers = this.layers.markers && this.style !== "line"
      ? analysis.chart.markers.map((m) => ({
        time: m.time,
        position: m.side === "COMPRA" ? "belowBar" : "aboveBar",
        shape: m.side === "COMPRA" ? "arrowUp" : "arrowDown",
        color: m.side === "COMPRA" ? COLORS.bull : COLORS.bear,
        text: m.text,
        size: 1,
      }))
      : [];
    this.markers.setMarkers(markers);

    for (const line of this.priceLines) this.candles.removePriceLine(line);
    this.priceLines = [];
    const add = (price, color, title, style = LWC.LineStyle.Dashed, axisLabelVisible = true) => {
      if (price === null || price === undefined) return;
      this.priceLines.push(this.candles.createPriceLine({ price, color, lineWidth: 1, lineStyle: style, axisLabelVisible, title }));
    };
    if (this.layers.levels) {
      for (const lv of analysis.chart.levels || []) {
        const color = lv.kind === "resistance" ? "rgba(240,69,79,0.45)" : "rgba(22,199,132,0.45)";
        add(lv.price, color, `${lv.kind === "resistance" ? "R" : "S"} ${lv.strength}`, LWC.LineStyle.SparseDotted, false);
      }
    }
    if (!this.layers.plan) return;
    const pos = analysis.position;
    if (pos) {
      add(pos.entry_price, COLORS.gold, "ENTRADA", LWC.LineStyle.Solid);
      const stop = pos.stop_price ?? analysis.plan.stop;
      add(stop, COLORS.bear, "STOP");
      add(pos.target_price, COLORS.bull, "ALVO");
      return;
    }
    const action = analysis.signal.action;
    if (action === "ENTRAR_AGORA" || action === "PREPARE_SE") {
      const p = analysis.plan;
      add(p.stop, COLORS.bear, `STOP ${fmtPrice(p.stop_pct, 2)}%`);
      add(p.target1, "rgba(22,199,132,0.6)", "ALVO 1");
      add(p.target2, COLORS.bull, "ALVO 2");
    }
  }

  _legend(param) {
    if (!this.legendEl || !this.lastPayload) return;
    const d = this.decimals;
    let bar = param?.seriesData?.get(this.style === "line" ? this.closeLine : this.candles);
    let rsi = param?.seriesData?.get(this.rsi);
    if (bar && bar.value !== undefined) bar = this.bars.find((b) => b.time === bar.time);
    if (!bar) {
      bar = this.bars[this.bars.length - 1];
      const r = this.lastPayload.chart.rsi;
      rsi = r[r.length - 1];
    }
    if (!bar) return;
    const change = ((bar.close - bar.open) / bar.open) * 100;
    const cls = change >= 0 ? "up" : "down";
    this.legendEl.innerHTML = `
      <span>A <b>${fmtPrice(bar.open, d)}</b></span><span>M <b>${fmtPrice(bar.high, d)}</b></span>
      <span>m <b>${fmtPrice(bar.low, d)}</b></span><span>F <b class="${cls}">${fmtPrice(bar.close, d)}</b></span>
      <span class="${cls}">${change >= 0 ? "+" : ""}${fmtPrice(change, 2)}%</span>
      ${rsi ? `<span>RSI <b>${fmtPrice(rsi.value, 1)}</b></span>` : ""}`;
  }
}

function heikinAshi(bars) {
  const out = [];
  for (let i = 0; i < bars.length; i += 1) {
    const b = bars[i];
    const close = (b.open + b.high + b.low + b.close) / 4;
    const open = i === 0 ? (b.open + b.close) / 2 : (out[i - 1].open + out[i - 1].close) / 2;
    out.push({ time: b.time, open, close, high: Math.max(b.high, open, close), low: Math.min(b.low, open, close) });
  }
  return out;
}

// Atualiza só o último ponto quando possível (preserva zoom/rolagem do usuário).
function sync(series, data, fresh) {
  if (!data.length) {
    series.setData([]);
    series._n = 0;
    return;
  }
  const current = series._last;
  const last = data[data.length - 1];
  const prev = data[data.length - 2];
  if (!fresh && current && series._n && Math.abs(data.length - series._n) <= 1 && prev
      && (last.time === current || prev.time === current)) {
    if (prev.time === current) series.update(prev);
    series.update(last);
  } else {
    series.setData(data);
  }
  series._last = last.time;
  series._n = data.length;
}
