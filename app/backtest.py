"""Backtest das regras de entrada, calibração de probabilidades e estatísticas de horário.

Para cada disparo histórico: entrada na abertura do candle seguinte, stop pelo ATR
(limitado ao stop máximo) e alvo em múltiplos do risco. Se nenhum for atingido em
`horizon` candles, a operação é encerrada no fechamento do último candle. Custos
(corretagem + spread) são descontados de cada operação.

O histórico é dividido em "antigo" (70%) e "recente" (30%). O filtro de qualidade das
regras é decidido só com o período antigo e medido no recente — ou seja, fora da amostra.
"""

from __future__ import annotations

from typing import Callable

import numpy as np
import pandas as pd

from .config import LOCAL_TZ
from .strategy import BUY, Setup

IN_SAMPLE_SHARE = 0.7
QUALITY_MIN_TRADES = 15
QUALITY_MIN_PF = 0.9

Gate = Callable[[Setup, int, float, float], bool]


def stop_distance(price: float, atr: float, atr_mult: float, max_stop_pct: float) -> float:
    by_atr = atr * atr_mult if atr and not np.isnan(atr) else price * max_stop_pct / 100
    return float(min(by_atr, price * max_stop_pct / 100))


def simulate_trades(
    df: pd.DataFrame,
    setup: Setup,
    fired: np.ndarray,
    *,
    atr_mult: float,
    max_stop_pct: float,
    reward_ratio: float,
    cost_pct: float = 0.0,
    horizon: int = 20,
    direction_bars: int = 3,
    gate: Gate | None = None,
) -> list[dict]:
    opens, highs, lows, closes = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    atrs = df["atr"].to_numpy(float)
    n = len(df)
    long = setup.side == BUY
    trades = []
    for i in np.flatnonzero(fired):
        if i + 2 >= n:
            continue  # disparo recente demais para avaliar
        entry = opens[i + 1]
        dist = stop_distance(entry, atrs[i], atr_mult, max_stop_pct)
        if dist <= 0 or (gate is not None and not gate(setup, int(i), entry, dist)):
            continue
        stop = entry - dist if long else entry + dist
        target = entry + dist * reward_ratio if long else entry - dist * reward_ratio
        last = min(i + horizon, n - 1)
        result_r, outcome, exit_i = None, "tempo", last
        for j in range(i + 1, last + 1):
            if (lows[j] <= stop) if long else (highs[j] >= stop):  # conservador: stop antes do alvo
                result_r, outcome, exit_i = -1.0, "stop", j
                break
            if (highs[j] >= target) if long else (lows[j] <= target):
                result_r, outcome, exit_i = reward_ratio, "alvo", j
                break
        if result_r is None:
            move = closes[last] - entry
            result_r = (move if long else -move) / dist
        result_r -= entry * cost_pct / 100 / dist
        dir_idx = i + direction_bars
        direction_ok = None
        if dir_idx < n:
            direction_ok = bool(closes[dir_idx] > entry) if long else bool(closes[dir_idx] < entry)
        trades.append({"r": float(result_r), "outcome": outcome, "direction_ok": direction_ok,
                       "index": int(i), "exit_index": int(exit_i)})
    return trades


def run_backtest(
    df: pd.DataFrame,
    setups: list[Setup],
    score: pd.Series,
    *,
    atr_mult: float,
    max_stop_pct: float,
    reward_ratio: float,
    cost_pct: float = 0.0,
    horizon: int = 20,
    direction_bars: int = 3,
    gate: Gate | None = None,
) -> dict:
    n = len(df)
    split = int(n * IN_SAMPLE_SHARE)
    per_setup, all_trades, recent_filtered = [], [], []
    for setup in setups:
        fired = setup.signals(score).to_numpy()
        trades = simulate_trades(df, setup, fired, atr_mult=atr_mult, max_stop_pct=max_stop_pct,
                                 reward_ratio=reward_ratio, cost_pct=cost_pct, horizon=horizon,
                                 direction_bars=direction_bars, gate=gate)
        older = [t for t in trades if t["index"] < split]
        recent = [t for t in trades if t["index"] >= split]
        older_stats, full_stats = summarize(older), summarize(trades)
        # Validação: decide com o período antigo e mede no recente (fora da amostra).
        if passes_quality(older_stats):
            recent_filtered.extend(recent)
        per_setup.append({
            "id": setup.id, "name": setup.name, "side": setup.side, **full_stats,
            "older": older_stats, "recent": summarize(recent),
            "enabled": passes_quality(full_stats),  # ao vivo: decide com todo o histórico disponível
            "last_trades": trades[-5:],
        })
        all_trades.extend(trades)

    first, last_ts = df.index[0], df.index[-1]
    return {
        "overall": summarize(all_trades),
        "recent_all": summarize([t for t in all_trades if t["index"] >= split]),
        "recent_filtered": summarize(recent_filtered),
        "setups": per_setup,
        "horizon": horizon,
        "direction_bars": direction_bars,
        "reward_ratio": reward_ratio,
        "cost_pct": cost_pct,
        "candles": n,
        "split_date": df.index[split].tz_convert(LOCAL_TZ).strftime("%d/%m/%Y") if n > split else None,
        "period": {
            "from": first.tz_convert(LOCAL_TZ).strftime("%d/%m/%Y"),
            "to": last_ts.tz_convert(LOCAL_TZ).strftime("%d/%m/%Y"),
        },
    }


def passes_quality(stats: dict) -> bool:
    """Regra só é bloqueada com amostra suficiente e resultado claramente ruim."""
    return not (stats["trades"] >= QUALITY_MIN_TRADES and (stats["profit_factor"] or 0) < QUALITY_MIN_PF)


