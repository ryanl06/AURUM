"""Orquestra busca de dados, análise, detecção de mudança de sinal e alertas."""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from datetime import datetime

import pandas as pd

from . import database as db
from . import market_data, news
from .analysis import analyze
from .assets import asset_info
from .config import LOCAL_TZ
from .market_data import MarketDataError, get_candles, lookup_name
from .notifier import notify_alert
from .pipeline import context_snaps, exchange_for, zone_inputs

log = logging.getLogger(__name__)

ALERT_ACTIONS = {"ENTRAR_AGORA", "SAIR_AGORA", "PREPARE_SE"}
_detect_lock = threading.Lock()
_cache: dict[tuple, tuple[float, dict]] = {}
_CACHE_SECONDS = 5


SLOW_ANALYSIS = 8.0  # segundos: acima disso o log mostra onde o tempo foi gasto
_analysis_locks: dict[tuple[str, str], threading.Lock] = {}
_analysis_locks_guard = threading.Lock()


def _analysis_lock(symbol: str, timeframe: str) -> threading.Lock:
    with _analysis_locks_guard:
        return _analysis_locks.setdefault((symbol, timeframe), threading.Lock())


def run_analysis(symbol: str, timeframe: str, *, force: bool = False, record: bool = False,
                 source: str = "painel") -> dict:
    started = time.perf_counter()
    stages: list[tuple[str, float]] = []

    def stage(name: str) -> None:
        stages.append((name, time.perf_counter()))

    settings = db.get_settings()
    market_data.configure(settings)
    snap = get_candles(symbol, timeframe, force=force)
    stage("candles")
    if snap.name is None and asset_info(symbol)["name"] == symbol:
        snap.name = lookup_name(symbol)
    htf_snap, daily_snap = context_snaps(symbol, timeframe, snap)
    zones_in = zone_inputs(symbol, snap)
    stage("contexto")
    position = db.open_position_for(symbol)
    guard = risk_state(settings)
    events = news.load()
    exchange = exchange_for(symbol, snap)
    stage("corretora e agenda")

    key = (symbol, timeframe, snap.fetched_at, htf_snap and htf_snap.fetched_at, daily_snap and daily_snap.fetched_at,
           position and position["id"], tuple(sorted(settings.items())), guard["blocked"], guard["trades_today"],
           len(events), tuple((u.low, u.high) for u in zones_in["user"]), bool(zones_in["native"]),
           bool(exchange and exchange.get("mt5")))
    # Painel, radar e scanner pedindo o mesmo ativo ao mesmo tempo: um calcula, os outros aproveitam o resultado.
    lock = _analysis_lock(symbol, timeframe)
    locked = lock.acquire(timeout=90)
    try:
        cached = _cache.get(key)
        if cached and time.time() - cached[0] < _CACHE_SECONDS and not record:
            result = cached[1]
        else:
            # Cripto: boleta com as regras reais do par na Binance e capital convertido para USDT.
            result = analyze(snap, settings, position, htf_snap=htf_snap, risk=guard, events=events,
                             exchange=exchange, daily_snap=daily_snap, zone_inputs=zones_in)
            _cache[key] = (time.time(), result)
            _prune_cache()
            stage("análise")
    finally:
        if locked:
            lock.release()

    result = {**result, "alerts": detect_changes(result, settings, record)}
    if record:
        db.save_analysis(result, source)
    total = time.perf_counter() - started
    if total > SLOW_ANALYSIS:
        marks, previous = [], started
        for name, at in stages:
            marks.append(f"{name} {at - previous:.1f}s")
            previous = at
        log.warning("Análise lenta de %s %s (%s): %.1fs — %s · fonte %s", symbol, timeframe, source, total,
                    ", ".join(marks), snap.source)
    return result


def _prune_cache() -> None:
    if len(_cache) > 64:
        for key, _ in sorted(_cache.items(), key=lambda kv: kv[1][0])[:32]:
            _cache.pop(key, None)


