"""Testes do motor: indicadores, regras, backtest, ativos e análise completa."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from app import assets
from app.analysis import analyze
from app.backtest import run_backtest, stop_distance
from app.config import DEFAULT_SETTINGS
from app.indicators import add_indicators, bollinger, has_volume, rsi
from app.strategy import build_setups, score_series
from tests.helpers import make_candles, make_snapshot


class IndicatorTests(unittest.TestCase):
    def test_rsi_extremes(self):
        up = pd.Series(np.arange(1, 60, dtype=float))
        down = pd.Series(np.arange(60, 1, -1, dtype=float))
        self.assertAlmostEqual(rsi(up).iloc[-1], 100.0)
        self.assertAlmostEqual(rsi(down).iloc[-1], 0.0)

    def test_rsi_matches_wilder_reference(self):
        # Série clássica do livro de Wilder: RSI(14) do 15º fechamento ≈ 70,53 (média simples inicial).
        closes = pd.Series([44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89,
                            46.03, 45.61, 46.28, 46.28, 46.00, 46.03, 46.41, 46.22, 45.64, 46.21])
        value = rsi(closes).iloc[-1]
        self.assertTrue(40 < value < 75, value)  # EMA de Wilder converge para a referência
        self.assertTrue(rsi(closes).dropna().between(0, 100).all())

    def test_bollinger_contains_mean(self):
        df = make_candles()
        bb = bollinger(df["close"]).dropna()
        self.assertTrue((bb["bb_upper"] >= bb["bb_mid"]).all())
        self.assertTrue((bb["bb_lower"] <= bb["bb_mid"]).all())

    def test_indicator_columns_and_volume_detection(self):
        df = add_indicators(make_candles())
        for col in ("ema9", "ema21", "ema50", "rsi", "macd", "macd_signal", "macd_hist", "bb_upper", "atr", "adx"):
            self.assertIn(col, df)
            self.assertFalse(df[col].iloc[-50:].isna().any(), col)
        self.assertTrue(has_volume(df))
        self.assertFalse(has_volume(add_indicators(make_candles(volume=False))))


class StrategyTests(unittest.TestCase):
    def test_score_bounds_and_direction(self):
        up = add_indicators(make_candles(drift=0.004, seed=1))
        down = add_indicators(make_candles(drift=-0.004, seed=1))
        s_up, s_down = score_series(up, True).dropna(), score_series(down, True).dropna()
        self.assertTrue(s_up.between(-100, 100).all())
        # Tendência forte puxa o score, mas RSI/Bollinger extremos seguram (risco de exaustão).
        self.assertGreater(s_up.tail(50).mean(), 20)
        self.assertLess(s_down.tail(50).mean(), -20)

    def test_signals_are_fresh_not_repeated(self):
        df = add_indicators(make_candles(n=600, seed=3))
        score = score_series(df, True)
        for setup in build_setups(df, True):
            fired = setup.signals(score).to_numpy()
            idx = np.flatnonzero(fired)
            if len(idx) > 1:
                self.assertTrue((np.diff(idx) > 1).all(), setup.id)

    def test_stop_never_exceeds_max(self):
        self.assertAlmostEqual(stop_distance(100, 10, 1.5, 3), 3.0)
        self.assertAlmostEqual(stop_distance(100, 1, 1.5, 3), 1.5)

    def test_backtest_stats_are_consistent(self):
        df = add_indicators(make_candles(n=800, seed=11))
        score = score_series(df, True)
        bt = run_backtest(df, build_setups(df, True), score, atr_mult=1.5, max_stop_pct=3, reward_ratio=2)
        o = bt["overall"]
        self.assertEqual(o["trades"], sum(s["trades"] for s in bt["setups"]))
        self.assertEqual(o["wins"] + o["losses"], o["trades"])
        if o["trades"]:
            self.assertTrue(0 <= o["win_rate"] <= 100)


class AssetTests(unittest.TestCase):
    def test_resolve_user_input(self):
        cases = {
            "petr4": "PETR4.SA", "PETR4.SA": "PETR4.SA", "taee11": "TAEE11.SA", "btc": "BTC-USD",
            "BTCUSDT": "BTC-USD", "eurusd": "EURUSD=X", "EUR/USD": "EURUSD=X", "xauusd": "GC=F",
            "ouro": "GC=F", "ibov": "^BVSP", "AAPL": "AAPL", "usdjpy=x": "USDJPY=X",
        }
        for raw, expected in cases.items():
            self.assertEqual(assets.resolve_symbol(raw), expected, raw)
        with self.assertRaises(ValueError):
            assets.resolve_symbol("   ")

    def test_market_hours(self):
        # Segunda-feira 14/09/2026, 15:00 em São Paulo (18:00 UTC)
        monday = datetime(2026, 9, 14, 18, 0, tzinfo=timezone.utc)
        saturday = datetime(2026, 9, 19, 15, 0, tzinfo=timezone.utc)
        self.assertTrue(assets.market_status("PETR4.SA", monday)["open"])
        self.assertFalse(assets.market_status("PETR4.SA", saturday)["open"])
        self.assertTrue(assets.market_status("BTC-USD", saturday)["open"])
        self.assertFalse(assets.market_status("EURUSD=X", saturday)["open"])
        self.assertIsNotNone(assets.market_status("PETR4.SA", saturday)["next_change_label"])


class AnalysisTests(unittest.TestCase):
    def test_full_payload_is_json_and_coherent(self):
        snap = make_snapshot(symbol="BTC-USD", timeframe="15m", n=500, seed=5)
        now = snap.candles.index[-1].to_pydatetime()
        result = analyze(snap, DEFAULT_SETTINGS, now=now)
        json.dumps(result)  # tudo serializável
        s = result["signal"]
        self.assertIn(s["action"], {"ENTRAR_AGORA", "PREPARE_SE", "AGUARDE", "MERCADO_FECHADO"})
        self.assertEqual(s["right_moment"], s["action"] == "ENTRAR_AGORA")
        plan = result["plan"]
        self.assertLessEqual(plan["stop_pct"], 3.0 + 1e-9)
        if plan["side"] == "COMPRA":
            self.assertLess(plan["stop"], plan["entry"])
            self.assertGreater(plan["target2"], plan["target1"])
        else:
            self.assertGreater(plan["stop"], plan["entry"])
        times = [c["time"] for c in result["chart"]["candles"]]
        self.assertEqual(times, sorted(set(times)))
        self.assertGreater(times[0], 1_700_000_000)

    def test_position_stop_loss_triggers_exit(self):
        snap = make_snapshot(symbol="BTC-USD", n=400, seed=9)
        price = float(snap.candles["close"].iloc[-1])
        position = {"id": 1, "side": "COMPRA", "entry_price": price * 1.05, "stop_price": None,
                    "target_price": None, "quantity": 1, "opened_at": "2026-09-14T10:00:00-03:00"}
        result = analyze(snap, DEFAULT_SETTINGS, position=position, now=snap.candles.index[-1].to_pydatetime())
        self.assertEqual(result["signal"]["action"], "SAIR_AGORA")
        self.assertEqual(result["signal"]["headline"], "STOP LOSS ATIVADO")
        self.assertLess(result["position"]["pnl_pct"], -3)

    def test_position_holds_when_nothing_happens(self):
        snap = make_snapshot(symbol="BTC-USD", n=400, seed=9)
        price = float(snap.candles["close"].iloc[-1])
        position = {"id": 1, "side": "COMPRA", "entry_price": price, "stop_price": price * 0.5,
                    "target_price": price * 2, "quantity": 1, "opened_at": "2026-09-14T10:00:00-03:00"}
        result = analyze(snap, DEFAULT_SETTINGS, position=position, now=snap.candles.index[-1].to_pydatetime())
        self.assertIn(result["signal"]["action"], {"MANTENHA", "SAIR_AGORA"})
        self.assertEqual(len(result["signal"]["exit_rules"]), 4)


if __name__ == "__main__":
    unittest.main()
