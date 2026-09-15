import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from app import cloud


def _result(action="ENTRAR_AGORA", side="COMPRA", setup="compra_forte", symbol="EURUSD=X"):
    signal = {"action": action, "side": side, "setup": setup, "title": action.replace("_", " "),
              "headline": "Compra confirmada", "simple": "Entre com stop.", "warnings": [],
              "window": {"label": "Entre até 10:15"}, "confidence": 61}
    return {
        "symbol": symbol, "signal": signal, "decimals": 5, "price": 1.1,
        "asset": {"name": "Euro / Dólar"}, "timeframe": {"key": "1h", "label": "1 hora"},
        "data_time": "2026-09-15T09:00:00-03:00",
        "plan": {"side": side, "stop": 1.09, "stop_pct": 0.9, "target1": 1.11, "target2": 1.12},
        "ticket": {"available": True, "exchange": "Binance", "side": "COMPRAR", "quantity": 0.02548,
                   "qty_decimals": 5, "unit": "BTC", "code": "BTCUSDT"},
    }


class DecideAlertTests(unittest.TestCase):
    def test_first_sight_only_alerts_active_signals(self):
        self.assertIsNotNone(cloud.decide_alert(None, _result()))
        self.assertIsNone(cloud.decide_alert(None, _result("PREPARE_SE"), only_signals=False))
        self.assertIsNone(cloud.decide_alert(None, _result("AGUARDE", None, None)))

    def test_same_signal_is_not_repeated(self):
        prev = {"action": "ENTRAR_AGORA", "side": "COMPRA", "setup": "compra_forte"}
        self.assertIsNone(cloud.decide_alert(prev, _result()))
        self.assertIsNotNone(cloud.decide_alert(prev, _result(side="VENDA", setup="venda_forte")))

    def test_prepare_only_when_enabled(self):
        prev = {"action": "AGUARDE", "side": None, "setup": None}
        self.assertIsNone(cloud.decide_alert(prev, _result("PREPARE_SE")))
        alert = cloud.decide_alert(prev, _result("PREPARE_SE"), only_signals=False)
        self.assertEqual(alert["level"], "warning")

    def test_message_has_cloud_tag_candle_time_and_ticket(self):
        result = _result()
        text = cloud.cloud_message(cloud.decide_alert(None, result), result)
        self.assertIn("AURUM nuvem", text)
        self.assertIn("Candle de 15/09 09:00", text)
        self.assertIn("Boleta Binance: COMPRAR 0.02548 BTC · BTCUSDT", text)
        self.assertTrue(text.rstrip().endswith("Use stop loss.</i>"))


class RunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "state.json"
        self.sent = []
        patches = [
            mock.patch.object(cloud, "STATE_FILE", self.state),
            mock.patch.object(cloud.news, "load", return_value=[]),
            mock.patch.dict(os.environ, {"TELEGRAM_TOKEN": "t", "TELEGRAM_CHAT_ID": "1"}),
            mock.patch.object(cloud, "send_telegram", side_effect=lambda t, c, text: (self.sent.append(text), (True, "ok"))[1]),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)
        self.config = {"timeframe": "1h", "symbols": ["EURUSD=X", "BAD"], "only_signals": True, "heartbeat_hour": 8}

    def _analyze(self, symbol, *_):
        if symbol == "BAD":
            raise cloud.MarketDataError("sem dados")
        return _result(symbol=symbol)

    def test_alerts_once_and_keeps_state_between_runs(self):
        morning = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)  # 09:00 em Brasília
        with mock.patch.object(cloud, "analyze_symbol", side_effect=self._analyze):
            first = cloud.run(config=self.config, now=morning)
            second = cloud.run(config=self.config, now=morning)
        self.assertEqual(first["sent"], 1)
        self.assertEqual(second["sent"], 0)
        self.assertEqual(first["errors"], ["BAD: sem dados"])
        signals = [t for t in self.sent if "AURUM nuvem ativo" not in t]
        heartbeats = [t for t in self.sent if "AURUM nuvem ativo" in t]
        self.assertEqual(len(signals), 1)
        self.assertEqual(len(heartbeats), 1)  # um resumo por dia
        self.assertTrue(self.state.exists())

    def test_failed_telegram_retries_next_run(self):
        with mock.patch.object(cloud, "analyze_symbol", side_effect=self._analyze), \
                mock.patch.object(cloud, "send_telegram", return_value=(False, "offline")):
            cloud.run(config={**self.config, "heartbeat_hour": None})
        self.assertNotIn("EURUSD=X|1h", cloud.load_state().get("assets", {}))

    def test_missing_secrets_stop_with_clear_message(self):
        with mock.patch.dict(os.environ, {"TELEGRAM_TOKEN": ""}), self.assertRaises(SystemExit):
            cloud.run(config=self.config)


if __name__ == "__main__":
    unittest.main()
