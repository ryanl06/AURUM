"""Velas do MetaTrader 4 (ex.: Hantec Markets) lidas dos arquivos do robô mt4/AURUM_Exporter.mq4.

O MT4 não tem API para Python. O robô, instalado pelo próprio usuário no MT4, grava as velas de cada ativo
em %APPDATA%\\MetaQuotes\\Terminal\\Common\\Files\\AURUM\\<ATIVO>_<TEMPO>.csv; aqui só lemos esses arquivos.
Nada é enviado ao MT4 e nenhuma ordem é criada.

Formato: 1ª linha "#AURUM,símbolo,tempo,fuso_do_servidor_em_segundos,fuso_confiável,dígitos,corretora";
depois "unix_hora_do_servidor,open,high,low,close,volume" do mais antigo para o mais novo.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

FOLDER = Path(os.environ.get("AURUM_MT4_DIR") or Path(os.environ.get("APPDATA", "")) / "MetaQuotes" / "Terminal"
              / "Common" / "Files" / "AURUM")
TF_FILE = {"1m": "M1", "5m": "M5", "15m": "M15", "1h": "H1", "1d": "D1", "1w": "W1", "1mo": "MN1"}
STALE_SECONDS = 180  # o robô grava a cada poucos segundos: arquivo parado há 3 min = MT4 fechado
ALIASES = {"GC=F": "XAUUSD", "SI=F": "XAGUSD", "BTC-USD": "BTCUSD", "ETH-USD": "ETHUSD"}


def mt4_symbol(symbol: str, suffix: str = "") -> str | None:
    """EURUSD=X -> EURUSD (+ sufixo da corretora, ex.: ".r"). Devolve None para ativos que o MT4 não tem."""
    if symbol in ALIASES:
        base = ALIASES[symbol]
    elif symbol.endswith("=X") and len(symbol) == 8:
        base = symbol[:6]
    else:
        return None
    return base + (suffix or "")


def _path(symbol: str, timeframe: str, suffix: str, folder: Path) -> Path | None:
    name = mt4_symbol(symbol, suffix)
    code = TF_FILE.get(timeframe)
    return folder / f"{name}_{code}.csv" if name and code else None


def read_file(path: Path) -> tuple[pd.DataFrame, dict]:
    with path.open("r", encoding="latin-1") as fh:
        header = fh.readline().strip().split(",")
        if not header or header[0] != "#AURUM" or len(header) < 6:
            raise ValueError(f"cabeçalho inválido em {path.name}")
        frame = pd.read_csv(fh, header=None, names=["time", "open", "high", "low", "close", "volume"])
    offset, offset_ok = int(header[3]), header[4] == "1"
    meta = {"symbol": header[1], "timeframe": header[2], "offset": offset, "offset_ok": offset_ok,
            "digits": int(header[5]), "broker": ",".join(header[6:]).strip() or "MetaTrader 4"}
    frame = frame.dropna()
    frame = frame[frame["close"] > 0]
    # A hora do MT4 é a do servidor da corretora: volta para UTC com o fuso informado pelo robô.
    frame.index = pd.DatetimeIndex(pd.to_datetime(frame.pop("time").astype("int64") - offset, unit="s", utc=True)).as_unit("ns")
    frame = frame[~frame.index.duplicated(keep="last")].sort_index().astype(float)
    return frame, meta


def fetch(symbol: str, timeframe: str, suffix: str = "", folder: Path | None = None,
          now: float | None = None) -> tuple[pd.DataFrame, str] | None:
    path = _path(symbol, timeframe, suffix, folder or FOLDER)
    if path is None or not path.exists():
        return None
    if (now or time.time()) - path.stat().st_mtime > STALE_SECONDS:
        return None  # MT4 fechado ou robô parado: usa outra fonte
    try:
        df, meta = read_file(path)
    except (OSError, ValueError):
        return None
    if df.empty:
        return None
    note = "" if meta["offset_ok"] else " · fuso do servidor não confirmado"
    return df, f"{meta['broker']} · MetaTrader 4 (tempo real){note}"


def status(folder: Path | None = None, now: float | None = None) -> dict:
    folder = folder or FOLDER
    now = now or time.time()
    files = sorted(folder.glob("*_*.csv")) if folder.exists() else []
    newest = max((f.stat().st_mtime for f in files), default=None)
    symbols = sorted({f.stem.rsplit("_", 1)[0] for f in files})
    broker = None
    if files:
        try:
            broker = read_file(files[0])[1]["broker"]
        except (OSError, ValueError):
            pass
    return {
        "folder": str(folder), "exists": folder.exists(), "files": len(files), "symbols": symbols, "broker": broker,
        "last_write": datetime.fromtimestamp(newest, timezone.utc).isoformat() if newest else None,
        "connected": bool(newest and now - newest <= STALE_SECONDS),
    }
