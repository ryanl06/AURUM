"""Indicadores técnicos calculados com pandas (fórmulas clássicas de Wilder/Appel/Bollinger)."""

from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False, min_periods=span).mean()


def wilder(series: pd.Series, period: int) -> pd.Series:
    """Média móvel de Wilder (RMA), usada por RSI, ATR e ADX."""
    return series.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = wilder(delta.clip(lower=0), period)
    loss = wilder(-delta.clip(upper=0), period)
    rs = gain / loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    # Sem perdas no período = RSI 100; sem ganhos nem perdas = neutro.
    out = out.where(loss != 0, np.where(gain > 0, 100.0, 50.0))
    return out.where(gain.notna())


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = ema(close, fast) - ema(close, slow)
    sig = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return pd.DataFrame({"macd": line, "macd_signal": sig, "macd_hist": line - sig})


def bollinger(close: pd.Series, period: int = 20, stdevs: float = 2.0) -> pd.DataFrame:
    mid = close.rolling(period).mean()
    std = close.rolling(period).std(ddof=0)
    upper, lower = mid + stdevs * std, mid - stdevs * std
    width = (upper - lower).replace(0, np.nan)
    return pd.DataFrame({
        "bb_mid": mid,
        "bb_upper": upper,
        "bb_lower": lower,
        "bb_pctb": (close - lower) / width,  # 0 = na banda inferior, 1 = na superior
        "bb_width": width / mid,
    })


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    return pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return wilder(true_range(df), period)


def adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    tr = wilder(true_range(df), period).replace(0, np.nan)
    plus_di = 100 * wilder(plus_dm, period) / tr
    minus_di = 100 * wilder(minus_dm, period) / tr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return pd.DataFrame({"adx": wilder(dx.fillna(0), period), "plus_di": plus_di, "minus_di": minus_di})


def add_indicators(candles: pd.DataFrame) -> pd.DataFrame:
    df = candles.copy()
    close = df["close"]
    df["ema9"] = ema(close, 9)
    df["ema21"] = ema(close, 21)
    df["ema50"] = ema(close, 50)
    df["rsi"] = rsi(close, 14)
    df = df.join(macd(close)).join(bollinger(close)).join(adx(df))
    df["atr"] = atr(df, 14)
    df["atr_pct"] = df["atr"] / close * 100
    vol_avg = df["volume"].rolling(20).mean()
    df["vol_ratio"] = (df["volume"] / vol_avg.replace(0, np.nan)).fillna(0)
    return df


def session_vwap(df: pd.DataFrame, tz) -> pd.Series:
    """VWAP do dia (reinicia a cada data local): preço médio ponderado pelo volume — referência institucional."""
    typical = (df["high"] + df["low"] + df["close"]) / 3
    day = df.index.tz_convert(tz).date
    pv = (typical * df["volume"]).groupby(day).cumsum()
    vol = df["volume"].groupby(day).cumsum()
    return (pv / vol.replace(0, np.nan)).set_axis(df.index)


def has_volume(df: pd.DataFrame) -> bool:
    """Forex e índices no Yahoo vêm sem volume; nesses casos o filtro de volume é ignorado."""
    return bool((df["volume"].tail(100) > 0).mean() > 0.8)