def detect_changes(result: dict, settings: dict, record: bool) -> list[dict]:
    """Cria alertas quando o sinal muda ou quando o preço anda muito desde a última análise registrada."""
    symbol, tf = result["symbol"], result["timeframe"]["key"]
    signal, price = result["signal"], result["price"]
    created = []
    with _detect_lock:
        state = db.get_signal_state(symbol, tf)
        changed = (not state or state["action"] != signal["action"] or state["side"] != signal.get("side")
                   or state["setup"] != signal.get("setup"))
        if changed and (signal["action"] in ALERT_ACTIONS if state is not None
                        else signal["action"] in {"ENTRAR_AGORA", "SAIR_AGORA"}):
            created.append(_signal_alert(result))
            if signal["action"] == "ENTRAR_AGORA" and not result.get("source_warning"):
                # Acompanhamento real: o resultado é conferido nos próximos candles — com a mesma fonte de preços.
                db.record_signal(result)

        threshold = float(settings.get("price_move_alert_pct") or 0)
        moved = False
        if state and state["price"] and threshold > 0 and price:
            move = (price / state["price"] - 1) * 100
            if abs(move) >= threshold:
                moved = True
                created.append(db.add_alert(
                    symbol, tf, "warning", f"Preço mudou {move:+.2f}%",
                    f"{symbol} foi de {state['price']} para {price} desde a última análise registrada.",
                    "PRECO", price))

        if changed or record or moved or state is None:
            ref_price = price if (record or moved or state is None or not state["price"]) else state["price"]
            db.set_signal_state(symbol, tf, signal["action"], signal.get("side"), signal.get("setup"), ref_price)

    for alert in created:
        notify_alert(settings, alert, result if alert["action"] in ALERT_ACTIONS else None)
    return created


def _signal_alert(result: dict) -> dict:
    s = result["signal"]
    level = {"ENTRAR_AGORA": "success" if s.get("side") == "COMPRA" else "danger",
             "SAIR_AGORA": "danger", "PREPARE_SE": "warning"}.get(s["action"], "info")
    title = f"{s['title']} · {result['symbol']}"
    message = f"{s['headline']}. {s['simple']}"
    if s["action"] == "ENTRAR_AGORA" and s.get("window"):
        message += f" {s['window']['label']}."
    return db.add_alert(result["symbol"], result["timeframe"]["key"], level, title, message,
                        s["action"], result["price"])


def risk_state(settings: dict, now: datetime | None = None) -> dict:
    """Disciplina do dia: número de operações, resultado em R e perdas seguidas contra os limites configurados."""
    now = now or datetime.now(LOCAL_TZ)
    start = now.astimezone(LOCAL_TZ).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()

    def number(key: str, default: float) -> float:
        try:
            return float(settings.get(key, default))
        except (TypeError, ValueError):
            return default

    today = db.positions_since(start)
    opened_today = [p for p in today if p["opened_at"] >= start]
    closed_today = sorted((p for p in today if p["status"] == "closed" and (p["closed_at"] or "") >= start),
                          key=lambda p: p["closed_at"])
    day_r = round(sum(p["result_r"] or 0 for p in closed_today), 2)
    streak = 0
    for p in reversed(closed_today):
        if (p["result_r"] if p["result_r"] is not None else p["pnl_pct"] or 0) < 0:
            streak += 1
        else:
            break
    limits = {"max_trades": int(number("max_trades_day", 3)), "max_loss_r": number("daily_max_loss_r", 2),
              "max_streak": int(number("max_consecutive_losses", 2))}
    reasons = []
    if limits["max_trades"] > 0 and len(opened_today) >= limits["max_trades"]:
        reasons.append(f"Você já fez {len(opened_today)} operações hoje (limite {limits['max_trades']}).")
    if limits["max_loss_r"] > 0 and day_r <= -limits["max_loss_r"]:
        reasons.append(f"Perda do dia de {day_r:+.1f}R atingiu o limite de −{limits['max_loss_r']:g}R.")
    if limits["max_streak"] > 0 and streak >= limits["max_streak"]:
        reasons.append(f"{streak} perdas seguidas (limite {limits['max_streak']}). Pare e revise antes de continuar.")
    enabled = settings.get("risk_guard", "1") == "1"
    return {
        "enabled": enabled, "blocked": enabled and bool(reasons), "reasons": reasons,
        "trades_today": len(opened_today), "closed_today": len(closed_today), "day_r": day_r,
        "losing_streak": streak, "open_positions": sum(1 for p in today if p["status"] == "open"), "limits": limits,
    }


