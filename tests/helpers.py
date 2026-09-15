"""Dados sintéticos para testes (sem internet)."""

from __future__ import annotations

import time

import numpy as np
import pandas as pd

from app.config import TIMEFRAMES
from app.market_data import Snapshot


def make_candles(n: int = 400, seed: int = 7, drift: float = 0.0, freq: str = "15min",
                 start: str = "2026-09-01 13:00", volume: bool = True) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    returns = rng.normal(drift, 0.004, n)
    close = 100 * np.exp(np.cumsum(returns))
    open_ = np.concatenate([[close[0]], close[:-1]])
    spread = np.abs(rng.normal(0, 0.002, n)) * close
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread
    index = pd.date_range(start, periods=n, freq=freq, tz="UTC")
    vol = rng.integers(1_000, 10_000, n).astype(float) if volume else np.zeros(n)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": vol}, index=index)


def make_snapshot(symbol: str = "TEST.SA", timeframe: str = "15m", **kwargs) -> Snapshot:
    return Snapshot(symbol, TIMEFRAMES[timeframe], make_candles(**kwargs), "Ativo Teste", time.time())
