"""Testes de padrões (análogos, candles, estrutura, consenso) e do candle ao vivo."""

from __future__ import annotations

import unittest
from unittest import mock

import numpy as np
import pandas as pd

from app import market_data, patterns
from app.indicators import add_indicators
from app.levels import find_pivots
from tests.helpers import make_candles


class AnalogTests(unittest.TestCase):
    def setUp(self):
        self.df = add_indicators(make_candles(n=1500, seed=31))

    def test_features_never_use_future(self):
        f1 = patterns.feature_matrix(self.df)
        changed = self.df.copy()
        changed.iloc[1000:, changed.columns.get_indexer(["open", "high", "low", "close"])] *= 1.3
        f2 = patterns.feature_matrix(add_indicators(changed[["open", "high", "low", "close", "volume"]]))
        np.testing.assert_allclose(np.nan_to_num(f1[:1000]), np.nan_to_num(f2[:1000]))

    def test_forecast_shape_and_validation(self):
        res = patterns.analog_forecast(self.df, len(self.df) - 1)
        self.assertIsNotNone(res)
        self.assertEqual(len(res["median"]), patterns.PROJECTION)
        self.assertTrue(all(lo <= hi for lo, hi in zip(res["p10"], res["p90"])))
        self.assertTrue(0 <= res["up_rate"] <= 100)
        v = res["validation"]
        self.assertGreater(v["tested"], 50)
        self.assertIn(v["edge"], {"possível", "fraca", "nenhuma"})
        self.assertTrue(0 <= v["range_coverage"] <= 100)
        # Passeio aleatório não pode "ter vantagem real" — se tiver, o critério está frouxo demais.
        self.assertNotEqual(v["edge"], "possível")

    def test_neighbors_exclude_recent_bars(self):
        features = patterns.feature_matrix(self.df)
        i = len(self.df) - 1
        idx, _ = patterns._neighbors(features, i, i - patterns.PROJECTION, patterns.K_NEIGHBORS)
        self.assertTrue((idx < i - patterns.PROJECTION).all())


class CandleAndStructureTests(unittest.TestCase):
    def test_bullish_engulfing_detected(self):
        idx = pd.date_range("2026-09-01", periods=40, freq="1h", tz="UTC")
        close = np.linspace(110, 100, 40)
        df = pd.DataFrame({"open": close + 0.3, "high": close + 0.6, "low": close - 0.6, "close": close, "volume": 1.0}, index=idx)
        df.iloc[-2, df.columns.get_indexer(["open", "close", "high", "low"])] = [100.8, 100.2, 101.0, 100.0]  # candle de baixa
        df.iloc[-1, df.columns.get_indexer(["open", "close", "high", "low"])] = [100.1, 101.2, 101.4, 99.9]  # engole o anterior
        flags = patterns.candle_flags(add_indicators(df))
        self.assertTrue(bool(flags["engolfo_alta"].iat[-1]))
        report = patterns.pattern_report(add_indicators(df), flags, len(df) - 1)
        self.assertEqual(report[0]["name"], "Engolfo de alta")

    def test_uptrend_structure(self):
        x = np.arange(400)
        close = 100 + x * 0.05 + np.sin(x / 6) * 2
        idx = pd.date_range("2026-01-01", periods=400, freq="1h", tz="UTC")
        df = add_indicators(pd.DataFrame({"open": close, "high": close + 0.3, "low": close - 0.3, "close": close, "volume": 1.0}, index=idx))
        structure = patterns.market_structure(df, find_pivots(df), len(df) - 1)
        self.assertEqual(structure["trend"], "alta")

    def test_consensus_counts(self):
        cons = patterns.consensus(60, 58, {"up_rate": 70, "validation": {"edge": "fraca"}}, 1, {"trend": "alta"})
        self.assertEqual(cons["up"], 5)
        self.assertEqual(cons["label"], "Consenso de ALTA")
        mixed = patterns.consensus(-30, 52, {"up_rate": 50, "validation": {}}, 1, {"trend": "lateral"})
        self.assertEqual(mixed["label"], "Sem consenso")


class LiveCandleTests(unittest.TestCase):
    def _minutes(self, start="2026-09-15 13:00", n=37):
        idx = pd.date_range(start, periods=n, freq="1min", tz="UTC")
        close = 100 + np.arange(n) * 0.1
        return pd.DataFrame({"open": close - 0.05, "high": close + 0.2, "low": close - 0.2, "close": close, "volume": 10.0}, index=idx)

    def test_aggregates_current_bar_on_chart_grid(self):
        minutes = self._minutes()
        bar_start = int(pd.Timestamp("2026-09-15 13:00", tz="UTC").value // 10**9)
        with mock.patch.object(market_data, "recent_minutes", return_value=(minutes, "teste")):
            c = market_data.live_candle("EURUSD=X", "15m", bar_start)
        self.assertEqual(c["time"], bar_start + 2 * 900)  # 13:36 cai no candle das 13:30
        part = minutes[minutes.index >= pd.Timestamp("2026-09-15 13:30", tz="UTC")]
        self.assertAlmostEqual(c["open"], part["open"].iloc[0])
        self.assertAlmostEqual(c["high"], part["high"].max())
        self.assertAlmostEqual(c["close"], minutes["close"].iloc[-1])

    def test_daily_bar_keeps_exchange_start(self):
        minutes = self._minutes("2026-09-15 16:00", 30)
        bar_start = int(pd.Timestamp("2026-09-15 03:00", tz="UTC").value // 10**9)  # diário da B3 começa 00h local
        with mock.patch.object(market_data, "recent_minutes", return_value=(minutes, "teste")):
            c = market_data.live_candle("PETR4.SA", "1d", bar_start)
        self.assertEqual(c["time"], bar_start)


if __name__ == "__main__":
    unittest.main()
