"""API HTTP + servidor do painel web."""

from __future__ import annotations

import csv
import io
import logging
import mimetypes
import os
import re
import threading
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from datetime import datetime, timezone

from . import __version__, assets, market_data, mt4_source, mt5_source, news, scaling
from . import database as db
from .config import DEFAULT_SETTINGS, DEFAULT_TIMEFRAME, DISCLAIMER, FOREX_MAJORS, STATIC_DIR, TIMEFRAMES
from .market_data import MarketDataError, get_candles, search_remote
from .notifier import detect_chat, send_telegram
from .scanner import scanner
from .screener import screener
from .service import radar, risk_state, run_analysis

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("text/javascript", ".js")  # alguns Windows registram .js como text/plain e quebram os módulos
for noisy in ("httpx", "yfinance", "peewee"):
    logging.getLogger(noisy).setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init()
    scanner.start()
    yield
    scanner.stop()


app = FastAPI(title="AURUM — Analisador de Mercado", version=__version__, lifespan=lifespan)


ALLOWED_HOSTS = {"localhost", "127.0.0.1", "[::1]", "testserver"}
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


@app.middleware("http")
async def local_only_guard(request: Request, call_next):
    """Proteção de app local: só atende pelo endereço local e exige cabeçalho próprio em ações que alteram dados.

    Sem isso, qualquer site aberto no navegador poderia mandar comandos ao AURUM (CSRF / DNS rebinding).
    O cabeçalho X-AURUM força o navegador a pedir permissão (preflight), que este servidor nunca concede a outros sites.
    """
    host = (request.headers.get("host") or "").rsplit(":", 1)[0].lower()
    if host not in ALLOWED_HOSTS:
        return JSONResponse(status_code=403, content={"detail": "Acesso permitido apenas em http://localhost."})
    if request.method in MUTATING and request.url.path.startswith("/api/") and request.headers.get("x-aurum") != "1":
        return JSONResponse(status_code=403, content={"detail": "Requisição recusada (cabeçalho X-AURUM ausente)."})
    return await call_next(request)


@app.middleware("http")
async def no_cache_static(request, call_next):
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache"  # app local: sempre revalida o front após atualizações
    return response


@app.exception_handler(Exception)
async def unexpected_error(_, exc: Exception):
    logging.getLogger("aurum").exception("Erro inesperado: %s", exc)
    return JSONResponse(status_code=500, content={"detail": f"Erro interno do AURUM: {exc}"})


@app.exception_handler(MarketDataError)
async def market_error(_, exc: MarketDataError):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


def _tf(value: str) -> str:
    if value not in TIMEFRAMES:
        raise HTTPException(422, f"Tempo gráfico inválido. Use: {', '.join(TIMEFRAMES)}")
    return value


def _symbol(value: str) -> str:
    try:
        return assets.resolve_symbol(value)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


# ---------------------------------------------------------------- sistema

@app.get("/api/health")
def health():
    return {"app": "AURUM", "version": __version__, "pid": os.getpid(), "time": datetime.now(timezone.utc).isoformat()}


@app.post("/api/system/shutdown")
def shutdown(request: Request):
    """Desliga o servidor (usado pelo PARAR_AURUM.bat). Só aceita pedidos do próprio computador."""
    if request.client is None or request.client.host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(403, "Permitido apenas neste computador.")
    scanner.stop()
    threading.Timer(0.5, lambda: os._exit(0)).start()
    return {"ok": True, "detail": "AURUM encerrando."}


# ---------------------------------------------------------------- meta

@app.get("/api/meta")
def meta():
    return {
        "version": __version__,
        "disclaimer": DISCLAIMER,
        "timeframes": [{"key": t.key, "label": t.label, "seconds": t.seconds} for t in TIMEFRAMES.values()],
        "default_timeframe": DEFAULT_TIMEFRAME,
        "catalog": assets.search_catalog("", limit=100),
    }


