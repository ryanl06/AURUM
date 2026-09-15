"""Leitura de cotações em tempo real do MetaTrader 5 aberto no computador (ex.: MT5 da XP).

Somente leitura: o AURUM nunca envia ordens. A conexão usa o terminal já logado pelo próprio
usuário — nenhuma senha passa pelo AURUM.

Regra de ouro: nada aqui pode travar o painel. `mt5.initialize()` pode ficar preso por muitos
segundos quando o terminal está aberto com um diálogo na tela ou ainda sem login ("IPC timeout"),
e durante a espera a biblioteca nativa segura o processo Python inteiro (GIL). Por isso:
1. só tentamos quando existe um terminal rodando;
2. a tentativa é testada antes num processo separado, com tempo limite;
3. só depois de o teste passar conectamos neste processo (aí a chamada é instantânea);
4. tudo roda numa thread de fundo, com intervalos crescentes; as leituras nunca esperam.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
import time
from datetime import date

import pandas as pd

from . import b3

log = logging.getLogger(__name__)

try:  # pacote oficial da MetaQuotes (opcional)
    import MetaTrader5 as mt5  # type: ignore
except ImportError:  # pragma: no cover - depende da máquina
    mt5 = None

INIT_TIMEOUT_MS = 3000
LOCK_WAIT = 1.0  # leitura desiste rápido se outra chamada ao MT5 estiver em andamento
BACKOFF = (15, 30, 60, 120, 300)
TERMINAL_NAMES = ("terminal64.exe", "terminal.exe")

_lock = threading.RLock()
_wake = threading.Event()
_worker: threading.Thread | None = None
_state = {"connected": False, "connecting": False, "error": None, "last_try": 0.0, "failures": 0,
          "offset": None, "terminal": None, "terminal_running": None, "checked_at": 0.0}
_symbol_cache: dict[str, str | None] = {}

TIMEFRAME_NAMES = {"1m": "TIMEFRAME_M1", "5m": "TIMEFRAME_M5", "15m": "TIMEFRAME_M15", "1h": "TIMEFRAME_H1", "1d": "TIMEFRAME_D1"}

FRIENDLY_ERRORS = {
    -10003: "O MetaTrader 5 não está aberto neste computador. Abra o MT5, faça login e clique em Reconectar.",
    -10005: ("O MetaTrader 5 está aberto, mas não respondeu. Feche qualquer janela de diálogo dele (ex.: “Abrir conta”), "
             "confirme que está logado e clique em Reconectar. Se continuar, feche e abra o MT5 de novo."),
    -6: "O MetaTrader 5 recusou a conexão: confira se a conta está logada.",
}


def installed() -> bool:
    return mt5 is not None


def terminal_running(max_age: float = 10.0) -> bool:
    """Existe algum terminal do MT5 rodando? (consulta barata, com cache de alguns segundos)."""
    now = time.time()
    if _state["terminal_running"] is not None and now - _state["checked_at"] < max_age:
        return _state["terminal_running"]
    running = False
    try:
        out = subprocess.run(["tasklist", "/NH", "/FO", "CSV"], capture_output=True, text=True, timeout=5,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.lower()
        running = any(f'"{name}"' in out for name in TERMINAL_NAMES)
    except (OSError, subprocess.SubprocessError):
        running = False
    _state.update(terminal_running=running, checked_at=now)
    return running


PROBE_CODE = (
    "import MetaTrader5 as m\n"
    "ok = m.initialize(timeout=4000)\n"
    "code, msg = m.last_error()\n"
    "print('OK' if ok else 'ERR', code, msg, sep='|')\n"
    "m.shutdown()\n"
)


def probe() -> tuple[bool, int, str]:
    """Testa a conexão num processo separado: se o MT5 travar, só esse processo espera."""
    try:
        out = subprocess.run([sys.executable, "-c", PROBE_CODE], capture_output=True, text=True, timeout=15,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip().splitlines()
        status, code, message = (out[-1].split("|", 2) + ["", ""])[:3] if out else ("ERR", "-10005", "sem resposta")
        return status == "OK", int(code or 0), message
    except subprocess.TimeoutExpired:
        return False, -10005, "IPC timeout"
    except (OSError, ValueError) as exc:
        return False, -1, str(exc)


def connect_now() -> bool:
    """Tentativa de conexão — só chamada pela thread de fundo (e pelos testes)."""
    if mt5 is None:
        _state.update(connected=False, error="Pacote MetaTrader5 não instalado (pip install MetaTrader5).")
        return False
    if not terminal_running():
        _state.update(connected=False, connecting=False, error=FRIENDLY_ERRORS[-10003])
        return False
    _state.update(connecting=True, last_try=time.time())
    ok, code, message = probe()
    if not ok:
        _state.update(connected=False, connecting=False, failures=_state["failures"] + 1,
                      error=FRIENDLY_ERRORS.get(code, f"Não foi possível conectar ao MetaTrader 5 ({code}: {message})."))
        return False
    if not _lock.acquire(timeout=LOCK_WAIT):
        _state["connecting"] = False
        return _state["connected"]
    try:
        ok = mt5.initialize(timeout=INIT_TIMEOUT_MS)
        if not ok:
            code, message = mt5.last_error()
            _state.update(connected=False, failures=_state["failures"] + 1,
                          error=FRIENDLY_ERRORS.get(code, f"Não foi possível conectar ao MetaTrader 5 ({code}: {message})."))
            return False
        info, account = mt5.terminal_info(), mt5.account_info()
        _state.update(connected=True, error=None, failures=0, offset=None, terminal={
            "name": getattr(info, "name", "MetaTrader 5"),
            "company": getattr(account, "company", "") or getattr(info, "company", ""),
            "server": getattr(account, "server", ""),
            "logged_in": account is not None,
        })
        _symbol_cache.clear()
        return True
    except Exception as exc:  # a biblioteca nativa pode lançar erros inesperados
        _state.update(connected=False, failures=_state["failures"] + 1, error=f"Erro no MetaTrader 5: {exc}")
        return False
    finally:
        _state["connecting"] = False
        _lock.release()


def _worker_loop() -> None:
    while True:
        wait = 0 if _state["last_try"] == 0 else BACKOFF[min(_state["failures"], len(BACKOFF) - 1)]
        if _state["connected"]:
            wait = 60
        _wake.wait(timeout=wait)
        _wake.clear()
        if _state["connected"]:
            if not _alive():
                _state.update(connected=False, error="A conexão com o MetaTrader 5 caiu. Tentando de novo…")
            continue
        connect_now()


def _alive() -> bool:
    if not terminal_running(max_age=0):
        return False
    if not _lock.acquire(timeout=LOCK_WAIT):
        return True  # ocupado com leitura: considera vivo
    try:
        return mt5.terminal_info() is not None
    except Exception:
        return False
    finally:
        _lock.release()


def request_connect(force: bool = False) -> None:
    """Pede uma tentativa de conexão em segundo plano. Nunca bloqueia."""
    global _worker
    if mt5 is None:
        return
    if force:
        _state.update(failures=0, terminal_running=None)
    if _worker is None or not _worker.is_alive():
        _worker = threading.Thread(target=_worker_loop, name="aurum-mt5", daemon=True)
        _worker.start()
    if force or not _state["connected"]:
        _wake.set()


def disconnect() -> None:
    if mt5 is not None and _lock.acquire(timeout=LOCK_WAIT):
        try:
            mt5.shutdown()
        finally:
            _state.update(connected=False, terminal=None)
            _lock.release()


def status(enabled: bool = False) -> dict:
    """Situação da conexão. Só dispara tentativas quando o uso do MT5 está ligado nas configurações."""
    if mt5 is not None and enabled:
        request_connect()
    return {"installed": installed(), "connected": _state["connected"], "connecting": _state["connecting"],
            "error": _state["error"], "terminal": _state["terminal"],
            "terminal_running": bool(_state["terminal_running"]) if mt5 is not None else False}


def candidates(symbol: str, today: date | None = None) -> list[str]:
    """Nomes que as corretoras usam no MT5 para o mesmo ativo."""
    today = today or date.today()
    if symbol in b3.FUTURES:
        root = b3.FUTURES[symbol].root
        code = b3.current_contract(symbol, today)["code"]
        return [f"{root}$N", f"{root}$", f"{root}@N", f"{root}$D", code, f"{root}FUT"]
    if symbol.endswith(".SA"):
        return [symbol[:-3]]
    if symbol.endswith("=X"):
        pair = symbol[:-2]
        return [pair, f"{pair}.a", f"{pair}m"]
    if symbol == "GC=F":
        return ["XAUUSD", "GOLD"]
    return [symbol.replace("-USD", "USD"), symbol]


def find_symbol(symbol: str) -> str | None:
    if symbol in _symbol_cache:
        return _symbol_cache[symbol]
    found = None
    for name in candidates(symbol):
        info = mt5.symbol_info(name)
        if info is not None and mt5.symbol_select(name, True):
            found = name
            break
    _symbol_cache[symbol] = found
    return found


def measure_offset(server_time: float, utc_now: float) -> int:
    """Fuso do servidor em múltiplos de 30 min; só é gravado se o tick for recente (±2 min de sobra)."""
    diff = server_time - utc_now
    offset = int(round(diff / 1800.0)) * 1800
    if abs(diff - offset) <= 120 and abs(offset) <= 14 * 3600:
        _state["offset"] = offset
        return offset
    return 0  # mercado parado: mede de novo na próxima leitura


def _server_offset(name: str) -> int:
    if _state["offset"] is not None:
        return _state["offset"]
    tick = mt5.symbol_info_tick(name)
    if tick is None or not tick.time:
        return 0
    return measure_offset(tick.time, time.time())


def fetch(symbol: str, timeframe: str, bars: int) -> tuple[pd.DataFrame, str] | None:
    """Candles do MT5 (índice UTC) ou None — sem nunca esperar por conexão."""
    if mt5 is None:
        return None
    if not _state["connected"]:
        request_connect()
        return None
    if not _lock.acquire(timeout=LOCK_WAIT):
        return None
    try:
        name = find_symbol(symbol)
        if not name:
            return None
        rates = mt5.copy_rates_from_pos(name, getattr(mt5, TIMEFRAME_NAMES[timeframe]), 0, bars)
        offset = _server_offset(name)
    except Exception as exc:
        log.info("Leitura do MT5 falhou para %s: %s", symbol, exc)
        _state.update(connected=False, error=f"Leitura do MetaTrader 5 falhou: {exc}")
        request_connect()
        return None
    finally:
        _lock.release()
    if rates is None or len(rates) == 0:
        return None
    frame = pd.DataFrame(rates)
    frame.index = pd.DatetimeIndex(pd.to_datetime(frame["time"] - offset, unit="s", utc=True)).as_unit("ns")
    volume = frame["real_volume"] if "real_volume" in frame and frame["real_volume"].sum() > 0 else frame.get("tick_volume", 0)
    df = pd.DataFrame({"open": frame["open"], "high": frame["high"], "low": frame["low"], "close": frame["close"],
                       "volume": volume}, index=frame.index).astype(float)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    company = (_state["terminal"] or {}).get("company") or "MetaTrader 5"
    return df, f"MetaTrader 5 · {company} ({name}, tempo real)"
