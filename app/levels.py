"""Leitura de estrutura de preço: pivôs, suporte/resistência, divergências e padrões de candle.

Tudo respeita o tempo: um pivô só existe depois de confirmado (k candles depois dele),
para que o backtest nunca use informação que ainda não estava disponível.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

PIVOT_K = 5  # candles de cada lado para confirmar um topo/fundo


@dataclass
class Pivots:
    high_idx: np.ndarray  # índice do candle do topo
    low_idx: np.ndarray  # índice do candle do fundo
    k: int = PIVOT_K


def find_pivots(df: pd.DataFrame, k: int = PIVOT_K) -> Pivots:
    window = 2 * k + 1
    highs, lows = df["high"], df["low"]
    is_high = highs == highs.rolling(window, center=True).max()
    is_low = lows == lows.rolling(window, center=True).min()
    return Pivots(np.flatnonzero(is_high.to_numpy()), np.flatnonzero(is_low.to_numpy()), k)


def levels_at(df: pd.DataFrame, pivots: Pivots, i: int, lookback: int = 300, max_levels: int = 3) -> dict:
    """Suportes e resistências conhecidos no candle i (só pivôs confirmados até i)."""
    lo = i - lookback
    hi_idx = pivots.high_idx[(pivots.high_idx + pivots.k <= i) & (pivots.high_idx >= lo)]
    lw_idx = pivots.low_idx[(pivots.low_idx + pivots.k <= i) & (pivots.low_idx >= lo)]
    prices = np.concatenate([df["high"].to_numpy()[hi_idx], df["low"].to_numpy()[lw_idx]])
    ages = np.concatenate([hi_idx, lw_idx])
    close = float(df["close"].iat[i])
    empty = {"supports": [], "resistances": [], "nearest_support": None, "nearest_resistance": None}
    if prices.size == 0:
        return empty

    atr = df["atr"].iat[i] if "atr" in df and pd.notna(df["atr"].iat[i]) else close * 0.004
    tolerance = max(float(atr) * 0.6, close * 0.0008)
    order = np.argsort(prices)
    clusters: list[list[int]] = [[order[0]]]
    for idx in order[1:]:
        if prices[idx] - prices[clusters[-1][-1]] <= tolerance:
            clusters[-1].append(idx)
        else:
            clusters.append([idx])

    levels = []
    for members in clusters:
        level_price = float(np.mean(prices[members]))
        touches = len(members)
        last = int(ages[members].max())
        levels.append({
            "price": level_price,
            "touches": touches,
            "strength": "forte" if touches >= 3 else "média" if touches == 2 else "fraca",
            "last_index": last,
            "distance_pct": (level_price / close - 1) * 100,
        })
    supports = sorted((lv for lv in levels if lv["price"] < close), key=lambda lv: -lv["price"])[:max_levels]
    resistances = sorted((lv for lv in levels if lv["price"] > close), key=lambda lv: lv["price"])[:max_levels]
    return {
        "supports": supports,
        "resistances": resistances,
        "nearest_support": supports[0] if supports else None,
        "nearest_resistance": resistances[0] if resistances else None,
    }


def room_to_level(levels: dict, side: str, entry: float, risk: float) -> float | None:
    """Quantos 'riscos' (R) cabem até o nível contrário mais próximo. None = caminho livre."""
    if risk <= 0:
        return None
    level = levels["nearest_resistance"] if side == "COMPRA" else levels["nearest_support"]
    if not level:
        return None
    return abs(level["price"] - entry) / risk


def divergence_series(df: pd.DataFrame, pivots: Pivots, max_gap: int = 60, active_for: int = 12) -> tuple[pd.Series, pd.Series]:
    """Divergência de RSI: preço faz fundo mais baixo e RSI fundo mais alto (alta) — e o espelho (baixa).

    A divergência fica "ativa" a partir da confirmação do segundo pivô, por `active_for` candles.
    """
    n = len(df)
    bull = np.zeros(n, dtype=bool)
    bear = np.zeros(n, dtype=bool)
    rsi = df["rsi"].to_numpy()
    lows, highs = df["low"].to_numpy(), df["high"].to_numpy()

    def mark(idx: np.ndarray, prices: np.ndarray, out: np.ndarray, bullish: bool) -> None:
        for a, b in zip(idx[:-1], idx[1:]):
            if b - a > max_gap or np.isnan(rsi[a]) or np.isnan(rsi[b]):
                continue
            price_ok = prices[b] < prices[a] if bullish else prices[b] > prices[a]
            rsi_ok = rsi[b] > rsi[a] + 2 if bullish else rsi[b] < rsi[a] - 2
            extreme = rsi[a] < 40 if bullish else rsi[a] > 60
            if price_ok and rsi_ok and extreme:
                start = b + pivots.k
                out[start: min(n, start + active_for)] = True

    mark(pivots.low_idx, lows, bull, True)
    mark(pivots.high_idx, highs, bear, False)
    return pd.Series(bull, index=df.index), pd.Series(bear, index=df.index)


def candle_patterns(df: pd.DataFrame, i: int) -> list[dict]:
    """Padrões clássicos no candle i (use um candle fechado)."""
    if i < 1:
        return []
    o, h, l, c = (float(df[k].iat[i]) for k in ("open", "high", "low", "close"))
    po, pc = float(df["open"].iat[i - 1]), float(df["close"].iat[i - 1])
    body, rng = abs(c - o), h - l
    if rng <= 0:
        return []
    upper, lower = h - max(o, c), min(o, c) - l
    downtrend = c < df["ema21"].iat[i] if pd.notna(df["ema21"].iat[i]) else False
    uptrend = c > df["ema21"].iat[i] if pd.notna(df["ema21"].iat[i]) else False
    out = []
    if pc < po and c > o and o <= pc and c >= po and body > abs(pc - po):
        out.append({"name": "Engolfo de alta", "tone": "bull", "text": "Engolfo de alta — compradores tomaram o controle do candle anterior"})
    if pc > po and c < o and o >= pc and c <= po and body > abs(pc - po):
        out.append({"name": "Engolfo de baixa", "tone": "bear", "text": "Engolfo de baixa — vendedores engoliram o candle anterior"})
    if lower >= 2 * body and upper <= max(body, rng * 0.1) and downtrend:
        out.append({"name": "Martelo", "tone": "bull", "text": "Martelo — preço foi rejeitado no fundo (possível reversão para cima)"})
    if upper >= 2 * body and lower <= max(body, rng * 0.1) and uptrend:
        out.append({"name": "Estrela cadente", "tone": "bear", "text": "Estrela cadente — preço foi rejeitado no topo (possível reversão para baixo)"})
    if body <= rng * 0.1 and not out:
        out.append({"name": "Doji", "tone": "neutral", "text": "Doji — indecisão entre compradores e vendedores"})
    return out
