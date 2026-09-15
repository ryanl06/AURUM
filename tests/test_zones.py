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
from app.strategy import PULLBACK_SETUP_IDS, ZONE_SETUP_IDS, build_pullback_setups, build_zone_setups
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
        result = analyze(snap, {**DEFAULT_SETTINGS, "strategy_mode": "zonas"}, now=now, daily_snap=dsnap)
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
        result = analyze(m15, {**DEFAULT_SETTINGS, "strategy_mode": "zonas"}, daily_snap=daily)
        self.assertTrue(result["context"]["strategy"]["zones_active"])
        self.assertTrue({s["id"] for s in result["setups"]} <= set(ZONE_SETUP_IDS))
        default = analyze(m15, {**DEFAULT_SETTINGS}, daily_snap=daily)  # padrão: rompimento + pullback
        self.assertEqual({s["id"] for s in default["setups"]}, set(PULLBACK_SETUP_IDS))
        self.assertTrue(result["context"]["zones"]["supports"] or result["context"]["zones"]["resistances"])
        classic = analyze(m15, {**DEFAULT_SETTINGS, "strategy_mode": "indicadores"}, daily_snap=daily)
        self.assertFalse(classic["context"]["strategy"]["zones_active"])
        fallback = analyze(m15, {**DEFAULT_SETTINGS})  # sem diário: volta para os indicadores
        self.assertFalse(fallback["context"]["strategy"]["zones_active"])


def _gold_breakout(bars: list[tuple[float, float, float, float]], zone=(99.0, 100.0)):
    """Candles M15 sintéticos (o, h, l, c) com uma resistência principal pronta em `zone`."""
    idx = pd.date_range("2026-03-02 08:00", periods=len(bars), freq="15min", tz="UTC")
    df = pd.DataFrame(bars, columns=["open", "high", "low", "close"], index=idx)
    df["volume"] = 100.0
    df["atr"] = 1.0
    lo, hi = zone
    pc = df["close"].shift(1)
    feat = pd.DataFrame(index=idx)
    feat["break_up"] = (pc <= hi) & (df["open"] <= hi) & (df["close"] > hi + 0.1)
    feat["break_down"] = False
    for col, val in (("bup_low", lo), ("bup_high", hi), ("bdn_low", np.nan), ("bdn_high", np.nan)):
        feat[col] = np.where(feat["break_up"], val, np.nan) if col.startswith("bup") else val
    feat["bup_label"] = np.where(feat["break_up"], "Semanal+Diário", None)
    feat["bdn_label"] = None
    feat["next_res"], feat["next_sup"] = np.nan, np.nan
    return df, feat


# preço abaixo da zona → rompe → sobe até 104 → corrige com fundo em 101,5 → rompe o topo de 104
PATTERN = ([(98.0, 98.4, 97.8, 98.2)] * 6 + [(98.2, 101.2, 98.1, 101.0), (101.0, 102.5, 100.8, 102.3),
           (102.3, 104.0, 102.1, 103.8), (103.8, 103.9, 102.6, 102.8), (102.8, 102.9, 101.8, 102.0),
           (102.0, 102.1, 101.5, 101.7), (101.7, 102.6, 101.6, 102.5), (102.5, 103.2, 102.3, 103.0),
           (103.0, 103.6, 102.9, 103.5), (103.5, 104.8, 103.4, 104.6)])