@app.get("/api/search")
def search(q: str = Query("", max_length=40)):
    local = assets.search_catalog(q)
    resolved = None
    if q.strip():
        try:
            resolved = assets.resolve_symbol(q)
        except ValueError:
            pass
    remote = search_remote(q) if len(q.strip()) >= 2 and len(local) < 5 else []
    seen = {item["symbol"] for item in local}
    merged = local + [r for r in remote if r["symbol"] not in seen]
    return {"resolved": resolved, "results": merged[:10]}


# ---------------------------------------------------------------- análise

@app.get("/api/analysis")
def analysis(symbol: str = Query(..., max_length=40), tf: str = DEFAULT_TIMEFRAME, force: bool = False):
    sym, timeframe = _symbol(symbol), _tf(tf)
    result = run_analysis(sym, timeframe, force=force)
    scanner.touch(sym, timeframe)
    return result


@app.get("/api/live")
def live(symbol: str = Query(..., max_length=40), tf: str = DEFAULT_TIMEFRAME, bar_start: int | None = None):
    """Candle em formação (atualização a cada poucos segundos para o gráfico ao vivo)."""
    sym, timeframe = _symbol(symbol), _tf(tf)
    market_data.configure(db.get_settings())
    candle = market_data.live_candle(sym, timeframe, bar_start)
    pair = market_data.binance_pair(sym)
    stream = None
    if pair and pair not in market_data._binance_unsupported:
        interval = "1d" if timeframe == "1d" else timeframe
        stream = f"{pair.lower()}@kline_{interval}"
    return {"symbol": sym, "timeframe": timeframe, "candle": candle, "binance_stream": stream}


@app.post("/api/analysis/record")
def record_analysis(symbol: str, tf: str = DEFAULT_TIMEFRAME):
    result = run_analysis(_symbol(symbol), _tf(tf), record=True, source="manual")
    return {"ok": True, "action": result["signal"]["action"]}


@app.get("/api/radar")
def radar_view(tf: str = DEFAULT_TIMEFRAME):
    symbols = [f["symbol"] for f in db.list_favorites()]
    return {"timeframe": _tf(tf), "items": radar(symbols, tf)}


# ---------------------------------------------------------------- favoritos

class FavoriteIn(BaseModel):
    symbol: str = Field(..., max_length=40)


@app.get("/api/favorites")
def favorites():
    return [{**f, **{"name": f["name"] or assets.asset_info(f["symbol"])["name"]}} for f in db.list_favorites()]


@app.post("/api/favorites")
def add_favorite(body: FavoriteIn):
    sym = _symbol(body.symbol)
    get_candles(sym, DEFAULT_TIMEFRAME)  # valida se o ativo existe antes de salvar
    db.add_favorite(sym)
    return favorites()


PACKS = {"forex-pack": FOREX_MAJORS, "b3-pack": ["WDOFUT", "WINFUT"]}


@app.post("/api/favorites/{pack}")
def add_pack(pack: Literal["forex-pack", "b3-pack"]):
    for symbol in PACKS[pack]:
        db.add_favorite(symbol)
    scanner.trigger()
    return favorites()


@app.delete("/api/favorites/{symbol}")
def remove_favorite(symbol: str):
    db.remove_favorite(symbol)
    return favorites()


# ---------------------------------------------------------------- histórico, alertas e relatório

@app.get("/api/history")
def history(symbol: str | None = None, tf: str | None = None, limit: int = Query(200, le=2000),
            only_signals: bool = False):
    return db.history(_symbol(symbol) if symbol else None, tf, limit, only_signals)


@app.get("/api/history.csv")
def history_csv(symbol: str | None = None, tf: str | None = None):
    rows = db.history(_symbol(symbol) if symbol else None, tf, limit=5000)
    buf = io.StringIO()
    if rows:
        writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()), delimiter=";")
        writer.writeheader()
        writer.writerows(rows)
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="aurum_historico.csv"'})


