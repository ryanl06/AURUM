"""Agenda econômica (feed público semanal do Forex Factory) e proteção perto de notícias de alto impacto.

Notícias como juros do Fed, Payroll e inflação americana fazem o preço saltar em segundos e
invalidam qualquer leitura técnica. O AURUM avisa e, com o filtro ligado, não manda ENTRAR
entre 30 min antes e 30 min depois de um evento de alto impacto da moeda do ativo.
Eventos do Brasil (Copom, IPCA) não fazem parte deste feed.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone

import httpx

from .b3 import FUTURES
from .config import DATA_DIR, LOCAL_TZ

log = logging.getLogger(__name__)

FEED_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
CACHE_FILE = DATA_DIR / "calendar.json"
REFRESH_SECONDS = 30 * 60
GUARD_BEFORE = timedelta(minutes=30)
GUARD_AFTER = timedelta(minutes=30)

_lock = threading.Lock()
_events: list[dict] = []
_fetched_at = 0.0

TITLE_PT = [
    ("Non-Farm Employment Change", "Payroll (empregos nos EUA)"),
    ("FOMC Press Conference", "Coletiva do Fed"),
    ("Federal Funds Rate", "Juros do Fed (FOMC)"),
    ("FOMC Statement", "Comunicado do Fed"),
    ("FOMC Meeting Minutes", "Ata do Fed"),
    ("Core CPI", "Inflação núcleo (CPI)"),
    ("CPI", "Inflação (CPI)"),
    ("Core PCE Price Index", "Inflação PCE núcleo"),
    ("Unemployment Rate", "Taxa de desemprego"),
    ("Unemployment Claims", "Pedidos de seguro-desemprego"),
    ("Core Retail Sales", "Vendas no varejo núcleo"),
    ("Retail Sales", "Vendas no varejo"),
    ("Core PPI", "Preços ao produtor núcleo (PPI)"),
    ("PPI", "Preços ao produtor (PPI)"),
    ("Flash Manufacturing PMI", "PMI industrial prévio"),
    ("Flash Services PMI", "PMI de serviços prévio"),
    ("GDP", "PIB"),
    ("ISM Manufacturing PMI", "PMI industrial ISM"),
    ("ISM Services PMI", "PMI de serviços ISM"),
    ("Official Bank Rate", "Juros do Banco da Inglaterra"),
    ("Main Refinancing Rate", "Juros do BCE"),
    ("ECB Press Conference", "Coletiva do BCE"),
    ("BOJ Policy Rate", "Juros do Banco do Japão"),
    ("Overnight Rate", "Juros do Banco do Canadá"),
    ("Cash Rate", "Juros do Banco da Austrália"),
    ("Official Cash Rate", "Juros do Banco da Nova Zelândia"),
    ("Employment Change", "Variação do emprego"),
    ("Fed Chair", "Discurso do presidente do Fed"),
]


def translate(title: str) -> str:
    for key, pt in TITLE_PT:
        if key.lower() in title.lower():
            suffix = " m/m" if "m/m" in title else " a/a" if "y/y" in title else " t/t" if "q/q" in title else ""
            return pt + suffix
    return title


def currencies_for(symbol: str, kind: str) -> set[str]:
    if kind == "forex" and symbol.endswith("=X") and len(symbol) >= 8:
        return {symbol[:3], symbol[3:6], "All"}
    if symbol in FUTURES or kind in ("futures", "crypto", "us", "b3fut"):
        return {"USD", "All"}  # WDO, WIN, ouro e cripto reagem forte às notícias americanas
    if kind in ("b3", "index"):
        return {"USD", "All"}
    return {"USD", "All"}


def _parse(raw: list[dict]) -> list[dict]:
    events = []
    for item in raw:
        try:
            when = datetime.fromisoformat(item["date"]).astimezone(timezone.utc)
        except (KeyError, ValueError):
            continue
        events.append({
            "title": item.get("title", ""), "title_pt": translate(item.get("title", "")),
            "country": item.get("country", ""), "impact": item.get("impact", ""),
            "forecast": item.get("forecast") or None, "previous": item.get("previous") or None,
            "time_utc": when,
        })
    return sorted(events, key=lambda e: e["time_utc"])


def load(force: bool = False) -> list[dict]:
    """Eventos da semana; baixa no máximo a cada 30 min e guarda uma cópia para quando estiver offline."""
    global _events, _fetched_at
    with _lock:
        if _fetched_at and not force and time.time() - _fetched_at < REFRESH_SECONDS:
            return _events
        try:
            resp = httpx.get(FEED_URL, timeout=15, headers={"User-Agent": "AURUM/2.0"})
            resp.raise_for_status()
            raw = resp.json()
            CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            CACHE_FILE.write_text(json.dumps(raw), encoding="utf-8")
            _events, _fetched_at = _parse(raw), time.time()
        except Exception as exc:
            log.info("Agenda econômica indisponível (%s); usando cópia local.", exc)
            if not _events and CACHE_FILE.exists():
                try:
                    _events = _parse(json.loads(CACHE_FILE.read_text(encoding="utf-8")))
                except (OSError, ValueError):
                    _events = []
            _fetched_at = time.time() - REFRESH_SECONDS + 300  # tenta de novo em 5 min
        return _events


def set_events(raw: list[dict]) -> None:
    """Usado nos testes para injetar eventos sem internet."""
    global _events, _fetched_at
    _events, _fetched_at = _parse(raw), time.time()


def context(symbol: str, kind: str, now: datetime, events: list[dict] | None = None, hours: int = 48) -> dict:
    events = load() if events is None else events
    currencies = currencies_for(symbol, kind)
    relevant = [e for e in events if e["country"] in currencies and e["impact"] in ("High", "Medium")]
    upcoming, blocking = [], None
    for e in relevant:
        delta = e["time_utc"] - now
        if -GUARD_AFTER <= delta <= timedelta(hours=hours):
            minutes = int(delta.total_seconds() // 60)
            view = {
                "title": e["title_pt"], "original": e["title"], "country": e["country"], "impact": e["impact"],
                "forecast": e["forecast"], "previous": e["previous"], "minutes": minutes,
                "time": e["time_utc"].astimezone(LOCAL_TZ).strftime("%d/%m %H:%M"),
                "iso": e["time_utc"].astimezone(LOCAL_TZ).isoformat(),
            }
            upcoming.append(view)
            if e["impact"] == "High" and -GUARD_AFTER <= delta <= GUARD_BEFORE and blocking is None:
                blocking = view
    return {"upcoming": upcoming[:8], "blocking": blocking, "currencies": sorted(c for c in currencies if c != "All"),
            "source": "Forex Factory (semana atual)", "available": bool(events)}


def describe_block(event: dict) -> str:
    when = f"em {event['minutes']} min" if event["minutes"] >= 0 else f"há {-event['minutes']} min"
    return f"Notícia de alto impacto: {event['title']} ({event['country']}) às {event['time'][-5:]} — {when}."
