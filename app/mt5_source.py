"""Cotações, especificações de ativo e fuso do servidor lidos do MetaTrader 5 aberto no computador.

Somente leitura: o AURUM nunca envia ordens (não existe `order_send` neste projeto). A conexão usa o
terminal já logado pelo próprio usuário — nenhuma senha passa pelo AURUM.

Regra de ouro: nada aqui pode travar o painel. `mt5.initialize()` pode ficar preso por muitos
segundos quando o terminal está aberto com um diálogo na tela ou ainda sem login ("IPC timeout"),
e durante a espera a biblioteca nativa segura o processo Python inteiro (GIL). Por isso:
1. só tentamos quando existe um terminal rodando;
2. a tentativa é testada antes num processo separado, com tempo limite;
3. só depois de o teste passar conectamos neste processo (aí a chamada é instantânea);
4. tudo roda numa thread de fundo, com intervalos crescentes; uma leitura só espera por uma tentativa de
   conexão que já esteja em andamento, com tempo limite.

Precisão:
- o nome do ativo é procurado com os sufixos da corretora (XAUUSD, XAUUSD.m, XAUUSDm, GOLD…) ou vem de um
  mapeamento manual nas configurações;
- os candles do MT5 vêm na hora do SERVIDOR. O fuso é medido pela cotação mais recente de todos os ativos,
  guardado por servidor em data/mt5_fuso.json e, sem medição possível (mercado fechado), estimado pela
  convenção da corretora — marcado como estimado no status;
- corretoras de forex no "fechamento de Nova York" (ex.: Hantec: UTC+3 no verão americano, UTC+2 no inverno)
  mudam o fuso ao longo do ano. Cada candle do histórico é convertido com o fuso da SUA época — sem isso os
  candles de inverno ficariam 1 hora deslocados (filtro de sessão, horários e backtest errados);
- logo depois de o AURUM ligar, quem pede candles espera a conexão em andamento (alguns segundos) em vez de
  cair numa fonte pública com outro preço (ex.: GC=F futuro no lugar do XAUUSD da corretora).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from . import b3
from .config import DATA_DIR

log = logging.getLogger(__name__)

try:  # pacote oficial da MetaQuotes (Windows)
    import MetaTrader5 as mt5  # type: ignore
except ImportError:  # pragma: no cover - depende da máquina
    mt5 = None

INIT_TIMEOUT_MS = 3000
LOCK_WAIT = 1.0  # conexão e checagem de vida desistem rápido se houver leitura em andamento
READ_LOCK_WAIT = 5.0  # leituras levam milissegundos: esperar a vez é melhor que cair em outra fonte
BACKOFF = (15, 30, 60, 120, 300)
ALIVE_EVERY = 60  # conectado: confere a cada minuto se o terminal continua respondendo
TERMINAL_NAMES = ("terminal64.exe", "terminal.exe")
OFFSET_FILE = DATA_DIR / "mt5_fuso.json"
OFFSET_REFRESH = 600  # re-mede o fuso a cada 10 min (horário de verão muda o fuso da corretora)
SYMBOL_LIST_TTL = 600
COMMON_FILES = Path(os.environ.get("AURUM_MT5_COMMON") or Path(os.environ.get("APPDATA", "")) / "MetaQuotes" / "Terminal"
                    / "Common" / "Files")

TIMEFRAME_NAMES = {"1m": "TIMEFRAME_M1", "5m": "TIMEFRAME_M5", "15m": "TIMEFRAME_M15", "1h": "TIMEFRAME_H1",
                   "1d": "TIMEFRAME_D1", "1w": "TIMEFRAME_W1", "1mo": "TIMEFRAME_MN1"}
# Histórico pedido ao MT5 (bem mais que as fontes públicas). No M15, 80 mil candles = ~3 anos e meio de XAUUSD
# na Hantec: amostra 4x maior para o backtest do método. O terminal limita ao "Máx. barras no gráfico".
HISTORY_BARS = {"1m": 3000, "5m": 20000, "15m": 80000, "1h": 22000, "1d": 3000, "1w": 600, "1mo": 240}

_lock = threading.RLock()
_wake = threading.Event()
_force = threading.Event()
_attempt_done = threading.Condition()
_worker: threading.Thread | None = None
_state = {"connected": False, "connecting": False, "error": None, "last_try": 0.0, "failures": 0,
          "offset": None, "offset_source": None, "offset_at": 0.0, "terminal": None, "terminal_running": None,
          "checked_at": 0.0, "attempts": 0, "max_bars": None}
_symbol_cache: dict[str, str | None] = {}
_symbol_list: dict = {"names": [], "at": 0.0}
_overrides: dict[str, str] = {}

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
    # last_try: sem isto a thread de fundo giraria sem pausa com o MT5 fechado.
    # connecting já na largada: quem espera a conexão (wait_ready) sabe que há uma tentativa em andamento.
    _state.update(last_try=time.time(), connecting=True)
    try:
        return _connect_attempt()
    finally:
        with _attempt_done:
            _state["connecting"] = False
            _state["attempts"] += 1
            _attempt_done.notify_all()


def _connect_attempt() -> bool:
    if not terminal_running():
        _state.update(connected=False, error=FRIENDLY_ERRORS[-10003])
        return False
    ok, code, message = probe()
    if not ok:
        _state.update(connected=False, failures=_state["failures"] + 1,
                      error=FRIENDLY_ERRORS.get(code, f"Não foi possível conectar ao MetaTrader 5 ({code}: {message})."))
        return False
    if not _lock.acquire(timeout=LOCK_WAIT):
        return _state["connected"]
    try:
        ok = mt5.initialize(timeout=INIT_TIMEOUT_MS)
        if not ok:
            code, message = mt5.last_error()
            _state.update(connected=False, failures=_state["failures"] + 1,
                          error=FRIENDLY_ERRORS.get(code, f"Não foi possível conectar ao MetaTrader 5 ({code}: {message})."))
            return False
        info, account = mt5.terminal_info(), mt5.account_info()
        _state.update(connected=True, error=None, failures=0, offset=None, offset_source=None, offset_at=0.0,
                      max_bars=int(getattr(info, "maxbars", 0) or 0) or None, terminal={
            "name": getattr(info, "name", "MetaTrader 5"),
            "company": getattr(account, "company", "") or getattr(info, "company", ""),
            "server": getattr(account, "server", ""),
            "currency": getattr(account, "currency", ""),
            "logged_in": account is not None,
            # conta demo ou real: o painel lembra de treinar na demo antes de arriscar dinheiro
            "demo": getattr(account, "trade_mode", None) == 0 if account is not None else None,
            "leverage": int(getattr(account, "leverage", 0) or 0) or None,
            "data_path": getattr(info, "data_path", "") or "",
        })
        _symbol_cache.clear()
        _symbol_list.update(names=[], at=0.0)
        _refresh_offset()
        return True
    except Exception as exc:  # a biblioteca nativa pode lançar erros inesperados
        _state.update(connected=False, failures=_state["failures"] + 1, error=f"Erro no MetaTrader 5: {exc}")
        return False
    finally:
        _lock.release()


TERMINAL_POLL = 10  # MT5 fechado: confere a cada 10 s se foi aberto (consulta barata à lista de processos)


def _next_check_delay() -> float:
    if _state["connected"]:
        return ALIVE_EVERY
    if _state["terminal_running"] is False:
        return TERMINAL_POLL  # conecta logo que o usuário abrir o MT5, sem girar a CPU enquanto isso
    return BACKOFF[min(max(_state["failures"] - 1, 0), len(BACKOFF) - 1)]


def _worker_loop() -> None:
    next_check = 0.0
    while True:
        _wake.wait(timeout=max(0.0, next_check - time.time()))
        _wake.clear()
        if not _force.is_set() and time.time() < next_check:
            continue  # pedido comum durante o intervalo de espera: respeita o intervalo (sem martelar o terminal)
        _force.clear()
        if _state["connected"]:
            if _alive():
                next_check = time.time() + ALIVE_EVERY
                continue
            _state.update(connected=False, error="A conexão com o MetaTrader 5 caiu. Tentando de novo…")
        connect_now()
        next_check = time.time() + _next_check_delay()


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
    """Pede uma tentativa de conexão em segundo plano. Nunca bloqueia.

    Sem `force`, só acorda a thread se ela não estiver no intervalo de espera entre tentativas."""
    global _worker
    if mt5 is None:
        return
    if force:
        _state.update(failures=0, terminal_running=None)
        _force.set()
    if _worker is None or not _worker.is_alive():
        _worker = threading.Thread(target=_worker_loop, name="aurum-mt5", daemon=True)
        _worker.start()
    if force or not _state["connected"]:
        _wake.set()


def wait_ready(timeout: float) -> bool:
    """Conectado? Se uma tentativa está em andamento (ex.: o AURUM acabou de ligar), espera por ela até `timeout`.

    Não espera à toa: com o MT5 fechado ou no intervalo entre tentativas que falharam, responde na hora."""
    if mt5 is None:
        return False
    if _state["connected"]:
        return True
    request_connect()
    deadline = time.monotonic() + timeout
    with _attempt_done:
        while not _state["connected"]:
            pending = _state["connecting"] or _state["attempts"] == 0  # nenhuma tentativa terminou ainda
            remaining = deadline - time.monotonic()
            if not pending or remaining <= 0:
                return False
            _attempt_done.wait(min(remaining, 0.5))
    return True


def connected() -> bool:
    return mt5 is not None and _state["connected"]


def expected() -> bool:
    """O MT5 deveria estar respondendo (terminal aberto)? Nesse caso não vale trocar a corretora por outra fonte."""
    return mt5 is not None and bool(_state["terminal_running"])


def _lost(error: str) -> None:
    """Leitura falhou: marca desconectado e tenta reconectar já (se falhar de novo, vale o intervalo normal)."""
    _state.update(connected=False, error=error)
    _force.set()
    request_connect()


def status(enabled: bool = False) -> dict:
    """Situação da conexão. Só dispara tentativas quando o uso do MT5 está ligado nas configurações."""
    if mt5 is not None and enabled:
        request_connect()
    offset = _state["offset"]
    return {"installed": installed(), "connected": _state["connected"], "connecting": _state["connecting"],
            "error": _state["error"], "terminal": _state["terminal"],
            "terminal_running": bool(_state["terminal_running"]) if mt5 is not None else False,
            "server_offset_hours": None if offset is None else offset / 3600, "offset_source": _state["offset_source"],
            "dst_convention": _follows_ny_dst() if offset is not None else None,
            "indicator_installed": indicator_installed(),
            "symbols": {k: v for k, v in _symbol_cache.items() if v}}


INDICATOR = "AURUM_Zonas"


def indicator_installed() -> bool | None:
    """O indicador AURUM_Zonas já está (compilado) na pasta MQL5/Indicators deste terminal? None = sem terminal."""
    data_path = (_state["terminal"] or {}).get("data_path")
    if not data_path:
        return None
    folder = Path(data_path) / "MQL5" / "Indicators"
    return (folder / f"{INDICATOR}.ex5").exists()


# ---------------------------------------------------------------- nomes dos ativos na corretora

BASES = {
    "GC=F": ["XAUUSD", "GOLD"], "SI=F": ["XAGUSD", "SILVER"], "CL=F": ["USOIL", "WTI", "XTIUSD"],
    "BTC-USD": ["BTCUSD", "BITCOIN"], "ETH-USD": ["ETHUSD", "ETHEREUM"], "^GSPC": ["US500", "SPX500", "SP500"],
    "^NDX": ["US100", "NAS100", "USTEC", "NDX100"], "^DJI": ["US30", "DJ30", "WS30"], "^BVSP": ["IBOV", "IBOV$"],
}


def set_overrides(text: str) -> None:
    """Mapeamento manual das configurações, ex.: "XAUUSD=XAUUSD.pro; EURUSD=EURUSDm"."""
    from .assets import resolve_symbol  # import local: assets não depende deste módulo

    parsed = {}
    for part in (text or "").replace("\n", ";").replace(",", ";").split(";"):
        if "=" not in part:
            continue
        left, _, right = part.strip().rpartition("=")  # rpartition: "EURUSD=X=EURUSDm" também funciona
        if not left or not right.strip():
            continue
        try:
            parsed[resolve_symbol(left)] = right.strip()
        except ValueError:
            continue
    if parsed != _overrides:
        _overrides.clear()
        _overrides.update(parsed)
        _symbol_cache.clear()


def candidates(symbol: str, today: date | None = None) -> list[str]:
    """Nomes que as corretoras costumam usar no MT5 para o mesmo ativo (sem sufixo)."""
    today = today or date.today()
    if symbol in b3.FUTURES:
        root = b3.FUTURES[symbol].root
        code = b3.current_contract(symbol, today)["code"]
        return [f"{root}$N", f"{root}$", f"{root}@N", f"{root}$D", code, f"{root}FUT"]
    if symbol.endswith(".SA"):
        return [symbol[:-3]]
    if symbol.endswith("=X"):
        return [symbol[:-2]]
    if symbol in BASES:
        return BASES[symbol]
    return [symbol.replace("-USD", "USD"), symbol]


def _all_symbols() -> list:
    if time.time() - _symbol_list["at"] > SYMBOL_LIST_TTL or not _symbol_list["names"]:
        found = mt5.symbols_get() or ()
        _symbol_list.update(names=list(found), at=time.time())
    return _symbol_list["names"]


def pick_symbol(bases: list[str], infos: list) -> str | None:
    """Escolhe o nome da corretora: exato, ou base + sufixo curto (XAUUSD.m, XAUUSDm, XAUUSD+).
    Prefere o habilitado para negociação e visível na Observação do Mercado; depois o nome exato e o mais curto."""
    best, best_key = None, None
    for info in infos:
        name = getattr(info, "name", "")
        upper = name.upper()
        for rank, base in enumerate(bases):
            b = base.upper()
            suffix = upper[len(b):] if upper.startswith(b) else None
            if suffix is None:
                continue
            # sufixo aceito: nenhum, 1 letra (XAUUSDm) ou até 4 caracteres começando com símbolo (.m, .pro, _i, +, #)
            if not (suffix == "" or len(suffix) == 1 or (len(suffix) <= 4 and not suffix[0].isalnum())):
                continue
            key = (getattr(info, "trade_mode", 4) == 0, not getattr(info, "visible", True), suffix != "", rank, len(name))
            if best_key is None or key < best_key:
                best, best_key = name, key
    return best


def broker_name(symbol: str) -> str | None:
    """Nome já encontrado na corretora (sem consultar o MT5)."""
    return _symbol_cache.get(symbol)


def symbol_missing(symbol: str) -> bool:
    """A corretora conectada já foi consultada e não tem este ativo (ex.: PETR4 numa corretora de forex)."""
    return _state["connected"] and symbol in _symbol_cache and _symbol_cache[symbol] is None


def find_symbol(symbol: str) -> str | None:
    if symbol in _symbol_cache:
        return _symbol_cache[symbol]
    found = None
    if symbol in _overrides and mt5.symbol_info(_overrides[symbol]) is not None:
        found = _overrides[symbol]
    if found is None:
        found = pick_symbol(candidates(symbol), _all_symbols())
    if found is None:  # lista de ativos indisponível: tenta os nomes exatos
        found = next((name for name in candidates(symbol) if mt5.symbol_info(name) is not None), None)
    if found is not None:
        mt5.symbol_select(found, True)  # coloca na Observação do Mercado para receber cotações
    _symbol_cache[symbol] = found
    return found


# ---------------------------------------------------------------- fuso do servidor

def measure_offset(server_time: float, utc_now: float) -> int | None:
    """Fuso em múltiplos de 30 min, só se a cotação for recente (até 2 min de sobra); senão None."""
    diff = server_time - utc_now
    offset = int(round(diff / 1800.0)) * 1800
    if abs(diff - offset) <= 120 and abs(offset) <= 14 * 3600:
        return offset
    return None


BRAZIL_BROKERS = ("XP", "CLEAR", "RICO", "BTG", "GENIAL", "MODAL", "TORO", "NOVA FUTURA", "AGORA", "ÁGORA", "ORAMA")


def _brazilian(company: str, server: str) -> bool:
    text = f"{company} {server}".upper()
    return any(name in text for name in BRAZIL_BROKERS)


def estimated_offset(company: str, server: str, when: datetime | None = None) -> int:
    """Sem cotação recente: corretoras brasileiras usam o horário de Brasília; as de forex, o "fechamento de
    Nova York" (GMT+2 no inverno americano, GMT+3 no verão)."""
    if _brazilian(company, server):
        return -3 * 3600
    when = when or datetime.now(timezone.utc)
    ny = when.astimezone(ZoneInfo("America/New_York")).utcoffset().total_seconds()
    return int(ny + 7 * 3600)


