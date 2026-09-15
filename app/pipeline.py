"""Análise completa de um ativo sem banco de dados (usada pela nuvem e pelo ranking de moedas)."""

from __future__ import annotations

import logging

from . import market_data
from .analysis import analyze
from .config import TIMEFRAMES
from .market_data import MarketDataError, Snapshot, get_candles

log = logging.getLogger(__name__)


def exchange_for(symbol: str) -> dict | None:
    """Regras do par na Binance e dólar do dia, para a boleta de cripto."""
    pair = market_data.binance_pair(symbol)
    if not pair:
        return None
    return {"binance": market_data.binance_filters(pair), "brl_per_usd": market_data.usd_brl()}


def context_snaps(symbol: str, timeframe: str) -> tuple[Snapshot | None, Snapshot | None]:
    """Tempo gráfico maior (viés de tendência) e diário (de onde saem as zonas semanais e mensais)."""
    def optional(tf: str | None) -> Snapshot | None:
        if not tf:
            return None
        try:
            return get_candles(symbol, tf)
        except MarketDataError as exc:
            log.info("Gráfico %s indisponível para %s: %s", tf, symbol, exc)
            return None

    higher = TIMEFRAMES[timeframe].higher
    htf = optional(higher)
    daily = htf if higher == "1d" else optional("1d") if timeframe != "1d" else None
    return htf, daily


def analyze_symbol(symbol: str, timeframe: str, settings: dict, events: list[dict], force: bool = True) -> dict:
    snap = get_candles(symbol, timeframe, force=force)
    htf_snap, daily_snap = context_snaps(symbol, timeframe)
    return analyze(snap, settings, None, htf_snap=htf_snap, risk=None, events=events, exchange=exchange_for(symbol),
                   daily_snap=daily_snap)
