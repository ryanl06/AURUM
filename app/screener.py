"""Ranking de moedas da Binance Spot para quem começa com pouco dinheiro.

Não é recomendação: cada moeda passa pelo mesmo motor do painel (só regras de COMPRA, que é o
que dá para fazer na Spot sem ter a moeda) nos gráficos de 1h e diário, e o ranking ordena pelo
que as regras fizeram no período recente FORA da amostra, depois das taxas. Também confere se o
capital configurado alcança a ordem mínima da Binance (entrada + proteção OCO) e o spread.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import httpx

from . import news
from .config import DATA_DIR, LOCAL_TZ
from .market_data import SOURCE_BINANCE, usd_brl
from .pipeline import analyze_symbol

log = logging.getLogger(__name__)

API_HOSTS = ("https://data-api.binance.vision", "https://api.binance.com")
CACHE_FILE = DATA_DIR / "screener.json"
UNIVERSE_SIZE = 24
MIN_QUOTE_VOLUME = 20_000_000  # US$ negociados em 24h: liquidez para o preço da boleta ser realista
SCAN_TIMEFRAMES = ("15m", "1h", "1d")
REFRESH_SECONDS = 6 * 3600
WORKERS = 3
STABLES = {"USDC", "FDUSD", "TUSD", "USDP", "DAI", "BUSD", "EUR", "AEUR", "EURI", "USDE", "USD1", "BFUSD", "XUSD",
           "PAXG", "WBTC", "WBETH", "BNSOL", "USDS"}
LEVERAGED = ("UP", "DOWN", "BULL", "BEAR")
LEVEL_RANK = {"ALTA": 3, "MÉDIA": 2, "INDEFINIDA": 1, "BAIXA": 0}


def _get(path: str, params: dict | None = None):
    last = None
    for host in API_HOSTS:
        try:
            resp = httpx.get(host + path, params=params, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # tenta o próximo endereço público
            last = exc
    raise RuntimeError(f"Binance sem resposta: {last}")


def universe(limit: int = UNIVERSE_SIZE) -> list[dict]:
    """Pares USDT mais negociados, sem stablecoins, tokens alavancados nem moedas embrulhadas."""
    tickers = _get("/api/v3/ticker/24hr")
    try:
        books = {b["symbol"]: b for b in _get("/api/v3/ticker/bookTicker")}
    except RuntimeError:
        books = {}
    coins = []
    for t in tickers:
        pair = t["symbol"]
        if not pair.endswith("USDT"):
            continue
        base = pair[:-4]
        price, change = float(t.get("lastPrice") or 0), float(t.get("priceChangePercent") or 0)
        pegged = 0.98 <= price <= 1.02 and abs(change) < 0.5  # stablecoin que não está na lista
        if (not (base.isascii() and base.isalnum()) or base in STABLES or "USD" in base or base.endswith(LEVERAGED)
                or pegged or float(t.get("quoteVolume") or 0) < MIN_QUOTE_VOLUME or price <= 0):
            continue
        book = books.get(pair) or {}
        bid, ask = float(book.get("bidPrice") or 0), float(book.get("askPrice") or 0)
        spread = (ask - bid) / ((ask + bid) / 2) * 100 if bid > 0 and ask > 0 else None
        coins.append({"pair": pair, "base": base, "symbol": f"{base}-USD", "price": float(t["lastPrice"]),
                      "change_pct": round(float(t.get("priceChangePercent") or 0), 2),
                      "volume_usd": round(float(t["quoteVolume"])), "spread_pct": round(spread, 4) if spread is not None else None})
    coins.sort(key=lambda c: -c["volume_usd"])
    return coins[:limit]


def timeframe_view(result: dict) -> dict:
    """O que importa de uma análise completa para o ranking."""
    bt, rel, s, t = result["backtest"], result["context"]["reliability"], result["signal"], result["ticket"] or {}
    oos = bt["recent_filtered"]
    return {
        "timeframe": result["timeframe"]["key"], "label": result["timeframe"]["label"],
        "reliability": rel["level"], "reliability_text": rel["short"],
        "trades": oos["trades"], "win_rate": oos["win_rate"], "profit_factor": oos["profit_factor"],
        "total_r": oos["total_r"], "avg_r": oos["avg_r"], "max_drawdown_r": oos["max_drawdown_r"],
        "history_trades": bt["overall"]["trades"], "history_pf": bt["overall"]["profit_factor"],
        "action": s["action"], "title": s["title"], "headline": s["headline"], "side": s.get("side"),
        "confidence": s.get("confidence"), "stop_pct": result["plan"].get("stop_pct"),
        "min_capital_brl": t.get("min_capital_brl"), "fees_money": t.get("fees_money"),
        "source_ok": result["data_source"] == SOURCE_BINANCE, "decimals": result["decimals"],
    }


def _category(view: dict) -> str:
    pf, level = view["profit_factor"], view["reliability"]
    if level == "ALTA":
        return "OPERAVEL"
    if level in ("MÉDIA", "INDEFINIDA") and pf is not None and pf >= 1:
        return "OBSERVAR"
    return "EVITAR"


def rate(coin: dict, views: list[dict], capital_brl: float) -> dict:
    """Escolhe o melhor tempo gráfico da moeda e dá uma nota de 0 a 100 explicada."""
    usable = [v for v in views if v["source_ok"]]
    if not usable:
        return {**coin, "category": "EVITAR", "score": 0, "best": None, "timeframes": views,
                "reasons": ["Sem histórico confiável da Binance para medir."], "fits_capital": None}
    best = max(usable, key=lambda v: (LEVEL_RANK.get(v["reliability"], 0), v["avg_r"] if v["avg_r"] is not None else -9,
                                      v["trades"]))
    category = _category(best)
    score = {"OPERAVEL": 70, "OBSERVAR": 45, "EVITAR": 15}[category]
    reasons = [f"{best['label']}: {best['reliability_text']}."]
    if best["avg_r"] is not None:
        score += max(-15, min(15, best["avg_r"] * 40))
    score += min(best["trades"], 40) / 40 * 10
    min_capital = best["min_capital_brl"]
    fits = min_capital is None or capital_brl >= min_capital
    if not fits:
        score -= 20
        reasons.append(f"Seu capital (R$ {capital_brl:,.2f}) não alcança a ordem mínima com proteção "
                       f"(~R$ {min_capital:,.2f}).".replace(",", "X").replace(".", ",").replace("X", "."))
    if coin.get("spread_pct") is not None and coin["spread_pct"] > 0.05:
        score -= 5
        reasons.append(f"Spread alto ({coin['spread_pct']:.3f}%): a entrada sai mais cara.".replace(".", ","))
    if best["action"] == "ENTRAR_AGORA" and best["side"] == "COMPRA":
        score += 5
        reasons.append("Sinal de COMPRA ativo agora.")
    elif best["action"] == "PREPARE_SE":
        reasons.append("Sinal de compra se formando.")
    if category == "EVITAR":
        reasons.append("As regras perderam dinheiro aqui no período recente: melhor não operar esta moeda agora.")
    return {**coin, "category": category, "score": int(round(max(0, min(100, score)))), "best": best,
            "timeframes": views, "reasons": reasons, "fits_capital": fits}


class Screener:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.running = False
        self.progress = {"done": 0, "total": 0}
        self.data: dict = {"items": [], "finished_at": None, "capital_brl": None, "error": None}
        try:
            self.data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass

    def status(self, settings: dict | None = None) -> dict:
        finished = self.data.get("finished_at")
        age = time.time() - datetime.fromisoformat(finished).timestamp() if finished else None
        changed = False
        if settings is not None:  # estratégia ou capital mudaram: o ranking antigo não vale mais
            try:
                changed = (self.data.get("strategy_mode") != settings.get("strategy_mode", "zonas")
                           or abs(float(self.data.get("capital_brl") or 0) - float(settings.get("capital") or 0)) > 0.01)
            except ValueError:
                changed = True
        return {**self.data, "running": self.running, "progress": self.progress,
                "stale": age is None or age > REFRESH_SECONDS or changed}

    def start(self, settings: dict) -> bool:
        with self._lock:
            if self.running:
                return False
            self.running = True
        threading.Thread(target=self._run, args=(dict(settings),), name="aurum-screener", daemon=True).start()
        return True

    def _run(self, settings: dict) -> None:
        try:
            self.data = run(settings, self._tick)
            CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            CACHE_FILE.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            log.exception("Ranking de moedas falhou")
            self.data = {**self.data, "error": str(exc)}
        finally:
            self.running = False

    def _tick(self, done: int, total: int) -> None:
        self.progress = {"done": done, "total": total}


def run(settings: dict, on_progress=None) -> dict:
    settings = {**settings, "crypto_spot_only": "1"}  # o ranking é para a Spot: só regras de compra
    try:
        capital_brl = float(settings.get("capital") or 0)
    except ValueError:
        capital_brl = 0.0
    coins = universe()
    events = news.load()
    jobs = [(c, tf) for c in coins for tf in SCAN_TIMEFRAMES]
    views: dict[str, list[dict]] = {c["symbol"]: [] for c in coins}
    done = 0

    def one(job):
        coin, tf = job
        try:
            return coin["symbol"], timeframe_view(analyze_symbol(coin["symbol"], tf, settings, events, force=False))
        except Exception as exc:
            log.info("Ranking: %s %s falhou: %s", coin["symbol"], tf, exc)
            return coin["symbol"], None

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for symbol, view in pool.map(one, jobs):
            done += 1
            if view:
                views[symbol].append(view)
            if on_progress:
                on_progress(done, len(jobs))

    items = sorted((rate(c, views[c["symbol"]], capital_brl) for c in coins), key=lambda x: -x["score"])
    return {"items": items, "capital_brl": capital_brl, "brl_per_usd": usd_brl(), "error": None,
            "strategy_mode": settings.get("strategy_mode", "zonas"),
            "finished_at": datetime.now(timezone.utc).astimezone(LOCAL_TZ).isoformat(timespec="seconds"),
            "timeframes": list(SCAN_TIMEFRAMES)}


screener = Screener()