def _follows_ny_dst(offset: int | None = None, company: str | None = None, server: str | None = None,
                    now: datetime | None = None) -> bool:
    """O servidor segue o "fechamento de Nova York" (fuso muda com o horário de verão americano)?

    Sim quando o fuso medido agora bate com essa convenção. (Confirmado na Hantec: a pausa diária do ouro fica
    sempre às 00h–01h do servidor, no verão e no inverno.)"""
    term = _state["terminal"] or {}
    company = term.get("company", "") if company is None else company
    server = term.get("server", "") if server is None else server
    offset = _state["offset"] if offset is None else offset
    if offset is None or _brazilian(company, server):
        return False
    return offset == estimated_offset(company, server, now)


def server_to_utc(server_times, offset: int, ny_dst: bool) -> pd.DatetimeIndex:
    """Horário do servidor (segundos) → UTC. Com a convenção de Nova York, cada candle usa o fuso da sua época."""
    t = np.asarray(server_times, dtype="int64")
    if not ny_dst or not len(t):
        return pd.DatetimeIndex(pd.to_datetime(t - offset, unit="s", utc=True)).as_unit("ns")
    approx = pd.DatetimeIndex(pd.to_datetime(t - offset, unit="s", utc=True))
    local = approx.tz_convert("America/New_York").tz_localize(None)
    ny_offset = (local - approx.tz_localize(None)).total_seconds().to_numpy().astype("int64")  # -4h ou -5h
    return pd.DatetimeIndex(pd.to_datetime(t - (ny_offset + 7 * 3600), unit="s", utc=True)).as_unit("ns")


