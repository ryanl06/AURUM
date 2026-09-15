"""Regras de entrada (setups), pontuação de força e estimativas de tempo.

Tudo é calculado de forma vetorizada sobre o DataFrame inteiro: o último candle
alimenta o sinal ao vivo e o histórico completo alimenta o backtest. Assim o que
o backtest mede é exatamente a mesma regra que gera o sinal na tela.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

BUY, SELL = "COMPRA", "VENDA"

# Candles anteriores sem sinal exigidos para um novo disparo contar como "fresco".
FRESH_LOOKBACK = 3


@dataclass
class Condition:
    label: str
    series: pd.Series
    eta: str | None = None  # estimador que diz quando essa condição deve virar verdadeira


@dataclass
class Setup:
    id: str
    side: str
    name: str
    short: str
    kind: str  # trend | reversal | momentum
    description: str
    conditions: list[Condition]
    min_score: float  # compra exige score >= min_score; venda exige score <= -min_score

    def all_true(self) -> pd.Series:
        out = pd.Series(True, index=self.conditions[0].series.index)
        for cond in self.conditions:
            out &= cond.series.fillna(False).astype(bool)
        return out

    def score_ok(self, score: pd.Series) -> pd.Series:
        return (score >= self.min_score) if self.side == BUY else (score <= -self.min_score)

    def signals(self, score: pd.Series) -> pd.Series:
        """Disparos válidos: todas as condições + pontuação coerente + não disparou nos candles anteriores."""
        active = self.all_true() & self.score_ok(score).fillna(False)
        recent = active.shift(1, fill_value=False).astype(int).rolling(FRESH_LOOKBACK, min_periods=1).max()
        return active & (recent == 0)


def _cross_up(a: pd.Series, b: pd.Series) -> pd.Series:
    return (a > b) & (a.shift(1) <= b.shift(1))


def _recent(flag: pd.Series, bars: int) -> pd.Series:
    return flag.fillna(False).astype(int).rolling(bars, min_periods=1).max().astype(bool)


@dataclass
class StrategyContext:
    """Filtros validados no histórico (ver README): tendência do tempo maior e sessão de liquidez."""
    htf_bias: pd.Series | None = None  # +1/0/-1 alinhado ao gráfico, sem olhar o futuro
    htf_label: str | None = None  # ex.: "1h"
    session_mask: pd.Series | None = None  # True quando a sessão líquida está aberta
    session_label: str | None = None


def build_setups(df: pd.DataFrame, volume_available: bool, ctx: StrategyContext | None = None) -> list[Setup]:
    setups = _base_setups(df, volume_available)
    ctx = ctx or StrategyContext()
    for s in setups:
        long = s.side == BUY
        if ctx.htf_bias is not None and s.kind in ("trend", "momentum"):
            bias = ctx.htf_bias.reindex(df.index)
            if s.kind == "trend":  # tendência exige o tempo maior a favor
                ok = (bias > 0) if long else (bias < 0)
                label = f"Tempo maior ({ctx.htf_label}) em {'alta' if long else 'baixa'}"
            else:  # virada rápida aceita tempo maior neutro
                ok = (bias >= 0) if long else (bias <= 0)
                label = f"Tempo maior ({ctx.htf_label}) não está contra"
            s.conditions.append(Condition(label, ok.fillna(False).astype(bool)))
        if ctx.session_mask is not None:
            s.conditions.append(Condition(ctx.session_label or "Sessão líquida aberta",
                                          ctx.session_mask.reindex(df.index).fillna(False).astype(bool)))
    return setups


def session_mask(index: pd.DatetimeIndex, start_hour_utc: int = 7, end_hour_utc: int = 17) -> pd.Series:
    """Sessões de Londres e Nova York (maior liquidez e menor spread no forex)."""
    hours = index.tz_convert("UTC").hour
    return pd.Series((hours >= start_hour_utc) & (hours < end_hour_utc), index=index)


def _base_setups(df: pd.DataFrame, volume_available: bool) -> list[Setup]:
    c = df
    ema_bull, ema_bear = c["ema9"] > c["ema21"], c["ema9"] < c["ema21"]
    macd_bull, macd_bear = c["macd"] > c["macd_signal"], c["macd"] < c["macd_signal"]
    rsi_mid = (c["rsi"] > 30) & (c["rsi"] < 70)
    hist_up = c["macd_hist"] > c["macd_hist"].shift(1)
    hist_down = c["macd_hist"] < c["macd_hist"].shift(1)
    adx_ok = c["adx"] >= 20

    if volume_available:
        vol_ok = c["vol_ratio"] >= 1.2
        vol_label = "Volume acima da média (≥ 1,2x)"
    else:
        vol_ok = pd.Series(True, index=c.index)
        vol_label = "Volume (ativo sem dado de volume — ignorado)"

    return [
        Setup(
            "compra_forte", BUY, "COMPRA FORTE", "FORTE", "trend",
            "Tendência de alta confirmada: médias, MACD e força alinhados.",
            [
                Condition("Média rápida (MME9) acima da lenta (MME21)", ema_bull, "ema_up"),
                Condition("MACD acima da linha de sinal", macd_bull, "macd_up"),
                Condition("RSI entre 30 e 70 (nem caro, nem barato)", rsi_mid),
                Condition("Preço acima da MME50 (tendência maior de alta)", c["close"] > c["ema50"]),
                Condition("ADX ≥ 20 (tendência com força)", adx_ok, "adx_up"),
            ],
            min_score=30,
        ),
        Setup(
            "compra_recuperacao", BUY, "COMPRA DE RECUPERAÇÃO", "RECUP", "reversal",
            "Preço caiu demais e começa a reagir perto do fundo.",
            [
                Condition("RSI abaixo de 30 nos últimos 3 candles (sobrevendido)", c["rsi"].rolling(3).min() < 30, "rsi_down_30"),
                Condition("Preço tocou a banda inferior de Bollinger", c["low"].rolling(3).min() <= c["bb_lower"] * 1.005),
                Condition("MACD estabilizando (histograma subindo)", hist_up),
                Condition("Candle de reação (fechou acima da abertura)", c["close"] > c["open"]),
            ],
            min_score=-45,
        ),
        Setup(
            "compra_momento", BUY, "COMPRA NO MOMENTO", "MOMENTO", "momentum",
            "Virada rápida: MACD e médias cruzaram para cima com participação.",
            [
                Condition("MACD cruzou para cima (últimos 3 candles)", _recent(_cross_up(c["macd"], c["macd_signal"]), 3), "macd_up"),
                Condition("MME9 cruzou acima da MME21 (últimos 5 candles)", _recent(_cross_up(c["ema9"], c["ema21"]), 5) & ema_bull, "ema_up"),
                Condition(vol_label, vol_ok),
                Condition("RSI acima de 50 (compradores no controle)", c["rsi"] > 50, "rsi_up_50"),
            ],
            min_score=15,
        ),
        Setup(
            "venda_forte", SELL, "VENDA FORTE", "FORTE", "trend",
            "Tendência de baixa confirmada: médias, MACD e força alinhados.",
            [
                Condition("Média rápida (MME9) abaixo da lenta (MME21)", ema_bear, "ema_down"),
                Condition("MACD abaixo da linha de sinal", macd_bear, "macd_down"),
                Condition("RSI entre 30 e 70 (nem caro, nem barato)", rsi_mid),
                Condition("Preço abaixo da MME50 (tendência maior de baixa)", c["close"] < c["ema50"]),
                Condition("ADX ≥ 20 (tendência com força)", adx_ok, "adx_up"),
            ],
            min_score=30,
        ),
        Setup(
            "venda_exaustao", SELL, "VENDA DE EXAUSTÃO", "EXAUST", "reversal",
            "Preço subiu demais e começa a devolver perto do topo.",
            [
                Condition("RSI acima de 70 nos últimos 3 candles (sobrecomprado)", c["rsi"].rolling(3).max() > 70, "rsi_up_70"),
                Condition("Preço tocou a banda superior de Bollinger", c["high"].rolling(3).max() >= c["bb_upper"] * 0.995),
                Condition("MACD perdendo força (histograma caindo)", hist_down),
                Condition("Candle de rejeição (fechou abaixo da abertura)", c["close"] < c["open"]),
            ],
            min_score=-45,
        ),
        Setup(
            "venda_momento", SELL, "VENDA NO MOMENTO", "MOMENTO", "momentum",
            "Virada rápida: MACD e médias cruzaram para baixo com participação.",
            [
                Condition("MACD cruzou para baixo (últimos 3 candles)", _recent(_cross_up(c["macd_signal"], c["macd"]), 3), "macd_down"),
                Condition("MME9 cruzou abaixo da MME21 (últimos 5 candles)", _recent(_cross_up(c["ema21"], c["ema9"]), 5) & ema_bear, "ema_down"),
                Condition(vol_label, vol_ok),
                Condition("RSI abaixo de 50 (vendedores no controle)", c["rsi"] < 50, "rsi_down_50"),
            ],
            min_score=15,
        ),
    ]


def score_series(df: pd.DataFrame, volume_available: bool) -> pd.Series:
    """Força do mercado de -100 (vendedores dominam) a +100 (compradores dominam)."""
    c = df
    rsi = c["rsi"]
    parts = [
        np.where(c["ema9"] > c["ema21"], 20, -20),
        np.where(c["close"] > c["ema50"], 10, -10),
        np.where(c["macd"] > c["macd_signal"], 15, -15),
        np.where(c["macd_hist"] > c["macd_hist"].shift(1), 5, -5),
        np.where(c["macd"] > 0, 5, -5),
        np.where(rsi < 30, 8, np.where(rsi > 70, -8, (rsi - 50) / 20 * 10)),
        np.where(c["bb_pctb"] < 0.05, 5, np.where(c["bb_pctb"] > 0.95, -5, 0)),
        np.where(c["plus_di"] > c["minus_di"], 10, -10),
    ]
    if volume_available:
        parts.append(np.where(c["vol_ratio"] >= 1.3, 5 * np.sign(c["close"] - c["open"]), 0))
    raw = sum(parts) / 85 * 100
    damp = np.where(c["adx"] < 18, 0.75, 1.0)  # mercado lateral: confiar menos
    score = pd.Series(np.clip(raw * damp, -100, 100), index=c.index)
    warm = c[["ema50", "rsi", "macd_signal", "adx", "bb_pctb"]].notna().all(axis=1)
    return score.where(warm)


def htf_bias_series(ltf_index: pd.DatetimeIndex, ltf_seconds: int, htf: pd.DataFrame, htf_seconds: int,
                    volume_available: bool, threshold: float = 20) -> pd.Series:
    """Viés do tempo gráfico maior (+1 alta, -1 baixa, 0 neutro) alinhado ao gráfico menor sem olhar o futuro.

    Cada candle maior só passa a valer depois que fecha; cada candle menor usa o último candle maior
    fechado até o seu próprio fechamento.
    """
    score = score_series(htf, volume_available)
    bias = pd.Series(np.select([score >= threshold, score <= -threshold], [1, -1], 0), index=htf.index)
    bias = bias.where(score.notna())
    known_at = htf.index + pd.Timedelta(seconds=htf_seconds)
    bias.index = known_at
    bias = bias[~bias.index.duplicated(keep="last")].sort_index()
    ltf_close = ltf_index + pd.Timedelta(seconds=ltf_seconds)
    aligned = bias.reindex(bias.index.union(ltf_close)).ffill().reindex(ltf_close)
    return pd.Series(aligned.to_numpy(), index=ltf_index)


# ---------------------------------------------------------------- estimativas de tempo

def _eta_to_zero(gap: pd.Series, idx: int, rising: bool, max_candles: int = 8) -> int | None:
    """Quantos candles até `gap` cruzar zero, extrapolando a inclinação dos últimos 3 candles."""
    window = gap.iloc[: idx + 1].dropna().tail(4)
    if len(window) < 4:
        return None
    value = float(window.iloc[-1])
    slope = float(window.diff().dropna().mean())
    if rising and not (value < 0 < slope):
        return None
    if not rising and not (value > 0 > slope):
        return None
    candles = math.ceil(abs(value) / abs(slope))
    return candles if 1 <= candles <= max_candles else None


def eta_estimates(df: pd.DataFrame, idx: int) -> dict[str, int | None]:
    c = df
    ema_gap = c["ema9"] - c["ema21"]
    return {
        "ema_up": _eta_to_zero(ema_gap, idx, rising=True),
        "ema_down": _eta_to_zero(-ema_gap, idx, rising=True),
        "macd_up": _eta_to_zero(c["macd_hist"], idx, rising=True),
        "macd_down": _eta_to_zero(-c["macd_hist"], idx, rising=True),
        "rsi_up_50": _eta_to_zero(c["rsi"] - 50, idx, rising=True),
        "rsi_down_50": _eta_to_zero(50 - c["rsi"], idx, rising=True),
        "rsi_up_70": _eta_to_zero(c["rsi"] - 70, idx, rising=True),
        "rsi_down_30": _eta_to_zero(30 - c["rsi"], idx, rising=True),
        "adx_up": _eta_to_zero(c["adx"] - 20, idx, rising=True),
    }
