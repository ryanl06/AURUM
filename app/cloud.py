"""Varredura sem interface para rodar na nuvem (GitHub Actions), com o PC desligado.

Cada execução analisa os ativos de `cloud_config.json`, compara com o estado da execução
anterior e manda no Telegram só o que mudou (ENTRAR AGORA, SAIR AGORA e, se quiser, PREPARE-SE).
Não usa banco de dados nem posições: o token e o chat id vêm das variáveis de ambiente
TELEGRAM_TOKEN e TELEGRAM_CHAT_ID (secrets do repositório). Nada é executado na corretora.

Uso:  python -m app.cloud            (envia no Telegram)
      python -m app.cloud --dry-run  (só mostra no terminal)
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from . import market_data, news
from .config import BASE_DIR, DEFAULT_FAVORITES, DEFAULT_SETTINGS, LOCAL_TZ, TIMEFRAMES
from .market_data import MarketDataError
from .notifier import format_alert, send_telegram
from .pipeline import analyze_symbol

log = logging.getLogger("aurum.cloud")

CONFIG_FILE = BASE_DIR / "cloud_config.json"
STATE_FILE = Path(os.environ.get("AURUM_STATE_FILE", BASE_DIR / "cloud_state" / "state.json"))
SIGNALS = {"ENTRAR_AGORA", "SAIR_AGORA"}
LEVELS = {"ENTRAR_AGORA": None, "SAIR_AGORA": "danger", "PREPARE_SE": "warning"}


def load_config(path: Path = CONFIG_FILE) -> dict:
    cfg = {"timeframe": "1h", "symbols": DEFAULT_FAVORITES, "only_signals": True, "heartbeat_hour": 8,
           "settings": {}}
    if path.exists():
        cfg.update(json.loads(path.read_text(encoding="utf-8")))
    if cfg["timeframe"] not in TIMEFRAMES:
        raise SystemExit(f"timeframe inválido em {path.name}: {cfg['timeframe']} (use {', '.join(TIMEFRAMES)})")
    cfg["symbols"] = [s.strip().upper() for s in cfg["symbols"] if s.strip()]
    return cfg


def load_state(path: Path | None = None) -> dict:
    try:
        return json.loads((path or STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(state: dict, path: Path | None = None) -> None:
    path = path or STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")


def decide_alert(previous: dict | None, result: dict, only_signals: bool = True) -> dict | None:
    """Mesma regra do app local: avisa quando ação/lado/setup mudam para um estado que importa.

    Na primeira vez que um ativo aparece só um sinal ativo (ENTRAR/SAIR) gera aviso, para não
    disparar uma rajada de PREPARE-SE quando a varredura começa.
    """
    s = result["signal"]
    changed = (not previous or previous.get("action") != s["action"] or previous.get("side") != s.get("side")
               or previous.get("setup") != s.get("setup"))
    wanted = SIGNALS if (only_signals or previous is None) else SIGNALS | {"PREPARE_SE"}
    if not changed or s["action"] not in wanted:
        return None
    level = LEVELS[s["action"]] or ("success" if s.get("side") == "COMPRA" else "danger")
    return {"level": level, "title": f"{s['title']} · {result['symbol']}", "action": s["action"],
            "message": s["headline"]}


def cloud_message(alert: dict, result: dict) -> str:
    text = format_alert(alert, result)
    t = result.get("ticket") or {}
    extra = [f"🕐 Candle de {datetime.fromisoformat(result['data_time']).strftime('%d/%m %H:%M')} (Brasília)"]
    if alert["action"] == "ENTRAR_AGORA" and t.get("available"):
        where = t.get("exchange") or "XP"
        qty = t.get("quantity")
        qty_txt = f"{qty:.{t.get('qty_decimals', 0)}f}" if isinstance(qty, (int, float)) else "—"
        extra.append(f"📋 Boleta {html.escape(where)}: {html.escape(str(t.get('side')))} {qty_txt} "
                     f"{html.escape(str(t.get('unit', '')))} · {html.escape(str(t.get('code', '')))}")
    head, sep, tail = text.partition("\n")
    return f"{head}\n☁️ <i>AURUM nuvem</i>\n" + tail.replace("\n\n<i>", "\n" + "\n".join(extra) + "\n\n<i>", 1)


def run(dry_run: bool = False, config: dict | None = None, now: datetime | None = None) -> dict:
    cfg = config or load_config()
    settings = {**DEFAULT_SETTINGS, **{k: str(v) for k, v in cfg.get("settings", {}).items()}}
    market_data.USE_MT5 = False
    token, chat_id = os.environ.get("TELEGRAM_TOKEN", "").strip(), os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not dry_run and not (token and chat_id):
        raise SystemExit("Faltam os secrets TELEGRAM_TOKEN e TELEGRAM_CHAT_ID no repositório.")

    def send(text: str) -> bool:
        if dry_run:
            print("\n" + text + "\n")
            return True
        ok, detail = send_telegram(token, chat_id, text)
        if not ok:
            log.error("Telegram falhou: %s", detail)
        return ok

    state = load_state()
    assets = state.setdefault("assets", {})
    events = news.load()
    tf = cfg["timeframe"]
    summary = {"sent": 0, "errors": [], "signals": {}}
    started = time.time()

    for symbol in cfg["symbols"]:
        key = f"{symbol}|{tf}"
        try:
            result = analyze_symbol(symbol, tf, settings, events)
        except Exception as exc:  # um ativo com problema não derruba a varredura
            log.warning("%s falhou: %s", symbol, exc)
            summary["errors"].append(f"{symbol}: {exc}")
            continue
        s = result["signal"]
        summary["signals"][symbol] = s["action"]
        log.info("%-10s %-13s %s", symbol, s["action"], s["headline"])
        alert = decide_alert(assets.get(key), result, cfg.get("only_signals", True))
        if alert and not send(cloud_message(alert, result)):
            continue  # não grava o estado: tenta avisar de novo na próxima execução
        summary["sent"] += bool(alert)
        assets[key] = {"action": s["action"], "side": s.get("side"), "setup": s.get("setup")}

    now = (now or datetime.now(timezone.utc)).astimezone(LOCAL_TZ)
    today = now.date().isoformat()
    hour = cfg.get("heartbeat_hour")
    if hour is not None and now.hour >= int(hour) and state.get("heartbeat") != today:
        lines = [f"☁️ <b>AURUM nuvem ativo</b> · {now:%d/%m %H:%M}",
                 f"Monitorando {len(cfg['symbols'])} ativos no gráfico {TIMEFRAMES[tf].label}."]
        lines += [f"• {html.escape(sym)}: {act.replace('_', ' ')}" for sym, act in summary["signals"].items()]
        lines += [f"⚠️ {html.escape(e)}" for e in summary["errors"]]
        if send("\n".join(lines)):
            state["heartbeat"] = today

    if summary["errors"] and len(summary["errors"]) == len(cfg["symbols"]) and state.get("failure_notice") != today:
        if send("⚠️ <b>AURUM nuvem</b>: nenhum ativo pôde ser analisado agora (fonte de dados fora do ar?). "
                "Tento de novo na próxima execução."):
            state["failure_notice"] = today

    state["last_run"] = now.isoformat(timespec="seconds")
    if not dry_run:
        save_state(state)
    log.info("Pronto em %.1fs: %d aviso(s), %d erro(s).", time.time() - started, summary["sent"], len(summary["errors"]))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="AURUM: varredura na nuvem com avisos no Telegram")
    parser.add_argument("--dry-run", action="store_true", help="não envia nada; mostra as mensagens no terminal")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("yfinance").setLevel(logging.ERROR)
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