def _load_offsets() -> dict:
    try:
        return json.loads(OFFSET_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _refresh_offset(force: bool = False) -> int:
    """Mede o fuso pela cotação mais recente entre todos os ativos; guarda por servidor; estima se não der."""
    now = time.time()
    if not force and _state["offset"] is not None and now - _state["offset_at"] < OFFSET_REFRESH:
        return _state["offset"]
    server = (_state["terminal"] or {}).get("server", "") or "desconhecido"
    newest = 0
    try:
        newest = max((int(getattr(s, "time", 0) or 0) for s in _all_symbols()), default=0)
    except Exception:
        newest = 0
    measured = measure_offset(newest, now) if newest else None
    saved = _load_offsets()
    if measured is not None:
        _state.update(offset=measured, offset_source="medido agora", offset_at=now)
        saved[server] = {"offset": measured, "measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        try:
            OFFSET_FILE.parent.mkdir(parents=True, exist_ok=True)
            OFFSET_FILE.write_text(json.dumps(saved, indent=1), encoding="utf-8")
        except OSError:
            pass
        return measured
    company = (_state["terminal"] or {}).get("company", "")
    if server in saved:
        # Última medição deste servidor, corrigida se o horário de verão americano mudou desde então.
        then = datetime.fromisoformat(saved[server]["measured_at"])
        shift = estimated_offset(company, server) - estimated_offset(company, server, then)
        _state.update(offset=int(saved[server]["offset"]) + shift, offset_at=now,
                      offset_source=f"última medição ({saved[server]['measured_at'][:10]})"
                                    + (" ajustada ao horário de verão" if shift else ""))
    else:
        _state.update(offset=estimated_offset(company, server), offset_at=now,
                      offset_source="estimado pela convenção da corretora (mercado sem cotação agora)")
    return _state["offset"]


# ---------------------------------------------------------------- leituras

def fetch(symbol: str, timeframe: str, bars: int | None = None) -> tuple[pd.DataFrame, str] | None:
    """Candles do MT5 (índice UTC) ou None — sem nunca esperar por conexão."""
    if mt5 is None or timeframe not in TIMEFRAME_NAMES:
        return None
    if not _state["connected"]:
        request_connect()
        return None
    if not _lock.acquire(timeout=READ_LOCK_WAIT):
        return None
    try:
        name = find_symbol(symbol)
        if not name:
            return None
        count = bars or HISTORY_BARS[timeframe]
        if _state["max_bars"]:
            count = min(count, _state["max_bars"] - 1)  # pedir o limite exato do terminal dá "Invalid params"
        code = getattr(mt5, TIMEFRAME_NAMES[timeframe])
        rates = mt5.copy_rates_from_pos(name, code, 0, count)
        if (rates is None or len(rates) == 0) and count > 5000:
            rates = mt5.copy_rates_from_pos(name, code, 0, 5000)  # histórico curto no terminal: pega o que houver
        offset = _refresh_offset()
        ny_dst = _follows_ny_dst(offset)
    except Exception as exc:
        log.info("Leitura do MT5 falhou para %s: %s", symbol, exc)
        _lost(f"Leitura do MetaTrader 5 falhou: {exc}")
        return None
    finally:
        _lock.release()
    if rates is None or len(rates) == 0:
        return None
    frame = pd.DataFrame(rates)
    frame.index = server_to_utc(frame["time"].to_numpy(), offset, ny_dst)
    volume = frame["real_volume"] if "real_volume" in frame and frame["real_volume"].sum() > 0 else frame.get("tick_volume", 0)
    df = pd.DataFrame({"open": frame["open"], "high": frame["high"], "low": frame["low"], "close": frame["close"],
                       "volume": volume}, index=frame.index).astype(float)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    company = (_state["terminal"] or {}).get("company") or "MetaTrader 5"
    return df, f"MetaTrader 5 · {company} ({name}, tempo real)"


def symbol_spec(symbol: str) -> dict | None:
    """Especificação do ativo na corretora e da conta, para calcular lotes, stop e alvo com exatidão."""
    if mt5 is None or not _state["connected"] or not _lock.acquire(timeout=READ_LOCK_WAIT):
        return None
    try:
        name = find_symbol(symbol)
        info = mt5.symbol_info(name) if name else None
        if info is None:
            return None
        tick, account = mt5.symbol_info_tick(name), mt5.account_info()
        tick_time = int(getattr(tick, "time", 0) or 0)
        return {
            "name": name, "description": getattr(info, "description", ""), "digits": int(info.digits),
            "point": float(info.point), "tick_size": float(info.trade_tick_size or info.point),
            "tick_value_loss": float(getattr(info, "trade_tick_value_loss", 0) or info.trade_tick_value),
            "tick_value_profit": float(getattr(info, "trade_tick_value_profit", 0) or info.trade_tick_value),
            "contract_size": float(info.trade_contract_size), "volume_min": float(info.volume_min),
            "volume_max": float(info.volume_max), "volume_step": float(info.volume_step),
            "stops_level": int(getattr(info, "trade_stops_level", 0)), "spread_points": int(getattr(info, "spread", 0)),
            "bid": float(getattr(tick, "bid", 0) or 0), "ask": float(getattr(tick, "ask", 0) or 0),
            "trade_allowed": int(getattr(info, "trade_mode", 4)) != 0,
            "account_currency": getattr(account, "currency", "") if account else "",
            "balance": float(getattr(account, "balance", 0) or 0) if account else 0.0,
            "company": getattr(account, "company", "") if account else "",
            "demo": getattr(account, "trade_mode", None) == 0 if account else None,
            # hora da última cotação em UTC (cotação velha = mercado parado ou terminal sem conexão com a corretora)
            "quote_time": (datetime.fromtimestamp(tick_time - (_state["offset"] or 0), timezone.utc).isoformat()
                           if tick_time else None),
        }
    except Exception as exc:
        log.info("Especificação do MT5 indisponível para %s: %s", symbol, exc)
        return None
    finally:
        _lock.release()