@app.get("/api/signals")
def signals(symbol: str | None = None, limit: int = Query(300, le=2000)):
    """Sinais ENTRAR AGORA emitidos e o que aconteceu depois (acompanhamento real, não backtest)."""
    rows = db.list_signals(symbol=_symbol(symbol) if symbol else None, limit=limit)
    done = [r for r in rows if r["status"] != "open"]
    rs = [r["result_r"] for r in done if r["result_r"] is not None]
    gains, losses = sum(r for r in rs if r > 0), -sum(r for r in rs if r < 0)
    by_key: dict[str, list[float]] = {}
    for r in done:
        by_key.setdefault(f"{r['symbol']} · {r['timeframe']}", []).append(r["result_r"] or 0)
    return {
        "summary": {
            "total": len(rows), "open": len(rows) - len(done), "closed": len(done),
            "wins": sum(1 for r in done if r["status"] == "win"),
            "win_rate": round(sum(1 for r in done if r["status"] == "win") / len(done) * 100, 1) if done else None,
            "total_r": round(sum(rs), 2), "profit_factor": round(gains / losses, 2) if losses else None,
        },
        "by_asset": sorted(({"key": k, "signals": len(v), "total_r": round(sum(v), 2),
                             "win_rate": round(sum(1 for x in v if x > 0) / len(v) * 100, 1)} for k, v in by_key.items()),
                           key=lambda x: -x["total_r"]),
        "items": rows,
    }


@app.get("/api/alerts")
def alerts(since_id: int = 0, limit: int = Query(50, le=500)):
    return db.list_alerts(since_id, limit)


