"""Monta a análise completa de um ativo: sinal, previsão, contexto, plano de risco, backtest e gráfico."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from . import assets, news, patterns
from .backtest import calibration, hourly_profile, probability_for, reliability, run_backtest
from .config import CHART_CANDLES, DEFAULT_COST_PCT, DISCLAIMER, LOCAL_TZ
from .decision import entry_signal, position_plan, position_signal, position_view, trade_plan
from .explain import atr_ratio, fmt_datetime, fmt_price, fmt_time, forecast, indicator_views, num
from .indicators import add_indicators, has_volume, session_vwap
from .levels import divergence_series, find_pivots, levels_at
from .market_data import Snapshot
from .strategy import BUY, SELL, StrategyContext, build_setups, eta_estimates, htf_bias_series, score_series, session_mask
from .ticket import build_ticket

PREPARE_MAX_ETA = 4  # candles: só avisa "prepare-se" se o disparo estiver próximo
PROBABILITY_HORIZON = 3
SESSION_LABEL = "Sessão de Londres/Nova York aberta (04h–14h de Brasília)"


def price_decimals(price: float, kind: str) -> int:
    if kind == "forex":
        return 5 if price < 20 else 3
    if price >= 10:
        return 2
    if price >= 1:
        return 4
    return max(4, -math.floor(math.log10(price)) + 3) if price > 0 else 6


def numeric_settings(settings: dict, timeframe_seconds: int, kind: str) -> dict:
    def f(key: str, default: float) -> float:
        try:
            return float(settings.get(key, default))
        except (TypeError, ValueError):
            return default

    daily = timeframe_seconds >= 86400
    atr_raw = str(settings.get("atr_stop_mult", "auto")).strip().lower()
    cost_raw = str(settings.get("cost_pct", "auto")).strip().lower()
    return {
        "atr_mult": (1.5 if daily else 2.0) if atr_raw in ("auto", "") else f("atr_stop_mult", 2.0),
        "max_stop": f("max_stop_pct", 3.0),
        "reward": f("reward_ratio", 2.0),
        "cost": DEFAULT_COST_PCT.get(kind, 0.05) if cost_raw in ("auto", "") else f("cost_pct", 0.05),
        "capital": f("capital", 10000),
        "risk_pct": f("risk_per_trade_pct", 1),
        "htf_filter": settings.get("htf_filter", "1") == "1",
        "session_filter": settings.get("session_filter", "1") == "1",
        "quality_gate": settings.get("quality_gate", "1") == "1",
    }


def analyze(snap: Snapshot, settings: dict, position: dict | None = None, now: datetime | None = None,
            htf_snap: Snapshot | None = None, risk: dict | None = None, events: list[dict] | None = None,
            exchange: dict | None = None) -> dict:
    """`exchange` traz dados da corretora para a boleta (ex.: {"binance": filtros, "brl_per_usd": 5.4})."""
    tf = snap.timeframe
    daily = tf.seconds >= 86400
    now = now or datetime.now(timezone.utc)
    info = assets.asset_info(snap.symbol, snap.name)
    kind = info["kind"]
    market = assets.market_status(snap.symbol, now)
    cfg = numeric_settings(settings, tf.seconds, kind)

    df = add_indicators(snap.candles)
    volume_ok = has_volume(df)
    if volume_ok and not daily:
        df["vwap"] = session_vwap(df, LOCAL_TZ)

    # Filtros de contexto validados no histórico.
    strat_ctx = StrategyContext()
    htf_view = None
    if tf.higher and htf_snap is not None and len(htf_snap.candles) > 60:
        htf_df = add_indicators(htf_snap.candles)
        bias = htf_bias_series(df.index, tf.seconds, htf_df, htf_snap.timeframe.seconds, has_volume(htf_df))
        last_bias = bias.iloc[-1]
        htf_view = {
            "timeframe": htf_snap.timeframe.key,
            "bias": None if pd.isna(last_bias) else int(last_bias),
            "text": {1: "em ALTA", -1: "em BAIXA", 0: "NEUTRO"}.get(None if pd.isna(last_bias) else int(last_bias), "—"),
            "filter": cfg["htf_filter"],
        }
        if cfg["htf_filter"]:
            strat_ctx.htf_bias, strat_ctx.htf_label = bias, htf_snap.timeframe.key
    session_applies = kind in ("forex", "futures") and not daily
    if session_applies and cfg["session_filter"]:
        strat_ctx.session_mask, strat_ctx.session_label = session_mask(df.index), SESSION_LABEL

    setups = build_setups(df, volume_ok, strat_ctx)
    score = score_series(df, volume_ok)
    n = len(df)

    last_start = df.index[-1].to_pydatetime()
    forming = bool(market["open"] and now < last_start + timedelta(seconds=tf.seconds))
    closed_i = n - 2 if forming else n - 1
    live_i = n - 1
    row = df.iloc[live_i]
    closed_row = df.iloc[closed_i]
    price = float(row["close"])
    decimals = price_decimals(price, kind)
    live_close_at = last_start + timedelta(seconds=tf.seconds)
    stale = market["open"] and now - live_close_at > timedelta(seconds=tf.seconds * 3)

    # Backtest, calibração e estrutura só com candles fechados (as mesmas regras do sinal ao vivo).
    closed_df = df.iloc[: closed_i + 1]
    closed_score = score.iloc[: closed_i + 1]
    bt = run_backtest(closed_df, build_setups(closed_df, volume_ok, strat_ctx), closed_score,
                      atr_mult=cfg["atr_mult"], max_stop_pct=cfg["max_stop"], reward_ratio=cfg["reward"],
                      cost_pct=cfg["cost"])
    bt_by_id = {s["id"]: s for s in bt["setups"]}
    rel = reliability(bt)
    calib = calibration(closed_df, closed_score, PROBABILITY_HORIZON)
    pivots = find_pivots(closed_df)
    levels = levels_at(closed_df, pivots, closed_i)
    bull_div, bear_div = divergence_series(closed_df, pivots)
    candle_list = patterns.pattern_report(closed_df, patterns.candle_flags(closed_df), closed_i)
    structure = patterns.market_structure(closed_df, pivots, closed_i)
    analog = _cached_analog(snap.symbol, tf.key, closed_df, closed_i)

    etas = eta_estimates(df, live_i)
    score_live = num(score.iloc[live_i], 1)
    score_closed = num(score.iloc[closed_i], 1)

    setup_views, fired_closed, fired_live, near, in_course, blocked = [], [], [], [], [], []
    for s in setups:
        signals = s.signals(score)
        active = s.all_true()
        stats = bt_by_id.get(s.id) or {}
        is_blocked = cfg["quality_gate"] and not stats.get("enabled", True)
        conds = [{"label": c.label, "ok": bool(c.series.fillna(False).iloc[live_i])} for c in s.conditions]
        missing = [c for c, view in zip(s.conditions, conds) if not view["ok"]]
        eta = etas.get(missing[0].eta) if len(missing) == 1 and missing[0].eta else None
        status = None
        if bool(signals.iloc[closed_i]):
            status = "BLOQUEADA" if is_blocked else "DISPAROU"
            (blocked if is_blocked else fired_closed).append(s)
        elif forming and bool(signals.iloc[live_i]):
            status = "BLOQUEADA" if is_blocked else "FORMANDO"
            if not is_blocked:
                fired_live.append(s)
        elif bool(active.iloc[closed_i]) and bool(s.score_ok(score).iloc[closed_i]):
            status = "EM CURSO"
            in_course.append(s)
        elif eta is not None and eta <= PREPARE_MAX_ETA and not is_blocked:
            status = "QUASE"
            near.append((s, eta, missing[0].label))
        elif is_blocked:
            status = "BLOQUEADA"
        last_fire = np.flatnonzero(signals.iloc[: closed_i + 1].to_numpy())
        setup_views.append({
            "id": s.id, "side": s.side, "name": s.name, "kind": s.kind, "description": s.description,
            "conditions": conds, "met": sum(v["ok"] for v in conds), "total": len(conds),
            "status": status, "eta": eta, "blocked": is_blocked,
            "eta_at": fmt_time(last_start + timedelta(seconds=tf.seconds * (eta + 1)), daily) if eta else None,
            "missing": [c.label for c in missing],
            "last_signal": fmt_datetime(df.index[last_fire[-1]]) if len(last_fire) else None,
            "backtest": stats or None,
        })

    trend_dir = "ALTA" if row["ema9"] > row["ema21"] else "BAIXA"
    if pd.notna(row["adx"]) and row["adx"] < 18:
        trend_dir = "LATERAL"

    news_ctx = news.context(snap.symbol, kind, now, events if events is not None else [])
    atr_now = float(row["atr"]) if pd.notna(row["atr"]) else 0.0
    default_side = BUY if (score_live or 0) >= 0 else SELL
    preview_plan = trade_plan(price, atr_now, default_side, cfg, decimals, snap.symbol, kind)
    ctx = {
        "tf": tf, "daily": daily, "now": now, "forming": forming, "market": market,
        "last_start": last_start, "live_close_at": live_close_at, "score": score_live or 0,
        "bt": bt_by_id, "bt_overall": bt["overall"], "price": price, "row": row, "closed_row": closed_row,
        "atr_ratio": atr_ratio(df, live_i), "reliability": rel, "levels": levels, "reward": cfg["reward"],
        "stop_dist": preview_plan["stop_dist"], "decimals": decimals,
        "risk": risk, "news": news_ctx, "news_guard": settings.get("news_guard", "1") == "1",
    }
    if position:
        opened = pd.Timestamp(position["opened_at"]).tz_convert("UTC")
        ctx["since_entry"] = df[df.index >= opened.floor(f"{tf.seconds}s")]
        signal = position_signal(position, ctx, cfg["max_stop"], decimals)
        plan = position_plan(position, price, atr_now, cfg, decimals, snap.symbol, kind)
    else:
        signal = entry_signal(ctx, fired_closed, fired_live, near, in_course, blocked)
        side = signal.get("side") or default_side
        plan = preview_plan if side == default_side else trade_plan(price, atr_now, side, cfg, decimals, snap.symbol, kind)

    extra_reads = _context_reads(df, closed_i, live_i, bull_div, bear_div, candle_list, levels, htf_view, decimals,
                                 session_applies, strat_ctx, structure)
    probability = probability_for(calib, score_live)
    forecast_view = forecast(df, live_i, score_live or 0, etas, last_start, tf.seconds, daily,
                             extra_reads=extra_reads, probability=probability, horizon=PROBABILITY_HORIZON)
    forecast_view["analog"] = _analog_view(analog, float(closed_row["close"]), decimals)
    forecast_view["structure"] = structure
    forecast_view["consensus"] = patterns.consensus(
        score_live, probability["up_rate"] if probability and probability["count"] >= 30 else None, analog,
        htf_view["bias"] if htf_view else None, structure)
    return {
        "symbol": snap.symbol,
        "asset": info,
        "timeframe": {"key": tf.key, "label": tf.label, "seconds": tf.seconds, "higher": tf.higher},
        "generated_at": now.astimezone(LOCAL_TZ).isoformat(),
        "data_time": last_start.astimezone(LOCAL_TZ).isoformat(),
        "fetched_at": datetime.fromtimestamp(snap.fetched_at, LOCAL_TZ).isoformat(),
        "data_source": snap.source,
        "decimals": decimals,
        "price": num(price, decimals),
        "change_pct": num(_change_pct(df, daily), 2),
        "market": market,
        "stale": bool(stale),
        "candle": {
            "start": last_start.astimezone(LOCAL_TZ).isoformat(),
            "close_at": live_close_at.astimezone(LOCAL_TZ).isoformat(),
            "forming": forming,
            "seconds": tf.seconds,
        },
        "score": score_live,
        "score_closed": score_closed,
        "pressure": {"buy": round(50 + (score_live or 0) / 2), "sell": round(50 - (score_live or 0) / 2)},
        "trend": {
            "direction": trend_dir,
            "text": {"ALTA": "Preço subindo (média rápida acima da lenta)",
                     "BAIXA": "Preço descendo (média rápida abaixo da lenta)",
                     "LATERAL": "Sem direção clara (ADX baixo)"}[trend_dir],
        },
        "signal": signal,
        "forecast": forecast_view,
        "context": {
            "htf": htf_view,
            "session": ({"active": bool(session_mask(df.index[-1:]).iloc[0]), "label": SESSION_LABEL,
                         "filter": cfg["session_filter"]} if session_applies else None),
            "reliability": rel,
            "news": news_ctx,
            "risk": risk,
            "levels": _levels_view(levels, decimals),
            "patterns": candle_list,
            "filters": {"htf": cfg["htf_filter"], "session": cfg["session_filter"], "quality_gate": cfg["quality_gate"],
                        "atr_mult": cfg["atr_mult"], "cost_pct": cfg["cost"]},
        },
        "indicators": indicator_views(df, live_i, decimals, volume_ok),
        "plan": {k: v for k, v in plan.items() if k != "stop_dist"},
        "ticket": build_ticket(snap.symbol, kind, plan, signal, snap.source, now.astimezone(LOCAL_TZ).date(), position,
                               info["name"], tf.key, **(exchange or {})),
        "setups": setup_views,
        "backtest": bt,
        "calibration": calib,
        "hours": hourly_profile(closed_df.tail(3000), intraday=not daily),
        "position": position_view(position, price, decimals) if position else None,
        "chart": {**_chart_payload(df, setups, score, closed_i, decimals, levels),
                  "projection": _projection(analog, df, closed_i, tf.seconds, decimals)},
        "volume_available": volume_ok,
        "disclaimer": DISCLAIMER,
    }


def _change_pct(df: pd.DataFrame, daily: bool) -> float | None:
    closes = df["close"]
    if daily:
        ref = closes.iloc[-2]
    else:
        dates = df.index.tz_convert(LOCAL_TZ).date
        previous = closes[dates < dates[-1]]
        ref = previous.iloc[-1] if len(previous) else closes.iloc[0]
    return (closes.iloc[-1] / ref - 1) * 100 if ref else None


def _levels_view(levels: dict, decimals: int) -> dict:
    def view(lv):
        return {"price": num(lv["price"], decimals), "touches": lv["touches"], "strength": lv["strength"],
                "distance_pct": num(lv["distance_pct"], 2)}
    return {"supports": [view(lv) for lv in levels["supports"]],
            "resistances": [view(lv) for lv in levels["resistances"]]}


def _context_reads(df, closed_i, live_i, bull_div, bear_div, candle_list, levels, htf_view, decimals,
                   session_applies, strat_ctx, structure) -> list[dict]:
    reads = []
    if bool(bull_div.iloc[closed_i]):
        reads.append({"tone": "bull", "text": "Divergência de alta no RSI — queda perdendo força (possível virada para cima)"})
    if bool(bear_div.iloc[closed_i]):
        reads.append({"tone": "bear", "text": "Divergência de baixa no RSI — alta perdendo força (possível virada para baixo)"})
    for p in candle_list:
        if p["occurrences"] >= 10 and p["hit_rate"] is not None:
            verdict = "costuma funcionar aqui" if p["hit_rate"] >= 55 else "costuma FALHAR aqui" if p["hit_rate"] < 45 else "sem vantagem aqui"
            text = f"{p['text']} · neste ativo acertou {p['hit_rate']:.0f}% em {p['occurrences']} vezes ({verdict})"
            tone = p["tone"] if p["hit_rate"] >= 55 else "neutral"
        else:
            text, tone = f"{p['text']} · poucas ocorrências neste ativo para medir", "neutral"
        reads.append({"tone": tone, "text": text})
    if structure["trend"] != "indefinida":
        reads.append({"tone": structure["tone"], "text": structure["text"]})
    reads.extend(structure["events"])
    if htf_view and htf_view["bias"] is not None:
        tone = {1: "bull", -1: "bear", 0: "neutral"}[htf_view["bias"]]
        reads.append({"tone": tone, "text": f"Tempo gráfico maior ({htf_view['timeframe']}) {htf_view['text']}"})
    row = df.iloc[live_i]
    if "vwap" in df and pd.notna(row.get("vwap")):
        above = row["close"] > row["vwap"]
        reads.append({"tone": "bull" if above else "bear",
                      "text": f"Preço {'acima' if above else 'abaixo'} da VWAP do dia ({fmt_price(row['vwap'], decimals)})"})
    close = float(row["close"])
    masculine = {"forte": "forte", "média": "médio", "fraca": "fraco"}
    for key, word, tone in (("nearest_resistance", "Resistência", "bear"), ("nearest_support", "Suporte", "bull")):
        lv = levels.get(key)
        if lv and abs(lv["price"] / close - 1) * 100 <= max(0.25, 1.5 * float(df["atr_pct"].iat[live_i] or 0)):
            strength = masculine[lv["strength"]] if word == "Suporte" else lv["strength"]
            reads.append({"tone": tone, "text": f"{word} {strength} perto: {fmt_price(lv['price'], decimals)} "
                                                f"({lv['touches']} toque{'s' if lv['touches'] > 1 else ''})"})
    if session_applies and not bool(session_mask(df.index[-1:]).iloc[0]):
        reads.append({"tone": "neutral", "text": "Fora das sessões de Londres/NY: liquidez menor e sinais mais fracos"})
    return reads


# ---------------------------------------------------------------- padrões semelhantes

_analog_cache: dict[tuple, dict | None] = {}


def _cached_analog(symbol: str, timeframe: str, closed_df: pd.DataFrame, closed_i: int) -> dict | None:
    """Os análogos só mudam quando fecha um candle novo: calcula uma vez por candle."""
    key = (symbol, timeframe, closed_df.index[closed_i], len(closed_df))
    if key not in _analog_cache:
        try:
            _analog_cache[key] = patterns.analog_forecast(closed_df, closed_i)
        except Exception:  # dados insuficientes ou degenerados: a análise segue sem análogos
            _analog_cache[key] = None
        if len(_analog_cache) > 200:
            for old in list(_analog_cache)[:100]:
                _analog_cache.pop(old, None)
    return _analog_cache[key]


def _analog_view(analog: dict | None, close: float, decimals: int) -> dict | None:
    if not analog:
        return None
    v = analog.get("validation") or {}
    bars = analog["projection_bars"]
    low, high = close * (1 + analog["p10"][-1] / 100), close * (1 + analog["p90"][-1] / 100)
    up = analog["up_rate"]
    edge = v.get("edge", "nenhuma")
    if edge == "possível":
        direction_text = f"Nos {analog['neighbors']} momentos mais parecidos, subiu em {up:.0f}% dos casos — e esse método acertou {v['confident_accuracy']:.0f}% quando confiante neste ativo."
    else:
        direction_text = (f"Nos {analog['neighbors']} momentos mais parecidos, subiu em {up:.0f}% dos casos, "
                          f"mas neste ativo esse método acertou só {v.get('accuracy', 0):.0f}% da direção: use como contexto, não como sinal.")
    return {
        "up_rate": up, "down_rate": round(100 - up, 1), "neighbors": analog["neighbors"], "horizon": analog["horizon"],
        "similarity": analog["similarity"], "edge": edge, "validation": v, "direction_text": direction_text,
        "range": {"bars": bars, "low": num(low, decimals), "high": num(high, decimals),
                  "median": num(close * (1 + analog["median"][-1] / 100), decimals),
                  "coverage": v.get("range_coverage"), "target": 80,
                  "text": f"Faixa provável em {bars} candles (~80% dos casos): {fmt_price(low, decimals)} a {fmt_price(high, decimals)}"},
    }


def _projection(analog: dict | None, df: pd.DataFrame, closed_i: int, seconds: int, decimals: int) -> dict | None:
    if not analog:
        return None
    start = int(df.index[closed_i].as_unit("s").asm8.astype("int64"))
    base = float(df["close"].iat[closed_i])
    points = {"median": [], "p10": [], "p90": []}
    for key in points:
        points[key].append({"time": start, "value": round(base, decimals)})
        for h, pct in enumerate(analog[key], start=1):
            points[key].append({"time": start + h * seconds, "value": round(base * (1 + pct / 100), decimals)})
    return points


# ---------------------------------------------------------------- gráfico

def _chart_payload(df, setups, score, closed_i, decimals, levels) -> dict:
    view = df.iloc[-CHART_CANDLES:]
    start = len(df) - len(view)
    times = view.index.as_unit("s").asi8.astype(int).tolist()  # pandas 3 nem sempre usa nanossegundos

    def line(col, digits=decimals):
        if col not in view:
            return []
        values = view[col].to_numpy(float)
        return [{"time": t, "value": round(v, digits)} for t, v in zip(times, values) if not np.isnan(v)]

    candles = [
        {"time": t, "open": round(o, decimals), "high": round(h, decimals), "low": round(lo, decimals), "close": round(c, decimals)}
        for t, o, h, lo, c in zip(times, view["open"], view["high"], view["low"], view["close"])
    ]
    volume = [
        {"time": t, "value": float(v), "up": bool(c >= o)}
        for t, v, o, c in zip(times, view["volume"], view["open"], view["close"])
    ]
    markers = []
    for s in setups:
        fired = s.signals(score).to_numpy()
        for idx in np.flatnonzero(fired[start: closed_i + 1]):
            markers.append({"time": times[idx], "side": s.side, "text": s.short, "setup": s.name})
    markers.sort(key=lambda m: m["time"])
    level_lines = ([{"price": round(lv["price"], decimals), "kind": "support", "strength": lv["strength"]}
                    for lv in levels["supports"][:2]]
                   + [{"price": round(lv["price"], decimals), "kind": "resistance", "strength": lv["strength"]}
                      for lv in levels["resistances"][:2]])
    return {
        "candles": candles, "volume": volume,
        "ema9": line("ema9"), "ema21": line("ema21"), "ema50": line("ema50"),
        "bb_upper": line("bb_upper"), "bb_lower": line("bb_lower"), "bb_mid": line("bb_mid"),
        "vwap": line("vwap"),
        "rsi": line("rsi", 2), "macd": line("macd", decimals + 3), "macd_signal": line("macd_signal", decimals + 3),
        "macd_hist": line("macd_hist", decimals + 3), "markers": markers, "levels": level_lines,
    }
