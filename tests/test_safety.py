"""Proteções: agenda de notícias, limites diários e prioridade das proteções sobre o sinal técnico."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app import database, news
from app.decision import apply_guards
from app.service import risk_state

NOW = datetime(2026, 9, 16, 17, 40, tzinfo=timezone.utc)


def _event(minutes: int, country="USD", impact="High", title="Federal Funds Rate") -> dict:
    when = (NOW + timedelta(minutes=minutes)).astimezone(timezone(timedelta(hours=-4)))
    return {"title": title, "country": country, "impact": impact, "date": when.isoformat(), "forecast": "4.00%", "previous": "3.75%"}


class NewsTests(unittest.TestCase):
    def test_translation_and_currency_mapping(self):
        self.assertEqual(news.translate("Federal Funds Rate"), "Juros do Fed (FOMC)")
        self.assertEqual(news.translate("Core CPI m/m"), "Inflação núcleo (CPI) m/m")
        self.assertEqual(news.currencies_for("EURUSD=X", "forex"), {"EUR", "USD", "All"})
        self.assertIn("USD", news.currencies_for("WDOFUT", "b3fut"))

    def test_blocking_window(self):
        events = news._parse([_event(20)])
        ctx = news.context("EURUSD=X", "forex", NOW, events)
        self.assertIsNotNone(ctx["blocking"])
        self.assertEqual(ctx["blocking"]["title"], "Juros do Fed (FOMC)")
        far = news.context("EURUSD=X", "forex", NOW, news._parse([_event(90)]))
        self.assertIsNone(far["blocking"])
        self.assertEqual(len(far["upcoming"]), 1)
        after = news.context("EURUSD=X", "forex", NOW, news._parse([_event(-25)]))
        self.assertIsNotNone(after["blocking"])  # ainda dentro dos 30 min depois
        other = news.context("EURUSD=X", "forex", NOW, news._parse([_event(10, country="JPY")]))
        self.assertIsNone(other["blocking"])

    def test_medium_impact_never_blocks(self):
        ctx = news.context("GBPUSD=X", "forex", NOW, news._parse([_event(5, impact="Medium")]))
        self.assertIsNone(ctx["blocking"])


class GuardTests(unittest.TestCase):
    entry = {"action": "ENTRAR_AGORA", "title": "ENTRAR AGORA", "tone": "buy", "side": "COMPRA", "setup": "COMPRA FORTE",
             "right_moment": True, "confidence": 55, "confidence_info": {}, "window": {"label": "x"}, "horizon": None,
             "headline": "h", "explanation": "e", "simple": "s", "warnings": []}

    def test_risk_guard_wins_over_signal(self):
        sig = apply_guards(dict(self.entry), {"risk": {"blocked": True, "reasons": ["3 perdas seguidas."]}})
        self.assertEqual(sig["action"], "PAUSA")
        self.assertFalse(sig["right_moment"])
        self.assertIn("COMPRA FORTE foi ignorado", sig["explanation"])

    def test_news_guard_holds_entry(self):
        block = news.context("EURUSD=X", "forex", NOW, news._parse([_event(15)]))["blocking"]
        ctx = {"risk": {"blocked": False}, "news": {"blocking": block}, "news_guard": True}
        sig = apply_guards(dict(self.entry), ctx)
        self.assertEqual(sig["action"], "AGUARDE")
        self.assertEqual(sig["guard"], "news")
        off = apply_guards(dict(self.entry), {**ctx, "news_guard": False})
        self.assertEqual(off["action"], "ENTRAR_AGORA")

    def test_no_guard_keeps_signal(self):
        self.assertEqual(apply_guards(dict(self.entry), {})["action"], "ENTRAR_AGORA")


class RiskStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        database.DATA_DIR = Path(self.tmp.name)
        database.DB_PATH = Path(self.tmp.name) / "risk.db"
        database._initialized = False

    def tearDown(self):
        self.tmp.cleanup()

    def _trade(self, exit_price):
        pos = database.open_position({"symbol": "WDOFUT", "side": "COMPRA", "entry_price": 5400.0, "quantity": 2,
                                      "stop_price": 5386.0, "timeframe": "15m"})
        return database.close_position(pos["id"], exit_price)

    def test_result_r_and_money_for_futures(self):
        closed = self._trade(5428.0)
        self.assertAlmostEqual(closed["result_r"], 2.0)
        self.assertAlmostEqual(closed["pnl_money"], 28 * 2 * 10.0)  # 28 pontos x 2 contratos x R$10

    def test_limits(self):
        settings = {"risk_guard": "1", "max_trades_day": "5", "daily_max_loss_r": "2", "max_consecutive_losses": "2"}
        self._trade(5386.0)
        state = risk_state(settings)
        self.assertFalse(state["blocked"])
        self.assertEqual(state["losing_streak"], 1)
        self._trade(5386.0)
        state = risk_state(settings)
        self.assertTrue(state["blocked"])
        self.assertAlmostEqual(state["day_r"], -2.0)
        self.assertEqual(len(state["reasons"]), 2)  # perda do dia e perdas seguidas
        self.assertFalse(risk_state({**settings, "risk_guard": "0"})["blocked"])


if __name__ == "__main__":
    unittest.main()