def reliability(bt: dict) -> dict:
    """Confiabilidade do ativo + tempo gráfico, medida fora da amostra (período recente)."""
    oos = bt["recent_filtered"]
    n, pf = oos["trades"], oos["profit_factor"]
    if n >= 20 and pf is not None and pf >= 1.1:
        level = "ALTA"
    elif n >= 10 and pf is not None and pf >= 0.95:
        level = "MÉDIA"
    elif n < 10:
        level = "INDEFINIDA"
    else:
        level = "BAIXA"
    pf_text = f"{pf:.2f}".replace(".", ",") if pf is not None else "—"
    short = {
        "ALTA": f"regras ativas ganharam no período recente (fator {pf_text}, {n} sinais)",
        "MÉDIA": f"resultado perto do empate no período recente (fator {pf_text}, {n} sinais)",
        "BAIXA": f"no período recente as regras perderam dinheiro aqui (fator {pf_text}, {n} sinais)",
        "INDEFINIDA": f"poucos sinais no período recente para medir ({n})",
    }[level]
    return {"level": level, "trades": n, "profit_factor": pf, "short": short}


def summarize(trades: list[dict]) -> dict:
    if not trades:
        return {"trades": 0, "wins": 0, "losses": 0, "win_rate": None, "avg_r": None,
                "profit_factor": None, "direction_rate": None, "total_r": 0.0, "max_drawdown_r": 0.0}
    rs = np.array([t["r"] for t in trades])
    wins = int((rs > 0).sum())
    gross_win, gross_loss = rs[rs > 0].sum(), -rs[rs < 0].sum()
    dirs = [t["direction_ok"] for t in trades if t["direction_ok"] is not None]
    ordered = np.array([t["r"] for t in sorted(trades, key=lambda t: t["index"])])
    equity = np.cumsum(ordered)
    drawdown = float((np.maximum.accumulate(np.concatenate([[0], equity]))[1:] - equity).max())
    return {
        "trades": len(trades),
        "wins": wins,
        "losses": len(trades) - wins,
        "win_rate": round(wins / len(trades) * 100, 1),
        "avg_r": round(float(rs.mean()), 3),
        "total_r": round(float(rs.sum()), 2),
        "profit_factor": round(float(gross_win / gross_loss), 2) if gross_loss > 0 else None,
        "direction_rate": round(sum(dirs) / len(dirs) * 100, 1) if dirs else None,
        "max_drawdown_r": round(drawdown, 2),
    }


# ---------------------------------------------------------------- probabilidade calibrada

SCORE_BUCKETS = [-100, -60, -35, -15, 15, 35, 60, 100.0001]


def calibration(df: pd.DataFrame, score: pd.Series, horizon: int = 3) -> dict:
    """Para cada faixa de força: quantas vezes o preço estava mais alto `horizon` candles depois."""
    closes = df["close"]
    forward = (closes.shift(-horizon) / closes - 1) * 100
    frame = pd.DataFrame({"score": score, "fwd": forward}).dropna()
    if frame.empty:
        return {"horizon": horizon, "buckets": []}
    frame["bucket"] = pd.cut(frame["score"], SCORE_BUCKETS, right=False, labels=False)
    buckets = []
    for b, group in frame.groupby("bucket"):
        lo, hi = SCORE_BUCKETS[int(b)], SCORE_BUCKETS[int(b) + 1]
        buckets.append({
            "from": lo, "to": round(hi),
            "count": int(len(group)),
            "up_rate": round(float((group["fwd"] > 0).mean() * 100), 1),
            "avg_move_pct": round(float(group["fwd"].mean()), 3),
        })
    return {"horizon": horizon, "buckets": buckets}


def probability_for(calib: dict, score: float | None) -> dict | None:
    if score is None:
        return None
    for b in calib["buckets"]:
        if b["from"] <= score < b["to"] or (score >= 100 and b["to"] >= 100):
            return b
    return None


def hourly_profile(df: pd.DataFrame, intraday: bool) -> dict:
    """Em quais horários (ou dias) o ativo costuma se movimentar mais."""
    if df.empty:
        return {"buckets": [], "best": [], "unit": "hora"}
    local = df.index.tz_convert(LOCAL_TZ)
    range_pct = (df["high"] - df["low"]) / df["close"] * 100
    body_up = (df["close"] > df["open"]).astype(float)
    if intraday:
        keys, labels, unit = local.hour, {h: f"{h:02d}h" for h in range(24)}, "hora"
    else:
        names = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]
        keys, labels, unit = local.weekday, dict(enumerate(names)), "dia"

    frame = pd.DataFrame({"key": keys, "range": range_pct.to_numpy(), "up": body_up.to_numpy()})
    grouped = frame.groupby("key").agg(range=("range", "mean"), up=("up", "mean"), count=("range", "size"))
    grouped = grouped[grouped["count"] >= 3]
    if grouped.empty:
        return {"buckets": [], "best": [], "unit": unit}
    top = grouped["range"].max()
    buckets = [
        {
            "key": int(k),
            "label": labels[int(k)],
            "range_pct": round(float(row["range"]), 3),
            "intensity": round(float(row["range"] / top), 3) if top else 0,
            "up_rate": round(float(row["up"]) * 100, 1),
            "count": int(row["count"]),
        }
        for k, row in grouped.iterrows()
    ]
    best = [labels[int(k)] for k in grouped["range"].sort_values(ascending=False).head(3).index]
    return {"buckets": buckets, "best": best, "unit": unit}
