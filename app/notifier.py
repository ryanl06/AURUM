"""Notificações externas opcionais (Telegram). Desligado até o usuário configurar."""

from __future__ import annotations

import html
import logging
import threading

import httpx

log = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/{method}"
ICONS = {"success": "🟢", "danger": "🔴", "warning": "🟡", "info": "🔵"}
SIGNAL_ACTIONS = {"ENTRAR_AGORA", "SAIR_AGORA"}


def _call(token: str, method: str, payload: dict | None = None) -> tuple[bool, dict | str]:
    if not token:
        return False, "Informe o token do bot."
    try:
        resp = httpx.post(API.format(token=token, method=method), json=payload or {}, timeout=10)
        data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        return False, f"Sem conexão com o Telegram: {exc}"
    if resp.status_code == 401:
        return False, "Token inválido. Copie de novo o token que o @BotFather enviou."
    if not data.get("ok"):
        return False, data.get("description", f"HTTP {resp.status_code}")
    return True, data["result"]


def send_telegram(token: str, chat_id: str, text: str) -> tuple[bool, str]:
    if not chat_id:
        return False, "Informe o chat id (use o botão “Descobrir meu chat id”)."
    ok, result = _call(token, "sendMessage", {
        "chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True,
    })
    if not ok and "chat not found" in str(result).lower():
        return False, "Chat não encontrado. Abra o seu bot no Telegram, envie /start e tente de novo."
    return ok, "Mensagem enviada." if ok else str(result)


def detect_chat(token: str) -> tuple[bool, dict | str]:
    """Descobre o chat id a partir da última mensagem enviada ao bot (ex.: /start)."""
    ok, me = _call(token, "getMe")
    if not ok:
        return False, me
    ok, updates = _call(token, "getUpdates", {"limit": 50, "timeout": 0})
    if not ok:
        return False, updates
    for update in reversed(updates):
        message = update.get("message") or update.get("channel_post") or update.get("my_chat_member") or {}
        chat = message.get("chat")
        if chat:
            name = chat.get("title") or " ".join(filter(None, [chat.get("first_name"), chat.get("last_name")]))
            return True, {"chat_id": str(chat["id"]), "name": name or chat.get("username") or "chat",
                          "bot": me.get("username")}
    return False, f"Nenhuma mensagem encontrada. Abra @{me.get('username')} no Telegram, envie /start e clique de novo."


# ---------------------------------------------------------------- formatação

def _fmt(value, decimals: int) -> str:
    if value is None:
        return "—"
    return f"{value:,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def format_alert(alert: dict, result: dict | None = None) -> str:
    esc = html.escape
    lines = [f"{ICONS.get(alert['level'], '•')} <b>{esc(alert['title'])}</b>"]
    if not result:
        lines.append(esc(alert["message"]))
    else:
        s, d, plan = result["signal"], result["decimals"], result["plan"]
        lines.append(f"{esc(result['asset']['name'])} · {esc(result['timeframe']['label'])}")
        lines.append(f"<b>{esc(s['headline'])}</b>")
        lines.append(f"💰 Preço: <b>{_fmt(result['price'], d)}</b>")
        if s["action"] == "ENTRAR_AGORA":
            if s.get("window"):
                lines.append(f"⏱ {esc(s['window']['label'])}")
            sign = "−" if plan["side"] == "COMPRA" else "+"
            lines.append(f"🛑 Stop: <b>{_fmt(plan['stop'], d)}</b> ({sign}{_fmt(plan['stop_pct'], 2)}%)")
            lines.append(f"🎯 Alvo 1: {_fmt(plan['target1'], d)} · Alvo 2: {_fmt(plan['target2'], d)}")
            if s.get("confidence") is not None:
                lines.append(f"📊 Confiança: {s['confidence']}%")
        elif s["action"] == "PREPARE_SE" and s.get("window"):
            lines.append(f"⏱ {esc(s['window']['label'])}")
        elif s["action"] == "SAIR_AGORA" and s.get("pnl_pct") is not None:
            lines.append(f"📈 Resultado: {s['pnl_pct']:+.2f}%")
        lines.append(esc(s["simple"]))
        lines.extend(f"⚠️ {esc(w)}" for w in s.get("warnings") or [])
    lines.append("\n<i>Isto não prevê resultados. Use stop loss.</i>")
    return "\n".join(lines)


def notify_alert(settings: dict, alert: dict, result: dict | None = None) -> None:
    if settings.get("telegram_enabled") != "1":
        return
    if settings.get("telegram_only_signals") == "1" and alert.get("action") not in SIGNAL_ACTIONS:
        return
    text = format_alert(alert, result)

    def worker():
        ok, detail = send_telegram(settings.get("telegram_token", ""), settings.get("telegram_chat_id", ""), text)
        if not ok:
            log.warning("Telegram falhou: %s", detail)

    threading.Thread(target=worker, daemon=True).start()
