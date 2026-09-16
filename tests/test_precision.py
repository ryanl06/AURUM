"""Testes de precisão: nada pode olhar o futuro, e as contas de risco precisam fechar."""

from __future__ import annotations

import unittest

import pandas as pd

from app.analysis import analyze, numeric_settings, price_decimals
from app.backtest import calibration, passes_quality, probability_for, reliability
from app.config import DEFAULT_SETTINGS
from app.decision import pip_size, trade_plan
from app.indicators import add_indicators
from app.levels import divergence_series, find_pivots, levels_at
from app.service import judge_signal
from app.strategy import htf_bias_series, score_series, session_mask
from tests.helpers import make_candles, make_snapshot


class NoLookaheadTests(unittest.TestCase):
    def test_htf_bias_only_uses_closed_higher_candles(self):
        ltf = add_indicators(make_candles(n=1200, freq="15min", seed=2))
        htf = add_indicators(make_candles(n=400, freq="1h", seed=3))
        bias = htf_bias_series(ltf.index, 900, htf, 3600, True)
        # Alterar candles maiores ainda não fechados não pode mudar o viés passado.
        cut = htf.index[200]
        changed = htf.copy()
        changed.loc[changed.index >= cut, ["open", "high", "low", "close"]] *= 1.5
        changed = add_indicators(changed[["open", "high", "low", "close", "volume"]])
        bias2 = htf_bias_series(ltf.index, 900, changed, 3600, True)
        known_before = ltf.index + pd.Timedelta(seconds=900) < cut + pd.Timedelta(seconds=3600)
        pd.testing.assert_series_equal(bias[known_before], bias2[known_before], check_names=False)

    def test_levels_ignore_future_bars(self):
        df = add_indicators(make_candles(n=600, seed=4))
        i = 400
        base = levels_at(df, find_pivots(df), i)
        future = df.copy()
        future.iloc[i + 1:, future.columns.get_indexer(["high", "low", "close"])] *= 0.7
        again = levels_at(future, find_pivots(future), i)
        self.assertEqual([round(x["price"], 6) for x in base["supports"]], [round(x["price"], 6) for x in again["supports"]])
        self.assertEqual([round(x["price"], 6) for x in base["resistances"]], [round(x["price"], 6) for x in again["resistances"]])

    def test_divergence_is_boolean_series_aligned(self):
        df = add_indicators(make_candles(n=800, seed=6))
        bull, bear = divergence_series(df, find_pivots(df))
        self.assertEqual(len(bull), len(df))
        self.assertFalse((bull & bear).all())

    def test_session_mask_hours(self):
        idx = pd.date_range("2026-09-14 00:00", periods=24, freq="1h", tz="UTC")
        mask = session_mask(idx)
        self.assertEqual(int(mask.sum()), 10)
        self.assertTrue(mask.iloc[7] and not mask.iloc[17])


class CalibrationTests(unittest.TestCase):
    def test_calibration_counts_and_lookup(self):
        df = add_indicators(make_candles(n=900, seed=8))
        score = score_series(df, True)
        calib = calibration(df, score, horizon=3)
        valid = pd.DataFrame({"s": score, "f": df["close"].shift(-3)}).dropna()
        self.assertEqual(sum(b["count"] for b in calib["buckets"]), len(valid))
        bucket = probability_for(calib, 0.0)
        self.assertTrue(bucket["from"] <= 0 < bucket["to"])
        self.assertTrue(0 <= bucket["up_rate"] <= 100)

    def test_quality_gate_and_reliability(self):
        self.assertFalse(passes_quality({"trades": 20, "profit_factor": 0.7}))
        self.assertTrue(passes_quality({"trades": 5, "profit_factor": 0.2}))  # amostra pequena não bloqueia
        self.assertTrue(passes_quality({"trades": 40, "profit_factor": 1.2}))
        low = reliability({"recent_filtered": {"trades": 30, "profit_factor": 0.6}})
        high = reliability({"recent_filtered": {"trades": 30, "profit_factor": 1.4}})
        self.assertEqual(low["level"], "BAIXA")
        self.assertEqual(high["level"], "ALTA")


