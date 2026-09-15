"""Zonas do mensal/semanal/diário com entrada no M15 e leitura do MetaTrader 4 (sem internet)."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from app import market_data, mt4_source, zones
from app.analysis import analyze
from app.backtest import structural_distance
from app.config import DEFAULT_SETTINGS, TIMEFRAMES
from app.indicators import add_indicators
from app.market_data import Snapshot
from app.strategy import ZONE_SETUP_IDS, build_zone_setups
from tests.helpers import make_candles


def _daily(n=900, seed=11):
    return make_candles(n=n, seed=seed, freq="1D", start="2023-01-02 00:00")


class ZoneTests(unittest.TestCase):
    def test_levels_are_known_only_after_confirmation(self):
        levels = zones.htf_levels(_daily())
        self.assertFalse(levels.empty)
        self.assertTrue((levels["known_at"] > levels["at"]).all())
        self.assertEqual(set(levels["name"]), {"Mensal", "Semanal", "Diário"})

    def test_zones_are_capped_in_width_and_weighted(self):
        daily = _daily()
        atr = float(zones.daily_atr(daily).dropna().iloc[-1])
        z = zones.zones_at(zones.htf_levels(daily), daily.index[-1] + pd.Timedelta(days=1), atr)
        self.assertGreater(len(z), 0)
        self.assertTrue(((z.high - z.low) <= atr * (zones.MAX_WIDTH_ATR + 2 * zones.PAD_ATR) + 1e-9).all())
        self.assertTrue((z.weight >= zones.MIN_WEIGHT).all())

    def test_features_do_not_look_ahead(self):
        daily = _daily()
        intraday = add_indicators(make_candles(n=3000, seed=4, freq="15min", start="2025-04-01 00:00"))
        full = zones.zone_features(intraday, 900, daily)
        cut = pd.Timestamp("2025-04-20", tz="UTC")
        # Muda todo o diário depois do corte: nada antes do corte pode mudar.
        changed = daily.copy()
        future = changed.index >= cut
        changed.loc[future, ["open", "high", "low", "close"]] *= 1.37
        partial = zones.zone_features(intraday, 900, changed)
        before = intraday.index + pd.Timedelta(minutes=15) <= cut
        cols = ["sup_low", "res_high", "next_res", "next_sup"]
        pd.testing.assert_frame_equal(full.loc[before, cols], partial.loc[before, cols])
        pd.testing.assert_series_equal(full.loc[before, "sup_reject"], partial.loc[before, "sup_reject"])

    def test_rejection_at_support_fires_covered_entry(self):
        idx = pd.date_range("2024-01-01", periods=400, freq="1D", tz="UTC")
        base = 100 + np.sin(np.arange(400) / 9) * 8  # oscila entre 92 e 108: fundos repetidos perto de 92
        daily = pd.DataFrame({"open": base, "high": base + 1, "low": base - 1, "close": base, "volume": 1000.0}, index=idx)
        m15_idx = pd.date_range(idx[-1] + pd.Timedelta(days=1), periods=200, freq="15min")
        price = np.full(200, 91.8)  # zona de suporte ~90,84–91,17 (fundos mensais, semanais e diários)
        frame = pd.DataFrame({"open": price, "high": price + 0.2, "low": price - 0.2, "close": price, "volume": 500.0},
                             index=m15_idx)
        frame.iloc[150, frame.columns.get_loc("open")] = 91.3
        frame.iloc[150, frame.columns.get_loc("low")] = 90.95  # mergulha no suporte…
        frame.iloc[150, frame.columns.get_loc("close")] = 91.5  # …e fecha logo acima da zona, candle comprador
        df = add_indicators(frame)
        feat = zones.zone_features(df, 900, daily)
        self.assertTrue(feat["sup_reject"].iloc[150], feat.iloc[150][["sup_low", "sup_high", "sup_label"]])
        buy = next(s for s in build_zone_setups(df, feat) if s.id == "compra_suporte")
        self.assertTrue(bool(buy.all_true().iloc[150]))
        self.assertLess(buy.stop_price.iloc[150], feat["sup_low"].iloc[150])  # stop atrás da zona

    def test_approaching_support_prepares_with_stop_behind_zone(self):
        idx = pd.date_range("2024-01-01", periods=400, freq="1D", tz="UTC")
        base = 100 + np.sin(np.arange(400) / 9) * 8
        daily = pd.DataFrame({"open": base, "high": base + 1, "low": base - 1, "close": base, "volume": 1000.0}, index=idx)
        m15_idx = pd.date_range(idx[-1] + pd.Timedelta(days=1), periods=300, freq="15min")
        rng = np.random.default_rng(3)
        price = 91.32 + rng.normal(0, 0.01, 300)  # logo acima da zona ~90,84–91,17, sem tocar
        frame = pd.DataFrame({"open": price, "high": price + 0.12, "low": price - 0.12, "close": price, "volume": 500.0},
                             index=m15_idx)
        snap = Snapshot("ZZZ-USD", TIMEFRAMES["15m"], frame, "Teste", time.time())
        dsnap = Snapshot("ZZZ-USD", TIMEFRAMES["1d"], daily, "Teste", time.time())
        now = m15_idx[-1].to_pydatetime() + pd.Timedelta(minutes=5)
        result = analyze(snap, {**DEFAULT_SETTINGS}, now=now, daily_snap=dsnap)
        self.assertEqual(result["signal"]["action"], "PREPARE_SE", result["signal"]["headline"])
        self.assertIn("suporte", result["signal"]["headline"])
        self.assertEqual(result["plan"]["stop_by"], "zona")
        self.assertLess(result["plan"]["stop"], 90.84)

    def test_structural_stop_respects_cover_and_max_stop(self):
        self.assertAlmostEqual(structural_distance(100, 99, 0.5, True, 3), 1.0)
        self.assertAlmostEqual(structural_distance(100, 99.9, 1.0, True, 3), 0.5)  # nunca menor que 0,5 ATR
        self.assertIsNone(structural_distance(100, 96, 1.0, True, 3))  # stop não cabe em 3%: entrada descoberta
        self.assertIsNone(structural_distance(100, 101, 1.0, True, 3))  # abriu abaixo do stop

    def test_analysis_uses_zone_strategy_by_default(self):
        daily = Snapshot("EURUSD=X", TIMEFRAMES["1d"], _daily(), "Euro", time.time())
        m15 = Snapshot("EURUSD=X", TIMEFRAMES["15m"],
                       make_candles(n=900, seed=8, freq="15min", start="2025-05-01 00:00", volume=False), "Euro", time.time())
        result = analyze(m15, {**DEFAULT_SETTINGS}, daily_snap=daily)
        self.assertTrue(result["context"]["strategy"]["zones_active"])
        self.assertTrue({s["id"] for s in result["setups"]} <= set(ZONE_SETUP_IDS))
        self.assertTrue(result["context"]["zones"]["supports"] or result["context"]["zones"]["resistances"])
        classic = analyze(m15, {**DEFAULT_SETTINGS, "strategy_mode": "indicadores"}, daily_snap=daily)
        self.assertFalse(classic["context"]["strategy"]["zones_active"])
        fallback = analyze(m15, {**DEFAULT_SETTINGS})  # sem diário: volta para os indicadores
        self.assertFalse(fallback["context"]["strategy"]["zones_active"])


def _write_mt4(folder: Path, name: str, offset: int, rows: int = 120, start: int = 1_757_000_000, step: int = 900) -> Path:
    path = folder / name
    lines = [f"#AURUM,EURUSD,M15,{offset},1,5,Hantec Markets"]
    for i in range(rows):
        p = 1.1 + i * 0.0001
        lines.append(f"{start + i * step},{p:.5f},{p + 0.0005:.5f},{p - 0.0005:.5f},{p + 0.0002:.5f},{100 + i}")
    path.write_text("\r\n".join(lines) + "\r\n", encoding="latin-1")
    return path


class Mt4Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_symbol_mapping(self):
        self.assertEqual(mt4_source.mt4_symbol("EURUSD=X"), "EURUSD")
        self.assertEqual(mt4_source.mt4_symbol("GC=F", ".r"), "XAUUSD.r")
        self.assertIsNone(mt4_source.mt4_symbol("PETR4.SA"))

    def test_reads_server_time_as_utc_and_ignores_stale_files(self):
        path = _write_mt4(self.folder, "EURUSD_M15.csv", offset=3 * 3600)
        got = mt4_source.fetch("EURUSD=X", "15m", folder=self.folder)
        self.assertIsNotNone(got)
        df, source = got
        self.assertEqual(df.index[0], pd.Timestamp(1_757_000_000 - 3 * 3600, unit="s", tz="UTC"))
        self.assertIn("Hantec Markets", source)
        old = time.time() - 3600
        os.utime(path, (old, old))
        self.assertIsNone(mt4_source.fetch("EURUSD=X", "15m", folder=self.folder))  # MT4 fechado
        status = mt4_source.status(self.folder)
        self.assertEqual((status["files"], status["connected"], status["broker"]), (1, False, "Hantec Markets"))

    def test_market_data_prefers_mt4_when_enabled(self):
        _write_mt4(self.folder, "EURUSD_M15.csv", offset=0)
        original = mt4_source.FOLDER
        mt4_source.FOLDER = self.folder
        market_data._cache.pop(("EURUSD=X", "15m"), None)
        try:
            market_data.configure({"use_mt4": "1", "mt4_suffix": ""})
            snap = market_data.get_candles("EURUSD=X", "15m", force=True)
            self.assertIn("MetaTrader 4", snap.source)
            self.assertEqual(len(snap.candles), 120)
        finally:
            mt4_source.FOLDER = original
            market_data.configure({})
            market_data._cache.pop(("EURUSD=X", "15m"), None)


if __name__ == "__main__":
    unittest.main()
