"""Derivativos da B3 negociados na XP: mini dólar (WDO) e mini índice (WIN).

Especificações de contrato, código do vencimento vigente e arredondamento ao tick.
Feriados não são considerados no cálculo do vencimento (confira o código na plataforma).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta

MONTH_CODES = {1: "F", 2: "G", 3: "H", 4: "J", 5: "K", 6: "M", 7: "N", 8: "Q", 9: "U", 10: "V", 11: "X", 12: "Z"}


@dataclass(frozen=True)
class FutureSpec:
    root: str
    symbol: str  # símbolo interno do AURUM
    name: str
    tick: float  # variação mínima de preço, em pontos
    point_value: float  # R$ por ponto, por contrato
    proxy: str  # ativo do Yahoo usado quando não há MetaTrader 5
    proxy_factor: float  # multiplicador proxy -> pontos do contrato
    proxy_note: str


FUTURES: dict[str, FutureSpec] = {
    "WDOFUT": FutureSpec("WDO", "WDOFUT", "Mini dólar (WDO)", 0.5, 10.0, "USDBRL=X", 1000.0,
                         "sem MetaTrader 5 o preço é o dólar à vista × 1000; o WDO real tem ágio de alguns pontos"),
    "WINFUT": FutureSpec("WIN", "WINFUT", "Mini índice (WIN)", 5.0, 0.20, "^BVSP", 1.0,
                         "sem MetaTrader 5 o preço é o Ibovespa à vista; o WIN real costuma ter ágio"),
}

ALIASES = {
    "WDO": "WDOFUT", "WDOFUT": "WDOFUT", "WDO$": "WDOFUT", "WDO$N": "WDOFUT", "MINIDOLAR": "WDOFUT", "MINIDÓLAR": "WDOFUT",
    "WIN": "WINFUT", "WINFUT": "WINFUT", "WIN$": "WINFUT", "WIN$N": "WINFUT", "MINIINDICE": "WINFUT", "MINIÍNDICE": "WINFUT",
}


def resolve_future(text: str) -> str | None:
    raw = text.strip().upper().replace(" ", "").replace("-", "")
    if raw in ALIASES:
        return ALIASES[raw]
    for spec in FUTURES.values():  # código de vencimento digitado: WDOV26, WINZ26
        if raw.startswith(spec.root) and len(raw) == len(spec.root) + 3 and raw[len(spec.root)] in MONTH_CODES.values():
            return spec.symbol
    return None


def _first_business_day(year: int, month: int) -> date:
    d = date(year, month, 1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _wednesday_nearest_15(year: int, month: int) -> date:
    target = date(year, month, 15)
    offset = (2 - target.weekday() + 7) % 7  # quarta = 2
    after = target + timedelta(days=offset)
    before = after - timedelta(days=7)
    return before if (target - before) < (after - target) else after


def _add_months(year: int, month: int, n: int) -> tuple[int, int]:
    total = year * 12 + (month - 1) + n
    return total // 12, total % 12 + 1


def current_contract(symbol: str, today: date) -> dict:
    """Vencimento mais negociado hoje (regra simplificada, sem feriados)."""
    spec = FUTURES[symbol]
    if spec.root == "WDO":
        # O WDO vence no 1º dia útil do mês; a liquidez migra para o próximo 1 dia antes do vencimento.
        year, month = today.year, today.month
        while (_first_business_day(year, month) - today).days <= 1:
            year, month = _add_months(year, month, 1)
        expiry = _first_business_day(year, month)
    else:
        # O WIN vence na quarta mais próxima do dia 15 dos meses pares; a liquidez migra alguns dias antes.
        year, month = today.year, today.month
        while True:
            if month % 2 == 0:
                expiry = _wednesday_nearest_15(year, month)
                if (expiry - today).days > 2:
                    break
            year, month = _add_months(year, month, 1)
    code = f"{spec.root}{MONTH_CODES[month]}{str(year)[-2:]}"
    return {"code": code, "expiry": expiry.isoformat(), "days_to_expiry": (expiry - today).days}


def round_to_tick(price: float, tick: float, mode: str = "nearest") -> float:
    steps = price / tick
    if mode == "down":
        steps = math.floor(steps + 1e-9)
    elif mode == "up":
        steps = math.ceil(steps - 1e-9)
    else:
        steps = round(steps)
    return round(steps * tick, 6)