SIGNAL_HORIZON = 20  # candles até um sinal sem alvo nem stop ser encerrado pelo fechamento


def evaluate_signals() -> int:
    """Confere os sinais ENTRAR AGORA emitidos: bateu alvo, bateu stop ou expirou. Retorna quantos fecharam."""
    closed = 0
    for sig in db.list_signals(status="open"):
        try:
            snap = get_candles(sig["symbol"], sig["timeframe"])
        except MarketDataError:
            continue
        outcome = judge_signal(sig, snap.candles, snap.timeframe.seconds)
        if outcome:
            db.close_signal(sig["id"], **outcome)
            closed += 1
    return closed


def judge_signal(sig: dict, candles: pd.DataFrame, seconds: int) -> dict | None:
    emitted = pd.Timestamp(sig["ts"]).tz_convert("UTC")
    after = candles[candles.index > emitted.floor(f"{seconds}s")]
    now_closed = after[after.index + pd.Timedelta(seconds=seconds) <= pd.Timestamp.now(tz="UTC")]
    long = sig["side"] == "COMPRA"
    entry, stop, target = sig["entry"], sig["stop"], sig["target2"]
    risk = abs(entry - stop) or 1e-12
    for ts, bar in now_closed.iloc[:SIGNAL_HORIZON].iterrows():
        if (bar["low"] <= stop) if long else (bar["high"] >= stop):
            return {"status": "loss", "result_r": -1.0, "exit_price": stop, "closed_ts": ts.isoformat()}
        if (bar["high"] >= target) if long else (bar["low"] <= target):
            return {"status": "win", "result_r": round(abs(target - entry) / risk, 2), "exit_price": target,
                    "closed_ts": ts.isoformat()}
    if len(now_closed) >= SIGNAL_HORIZON:
        last = now_closed.iloc[SIGNAL_HORIZON - 1]
        move = (last["close"] - entry) if long else (entry - last["close"])
        return {"status": "win" if move > 0 else "loss", "result_r": round(move / risk, 2),
                "exit_price": float(last["close"]), "closed_ts": now_closed.index[SIGNAL_HORIZON - 1].isoformat()}
    return None


def radar(symbols: list[str], timeframe: str) -> list[dict]:
    def one(symbol: str) -> dict:
        try:
            a = run_analysis(symbol, timeframe)
        except MarketDataError as exc:
            return {"symbol": symbol, "error": str(exc)}
        except Exception as exc:  # um ativo com problema não pode derrubar o radar inteiro
            log.exception("Radar falhou para %s", symbol)
            return {"symbol": symbol, "error": str(exc)}
        closes = [c["close"] for c in a["chart"]["candles"][-48:]]
        s = a["signal"]
        return {
            "symbol": symbol, "name": a["asset"]["name"], "kind": a["asset"]["kind"], "price": a["price"],
            "decimals": a["decimals"], "change_pct": a["change_pct"], "score": a["score"],
            "trend": a["trend"]["direction"], "forecast": a["forecast"]["headline"],
            "action": s["action"], "title": s["title"], "tone": s["tone"], "side": s.get("side"),
            "setup": s.get("setup"), "headline": s["headline"], "confidence": s.get("confidence"),
            "rsi": a["indicators"]["rsi"]["value"], "market_open": a["market"]["open"],
            "win_rate": a["backtest"]["overall"]["win_rate"], "spark": closes,
            "reliability": a["context"]["reliability"]["level"],
            "probability": a["forecast"]["probability"],
        }

    # Análise é cálculo pesado em Python (uma thread por vez de fato): mais que 3 só disputa CPU com o painel.
    with ThreadPoolExecutor(max_workers=3) as pool:
        return list(pool.map(one, symbols))