class RiskMathTests(unittest.TestCase):
    def test_auto_settings(self):
        intraday = numeric_settings(DEFAULT_SETTINGS, 900, "forex")
        daily = numeric_settings(DEFAULT_SETTINGS, 86400, "crypto")
        self.assertEqual(intraday["atr_mult"], 2.0)
        self.assertEqual(daily["atr_mult"], 1.5)
        self.assertAlmostEqual(intraday["cost"], 0.012)
        self.assertAlmostEqual(daily["cost"], 0.2)  # Binance: 0,1% na compra + 0,1% na venda

    def test_forex_pips_and_lots(self):
        cfg = {"atr_mult": 2.0, "max_stop": 3.0, "reward": 2.0, "capital": 10000, "risk_pct": 1}
        eur = trade_plan(1.1000, 0.0010, "COMPRA", cfg, 5, "EURUSD=X", "forex")
        self.assertAlmostEqual(eur["pips"]["stop"], 20.0, places=1)
        self.assertAlmostEqual(eur["pips"]["pip_value_per_lot"], 10.0)
        self.assertAlmostEqual(eur["pips"]["lots"], 100 / (20 * 10), places=2)  # arrisca 1% = 100
        jpy = trade_plan(150.00, 0.10, "VENDA", cfg, 3, "USDJPY=X", "forex")
        self.assertEqual(pip_size("USDJPY=X", "forex"), 0.01)
        self.assertAlmostEqual(jpy["pips"]["stop"], 20.0, places=1)
        self.assertGreater(jpy["stop"], jpy["entry"])

    def test_tiny_prices_keep_significant_digits(self):
        self.assertGreaterEqual(price_decimals(0.00000356, "crypto"), 8)
        self.assertEqual(price_decimals(1.15, "forex"), 5)


class SignalTrackingTests(unittest.TestCase):
    def _candles(self, highs, lows, start="2026-09-14 12:00"):
        idx = pd.date_range(start, periods=len(highs), freq="15min", tz="UTC")
        closes = [(h + l) / 2 for h, l in zip(highs, lows)]
        return pd.DataFrame({"open": closes, "high": highs, "low": lows, "close": closes, "volume": 0.0}, index=idx)

    def test_win_and_loss(self):
        sig = {"ts": "2026-09-14T09:00:00-03:00", "side": "COMPRA", "entry": 100.0, "stop": 99.0, "target2": 102.0}
        win = judge_signal(sig, self._candles([100.5, 101.0, 102.5], [99.6, 99.8, 100.9]), 900)
        self.assertEqual(win["status"], "win")
        self.assertAlmostEqual(win["result_r"], 2.0)
        loss = judge_signal(sig, self._candles([100.5, 100.2, 101.0], [99.5, 98.9, 99.0]), 900)
        self.assertEqual(loss["status"], "loss")
        pending = judge_signal(sig, self._candles([100.5], [99.5]), 900)
        self.assertIsNone(pending)


class ContextPayloadTests(unittest.TestCase):
    def test_payload_has_context_and_calibration(self):
        snap = make_snapshot(symbol="EURUSD=X", timeframe="15m", n=900, seed=12, volume=False)
        htf = make_snapshot(symbol="EURUSD=X", timeframe="1h", n=400, seed=13, freq="1h", volume=False)
        result = analyze(snap, DEFAULT_SETTINGS, now=snap.candles.index[-1].to_pydatetime(), htf_snap=htf)
        ctx = result["context"]
        self.assertIsNotNone(ctx["htf"])
        self.assertIsNotNone(ctx["session"])
        self.assertIn(ctx["reliability"]["level"], {"ALTA", "MÉDIA", "BAIXA", "INDEFINIDA"})
        self.assertIn("pips", result["plan"])
        labels = [c["label"] for s in result["setups"] for c in s["conditions"]]
        self.assertTrue(any("Tempo maior" in label for label in labels))
        self.assertTrue(any("Londres" in label for label in labels))
        if result["signal"]["action"] == "ENTRAR_AGORA":
            blocked_ids = {s["id"] for s in result["setups"] if s["blocked"]}
            self.assertNotIn(result["signal"]["setup_id"], blocked_ids)


if __name__ == "__main__":
    unittest.main()