class PullbackTests(unittest.TestCase):
    def test_break_correction_bottom_and_new_break_fires_once(self):
        df, feat = _gold_breakout(PATTERN)
        pb = zones.pullback_breakouts(df, feat, zones.big_candle_limit("GC=F", df["atr"], 1500))
        last = len(df) - 1
        self.assertTrue(pb["pbl_broken"].iat[6])
        self.assertFalse(pb["pbl_corrected"].iat[10])  # fundo em 11 ainda não confirmado
        self.assertTrue(pb["pbl_corrected"].iat[13])  # confirmado 2 candles depois do fundo
        self.assertAlmostEqual(pb["pbl_trigger"].iat[last], 104.0)
        self.assertLess(pb["pbl_stop"].iat[last], 101.5)  # stop abaixo do fundo da correção
        # preços sintéticos perto de 100: stop de ~3,3% precisa de limite maior só neste teste
        buy = next(s for s in build_pullback_setups(df, pb, feat, max_stop_pct=10) if s.id == "compra_pullback")
        fired = buy.all_true()
        self.assertEqual(list(np.flatnonzero(fired.to_numpy())), [last])

    def test_giant_entry_candle_waits_for_new_correction(self):
        bars = PATTERN[:-1] + [(103.5, 121.0, 103.4, 120.0)]  # vela de US$ 17,60 (> 1.500 pontos)
        df, feat = _gold_breakout(bars)
        pb = zones.pullback_breakouts(df, feat, zones.big_candle_limit("GC=F", df["atr"], 1500))
        self.assertFalse(bool(pb["candle_ok"].iat[-1]))
        buy = next(s for s in build_pullback_setups(df, pb, feat, max_stop_pct=50) if s.id == "compra_pullback")
        self.assertTrue(pb["pbl_crossed"].iat[-1])  # rompeu o topo…
        self.assertFalse(buy.all_true().any())  # …mas com vela gigante: sem entrada
        more = bars + [(120.0, 120.5, 118.0, 118.5)]  # candle seguinte ainda aguarda nova correção
        df2, feat2 = _gold_breakout(more)
        pb2 = zones.pullback_breakouts(df2, feat2, zones.big_candle_limit("GC=F", df2["atr"], 1500))
        self.assertTrue(pb2["pbl_broken"].iat[-1])
        self.assertFalse(pb2["pbl_corrected"].iat[-1])

    def test_falling_back_into_zone_cancels_breakout(self):
        bars = PATTERN[:9] + [(103.8, 103.9, 98.0, 98.3)] + [(98.3, 104.9, 98.2, 104.8)]
        df, feat = _gold_breakout(bars)
        pb = zones.pullback_breakouts(df, feat, zones.big_candle_limit("GC=F", df["atr"], 1500))
        self.assertFalse(pb["pbl_broken"].iat[9])
        self.assertFalse(pb["pbl_crossed"].any())

    def test_detector_is_causal(self):
        df, feat = _gold_breakout(PATTERN)
        limit = zones.big_candle_limit("GC=F", df["atr"], 1500)
        full = zones.pullback_breakouts(df, feat, limit)
        for cut in range(8, len(df)):
            part = zones.pullback_breakouts(df.iloc[:cut], feat.iloc[:cut], limit.iloc[:cut])
            pd.testing.assert_frame_equal(full.iloc[:cut].astype(str), part.astype(str))

    def test_big_candle_limit_is_points_on_gold_and_atr_elsewhere(self):
        atr = pd.Series([2.0, 2.0])
        self.assertEqual(list(zones.big_candle_limit("GC=F", atr, 1500)), [15.0, 15.0])
        self.assertEqual(list(zones.big_candle_limit("BTC-USD", atr, 1500)), [6.0, 6.0])


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

    def test_live_file_updates_last_candles(self):
        _write_mt4(self.folder, "XAUUSD_M15.csv", offset=0, rows=120)
        live = self.folder / "XAUUSD_M15_live.csv"
        last = 1_757_000_000 + 119 * 900
        live.write_text(f"#AURUM,XAUUSD,M15,0,1,2,Hantec Markets\r\n{last},1.2,9.9,1.0,9.5,5\r\n{last + 900},9.5,9.8,9.1,9.7,3\r\n",
                        encoding="latin-1")
        df, _ = mt4_source.fetch("GC=F", "15m", folder=self.folder)
        self.assertEqual(len(df), 121)  # candle novo acrescentado
        self.assertEqual(df["close"].iloc[-2], 9.5)  # candle do histórico atualizado pelo arquivo ao vivo
        self.assertEqual(mt4_source.status(self.folder)["symbols"], ["XAUUSD"])

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