@app.get("/api/report.txt", response_class=PlainTextResponse)
def report(symbol: str, tf: str = DEFAULT_TIMEFRAME):
    sym, timeframe = _symbol(symbol), _tf(tf)
    a = run_analysis(sym, timeframe)
    s, f, p, bt = a["signal"], a["forecast"], a["plan"], a["backtest"]["overall"]
    ind = a["indicators"]
    lines = [
        f"RELATÓRIO AURUM — {a['asset']['name']} ({sym}) · {a['timeframe']['label']}",
        f"Gerado em {a['generated_at'][:19].replace('T', ' ')}",
        "=" * 64,
        f"Preço: {a['price']}  ({a['change_pct']:+.2f}% no dia)" if a["change_pct"] is not None else f"Preço: {a['price']}",
        f"Tendência: {a['trend']['direction']} — {a['trend']['text']}",
        f"Previsão: {f['headline']}",
        *([f"Probabilidade histórica: {f['probability']['text']}"] if f.get("probability") else []),
        f"Confiabilidade: {a['context']['reliability']['level']} — {a['context']['reliability']['short']}",
        f"Fonte dos dados: {a['data_source']}",
        "",
        f"SINAL: {s['title']} — {s['headline']}",
        f"  {s['explanation']}",
        f"  É o momento certo de operar? {'SIM' if s['right_moment'] else 'NÃO'}",
        "",
        "INDICADORES",
        f"  RSI {ind['rsi']['value']}: {ind['rsi']['text']}",
        f"  MACD {ind['macd']['value']}: {ind['macd']['text']}",
        f"  Médias: MME9 {ind['ema']['ema9']} / MME21 {ind['ema']['ema21']} — {ind['ema']['text']}",
        f"  Bollinger: {ind['bollinger']['lower']} – {ind['bollinger']['upper']} — {ind['bollinger']['text']}",
        f"  ATR {ind['atr']['pct']}%: {ind['atr']['text']}",
        f"  ADX {ind['adx']['value']}: {ind['adx']['text']}",
        "",
        f"PLANO ({p['side']}): entrada {p['entry']} · stop {p['stop']} (-{p['stop_pct']}%) · alvo 1 {p['target1']} · alvo 2 {p['target2']}",
        "",
        "LEITURAS",
        *[f"  • {r['text']}" for r in f["reads"]],
        "",
        f"BACKTEST ({a['backtest']['period']['from']} a {a['backtest']['period']['to']}): "
        f"{bt['trades']} sinais · acerto {bt['win_rate']}% · fator de lucro {bt['profit_factor']}",
        "",
        "HISTÓRICO RECENTE",
        *[f"  {h['ts'][:16].replace('T', ' ')}  {h['action']:<16} {h['price']}  {h['headline'] or ''}"
          for h in db.history(sym, timeframe, limit=20)],
        "",
        DISCLAIMER,
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------- posições

class PositionIn(BaseModel):
    symbol: str = Field(..., max_length=40)
    side: Literal["COMPRA", "VENDA"]
    entry_price: float = Field(..., gt=0)
    quantity: float | None = Field(None, ge=0)
    stop_price: float | None = Field(None, gt=0)
    target_price: float | None = Field(None, gt=0)
    timeframe: str | None = None
    notes: str | None = Field(None, max_length=500)


class CloseIn(BaseModel):
    exit_price: float | None = Field(None, gt=0)


@app.get("/api/positions")
def positions(status: Literal["open", "closed"] | None = None):
    return db.list_positions(status)


@app.post("/api/positions")
def open_position(body: PositionIn):
    data = body.model_dump()
    data["symbol"] = _symbol(body.symbol)
    long = body.side == "COMPRA"
    if body.stop_price and ((long and body.stop_price >= body.entry_price) or (not long and body.stop_price <= body.entry_price)):
        raise HTTPException(422, "O stop precisa ficar do lado da perda (abaixo da entrada na compra, acima na venda).")
    if db.open_position_for(data["symbol"]):
        raise HTTPException(409, "Já existe uma posição aberta neste ativo. Encerre-a antes de abrir outra.")
    return db.open_position(data)


@app.post("/api/positions/{position_id}/close")
def close_position(position_id: int, body: CloseIn):
    pos = db.get_position(position_id)
    if not pos:
        raise HTTPException(404, "Posição não encontrada.")
    exit_price = body.exit_price
    if exit_price is None:
        exit_price = get_candles(pos["symbol"], pos["timeframe"] or DEFAULT_TIMEFRAME, force=True).candles["close"].iloc[-1]
    try:
        max_stop = float(db.get_settings().get("max_stop_pct", 3))
    except ValueError:
        max_stop = 3.0
    return db.close_position(position_id, round(float(exit_price), 8), max_stop)


@app.get("/api/risk")
def risk():
    return risk_state(db.get_settings())


@app.get("/api/news")
def news_view(symbol: str = "EURUSD=X"):
    sym = _symbol(symbol)
    return news.context(sym, assets.classify(sym), datetime.now(timezone.utc), news.load())


@app.delete("/api/positions/{position_id}")
def delete_position(position_id: int):
    db.delete_position(position_id)
    return {"ok": True}


# ---------------------------------------------------------------- configurações e scanner

@app.get("/api/settings")
def get_settings():
    values = db.get_settings()
    token = values.get("telegram_token", "")
    values["telegram_token"] = f"••••{token[-4:]}" if token else ""
    return values


@app.put("/api/settings")
def put_settings(values: dict[str, str]):
    clean = {k: v for k, v in values.items() if k in DEFAULT_SETTINGS}
    if clean.get("telegram_token", "").startswith("••••"):
        clean.pop("telegram_token")  # campo mascarado não alterado
    for key in ("scan_interval_min", "max_stop_pct", "atr_stop_mult", "reward_ratio", "capital",
                "risk_per_trade_pct", "price_move_alert_pct", "cost_pct", "max_trades_day", "daily_max_loss_r",
                "max_consecutive_losses"):
        if key in clean:
            if key in ("atr_stop_mult", "cost_pct") and clean[key].strip().lower() in ("auto", ""):
                clean[key] = "auto"
                continue
            try:
                if float(clean[key].replace(",", ".")) < 0:
                    raise ValueError
                clean[key] = clean[key].replace(",", ".")
            except ValueError:
                raise HTTPException(422, f"Valor inválido para {key}.")
    for key in ("htf_filter", "session_filter", "quality_gate", "telegram_enabled", "telegram_only_signals", "use_mt5",
                "news_guard", "risk_guard", "crypto_spot_only", "use_mt4"):
        if key in clean and clean[key] not in ("0", "1"):
            raise HTTPException(422, f"Valor inválido para {key}.")
    if "strategy_mode" in clean and clean["strategy_mode"] not in ("zonas", "indicadores", "ambos"):
        raise HTTPException(422, "Estratégia inválida.")
    if "mt4_suffix" in clean:
        clean["mt4_suffix"] = clean["mt4_suffix"].strip()
        if not re.fullmatch(r"[A-Za-z0-9._#-]{0,10}", clean["mt4_suffix"]):
            raise HTTPException(422, "Sufixo do MT4 inválido (use só letras, números, ponto ou traço).")
    if "max_stop_pct" in clean and float(clean["max_stop_pct"]) > 3:
        raise HTTPException(422, "O stop loss máximo é 3% (regra obrigatória de proteção).")
    if "scan_timeframe" in clean:
        _tf(clean["scan_timeframe"])
    db.update_settings(clean)
    scanner.trigger()
    return get_settings()


class TelegramIn(BaseModel):
    token: str | None = Field(None, max_length=200)
    chat_id: str | None = Field(None, max_length=40)


def _telegram_credentials(body: TelegramIn) -> tuple[str, str]:
    """Usa o que está digitado no formulário; campos vazios ou mascarados caem no que já está salvo."""
    settings = db.get_settings()
    token = (body.token or "").strip()
    if not token or token.startswith("••••"):
        token = settings.get("telegram_token", "")
    chat_id = (body.chat_id or "").strip() or settings.get("telegram_chat_id", "")
    return token, chat_id


@app.post("/api/telegram/detect")
def telegram_detect(body: TelegramIn):
    token, _ = _telegram_credentials(body)
    ok, result = detect_chat(token)
    if not ok:
        return {"ok": False, "detail": result}
    db.update_settings({"telegram_token": token, "telegram_chat_id": result["chat_id"]})
    return {"ok": True, "chat_id": result["chat_id"],
            "detail": f"Conectado ao chat “{result['name']}” pelo bot @{result['bot']}."}


@app.post("/api/telegram/test")
def telegram_test(body: TelegramIn | None = None):
    token, chat_id = _telegram_credentials(body or TelegramIn())
    ok, detail = send_telegram(token, chat_id, (
        "✅ <b>AURUM conectado</b>\n"
        "Você vai receber aqui os alertas de <b>ENTRAR AGORA</b>, <b>PREPARE-SE</b> e <b>SAIR AGORA</b> "
        "dos seus favoritos e dos ativos abertos no painel.\n\n<i>Isto não prevê resultados. Use stop loss.</i>"))
    return {"ok": ok, "detail": detail}


@app.get("/api/mt4/status")
def mt4_status():
    enabled = db.get_settings().get("use_mt4", "0") == "1"
    return {**mt4_source.status(), "enabled": enabled}


@app.get("/api/mt5/status")
def mt5_status():
    enabled = db.get_settings().get("use_mt5", "0") == "1"
    return {**mt5_source.status(enabled), "enabled": enabled}


@app.post("/api/mt5/reconnect")
def mt5_reconnect():
    if db.get_settings().get("use_mt5", "0") != "1":
        raise HTTPException(409, "O uso do MetaTrader 5 está desligado em ⚙ Configurações.")
    mt5_source.request_connect(force=True)  # em segundo plano: a resposta volta na hora
    market_data._cache.clear()  # próxima leitura já tenta a fonte nova
    scanner.trigger()
    return {**mt5_status(), "connecting": True}


@app.get("/api/screener")
def screener_view(refresh: bool = False):
    """Ranking de moedas para capital pequeno. Recalcula em segundo plano quando está velho (6 h) ou a pedido."""
    status = screener.status()
    if (refresh or status["stale"]) and not status["running"]:
        screener.start(db.get_settings())
        status = screener.status()
    return status


class LevelIn(BaseModel):
    level: int = Field(..., ge=0, le=len(scaling.LADDER) - 1)


@app.get("/api/scaling")
def scaling_view():
    settings = db.get_settings()
    try:
        level = int(settings.get("scale_level") or 0)
    except ValueError:
        level = 0
    return scaling.evaluate(level, settings.get("scale_since"), db.list_positions("closed", limit=2000),
                            db.list_signals(limit=5000))


@app.post("/api/scaling/level")
def scaling_set_level(body: LevelIn):
    db.update_settings(scaling.level_settings(body.level))
    scanner.trigger()
    return scaling_view()


@app.get("/api/scanner")
def scanner_status():
    return scanner.status()


@app.post("/api/scanner/run")
def scanner_run():
    scanner.trigger()
    return {"ok": True}


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
