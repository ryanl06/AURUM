"""Capital pequeno: modo Spot só compra, boleta de R$ 50, plano de escalada e ranking de moedas (sem internet)."""

from __future__ import annotations

import unittest
from datetime import date
from unittest import mock

from app import scaling, screener
from app.analysis import analyze
from app.config import DEFAULT_SETTINGS
from app.strategy import BUY
from app.ticket import build_ticket
from tests.helpers import make_snapshot

BTC = {"pair": "BTCUSDT", "base": "BTC", "quote": "USDT", "status": "TRADING", "tick": 0.01,
       "step": 0.00001, "min_qty": 0.00001, "min_notional": 5.0, "oco": True}
SIGNAL = {"action": "ENTRAR_AGORA", "window": {"label": "Entre até 10:00"}}


def _plan(entry, stop, t1, t2, side="COMPRA", capital=50, risk_pct=1):
    return {"side": side, "entry": entry, "stop": stop, "target1": t1, "target2": t2, "capital": capital, "risk_pct": risk_pct}


class SpotOnlyTests(unittest.TestCase):
    def test_crypto_spot_only_never_sells_and_measures_only_buys(self):
        snap = make_snapshot(symbol="BTC-USD", timeframe="1h", n=600, seed=5, drift=-0.001, freq="1h")
        spot = analyze(snap, {**DEFAULT_SETTINGS})
        self.assertTrue(spot["context"]["filters"]["spot_only"])
        self.assertTrue(all(s["side"] == BUY for s in spot["backtest"]["setups"]))
        self.assertNotEqual(spot["signal"].get("side"), "VENDA")
        self.assertEqual(spot["plan"]["side"], "COMPRA")
        both = analyze(snap, {**DEFAULT_SETTINGS, "crypto_spot_only": "0"})
        self.assertTrue(any(s["side"] != BUY for s in both["backtest"]["setups"]))

    def test_spot_only_does_not_touch_forex(self):
        snap = make_snapshot(symbol="EURUSD=X", timeframe="1h", n=400, seed=3, freq="1h", volume=False)
        result = analyze(snap, {**DEFAULT_SETTINGS})
        self.assertFalse(result["context"]["filters"]["spot_only"])


class SmallTicketTests(unittest.TestCase):
    def test_one_percent_risk_on_fifty_reais_is_below_binance_minimum(self):
        t = build_ticket("BTC-USD", "crypto", _plan(77_000, 75_500, 78_500, 80_000, risk_pct=1), SIGNAL, "Binance",
                         date(2026, 9, 15), binance=BTC, brl_per_usd=5.0)
        self.assertTrue(any("mínimo da Binance" in w for w in t["warnings"]))  # por isso o plano usa 3% até R$ 200

    def test_fifty_reais_ticket_fits_and_oco_discounts_fee(self):
        t = build_ticket("BTC-USD", "crypto", _plan(77_000, 75_500, 78_500, 80_000, risk_pct=3), SIGNAL, "Binance",
                         date(2026, 9, 15), binance=BTC, brl_per_usd=5.0)
        # R$ 50 / 5 = US$ 10; 98% -> 0,00012 BTC (US$ 9,24): acima do mínimo de US$ 5
        self.assertAlmostEqual(t["quantity"], 0.00012, places=5)
        self.assertLess(t["oco_quantity"], t["quantity"])  # taxa de 0,1% descontada da moeda comprada
        self.assertAlmostEqual(t["oco_quantity"], 0.00011, places=5)
        oco = {f["label"]: f.get("key") for f in t["xp_orders"][1]["fields"]}
        self.assertEqual(oco["Quantidade (BTC)"], "oco_quantity")
        self.assertFalse(any("mínimo da Binance" in w for w in t["warnings"]))
        self.assertTrue(any("quase todo o saldo" in w for w in t["warnings"]))
        self.assertAlmostEqual(t["min_capital_brl"], 27.53, places=2)

    def test_oco_leg_below_minimum_is_flagged(self):
        t = build_ticket("BTC-USD", "crypto", _plan(77_000, 75_500, 78_500, 80_000, capital=27), SIGNAL, "Binance",
                         date(2026, 9, 15), binance=BTC, brl_per_usd=5.0)
        self.assertTrue(any("mínimo da Binance" in w for w in t["warnings"]))

    def test_tight_stop_warns_about_fees(self):
        t = build_ticket("BTC-USD", "crypto", _plan(77_000, 76_800, 77_200, 77_400, capital=5000), SIGNAL, "Binance",
                         date(2026, 9, 15), binance=BTC, brl_per_usd=5.0)
        self.assertTrue(any("taxas" in w.lower() and "risco" in w for w in t["warnings"]))


def _closed(r, day, symbol="BTC-USD"):
    return {"symbol": symbol, "status": "closed", "result_r": r, "closed_at": f"2026-09-{day:02d}T10:00:00-03:00"}


