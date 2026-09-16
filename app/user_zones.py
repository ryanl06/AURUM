"""Zonas desenhadas pelo usuário no MetaTrader 5 (linhas horizontais e retângulos).

O indicador mt5/AURUM_Zonas.mq5, colocado em qualquer gráfico do MT5, grava a cada poucos segundos todos os
objetos desses tipos de todos os gráficos abertos em
    %APPDATA%\\MetaQuotes\\Terminal\\Common\\Files\\AURUM\\zonas.csv
Aqui só lemos o arquivo. Suporte ou resistência não precisa ser informado: depende de onde o preço está.

Cada zona guarda quando o AURUM a viu pela primeira vez (data/zonas_vistas.json). Ela só vale para os
candles que fecharam depois disso — o backtest não ganha vantagem de linhas desenhadas olhando o passado.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from . import mt5_source
from .config import DATA_DIR
from .zones import UserZone

SEEN_FILE = DATA_DIR / "zonas_vistas.json"
STALE_SECONDS = 600  # indicador parado há 10 min: as zonas continuam, mas o status avisa


def zones_file() -> Path:
    return mt5_source.COMMON_FILES / "AURUM" / "zonas.csv"


@dataclass
class DrawnZone:
    symbol: str  # nome do ativo na corretora (ex.: XAUUSD.m)
    kind: str  # linha | retangulo
    name: str
    low: float
    high: float
    text: str


def read(path: Path | None = None, now: float | None = None) -> dict:
    path = path or zones_file()
    info = {"file": str(path), "exists": path.exists(), "active": False, "updated_at": None, "company": None,
            "server": None, "zones": []}
    if not info["exists"]:
        return info
    try:
        lines = path.read_text(encoding="latin-1").splitlines()
    except OSError:
        return info
    mtime = path.stat().st_mtime
    info.update(active=(now or time.time()) - mtime <= STALE_SECONDS,
                updated_at=datetime.fromtimestamp(mtime, timezone.utc).isoformat(timespec="seconds"))
    seen = set()
    for line in lines:
        parts = [p.strip() for p in line.split(",")]
        if not parts or not parts[0]:
            continue
        if parts[0] == "#AURUM-ZONAS":
            info["company"], info["server"] = (parts + ["", ""])[1:3]
            continue
        if len(parts) < 5:
            continue
        try:
            p1, p2 = float(parts[3]), float(parts[4])
        except ValueError:
            continue
        if p1 <= 0 or p2 <= 0:
            continue
        low, high = min(p1, p2), max(p1, p2)
        key = (parts[0].upper(), round(low, 8), round(high, 8))
        if key in seen:  # o mesmo desenho aparece em mais de um gráfico do mesmo ativo
            continue
        seen.add(key)
        info["zones"].append(DrawnZone(parts[0], parts[1], parts[2], low, high, parts[5] if len(parts) > 5 else ""))
    return info


def _first_seen(zones: list[DrawnZone], now: datetime) -> dict[str, str]:
    try:
        stored = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stored = {}
    current = {f"{z.symbol.upper()}|{z.low:.8f}|{z.high:.8f}" for z in zones}
    changed = False
    for key in current - stored.keys():
        stored[key] = now.isoformat(timespec="seconds")
        changed = True
    if changed:
        stored = {k: v for k, v in stored.items() if k in current}  # linha apagada ou movida: esquece a antiga
        try:
            SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
            SEEN_FILE.write_text(json.dumps(stored, indent=1), encoding="utf-8")
        except OSError:
            pass
    return stored


def matches(symbol: str, broker_name: str | None, drawn_symbol: str) -> bool:
    if broker_name:
        return drawn_symbol.upper() == broker_name.upper()
    return mt5_source.pick_symbol(mt5_source.candidates(symbol), [SimpleNamespace(name=drawn_symbol)]) is not None


def for_symbol(symbol: str, broker_name: str | None = None, path: Path | None = None,
               now: datetime | None = None) -> list[UserZone]:
    """Zonas desenhadas para o ativo, com a data em que o AURUM as viu pela primeira vez."""
    info = read(path)
    if not info["zones"]:
        return []
    now = now or datetime.now(timezone.utc)
    seen = _first_seen(info["zones"], now)
    out = []
    for z in info["zones"]:
        if not matches(symbol, broker_name, z.symbol):
            continue
        since = pd.Timestamp(seen.get(f"{z.symbol.upper()}|{z.low:.8f}|{z.high:.8f}", now.isoformat()))
        label = f"Sua zona: {z.text}" if z.text else "Sua zona (MT5)"
        out.append(UserZone(z.low, z.high, since.tz_convert("UTC") if since.tzinfo else since.tz_localize("UTC"), label))
    return out


def summary(path: Path | None = None) -> dict:
    info = read(path)
    by_symbol: dict[str, int] = {}
    for z in info["zones"]:
        by_symbol[z.symbol] = by_symbol.get(z.symbol, 0) + 1
    return {k: v for k, v in info.items() if k != "zones"} | {
        "count": len(info["zones"]), "by_symbol": by_symbol,
        "zones": [{"symbol": z.symbol, "kind": z.kind, "name": z.name, "low": z.low, "high": z.high, "text": z.text}
                  for z in info["zones"][:200]]}
