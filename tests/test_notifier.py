"""Testes das mensagens e filtros do Telegram (sem rede)."""

from __future__ import annotations

import unittest
from unittest import mock

from app import notifier
from app.analysis import analyze
from app.config import DEFAULT_SETTINGS
from tests.helpers import make_snapshot


def _fake_entry_result() -> dict:
    snap = make_snapshot(symbol="BTC-USD", n=400, seed=5)
    result = analyze(snap, DEFAULT_SETTINGS, now=snap.candles.index[-1].to_pydatetime())
    result["signal"].update({
        "action": "ENTRAR_AGORA", "title": "ENTRAR AGORA", "headline": "COMPRA ↑ · COMPRA FORTE <teste>",
        "simple": "Momento favorável para COMPRAR.", "confidence": 61, "warnings": ["Volatilidade & risco"],
        "window": {"label": "Entre até 16:55 (fechamento do candle atual)"},
    })
    return result


class TelegramFormatTests(unittest.TestCase):
    def test_entry_message_has_plan_and_is_html_safe(self):
        result = _fake_entry_result()
        alert = {"level": "success", "title": "ENTRAR AGORA · BTC-USD", "message": "", "action": "ENTRAR_AGORA"}
        text = notifier.format_alert(alert, result)
        for piece in ("<b>ENTRAR AGORA · BTC-USD</b>", "Stop:", "Alvo 1:", "Entre até 16:55", "Confiança: 61%",
                      "&lt;teste&gt;", "Volatilidade &amp; risco", "Use stop loss"):
            self.assertIn(piece, text)

    def test_only_signals_filter_skips_price_alerts(self):
        settings = {**DEFAULT_SETTINGS, "telegram_enabled": "1", "telegram_only_signals": "1",
                    "telegram_token": "x", "telegram_chat_id": "1"}
        with mock.patch.object(notifier, "send_telegram") as send, mock.patch.object(notifier.threading, "Thread") as thread:
            notifier.notify_alert(settings, {"level": "warning", "title": "Preço mudou", "message": "m", "action": "PRECO"})
            thread.assert_not_called()
            notifier.notify_alert(settings, {"level": "success", "title": "ENTRAR", "message": "m", "action": "ENTRAR_AGORA"})
            thread.assert_called_once()
            send.assert_not_called()  # envio acontece dentro da thread (mockada)

    def test_disabled_sends_nothing(self):
        with mock.patch.object(notifier.threading, "Thread") as thread:
            notifier.notify_alert(DEFAULT_SETTINGS, {"level": "success", "title": "t", "message": "m", "action": "ENTRAR_AGORA"})
            thread.assert_not_called()

    def test_detect_chat_reads_last_message(self):
        responses = [
            (True, {"username": "aurum_bot"}),
            (True, [{"message": {"chat": {"id": 111, "first_name": "Velho"}}},
                    {"message": {"chat": {"id": 222, "first_name": "Ryan"}}}]),
        ]
        with mock.patch.object(notifier, "_call", side_effect=responses):
            ok, info = notifier.detect_chat("token")
        self.assertTrue(ok)
        self.assertEqual(info["chat_id"], "222")
        self.assertEqual(info["bot"], "aurum_bot")

    def test_detect_chat_without_messages_explains_next_step(self):
        with mock.patch.object(notifier, "_call", side_effect=[(True, {"username": "aurum_bot"}), (True, [])]):
            ok, detail = notifier.detect_chat("token")
        self.assertFalse(ok)
        self.assertIn("/start", detail)


if __name__ == "__main__":
    unittest.main()