class ScalingTests(unittest.TestCase):
    def test_training_uses_crypto_signals_and_needs_sample(self):
        signals = [{"symbol": "BTC-USD", "status": "win", "result_r": 2.0, "closed_ts": "2026-09-10T10:00:00+00:00"},
                   {"symbol": "EURUSD=X", "status": "win", "result_r": 2.0, "closed_ts": "2026-09-10T11:00:00+00:00"},
                   {"symbol": "ETH-USD", "status": "open", "result_r": None, "closed_ts": None}]
        plan = scaling.evaluate(0, "", [], signals)
        self.assertEqual(plan["stats"]["trades"], 1)  # forex e sinais abertos ficam fora
        self.assertEqual(plan["decision"], "MANTER")
        self.assertEqual(plan["capital"], 50)
        self.assertFalse(plan["real_money"])

    def test_advance_when_all_rules_pass(self):
        rows = [_closed(2.0 if i % 2 else -1.0, 1 + i % 28) for i in range(22)]
        plan = scaling.evaluate(1, "2026-08-31T00:00:00-03:00", rows, [])
        self.assertTrue(all(c["ok"] for c in plan["checks"]), plan["checks"])
        self.assertEqual(plan["decision"], "SUBIR")
        self.assertEqual(plan["next_capital"], 100)

    def test_retreat_on_losing_streak_and_counts_only_since_level_start(self):
        old_wins = [_closed(2.0, 1) for _ in range(10)]
        streak = [_closed(-1.0, 12) for _ in range(5)]
        plan = scaling.evaluate(2, "2026-09-15T00:00:00-03:00", old_wins + streak, [])
        self.assertEqual(plan["stats"]["trades"], 0)  # tudo antes do início do nível
        plan = scaling.evaluate(2, "2026-09-10T00:00:00-03:00", old_wins + streak, [])
        self.assertEqual(plan["decision"], "DESCER")
        self.assertEqual(plan["stats"]["losing_streak"], 5)

    def test_level_settings_set_capital(self):
        values = scaling.level_settings(3)
        self.assertEqual(values["capital"], "200")
        self.assertEqual(values["scale_level"], "3")
        self.assertEqual(values["risk_per_trade_pct"], "3")
        self.assertEqual(scaling.level_settings(7)["risk_per_trade_pct"], "1")
        self.assertEqual(scaling.level_settings(0)["capital"], "50")


def _view(level, pf, total_r, trades, action="AGUARDE", tf="1h", min_capital=28.0, source_ok=True):
    return {"timeframe": tf, "label": tf, "reliability": level, "reliability_text": "x", "trades": trades,
            "win_rate": 40.0, "profit_factor": pf, "total_r": total_r, "avg_r": total_r / trades if trades else None,
            "max_drawdown_r": 2.0, "history_trades": 100, "history_pf": 1.0, "action": action, "title": action,
            "headline": "", "side": "COMPRA" if action == "ENTRAR_AGORA" else None, "confidence": None,
            "stop_pct": 2.0, "min_capital_brl": min_capital, "fees_money": 0.02, "source_ok": source_ok, "decimals": 2}


class ScreenerTests(unittest.TestCase):
    coin = {"pair": "SOLUSDT", "base": "SOL", "symbol": "SOL-USD", "price": 150.0, "change_pct": 1.0,
            "volume_usd": 200_000_000, "spread_pct": 0.01}

    def test_picks_best_timeframe_and_categories(self):
        good = screener.rate(self.coin, [_view("BAIXA", 0.6, -5, 30), _view("ALTA", 1.3, 6, 25, tf="1d")], 50)
        self.assertEqual(good["category"], "OPERAVEL")
        self.assertEqual(good["best"]["timeframe"], "1d")
        bad = screener.rate(self.coin, [_view("BAIXA", 0.6, -5, 30)], 50)
        self.assertEqual(bad["category"], "EVITAR")
        self.assertGreater(good["score"], bad["score"])

    def test_capital_and_signal_affect_score(self):
        base = screener.rate(self.coin, [_view("ALTA", 1.3, 6, 25)], 50)
        poor = screener.rate(self.coin, [_view("ALTA", 1.3, 6, 25)], 20)
        live = screener.rate(self.coin, [_view("ALTA", 1.3, 6, 25, action="ENTRAR_AGORA")], 50)
        self.assertFalse(poor["fits_capital"])
        self.assertLess(poor["score"], base["score"])
        self.assertGreater(live["score"], base["score"])

    def test_yahoo_fallback_data_is_not_ranked(self):
        item = screener.rate(self.coin, [_view("ALTA", 1.5, 8, 30, source_ok=False)], 50)
        self.assertEqual(item["category"], "EVITAR")

    def test_universe_filters_stables_leveraged_and_illiquid(self):
        def ticker(symbol, price, volume, change=2.0):
            return {"symbol": symbol, "lastPrice": str(price), "quoteVolume": str(volume), "priceChangePercent": str(change)}
        tickers = [ticker("BTCUSDT", 77000, 1e9), ticker("USDCUSDT", 1.0, 5e8, 0.01), ticker("RLUSDUSDT", 1.0, 7e7),
                   ticker("UUSDT", 1.0001, 4e7, 0.02), ticker("BTCUPUSDT", 10, 5e7), ticker("SOLUSDT", 150, 2e8),
                   ticker("TINYUSDT", 3, 1e5), ticker("ETHBTC", 0.04, 1e9), ticker("牛来USDT", 0.5, 3e7)]
        books = [{"symbol": "BTCUSDT", "bidPrice": "76999.99", "askPrice": "77000.00"}]
        with mock.patch.object(screener, "_get", side_effect=[tickers, books]):
            coins = screener.universe()
        self.assertEqual([c["base"] for c in coins], ["BTC", "SOL"])
        self.assertIsNotNone(coins[0]["spread_pct"])


if __name__ == "__main__":
    unittest.main()
