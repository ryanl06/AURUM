"""Análise completa de um ativo sem banco de dados (usada pela nuvem e pelo ranking de moedas)."""

from __future__ import annotations

import logging

from . import market_data, mt5_source, user_zones
from .analysis import analyze
from .assets import classify
from .config import TIMEFRAMES
from .market_data import MarketDataError, Snapshot, get_candles

log = logging.getLogger(__name__)

MT5_WAITING = ("Boleta em lotes aguardando os preços do MetaTrader 5: abra o MT5, confira o login e clique em "
               "Reconectar no card do MetaTrader 5. Sem os preços da corretora o AURUM não calcula lotes.")


def exchange_for(symbol: str, snap: Snapshot | None = None) -> dict | None:
    """Dados da corretora para a boleta: regras do par na Binance (cripto) ou especificação do ativo no MT5.

    A especificação do MT5 só vale com candles do próprio MT5: plano calculado sobre o GC=F do Yahoo e lotes com o
    ask do XAUUSD da corretora dariam stop e alvo em preços errados."""
    pair = market_data.binance_pair(symbol)
    if pair:
        return {"binance": market_data.binance_filters(pair), "brl_per_usd": market_data.usd_brl()}
    if market_data.mt5_applies(symbol) and not mt5_source.symbol_missing(symbol):
        if snap is None or snap.provider == "mt5":
            spec = mt5_source.symbol_spec(symbol)
            if spec:
                return {"mt5": spec}
        if classify(symbol) not in ("b3", "b3fut"):  # B3 sem MT5 continua com a boleta da XP
            return {"mt5_waiting": MT5_WAITING}
    return None


def zone_inputs(symbol: str, snap: Snapshot | None = None) -> dict:
    """Candles semanais/mensais da corretora (MT5) e as zonas desenhadas pelo usuário no MT5.

    Só entram quando o gráfico também é do MT5: uma linha desenhada no XAUUSD não vale sobre o GC=F do Yahoo."""
    if snap is not None and snap.provider != "mt5":
        return {"native": None, "user": []}
    native = market_data.native_htf(symbol)
    user = user_zones.for_symbol(symbol, mt5_source.broker_name(symbol)) if market_data.USE_MT5 else []
    return {"native": native, "user": user}


def context_snaps(symbol: str, timeframe: str, main: Snapshot | None = None) -> tuple[Snapshot | None, Snapshot | None]:
    """Tempo gráfico maior (viés de tendência) e diário (de onde saem as zonas semanais e mensais).

    Precisam vir da mesma fonte do gráfico principal: zonas do GC=F (Yahoo) sobre candles do XAUUSD (MT5) ficariam
    deslocadas dezenas de dólares. Fonte diferente → busca de novo; se continuar diferente, fica de fora."""
    def mixed(snap: Snapshot) -> bool:  # Binance x Yahoo no cripto dá o mesmo preço; MT5 x Yahoo, não
        return main is not None and snap.provider != main.provider and "mt5" in (snap.provider, main.provider)

    def optional(tf: str | None) -> Snapshot | None:
        if not tf:
            return None
        try:
            snap = get_candles(symbol, tf)
            if mixed(snap):
                snap = get_candles(symbol, tf, force=True)
            if mixed(snap):
                log.info("Gráfico %s de %s veio de outra fonte (%s); ignorado.", tf, symbol, snap.source)
                return None
            return snap
        except MarketDataError as exc:
            log.info("Gráfico %s indisponível para %s: %s", tf, symbol, exc)
            return None

    higher = TIMEFRAMES[timeframe].higher
    htf = optional(higher)
    daily = htf if higher == "1d" else optional("1d") if timeframe != "1d" else None
    return htf, daily


def analyze_symbol(symbol: str, timeframe: str, settings: dict, events: list[dict], force: bool = True) -> dict:
    snap = get_candles(symbol, timeframe, force=force)
    htf_snap, daily_snap = context_snaps(symbol, timeframe, snap)
    return analyze(snap, settings, None, htf_snap=htf_snap, risk=None, events=events, exchange=exchange_for(symbol, snap),
                   daily_snap=daily_snap, zone_inputs=zone_inputs(symbol, snap))
