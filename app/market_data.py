"""Busca de candles com cache em memória.

Cripto vem da Binance (tempo real, com atualização incremental dos últimos candles).
Todo o resto — e qualquer cripto que a Binance não tenha — vem do Yahoo Finance.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import httpx
import pandas as pd
import yfinance as yf

from . import b3, mt5_source
from .assets import classify
from .config import TIMEFRAMES, Timeframe

log = logging.getLogger(__name__)

BINANCE_URLS = ("https://data-api.binance.vision/api/v3/klines", "https://api.binance.com/api/v3/klines")
SOURCE_BINANCE = "Binance (tempo real)"
SOURCE_YAHOO = "Yahoo Finance"
USE_MT5 = False  # ajustado pelas configurações (service.py); desligado por padrão
LOCK_TIMEOUT = 25  # segundos esperando outra busca do mesmo ativo antes de desistir
YAHOO_TIMEOUT = 20


class MarketDataError(RuntimeError):
    pass


@dataclass
class Snapshot:
    symbol: str
    timeframe: Timeframe
    candles: pd.DataFrame  # colunas: open, high, low, close, volume; índice UTC
    name: str | None
    fetched_at: float
    source: str = SOURCE_YAHOO


_cache: dict[tuple[str, str], Snapshot] = {}
_names: dict[str, str | None] = {}
_binance_unsupported: set[str] = set()
_locks: dict[tuple[str, str], threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(key: tuple[str, str]) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def get_candles(symbol: str, timeframe: str, force: bool = False) -> Snapshot:
    if timeframe not in TIMEFRAMES:
        raise MarketDataError(f"Tempo gráfico inválido: {timeframe}")
    tf = TIMEFRAMES[timeframe]
    key = (symbol, timeframe)
    cached = _cache.get(key)
    if cached and not force and time.time() - cached.fetched_at < tf.cache_ttl:
        return cached

    lock = _lock_for(key)
    if not lock.acquire(timeout=LOCK_TIMEOUT):
        # Outra busca do mesmo ativo está demorando (rede lenta): não empilha requisições presas.
        if cached:
            return cached
        raise MarketDataError(f"A busca de dados de {symbol} está demorando. Tente de novo em alguns segundos.")
    try:
        return _fetch_locked(symbol, timeframe, tf, key, force)
    finally:
        lock.release()


def _fetch_locked(symbol: str, timeframe: str, tf: Timeframe, key: tuple[str, str], force: bool) -> Snapshot:
    cached = _cache.get(key)  # outra thread pode ter atualizado enquanto esperávamos
    if cached and not force and time.time() - cached.fetched_at < tf.cache_ttl:
        return cached

    df, source = None, SOURCE_YAHOO
    if USE_MT5 and mt5_source.installed():
        try:
            got = mt5_source.fetch(symbol, timeframe, tf.binance_bars)
            if got is not None and len(got[0]) >= 60:
                df, source = got
        except Exception as exc:  # terminal fechado no meio da leitura etc.
            log.info("MetaTrader 5 indisponível para %s: %s", symbol, exc)

    pair = binance_pair(symbol)
    if df is None and symbol in b3.FUTURES:
        spec = b3.FUTURES[symbol]
        try:
            proxy = _fetch_yahoo(spec.proxy, tf)
            proxy[["open", "high", "low", "close"]] *= spec.proxy_factor
            if tf.seconds < 86400:  # o dólar à vista negocia 24h; o WDO só no pregão da B3
                proxy = _b3_session(proxy)
            df, source = proxy, f"Yahoo Finance · referência aproximada ({spec.proxy_note})"
        except Exception as exc:
            if cached:
                return cached
            raise MarketDataError(f"Sem dados de referência para {spec.name}: {exc}") from exc
    elif df is None and pair and pair not in _binance_unsupported:
        try:
            previous = cached.candles if cached and cached.source == SOURCE_BINANCE else None
            df = _fetch_binance(pair, tf, previous)
            source = SOURCE_BINANCE
        except Exception as exc:  # rede ou par inexistente: cai para o Yahoo
            log.info("Binance indisponível para %s (%s); usando Yahoo.", pair, exc)
            if "Invalid symbol" in str(exc):
                _binance_unsupported.add(pair)
            df = None

    if df is None:
        try:
            df = _fetch_yahoo(symbol, tf)
        except MarketDataError:
            if cached:
                return cached
            raise
        except Exception as exc:  # yfinance lança exceções variadas em falhas de rede
            if cached:
                log.warning("Falha ao atualizar %s %s, usando cache: %s", symbol, timeframe, exc)
                return cached
            raise MarketDataError(f"Não foi possível buscar dados de {symbol}: {exc}") from exc

    if len(df) < 60:
        raise MarketDataError(
            f"Histórico insuficiente para {symbol} em {tf.label} ({len(df)} candles). Tente outro tempo gráfico."
        )
    snap = Snapshot(symbol, tf, df, _names.get(symbol), time.time(), source)
    _cache[key] = snap
    return snap




def _b3_session(df: pd.DataFrame) -> pd.DataFrame:
    """Mantém só candles do pregão de mini contratos da B3 (09h–18h30, dias úteis)."""
    local = df.index.tz_convert("America/Sao_Paulo")
    minutes = local.hour * 60 + local.minute
    return df[(local.weekday < 5) & (minutes >= 9 * 60) & (minutes < 18 * 60 + 30)]


# ---------------------------------------------------------------- Yahoo

def _fetch_yahoo(symbol: str, tf: Timeframe) -> pd.DataFrame:
    raw = yf.Ticker(symbol).history(period=tf.period, interval=tf.interval, auto_adjust=False, timeout=YAHOO_TIMEOUT)
    if raw is None or raw.empty:
        raise MarketDataError(f"Sem dados para “{symbol}”. Confira o código (ex.: PETR4, BTC, EURUSD, XAUUSD).")
    df = raw.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].copy()
    return _clean(df)


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    df = df.dropna(subset=["open", "high", "low", "close"])
    df = df[df["close"] > 0].astype(float)
    df.index = pd.DatetimeIndex(df.index).tz_convert("UTC").as_unit("ns")
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df["volume"] = df["volume"].fillna(0)
    return df


# ---------------------------------------------------------------- Binance

def binance_pair(symbol: str) -> str | None:
    if classify(symbol) != "crypto":
        return None
    base, _, quote = symbol.partition("-")
    if quote in ("USD", "USDT") and base.isalnum():
        return f"{base}USDT"
    return None


def _binance_get(params: dict) -> list:
    last_error: Exception | None = None
    for url in BINANCE_URLS:
        try:
            resp = httpx.get(url, params=params, timeout=10)
            if resp.status_code == 400:
                raise RuntimeError(resp.json().get("msg", "Invalid symbol"))
            resp.raise_for_status()
            return resp.json()
        except RuntimeError:
            raise
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"Binance sem resposta: {last_error}")


def _klines_to_df(rows: list) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    frame = pd.DataFrame(rows).iloc[:, :6]
    frame.columns = ["time", "open", "high", "low", "close", "volume"]
    frame.index = pd.to_datetime(frame.pop("time").astype("int64"), unit="ms", utc=True)
    return _clean(frame.astype(float))


def _fetch_binance(pair: str, tf: Timeframe, previous: pd.DataFrame | None) -> pd.DataFrame:
    interval = "1d" if tf.key == "1d" else tf.key
    if previous is not None and len(previous) >= tf.binance_bars // 2:
        # Atualização incremental: só os últimos candles, mesclados ao histórico em cache.
        latest = _klines_to_df(_binance_get({"symbol": pair, "interval": interval, "limit": 10}))
        merged = pd.concat([previous[~previous.index.isin(latest.index)], latest]).sort_index()
        return merged.iloc[-tf.binance_bars:]

    # Histórico completo: páginas de 1000 candles baixadas em paralelo.
    now_ms = int(time.time() * 1000)
    step_ms = tf.seconds * 1000 * 1000
    pages = -(-tf.binance_bars // 1000)
    params = [{"symbol": pair, "interval": interval, "limit": 1000, "endTime": now_ms - k * step_ms} for k in range(pages)]
    with ThreadPoolExecutor(max_workers=pages) as pool:
        frames = [_klines_to_df(rows) for rows in pool.map(_binance_get, params)]
    df = pd.concat(frames).sort_index()
    return df[~df.index.duplicated(keep="last")].iloc[-tf.binance_bars:]


# ---------------------------------------------------------------- regras da Binance e câmbio

_filters_cache: dict[str, tuple[float, dict | None]] = {}
_fx_cache: dict[str, tuple[float, float]] = {}


def binance_filters(pair: str) -> dict | None:
    """Tick, passo de quantidade e valor mínimo de ordem do par na Binance Spot (cache de 12 horas)."""
    cached = _filters_cache.get(pair)
    if cached and time.time() - cached[0] < 12 * 3600:
        return cached[1]
    info = None
    for url in ("https://data-api.binance.vision/api/v3/exchangeInfo", "https://api.binance.com/api/v3/exchangeInfo"):
        try:
            resp = httpx.get(url, params={"symbol": pair}, timeout=10)
            if resp.status_code != 200:
                continue
            data = resp.json()["symbols"][0]
            filters = {f["filterType"]: f for f in data["filters"]}
            notional = filters.get("NOTIONAL") or filters.get("MIN_NOTIONAL") or {}
            info = {
                "pair": pair, "base": data["baseAsset"], "quote": data["quoteAsset"], "status": data["status"],
                "tick": float(filters["PRICE_FILTER"]["tickSize"]), "step": float(filters["LOT_SIZE"]["stepSize"]),
                "min_qty": float(filters["LOT_SIZE"]["minQty"]), "min_notional": float(notional.get("minNotional", 5)),
                "oco": bool(data.get("ocoAllowed")),
            }
            break
        except (httpx.HTTPError, KeyError, IndexError, ValueError):
            continue
    _filters_cache[pair] = (time.time(), info)
    return info


def usd_brl() -> float | None:
    """Cotação do dólar em reais (para converter o capital configurado em USDT)."""
    cached = _fx_cache.get("USDBRL")
    if cached and time.time() - cached[0] < 1800:
        return cached[1]
    try:
        rate = float(get_candles("USDBRL=X", "1d").candles["close"].iloc[-1])
    except Exception:
        return cached[1] if cached else None
    _fx_cache["USDBRL"] = (time.time(), rate)
    return rate


# ---------------------------------------------------------------- tempo quase real (candle em formação)

LIVE_TTL = 6
_live_cache: dict[str, tuple[float, pd.DataFrame, str]] = {}
_live_lock = threading.Lock()


def recent_minutes(symbol: str) -> tuple[pd.DataFrame, str]:
    """Candles de 1 minuto do dia (leve e com cache curto) para atualizar o candle em formação do gráfico."""
    cached = _live_cache.get(symbol)
    if cached and time.time() - cached[0] < LIVE_TTL:
        return cached[1], cached[2]
    if not _live_lock.acquire(timeout=10):
        if cached:
            return cached[1], cached[2]
        raise MarketDataError("Atualização ao vivo ocupada; tente de novo.")
    try:
        cached = _live_cache.get(symbol)
        if cached and time.time() - cached[0] < LIVE_TTL:
            return cached[1], cached[2]
        df, source = None, SOURCE_YAHOO
        if USE_MT5 and mt5_source.installed():
            got = mt5_source.fetch(symbol, "1m", 600)
            if got is not None:
                df, source = got
        pair = binance_pair(symbol)
        if df is None and pair and pair not in _binance_unsupported:
            try:
                df, source = _klines_to_df(_binance_get({"symbol": pair, "interval": "1m", "limit": 600})), SOURCE_BINANCE
            except Exception:
                df = None
        if df is None:
            target = b3.FUTURES[symbol].proxy if symbol in b3.FUTURES else symbol
            # WDO/WIN usam 5 dias: antes da abertura da B3 o dia atual ainda não tem candle do pregão.
            period = "5d" if symbol in b3.FUTURES else "1d"
            raw = yf.Ticker(target).history(period=period, interval="1m", auto_adjust=False, timeout=YAHOO_TIMEOUT)
            if raw is None or raw.empty:
                raise MarketDataError(f"Sem cotação recente para {symbol}.")
            df = _clean(raw.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].copy())
            if symbol in b3.FUTURES:
                df[["open", "high", "low", "close"]] *= b3.FUTURES[symbol].proxy_factor
                df = _b3_session(df)  # igual ao histórico: só o horário do pregão
        _live_cache[symbol] = (time.time(), df, source)
        return df, source
    finally:
        _live_lock.release()


def live_candle(symbol: str, timeframe: str, bar_start: int | None = None) -> dict:
    """Candle em formação do tempo gráfico pedido, montado com os candles de 1 minuto mais recentes.

    `bar_start` é o horário do último candle que o gráfico já mostra: assim o candle ao vivo fica alinhado
    com a grade da fonte principal (ex.: diário do Yahoo começa no fuso da bolsa, não à meia-noite UTC).
    """
    tf = TIMEFRAMES[timeframe]
    minutes, source = recent_minutes(symbol)
    if minutes.empty:
        raise MarketDataError(f"Sem cotação recente para {symbol}.")
    last_minute = minutes.index[-1]
    last_ts = int(last_minute.value // 10**9)
    if bar_start and last_ts >= bar_start:
        periods = (last_ts - bar_start) // tf.seconds
        bar_ts = bar_start + periods * tf.seconds
    else:
        bar_ts = (last_ts // tf.seconds) * tf.seconds
    start = pd.Timestamp(bar_ts, unit="s", tz="UTC")
    bar_time = start
    part = minutes[minutes.index >= start]
    if part.empty:
        part = minutes.iloc[-1:]
    return {
        "time": int(bar_time.value // 10**9),
        "open": float(part["open"].iloc[0]), "high": float(part["high"].max()),
        "low": float(part["low"].min()), "close": float(part["close"].iloc[-1]),
        "volume": float(part["volume"].sum()), "price": float(part["close"].iloc[-1]),
        "last_update": last_minute.isoformat(), "source": source,
    }


# ---------------------------------------------------------------- nomes e busca

def lookup_name(symbol: str) -> str | None:
    """Nome amigável do ativo (consulta lenta; guardada em cache)."""
    if symbol in _names:
        return _names[symbol]
    name = None
    try:
        for quote in yf.Search(symbol, max_results=5, timeout=8).quotes:
            if quote.get("symbol") == symbol:
                name = quote.get("shortname") or quote.get("longname")
                break
    except Exception as exc:
        log.debug("Busca de nome falhou para %s: %s", symbol, exc)
    _names[symbol] = name.strip() if name else None
    return _names[symbol]


def search_remote(query: str, limit: int = 8) -> list[dict]:
    try:
        quotes = yf.Search(query, max_results=limit, timeout=8).quotes
    except Exception as exc:
        log.debug("Busca remota falhou: %s", exc)
        return []
    out = []
    for q in quotes:
        if not q.get("symbol"):
            continue
        out.append({
            "symbol": q["symbol"],
            "name": (q.get("shortname") or q.get("longname") or q["symbol"]).strip(),
            "kind": (q.get("quoteType") or "").lower(),
            "exchange": q.get("exchDisp") or q.get("exchange") or "",
        })
    return out
