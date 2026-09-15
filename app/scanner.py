"""Análise contínua em segundo plano.

Monitora favoritos, ativos abertos no painel e operações abertas. Cada ativo é analisado
logo depois que um candle do seu tempo gráfico fecha (hora exata do sinal) e, no mínimo,
a cada `scan_interval_min` minutos. Também confere o resultado dos sinais já emitidos.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime

from . import database as db
from .config import LOCAL_TZ, TIMEFRAMES
from .service import evaluate_signals, run_analysis

log = logging.getLogger(__name__)

WATCH_TTL = 15 * 60  # ativo aberto no painel continua monitorado por 15 min após a última visita
CANDLE_GRACE = 8  # segundos após o fechamento do candle para o provedor publicar o dado
TICK = 5
STARTUP_DELAY = 20
SIGNAL_CHECK_EVERY = 60


class Scanner:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._watch: dict[tuple[str, str], float] = {}
        self._last: dict[tuple[str, str], float] = {}
        self._errors: dict[tuple[str, str], str] = {}
        self._lock = threading.Lock()
        self._last_signal_check = 0.0
        self.last_run: str | None = None
        self.running = False
        self.last_count = 0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="aurum-scanner", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def trigger(self) -> None:
        with self._lock:
            self._last.clear()
        self._wake.set()

    def touch(self, symbol: str, timeframe: str) -> None:
        with self._lock:
            self._watch[(symbol, timeframe)] = time.time()

    def targets(self) -> list[tuple[str, str]]:
        settings = db.get_settings()
        tf = settings.get("scan_timeframe") if settings.get("scan_timeframe") in TIMEFRAMES else "1h"
        items = {(f["symbol"], tf) for f in db.list_favorites()}
        items.update((p["symbol"], p["timeframe"] if p["timeframe"] in TIMEFRAMES else tf)
                     for p in db.list_positions("open"))
        cutoff = time.time() - WATCH_TTL
        with self._lock:
            self._watch = {k: v for k, v in self._watch.items() if v >= cutoff}
            items.update(self._watch.keys())
        return sorted(items)

    def _interval(self) -> float:
        try:
            return max(1.0, float(db.get_settings().get("scan_interval_min", 5))) * 60
        except ValueError:
            return 300.0

    def _next_due(self, key: tuple[str, str], now: float, interval: float) -> float:
        last = self._last.get(key)
        if last is None:
            return now
        seconds = TIMEFRAMES[key[1]].seconds
        by_interval = last + interval
        if seconds >= 86400:
            return by_interval
        next_close = (last // seconds + 1) * seconds + CANDLE_GRACE
        if next_close - CANDLE_GRACE <= last:
            next_close += seconds
        return min(by_interval, next_close)

    def status(self) -> dict:
        now, interval = time.time(), self._interval()
        targets = self.targets()
        upcoming = [self._next_due(k, now, interval) for k in targets]
        next_run = min(upcoming) if upcoming else None
        return {
            "running": self.running, "last_run": self.last_run,
            "next_run": datetime.fromtimestamp(max(now, next_run), LOCAL_TZ).isoformat(timespec="seconds") if next_run else None,
            "last_count": self.last_count, "errors": [f"{s} {t}: {e}" for (s, t), e in self._errors.items()][-5:],
            "targets": [{"symbol": s, "timeframe": t} for s, t in targets],
            "mode": "no fechamento de cada candle",
        }

    def _loop(self) -> None:
        # Espera um pouco na largada: o painel aberto pelo usuário tem prioridade sobre a varredura completa.
        self._stop.wait(timeout=STARTUP_DELAY)
        while not self._stop.is_set():
            now, interval = time.time(), self._interval()
            due = [k for k in self.targets() if self._next_due(k, now, interval) <= now]
            if due:
                self.running = True
                for key in due:
                    if self._stop.is_set():
                        break
                    try:
                        run_analysis(*key, force=True, record=True, source="scanner")
                        self._errors.pop(key, None)
                    except Exception as exc:
                        self._errors[key] = str(exc)
                        log.warning("Scanner: %s %s falhou: %s", *key, exc)
                    with self._lock:
                        self._last[key] = time.time()
                self.running = False
                self.last_count = len(due)
                self.last_run = datetime.now(LOCAL_TZ).isoformat(timespec="seconds")

            if time.time() - self._last_signal_check >= SIGNAL_CHECK_EVERY:
                self._last_signal_check = time.time()
                try:
                    evaluate_signals()
                except Exception as exc:
                    log.warning("Conferência de sinais falhou: %s", exc)

            self._wake.clear()
            self._wake.wait(timeout=TICK)


scanner = Scanner()
