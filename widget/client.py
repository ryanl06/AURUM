"""Cliente leve da API do AURUM para o widget flutuante — só biblioteca padrão, abre rápido e não pesa.

O widget só LÊ a mesma análise que o painel mostra (GET /api/analysis, /api/live, /api/alerts). Nada aqui envia
ordens nem altera configurações do AURUM.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import DATA_DIR, PORT

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_URL = os.environ.get("AURUM_URL", f"http://127.0.0.1:{PORT}")
CONFIG_PATH = DATA_DIR / "widget.json"

TIMEFRAMES = ("1m", "5m", "15m", "1h", "1d")
TF_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "1d": 86400}
POLL_SECONDS = {"1m": 10, "5m": 15, "15m": 20, "1h": 30, "1d": 60}  # os mesmos intervalos do painel
CANDLE_GRACE = 4  # segundos depois do fechamento do candle para pedir a análise nova (o MT5 publica na hora)
LIVE_SECONDS = 3  # preço ao vivo (consulta leve)
ALERT_SECONDS = 5  # mudança de sinal detectada pelo AURUM (consulta leve)

# Cores do painel do AURUM
BUY, SELL, EXIT, PREPARE, HOLD, PAUSE, WAIT, CLOSED = ("#16c784", "#f0454f", "#ff7a33", "#f5a524", "#3ea6ff",
                                                       "#a78bfa", "#8a8f98", "#6b7280")
LIVE_ACTIONS = ("ENTRAR_AGORA", "SAIR_AGORA")


class ApiError(RuntimeError):
    def __init__(self, message: str, offline: bool = False):
        super().__init__(message)
        self.offline = offline


class AurumApi:
    """GETs na API local. Sem proxy: o AURUM só atende em localhost."""

    def __init__(self, base_url: str = DEFAULT_URL):
        self.base_url = base_url.rstrip("/")
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _get(self, path: str, timeout: float, **params):
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        request = urllib.request.Request(f"{self.base_url}{path}{'?' + query if query else ''}",
                                         headers={"Accept": "application/json", "User-Agent": "AURUM-widget"})
        try:
            with self._opener.open(request, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8")).get("detail")
            except (ValueError, AttributeError, OSError):
                detail = None
            raise ApiError(detail or f"O AURUM respondeu com erro {exc.code}.") from exc
        except (OSError, ValueError) as exc:  # URLError, recusa de conexão, tempo esgotado, resposta inválida
            raise ApiError("AURUM desligado ou sem resposta.", offline=True) from exc

    def health(self) -> dict:
        return self._get("/api/health", 3)

    def analysis(self, symbol: str, timeframe: str) -> dict:
        # A primeira análise depois de ligar pode levar ~15 s (histórico longo do MT5).
        return self._get("/api/analysis", 90, symbol=symbol, tf=timeframe)

    def live(self, symbol: str, timeframe: str, bar_start: int | None = None) -> dict:
        return self._get("/api/live", 10, symbol=symbol, tf=timeframe, bar_start=bar_start)

    def alerts(self, since_id: int = 0) -> list[dict]:
        return self._get("/api/alerts", 5, since_id=since_id, limit=50)

    def favorites(self) -> list[dict]:
        return self._get("/api/favorites", 5)


def start_server() -> None:
    """Liga o AURUM em segundo plano (o run.py nunca sobe duas cópias)."""
    exe = Path(sys.executable)
    windowless = exe.with_name("pythonw.exe")
    python = windowless if os.name == "nt" and windowless.exists() else exe
    subprocess.Popen([str(python), str(ROOT / "run.py"), "--background", "--no-browser"], cwd=ROOT,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), close_fds=True)


def panel_url(base_url: str, symbol: str, timeframe: str) -> str:
    host = base_url.replace("127.0.0.1", "localhost")
    return f"{host}/#/{urllib.parse.quote(symbol, safe='')}/{timeframe}"


# ---------------------------------------------------------------- formatação (padrão brasileiro)

def fmt_num(value, decimals: int = 2) -> str:
    if value is None:
        return "—"
    return f"{float(value):,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def parse_time(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def next_close(close_at: datetime | None, timeframe: str, now: datetime | None = None) -> datetime | None:
    """Próximo fechamento de candle. Se o último candle da fonte já fechou (dado atrasado), avança na mesma grade."""
    if close_at is None:
        return None
    now = now or datetime.now(timezone.utc)
    step = TF_SECONDS.get(timeframe, 900)
    behind = (now - close_at).total_seconds()
    if behind >= 0:
        close_at = close_at + timedelta(seconds=(int(behind // step) + 1) * step)
    return close_at


def countdown(close_at: datetime | None, timeframe: str = "15m", now: datetime | None = None) -> str:
    """Tempo até o fechamento do candle: 04:12, 1h03 ou — (mercado fechado / sem dado)."""
    now = now or datetime.now(timezone.utc)
    close_at = next_close(close_at, timeframe, now)
    if close_at is None:
        return "—"
    left = max(0, int((close_at - now).total_seconds()))
    if left >= 3600:
        return f"{left // 3600}h{left % 3600 // 60:02d}"
    return f"{left // 60:02d}:{left % 60:02d}"


def next_refresh(close_at: datetime | None, timeframe: str, now: datetime | None = None) -> float:
    """Segundos até pedir a análise de novo: logo depois do fechamento do candle ou no intervalo normal."""
    poll = POLL_SECONDS.get(timeframe, 20)
    now = now or datetime.now(timezone.utc)
    close_at = next_close(close_at, timeframe, now)
    if close_at is None:
        return poll
    until_close = (close_at - now).total_seconds() + CANDLE_GRACE
    return max(1.0, min(poll, until_close))


# ---------------------------------------------------------------- o que o widget mostra

@dataclass
class SignalView:
    action: str
    title: str
    color: str
    symbol: str  # como aparece na corretora (ex.: XAUUSD)
    symbol_key: str  # como o AURUM chama o ativo (ex.: GC=F)
    name: str
    timeframe: str
    tf_label: str
    decimals: int
    price: float | None
    headline: str
    simple: str
    side: str | None
    side_text: str
    simulated: bool  # plano de simulação (ainda não é hora de entrar)
    entry: str
    stop: str
    target: str
    stop_detail: str
    target_detail: str
    risk: str
    risk_detail: str
    gain: str
    gain_detail: str
    size: str
    confidence: str
    confidence_detail: str
    confidence_value: float  # 0–100, para a barra
    window: str
    reliability: str
    source: str
    close_at: datetime | None
    bar_start: int | None
    market_open: bool
    position: bool
    warnings: list[str] = field(default_factory=list)

    @property
    def key(self) -> tuple:
        """Identifica o sinal: mudou a chave, o widget pisca (e toca, em ENTRAR/SAIR)."""
        return self.symbol_key, self.timeframe, self.action, self.side

    def compact_line(self) -> str:
        price = fmt_num(self.price, self.decimals)
        return f"{self.title} · {self.symbol} {self.timeframe} · {price}"


def _first_sentence(text: str) -> str:
    cut = text.find(". ")
    return text[:cut + 1] if 0 < cut < len(text) - 2 else text


def action_color(action: str, side: str | None) -> str:
    if action == "ENTRAR_AGORA":
        return BUY if side == "COMPRA" else SELL
    return {"SAIR_AGORA": EXIT, "PREPARE_SE": PREPARE, "MANTENHA": HOLD, "PAUSA": PAUSE,
            "MERCADO_FECHADO": CLOSED}.get(action, WAIT)


def build_view(a: dict) -> SignalView:
    """Resume a análise do AURUM no que cabe no widget: sinal, entrada, stop, alvo, risco e confiança.

    Com a boleta pronta (MT5, Binance, XP) os preços, lotes e valores são os da boleta — os mesmos que você digita
    na corretora. Sem boleta, vale o plano da análise."""
    s, p = a["signal"], a["plan"]
    t = a.get("ticket") or {}
    d = int(a.get("decimals", 2))
    asset = a.get("asset") or {}
    action = s["action"]
    side = s.get("side") or p.get("side")
    simulated = action not in LIVE_ACTIONS and not a.get("position")
    ticket_ok = bool(t.get("available")) and t.get("mode") != "exit"
    money = t.get("currency_symbol") or "R$"

    entry, stop, target = (t["entry"], t["stop"], t["target2"]) if ticket_ok else (p["entry"], p["stop"], p["target2"])
    pips = p.get("pips") or {}
    unit = pips.get("label") or "pips"
    digits = 0 if unit == "pontos" else 1
    stop_detail = f"{fmt_num(pips['stop'], digits)} {unit} · {fmt_num(p.get('stop_pct'), 2)}%" if pips else \
        f"{fmt_num(p.get('stop_pct'), 2)}% do preço"
    target_detail = (f"{fmt_num(pips['target2'], digits)} {unit} · {fmt_num(p.get('reward_ratio'), 1)}R" if pips
                     else f"{fmt_num(p.get('target2_pct'), 2)}% · {fmt_num(p.get('reward_ratio'), 1)}R")

    balance = t.get("balance") or 0
    if ticket_ok and t.get("risk_money") is not None:
        risk_money = t["risk_money"]
        risk_pct = risk_money / balance * 100 if balance else p.get("risk_pct")
        risk = f"{fmt_num(risk_pct, 2)}% da conta" if balance else f"{fmt_num(risk_pct, 1)}% do capital"
        risk_detail = f"{money} {fmt_num(risk_money)} se bater o stop"
        gain = f"{money} {fmt_num(t.get('reward_money'))}"
    else:
        risk = f"{fmt_num(p.get('risk_pct'), 1)}% do capital"
        risk_detail = f"{fmt_num(p.get('risk_amount'))} se bater o stop"
        gain = f"+{fmt_num(p.get('target2_pct'), 2)}%"
    gain_detail = f"no alvo 2 ({fmt_num(p.get('reward_ratio'), 1)}× o risco)"

    position = a.get("position") or {}
    if position:  # operação registrada: tamanho e resultado dela, não do plano novo
        risk = f"stop a {fmt_num(p.get('stop_pct'), 2)}%"
        risk_detail = "da sua entrada"
        pnl = position.get("pnl_pct")
        gain = f"{'+' if (pnl or 0) >= 0 else ''}{fmt_num(pnl, 2)}%" if pnl is not None else "—"
        gain_detail = "resultado agora"
    if position and position.get("quantity"):
        size = f"{fmt_num(position['quantity'], 4).rstrip('0').rstrip(',')} · registrada"
    elif ticket_ok:
        qty_decimals = t.get("qty_decimals", 2 if t.get("kind") == "mt5" else 0)
        unit_name = t.get("unit") or ""
        plural = unit_name in ("lote", "contrato") and t.get("quantity") != 1
        size = f"{fmt_num(t.get('quantity'), qty_decimals)} {unit_name}{'s' if plural else ''}".strip()
    elif pips.get("lots") is not None:
        size = f"{fmt_num(pips['lots'], 2)} lote(s)"
    else:
        size = "—"

    conf = s.get("confidence")
    info = s.get("confidence_info") or {}
    score = a.get("score") or 0
    if conf is not None:
        confidence = f"{conf}%"
        confidence_detail = f"chance histórica · empate {fmt_num(info.get('breakeven'), 0)}%" if info else "chance histórica"
        confidence_value = float(conf)
    else:
        confidence = f"{abs(score):.0f}/100"
        confidence_detail = "força " + ("compradora" if score > 0 else "vendedora" if score < 0 else "neutra")
        confidence_value = min(100.0, abs(float(score)))

    warnings = []  # só a primeira frase de cada aviso: o texto completo está no painel
    if a.get("source_warning"):
        warnings.append(_first_sentence(a["source_warning"]))
    elif not t.get("available") and t.get("reason"):  # boleta em espera por outro motivo que não a fonte
        warnings.append(_first_sentence(t["reason"]))
    if action == "PAUSA":
        warnings.append(s.get("explanation") or "Limite do dia atingido.")
    for w in (s.get("warnings") or [])[:2]:
        w = _first_sentence(w)
        if w not in warnings:
            warnings.append(w)

    candle = a.get("candle") or {}
    start = parse_time(candle.get("start"))
    tf = a.get("timeframe") or {}
    provider = a.get("data_provider") or ""
    source = {"mt5": "MT5", "binance": "Binance"}.get(provider, "Yahoo")
    reliability = ((a.get("context") or {}).get("reliability") or {}).get("level") or "—"
    return SignalView(
        action=action, title=s.get("title") or action.replace("_", " "), color=action_color(action, s.get("side")),
        symbol=asset.get("broker_symbol") or a["symbol"], symbol_key=a["symbol"], name=asset.get("name") or a["symbol"],
        timeframe=tf.get("key", ""), tf_label=tf.get("label", ""), decimals=d, price=a.get("price"),
        headline=s.get("headline") or "", simple=s.get("simple") or "", side=side,
        side_text={"COMPRA": "COMPRA ↑", "VENDA": "VENDA ↓"}.get(side or "", ""), simulated=simulated,
        entry=fmt_num(entry, d), stop=fmt_num(stop, d), target=fmt_num(target, d),
        stop_detail=stop_detail, target_detail=target_detail, risk=risk, risk_detail=risk_detail,
        gain=gain, gain_detail=gain_detail, size=size, confidence=confidence, confidence_detail=confidence_detail,
        confidence_value=confidence_value, window=(s.get("window") or {}).get("label") or "",
        reliability=reliability, source=source,
        close_at=parse_time(candle.get("close_at")) if (a.get("market") or {}).get("open") else None,
        bar_start=int(start.timestamp()) if start else None, market_open=bool((a.get("market") or {}).get("open")),
        position=bool(a.get("position")), warnings=warnings,
    )


# ---------------------------------------------------------------- preferências do widget (data/widget.json)

@dataclass
class WidgetConfig:
    symbol: str = "GC=F"  # XAUUSD
    timeframe: str = "15m"
    x: int | None = None
    y: int | None = None
    opacity: float = 0.92
    compact: bool = False
    topmost: bool = True
    hotkey: str = "ctrl+alt+a"
    beep: bool = True  # som em ENTRAR AGORA / SAIR AGORA
    show_on_signal: bool = True  # widget escondido reaparece sozinho em ENTRAR AGORA / SAIR AGORA
    start_server: bool = True  # liga o AURUM se ele estiver desligado

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> "WidgetConfig":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        known = {f.name for f in fields(cls)}
        cfg = cls(**{k: v for k, v in data.items() if k in known})
        cfg.opacity = min(1.0, max(0.3, float(cfg.opacity)))
        if cfg.timeframe not in TIMEFRAMES:
            cfg.timeframe = "15m"
        return cfg

    def save(self, path: Path = CONFIG_PATH) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(asdict(self), indent=1, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
