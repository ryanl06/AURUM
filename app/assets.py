"""Resolução de ativos (o que o usuário digita -> símbolo do Yahoo) e horário de mercado."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from .b3 import resolve_future
from .config import LOCAL_TZ

NY = ZoneInfo("America/New_York")
SP = ZoneInfo("America/Sao_Paulo")


@dataclass(frozen=True)
class Asset:
    symbol: str
    name: str
    kind: str  # b3 | crypto | forex | futures | index | us
    currency: str


CATALOG: list[Asset] = [
    Asset("WDOFUT", "Mini dólar (WDO) · B3", "b3fut", "BRL"),
    Asset("WINFUT", "Mini índice (WIN) · B3", "b3fut", "BRL"),
    Asset("PETR4.SA", "Petrobras PN", "b3", "BRL"),
    Asset("VALE3.SA", "Vale ON", "b3", "BRL"),
    Asset("ITUB4.SA", "Itaú Unibanco PN", "b3", "BRL"),
    Asset("BBDC4.SA", "Bradesco PN", "b3", "BRL"),
    Asset("BBAS3.SA", "Banco do Brasil ON", "b3", "BRL"),
    Asset("WEGE3.SA", "WEG ON", "b3", "BRL"),
    Asset("ABEV3.SA", "Ambev ON", "b3", "BRL"),
    Asset("MGLU3.SA", "Magazine Luiza ON", "b3", "BRL"),
    Asset("^BVSP", "Ibovespa", "index", "BRL"),
    Asset("BTC-USD", "Bitcoin", "crypto", "USD"),
    Asset("ETH-USD", "Ethereum", "crypto", "USD"),
    Asset("SOL-USD", "Solana", "crypto", "USD"),
    Asset("XRP-USD", "XRP", "crypto", "USD"),
    Asset("BNB-USD", "BNB", "crypto", "USD"),
    Asset("DOGE-USD", "Dogecoin", "crypto", "USD"),
    Asset("EURUSD=X", "Euro / Dólar", "forex", "USD"),
    Asset("GBPUSD=X", "Libra / Dólar", "forex", "USD"),
    Asset("USDJPY=X", "Dólar / Iene", "forex", "JPY"),
    Asset("AUDUSD=X", "Dólar australiano / Dólar", "forex", "USD"),
    Asset("USDCAD=X", "Dólar / Dólar canadense", "forex", "CAD"),
    Asset("USDCHF=X", "Dólar / Franco suíço", "forex", "CHF"),
    Asset("NZDUSD=X", "Dólar neozelandês / Dólar", "forex", "USD"),
    Asset("EURJPY=X", "Euro / Iene", "forex", "JPY"),
    Asset("GBPJPY=X", "Libra / Iene", "forex", "JPY"),
    Asset("EURGBP=X", "Euro / Libra", "forex", "GBP"),
    Asset("AUDJPY=X", "Dólar australiano / Iene", "forex", "JPY"),
    Asset("USDBRL=X", "Dólar / Real", "forex", "BRL"),
    Asset("EURBRL=X", "Euro / Real", "forex", "BRL"),
    Asset("GC=F", "Ouro (XAU/USD · futuro COMEX)", "futures", "USD"),
    Asset("SI=F", "Prata (XAG/USD · futuro)", "futures", "USD"),
    Asset("CL=F", "Petróleo WTI (futuro)", "futures", "USD"),
    Asset("AAPL", "Apple", "us", "USD"),
    Asset("NVDA", "NVIDIA", "us", "USD"),
    Asset("TSLA", "Tesla", "us", "USD"),
    Asset("^GSPC", "S&P 500", "index", "USD"),
]
_BY_SYMBOL = {a.symbol: a for a in CATALOG}

# Apelidos em português e formatos de corretora que o Yahoo não entende.
ALIASES = {
    "XAUUSD": "GC=F", "XAU": "GC=F", "OURO": "GC=F", "GOLD": "GC=F",
    "XAGUSD": "SI=F", "PRATA": "SI=F", "SILVER": "SI=F",
    "WTI": "CL=F", "PETROLEO": "CL=F", "OIL": "CL=F",
    "BITCOIN": "BTC-USD", "ETHEREUM": "ETH-USD",
    "DOLAR": "USDBRL=X", "DÓLAR": "USDBRL=X", "USDBRL": "USDBRL=X",
    "EURO": "EURBRL=X", "IBOV": "^BVSP", "IBOVESPA": "^BVSP", "SP500": "^GSPC",
}
CRYPTO_BASES = {"BTC", "ETH", "SOL", "XRP", "BNB", "DOGE", "ADA", "AVAX", "LINK", "DOT", "LTC", "TRX", "SHIB", "PEPE"}
FIAT = {"USD", "EUR", "GBP", "JPY", "BRL", "AUD", "CAD", "CHF", "NZD", "MXN", "CNY"}


def resolve_symbol(text: str) -> str:
    """Converte o que o usuário digitou em um símbolo aceito pelo Yahoo Finance."""
    raw = text.strip().upper().replace(" ", "")
    if not raw:
        raise ValueError("Digite um ativo, por exemplo PETR4, BTC ou EURUSD.")
    raw = raw.replace("/", "")
    future = resolve_future(raw)
    if future:
        return future
    if raw in ALIASES:
        return ALIASES[raw]
    if raw in _BY_SYMBOL or any(ch in raw for ch in ".=^-"):
        return raw
    if raw in CRYPTO_BASES:
        return f"{raw}-USD"
    if raw.endswith("USDT") and len(raw) > 4 and raw[:-4].isalnum():  # par da Binance: PEPEUSDT
        return f"{raw[:-4]}-USD"
    if len(raw) == 6 and raw[:3] in FIAT and raw[3:] in FIAT:
        return f"{raw}=X"
    if re.fullmatch(r"[A-Z]{4}\d{1,2}", raw):  # padrão de ticker da B3: PETR4, TAEE11
        return f"{raw}.SA"
    return raw


def classify(symbol: str) -> str:
    if symbol in _BY_SYMBOL:
        return _BY_SYMBOL[symbol].kind
    if symbol.endswith(".SA"):
        return "b3"
    if symbol.endswith("=X"):
        return "forex"
    if symbol.endswith("=F"):
        return "futures"
    if symbol.startswith("^"):
        return "index"
    base, _, quote = symbol.partition("-")
    if quote in FIAT or quote == "USDT" or base in CRYPTO_BASES:
        return "crypto"
    return "us"


def asset_info(symbol: str, name: str | None = None) -> dict:
    known = _BY_SYMBOL.get(symbol)
    kind = classify(symbol)
    if known:
        return asdict(known)
    currency = {"b3": "BRL", "forex": symbol[3:6] if len(symbol) >= 6 else "USD"}.get(kind, "USD")
    return asdict(Asset(symbol, name or symbol, kind, currency))


def search_catalog(query: str, limit: int = 8) -> list[dict]:
    q = query.strip().upper()
    if not q:
        return [asdict(a) for a in CATALOG[:limit]]
    hits = [a for a in CATALOG if q in a.symbol.upper() or q in a.name.upper()]
    alias_target = ALIASES.get(q)
    if alias_target and alias_target in _BY_SYMBOL and _BY_SYMBOL[alias_target] not in hits:
        hits.insert(0, _BY_SYMBOL[alias_target])
    return [asdict(a) for a in hits[:limit]]


# ---------------------------------------------------------------- horário de mercado

KIND_LABEL = {
    "b3fut": "B3 · Mini contratos futuros (09h–18h30)",
    "b3": "B3 · Bolsa brasileira",
    "crypto": "Cripto · 24h por dia, 7 dias",
    "forex": "Forex · 24h de domingo à noite até sexta",
    "futures": "Futuros · quase 24h, com pausa diária",
    "index": "Índice",
    "us": "Bolsa americana",
}


def _is_open(kind: str, symbol: str, now_utc: datetime) -> bool:
    if kind == "crypto":
        return True
    if kind == "b3fut":  # WDO/WIN: pregão aproximado das 09h às 18h30
        t = now_utc.astimezone(SP)
        return t.weekday() < 5 and time(9, 0) <= t.time() < time(18, 30)
    if kind == "b3" or symbol == "^BVSP":
        t = now_utc.astimezone(SP)
        return t.weekday() < 5 and time(10, 0) <= t.time() < time(18, 0)
    if kind in ("forex", "futures"):
        t = now_utc.astimezone(NY)
        wd, hm = t.weekday(), t.time()
        if wd == 5 or (wd == 6 and hm < time(17 if kind == "forex" else 18, 0)) or (wd == 4 and hm >= time(17, 0)):
            return False
        if kind == "futures" and time(17, 0) <= hm < time(18, 0):
            return False
        return True
    t = now_utc.astimezone(NY)
    return t.weekday() < 5 and time(9, 30) <= t.time() < time(16, 0)


def _find_transition(kind: str, symbol: str, now_utc: datetime, target_open: bool) -> datetime | None:
    probe = now_utc.replace(second=0, microsecond=0)
    probe -= timedelta(minutes=probe.minute % 5)
    for _ in range(12 * 24 * 8):  # até 8 dias à frente, em passos de 5 min
        probe += timedelta(minutes=5)
        if _is_open(kind, symbol, probe) == target_open:
            return probe
    return None


def market_status(symbol: str, now_utc: datetime | None = None) -> dict:
    now_utc = now_utc or datetime.now(tz=ZoneInfo("UTC"))
    kind = classify(symbol)
    is_open = _is_open(kind, symbol, now_utc)
    status = {
        "kind": kind,
        "label": KIND_LABEL.get(kind, kind),
        "open": is_open,
        "next_change": None,
        "next_change_label": None,
        "sessions": forex_sessions(now_utc) if kind in ("forex", "futures") else [],
        "delayed": kind in ("b3", "index"),
        "note": "Feriados não são considerados." if kind != "crypto" else "",
    }
    if kind != "crypto":
        change = _find_transition(kind, symbol, now_utc, not is_open)
        if change:
            local = change.astimezone(LOCAL_TZ)
            status["next_change"] = local.isoformat()
            verb = "Fecha" if is_open else "Abre"
            status["next_change_label"] = f"{verb} {_weekday_pt(local)} às {local:%H:%M}"
    return status


_SESSIONS = [
    ("Sydney", "Australia/Sydney", time(7), time(16)),
    ("Tóquio", "Asia/Tokyo", time(9), time(18)),
    ("Londres", "Europe/London", time(8), time(17)),
    ("Nova York", "America/New_York", time(8), time(17)),
]


def forex_sessions(now_utc: datetime) -> list[dict]:
    out = []
    for name, tz, start, end in _SESSIONS:
        t = now_utc.astimezone(ZoneInfo(tz))
        active = t.weekday() < 5 and start <= t.time() < end
        open_local = datetime.combine(t.date(), start, ZoneInfo(tz)).astimezone(LOCAL_TZ)
        close_local = datetime.combine(t.date(), end, ZoneInfo(tz)).astimezone(LOCAL_TZ)
        out.append({"name": name, "active": active, "hours": f"{open_local:%H:%M}–{close_local:%H:%M}"})
    return out


def _weekday_pt(dt: datetime) -> str:
    today = datetime.now(tz=dt.tzinfo).date()
    if dt.date() == today:
        return "hoje"
    if dt.date() == today + timedelta(days=1):
        return "amanhã"
    return ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"][dt.weekday()]
