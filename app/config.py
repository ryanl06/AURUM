"""Configurações centrais do AURUM."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = Path(os.environ.get("AURUM_DATA_DIR", BASE_DIR / "data"))
DB_PATH = DATA_DIR / "aurum.db"

LOCAL_TZ = ZoneInfo(os.environ.get("AURUM_TZ", "America/Sao_Paulo"))

HOST = os.environ.get("AURUM_HOST", "127.0.0.1")
PORT = int(os.environ.get("AURUM_PORT", "8765"))

# Aviso obrigatório exibido em todas as telas.
DISCLAIMER = "ISTO NÃO PREVÊ RESULTADOS. USE STOP LOSS. NÃO APOSTE O QUE NÃO PODE PERDER."


@dataclass(frozen=True)
class Timeframe:
    key: str
    interval: str  # intervalo aceito pelo yfinance
    period: str  # histórico baixado (suficiente para indicadores e backtest)
    seconds: int
    cache_ttl: int  # segundos que o dado fica em cache
    label: str
    binance_bars: int  # candles baixados da Binance (cripto em tempo real)
    higher: str | None  # tempo gráfico maior usado para confirmar a tendência


TIMEFRAMES: dict[str, Timeframe] = {
    # Históricos no limite do Yahoo (1m: 7d, 5m/15m: 60d, 1h: 730d) para estatísticas mais confiáveis.
    "1m": Timeframe("1m", "1m", "7d", 60, 8, "1 minuto", 3000, "15m"),
    "5m": Timeframe("5m", "5m", "60d", 300, 15, "5 minutos", 5000, "1h"),
    "15m": Timeframe("15m", "15m", "60d", 900, 25, "15 minutos", 5000, "1h"),
    "1h": Timeframe("1h", "60m", "730d", 3600, 60, "1 hora", 8000, "1d"),
    "1d": Timeframe("1d", "1d", "5y", 86400, 300, "1 dia", 1500, None),
}
DEFAULT_TIMEFRAME = "15m"

# Quantos candles vão para o gráfico (o backtest usa o histórico inteiro).
CHART_CANDLES = 400

# Regras de risco padrão (podem ser alteradas nas configurações).
DEFAULT_SETTINGS: dict[str, str] = {
    "scan_interval_min": "5",
    "scan_timeframe": "15m",  # entradas no M15, guiadas pelas zonas do mensal/semanal/diário
    "strategy_mode": "pullback",  # pullback = rompimento M/S/D + correção + novo rompimento · zonas · indicadores · ambos
    "big_candle_points": "1500",  # ouro: vela maior que isso (1.500 pontos = US$ 15) não vale como entrada
    "news_guard_minutes": "30",  # bloqueio antes e depois de notícia de alto impacto
    "max_stop_pct": "3",  # stop loss obrigatório: nunca mais que 3% do preço de entrada
    "atr_stop_mult": "auto",  # auto = 2,0 no intraday e 1,5 no diário (validado no histórico)
    "reward_ratio": "2",
    "cost_pct": "auto",  # custo ida+volta (spread/corretagem); auto = por tipo de ativo
    "htf_filter": "1",  # exige o tempo gráfico maior a favor nas regras de tendência
    "session_filter": "1",  # forex/ouro intraday: só sessões de Londres e Nova York
    "quality_gate": "1",  # bloqueia regras que perderam dinheiro no histórico do ativo
    "crypto_spot_only": "1",  # cripto na Spot: só sinais de COMPRA (vender exige ter a moeda na carteira)
    "use_mt5": "1",  # preços, histórico, especificações e zonas desenhadas vêm do MetaTrader 5 aberto no PC
    "mt5_symbols": "",  # mapeamento manual se o AURUM não achar o nome na corretora (ex.: XAUUSD=XAUUSD.pro)
    "news_guard": "1",  # não manda ENTRAR de 30 min antes a 30 min depois de notícia de alto impacto
    "risk_guard": "1",  # pausa as entradas quando algum limite diário é atingido
    "max_trades_day": "3",
    "daily_max_loss_r": "2",  # perda máxima do dia em múltiplos do risco (2R = dois stops cheios)
    "max_consecutive_losses": "2",
    "capital": "10000",
    "risk_per_trade_pct": "1",
    "price_move_alert_pct": "2",
    "telegram_enabled": "0",
    "telegram_token": "",
    "telegram_chat_id": "",
    "telegram_only_signals": "0",  # 1 = só ENTRAR AGORA e SAIR AGORA
    "scale_level": "0",  # plano de escalada: 0 = treino sem dinheiro, 1 = R$ 50, 2 = R$ 100…
    "scale_since": "",  # quando o nível atual começou (ISO); as regras de subir/descer contam a partir daqui
}

DEFAULT_FAVORITES = ["EURUSD=X", "GBPUSD=X", "USDJPY=X", "GC=F", "USDBRL=X", "BTC-USD", "PETR4.SA"]

# Custo típico ida+volta em % do preço (spread + corretagem), usado no backtest quando cost_pct = auto.
DEFAULT_COST_PCT = {"forex": 0.012, "futures": 0.03, "crypto": 0.2, "b3": 0.06, "b3fut": 0.015, "us": 0.03, "index": 0.05}

FOREX_MAJORS = ["EURUSD=X", "GBPUSD=X", "USDJPY=X", "AUDUSD=X", "USDCAD=X", "USDCHF=X", "NZDUSD=X", "GC=F"]
