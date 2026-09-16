"""MetaTrader 5 simulado (sem terminal real): conexão sem travar, nome do ativo com sufixo da corretora, fuso do
servidor, boleta com lotes reais, candles semanais/mensais da corretora e zonas desenhadas no MT5."""

from __future__ import annotations

import tempfile
import threading
import time
import types
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd

from app import market_data, mt5_source, ticket_mt5, user_zones, zones
from app.indicators import add_indicators
from tests.helpers import make_candles


def _info(name, **kw):
    base = dict(name=name, visible=True, trade_mode=4, time=0, digits=2, point=0.01, trade_tick_size=0.01,
                trade_tick_value=1.0, trade_tick_value_loss=1.0, trade_tick_value_profit=1.0, trade_contract_size=100.0,
                volume_min=0.01, volume_max=100.0, volume_step=0.01, trade_stops_level=20, spread=25,
                description="Gold vs US Dollar")
    base.update(kw)
    return types.SimpleNamespace(**base)


class FakeMT5(types.SimpleNamespace):
    TIMEFRAME_M15, TIMEFRAME_W1, TIMEFRAME_MN1 = 15, 32769, 49153

    def __init__(self, server_offset=10800, fresh=True):
        super().__init__()
        self.server_offset = server_offset
        quote_time = int(time.time()) + server_offset if fresh else int(time.time()) - 3 * 86400
        self.symbols = [_info("XAUUSD", trade_mode=0, visible=False, time=quote_time),  # desabilitado nesta conta
                        _info("XAUUSD.m", time=quote_time), _info("EURUSDm", digits=5, point=0.00001, time=quote_time)]
        self.selected = []

    def initialize(self, **kwargs):
        return True

    def last_error(self):
        return (1, "ok")

    def terminal_info(self):
        return types.SimpleNamespace(name="MetaTrader 5", company="Broker Teste")

    def account_info(self):
        return types.SimpleNamespace(company="Broker Teste", server="BrokerTeste-Live", currency="USD", balance=1000.0)

    def symbols_get(self):
        return tuple(self.symbols)

    def symbol_info(self, name):
        return next((s for s in self.symbols if s.name == name), None)

    def symbol_select(self, name, enable):
        self.selected.append(name)
        return True

    def symbol_info_tick(self, name):
        return types.SimpleNamespace(time=int(time.time()) + self.server_offset, bid=4300.00, ask=4300.25)

    def copy_rates_from_pos(self, name, tf, start, count):
        now = (int(time.time()) // 900) * 900 + self.server_offset
        times = np.arange(now - (count - 1) * 900, now + 1, 900)
        dtype = [("time", "i8"), ("open", "f8"), ("high", "f8"), ("low", "f8"), ("close", "f8"),
                 ("tick_volume", "i8"), ("spread", "i4"), ("real_volume", "i8")]
        rates = np.zeros(len(times), dtype=dtype)
        rates["time"], rates["open"], rates["close"] = times, 4300.0, 4301.0
        rates["high"], rates["low"], rates["tick_volume"] = 4302.0, 4299.0, 50
        return rates


class HangingMT5(FakeMT5):
    """Terminal aberto com diálogo na tela: initialize demora muito ("IPC timeout")."""

    def initialize(self, **kwargs):
        time.sleep(3)
        return False

    def last_error(self):
        return (-10005, "IPC timeout")


def _reset():
    mt5_source._state.update(connected=False, connecting=False, last_try=0.0, offset=None, offset_source=None,
                             offset_at=0.0, terminal=None, error=None, failures=0, terminal_running=None, checked_at=0.0,
                             attempts=0, max_bars=None)
    mt5_source._symbol_cache.clear()
    mt5_source._symbol_list.update(names=[], at=0.0)
    mt5_source._overrides.clear()


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        _reset()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patch = mock.patch.object(mt5_source, "OFFSET_FILE", Path(self.tmp.name) / "fuso.json")
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(_reset)

    def _connect(self, fake):
        with mock.patch.object(mt5_source, "terminal_running", return_value=True), \
                mock.patch.object(mt5_source, "probe", return_value=(True, 1, "Success")):
            self.assertTrue(mt5_source.connect_now())

    def test_reads_never_wait_for_a_hanging_terminal(self):
        with mock.patch.object(mt5_source, "mt5", HangingMT5()), \
                mock.patch.object(mt5_source, "terminal_running", return_value=True), \
                mock.patch.object(mt5_source, "probe", return_value=(False, -10005, "IPC timeout")), \
                mock.patch.object(mt5_source, "request_connect"):
            started = time.time()
            self.assertIsNone(mt5_source.fetch("GC=F", "15m", 100))
            self.assertFalse(mt5_source.status()["connected"])
            self.assertFalse(mt5_source.connect_now())  # o teste em outro processo falhou: nem chama initialize aqui
            self.assertLess(time.time() - started, 0.5)
            self.assertIn("não respondeu", mt5_source._state["error"])

    def test_closed_terminal_does_not_spin_the_background_thread(self):
        calls = []
        with mock.patch.object(mt5_source, "mt5", FakeMT5()), \
                mock.patch.object(mt5_source, "terminal_running",
                                  side_effect=lambda *a, **k: calls.append(1) or mt5_source._state.update(terminal_running=False)):
            mt5_source.request_connect(force=True)
            time.sleep(0.6)
        self.assertLessEqual(len(calls), 2)  # antes da correção eram milhares de voltas por segundo

    def test_no_terminal_means_no_initialize(self):
        fake = FakeMT5()
        fake.initialize = mock.Mock(return_value=True)
        with mock.patch.object(mt5_source, "mt5", fake), mock.patch.object(mt5_source, "terminal_running", return_value=False):
            self.assertFalse(mt5_source.connect_now())
        fake.initialize.assert_not_called()  # nunca abre um MT5 sozinho nem espera por IPC

    def test_fetch_finds_broker_suffix_and_converts_server_time(self):
        fake = FakeMT5(server_offset=10800)
        with mock.patch.object(mt5_source, "mt5", fake):
            self._connect(fake)
            got = mt5_source.fetch("GC=F", "15m", 900)
            status = mt5_source.status()
        df, source = got
        self.assertIn("XAUUSD.m", source)  # o XAUUSD sem sufixo está desabilitado nesta conta
        self.assertIn("XAUUSD.m", fake.selected)
        self.assertEqual(len(df), 900)
        self.assertLess(abs(df.index[-1].timestamp() - time.time()), 1800)  # sem o fuso estaria 3h no futuro
        self.assertEqual((status["server_offset_hours"], status["offset_source"]), (3.0, "medido agora"))
        self.assertTrue(json_saved(mt5_source.OFFSET_FILE, "BrokerTeste-Live", 10800))

    def test_closed_market_uses_saved_offset_adjusted_for_dst(self):
        mt5_source.OFFSET_FILE.write_text('{"BrokerTeste-Live": {"offset": 7200, "measured_at": "2026-01-10T12:00:00+00:00"}}',
                                          encoding="utf-8")
        fake = FakeMT5(server_offset=10800, fresh=False)  # fim de semana: nenhuma cotação recente
        with mock.patch.object(mt5_source, "mt5", fake):
            self._connect(fake)
        self.assertEqual(mt5_source._state["offset"], 10800)  # inverno (+2) medido em janeiro → verão (+3) agora
        self.assertIn("horário de verão", mt5_source._state["offset_source"])

    def test_estimated_offset_conventions(self):
        sept, jan = datetime(2026, 9, 16, tzinfo=timezone.utc), datetime(2026, 1, 16, tzinfo=timezone.utc)
        self.assertEqual(mt5_source.estimated_offset("XP Investimentos CCTVM", "XPMT5-PRD", sept), -10800)
        self.assertEqual(mt5_source.estimated_offset("Some Forex Ltd", "Some-Live", sept), 10800)
        self.assertEqual(mt5_source.estimated_offset("Some Forex Ltd", "Some-Live", jan), 7200)
        self.assertIsNone(mt5_source.measure_offset(time.time() - 3 * 86400 - 777, time.time()))
        self.assertEqual(mt5_source.measure_offset(1_800_000_000 + 10800 + 20, 1_800_000_000), 10800)

    def test_history_uses_the_offset_of_each_season(self):
        # Hantec: pausa diária do ouro às 00h do servidor o ano todo = UTC+3 no verão americano, UTC+2 no inverno.
        summer = int(pd.Timestamp("2026-07-15 00:00", tz="UTC").timestamp())  # horário do servidor lido como UTC
        winter = int(pd.Timestamp("2026-01-15 00:00", tz="UTC").timestamp())
        idx = mt5_source.server_to_utc([winter, summer], 10800, ny_dst=True)
        self.assertEqual(idx[0], pd.Timestamp("2026-01-14 22:00", tz="UTC"))
        self.assertEqual(idx[1], pd.Timestamp("2026-07-14 21:00", tz="UTC"))
        fixed = mt5_source.server_to_utc([winter], 10800, ny_dst=False)
        self.assertEqual(fixed[0], pd.Timestamp("2026-01-14 21:00", tz="UTC"))
        sept = datetime(2026, 9, 16, tzinfo=timezone.utc)
        self.assertTrue(mt5_source._follows_ny_dst(10800, "Hantec Markets Ltd", "HantecMarketsMU-MT5", sept))
        self.assertFalse(mt5_source._follows_ny_dst(7200, "Fixed GMT+2 Ltd", "Fixed-Live", sept))
        self.assertFalse(mt5_source._follows_ny_dst(-10800, "XP Investimentos", "XPMT5", sept))

    def test_wait_ready_waits_only_for_an_attempt_in_progress(self):
        fake = FakeMT5()

        def slow_probe():
            time.sleep(0.4)
            return True, 1, "Success"

        with mock.patch.object(mt5_source, "mt5", fake), \
                mock.patch.object(mt5_source, "terminal_running", return_value=True), \
                mock.patch.object(mt5_source, "probe", side_effect=slow_probe), \
                mock.patch.object(mt5_source, "request_connect"):
            worker = threading.Thread(target=mt5_source.connect_now)
            worker.start()
            time.sleep(0.05)
            self.assertTrue(mt5_source.wait_ready(5))  # AURUM recém-ligado: espera a conexão em vez de cair no Yahoo
            worker.join()
        _reset()
        with mock.patch.object(mt5_source, "mt5", fake), mock.patch.object(mt5_source, "request_connect"):
            mt5_source._state.update(attempts=3, failures=3, connecting=False)  # tentativas falharam, em intervalo
            started = time.time()
            self.assertFalse(mt5_source.wait_ready(5))
            self.assertLess(time.time() - started, 0.2)  # não fica esperando à toa

    def test_open_terminal_keeps_broker_candles_instead_of_yahoo(self):
        from app.config import TIMEFRAMES
        key = ("GC=F", "15m")
        broker = market_data.Snapshot("GC=F", TIMEFRAMES["15m"], make_candles(n=120), "Ouro", time.time() - 3600,
                                      "MetaTrader 5 · Broker Teste (XAUUSD, tempo real)")
        market_data._cache[key] = broker
        self.addCleanup(market_data._cache.pop, key, None)
        yahoo = mock.Mock(side_effect=AssertionError("não deveria usar o Yahoo"))
        with mock.patch.object(market_data, "USE_MT5", True), mock.patch.object(mt5_source, "mt5", FakeMT5()), \
                mock.patch.object(mt5_source, "wait_ready", return_value=False), \
                mock.patch.object(mt5_source, "expected", return_value=True), \
                mock.patch.object(market_data, "_fetch_yahoo", yahoo):
            got = market_data.get_candles("GC=F", "15m")
        self.assertIs(got, broker)
        self.assertEqual(got.provider, "mt5")

    def test_without_package_everything_falls_back(self):
        with mock.patch.object(mt5_source, "mt5", None):
            self.assertFalse(mt5_source.installed())
            self.assertIsNone(mt5_source.fetch("GC=F", "15m", 100))
            self.assertFalse(mt5_source.status()["connected"])


def json_saved(path: Path, server: str, offset: int) -> bool:
    import json
    return json.loads(path.read_text(encoding="utf-8"))[server]["offset"] == offset


class SymbolTests(unittest.TestCase):
    def setUp(self):
        _reset()
        self.addCleanup(_reset)

    def test_pick_symbol_prefers_tradeable_then_exact(self):
        infos = [_info("XAUUSD", trade_mode=0), _info("XAUUSD.m"), _info("XAUUSDX2"), _info("XAUEUR")]
        self.assertEqual(mt5_source.pick_symbol(["XAUUSD", "GOLD"], infos), "XAUUSD.m")
        self.assertEqual(mt5_source.pick_symbol(["XAUUSD"], [_info("XAUUSD"), _info("XAUUSD.m")]), "XAUUSD")
        self.assertEqual(mt5_source.pick_symbol(["EURUSD"], [_info("EURUSDm"), _info("EURUSD.pro")]), "EURUSDm")
        self.assertIsNone(mt5_source.pick_symbol(["XAUUSD"], [_info("XAUUSDX2"), _info("XAUEUR")]))

    def test_manual_mapping(self):
        mt5_source.set_overrides("XAUUSD=XAUUSD.pro; EURUSD=X=EURUSDm, lixo")
        self.assertEqual(mt5_source._overrides, {"GC=F": "XAUUSD.pro", "EURUSD=X": "EURUSDm"})

    def test_crypto_stays_on_binance(self):
        with mock.patch.object(market_data, "USE_MT5", True), mock.patch.object(mt5_source, "mt5", object()):
            self.assertTrue(market_data.mt5_applies("GC=F"))
            self.assertFalse(market_data.mt5_applies("BTC-USD"))

    def test_candidates(self):
        self.assertIn("WDO$N", mt5_source.candidates("WDOFUT", date(2026, 9, 14)))
        self.assertEqual(mt5_source.candidates("PETR4.SA"), ["PETR4"])
        self.assertEqual(mt5_source.candidates("GC=F")[0], "XAUUSD")


SPEC = {"name": "XAUUSD.m", "description": "Gold", "digits": 2, "point": 0.01, "tick_size": 0.01, "tick_value_loss": 1.0,
        "tick_value_profit": 1.0, "contract_size": 100.0, "volume_min": 0.01, "volume_max": 100.0, "volume_step": 0.01,
        "stops_level": 20, "spread_points": 25, "bid": 4300.00, "ask": 4300.25, "trade_allowed": True,
        "account_currency": "USD", "balance": 5000.0, "company": "Broker Teste"}


def _plan(entry, stop, t1, t2, side="COMPRA"):
    return {"side": side, "entry": entry, "stop": stop, "target1": t1, "target2": t2, "capital": 10000, "risk_pct": 1}


class Mt5TicketTests(unittest.TestCase):
    signal = {"action": "ENTRAR_AGORA", "window": {"label": "Entre até 10:15"}}

    def test_lots_from_real_tick_value_and_ask_price(self):
        t = ticket_mt5.build(_plan(4300.0, 4290.0, 4310.0, 4320.0), self.signal, None, SPEC, "15m")
        # risco 1% de US$ 5.000 = US$ 50; stop a US$ 10 = 1.000 ticks × US$ 1 = US$ 1.000 por lote → 0,05 lote
        self.assertEqual(t["quantity"], 0.05)
        self.assertEqual(t["entry"], 4300.25)  # compra executa no ASK
        self.assertEqual(t["stop"], 4290.25)  # distância do plano mantida a partir do preço real
        self.assertEqual(t["target2"], 4320.25)
        self.assertAlmostEqual(t["risk_money"], 50.0)
        self.assertEqual(t["currency_symbol"], "US$")
        self.assertIn("XAUUSD.m", t["text"])
        fields = {f["label"]: f.get("value", f.get("key")) for f in t["xp_orders"][0]["fields"]}
        self.assertEqual(fields["Volume (lotes)"], "quantity")

    def test_warns_when_market_moved_away_from_the_analysis(self):
        moved = ticket_mt5.build(_plan(4250.0, 4240.0, 4260.0, 4270.0), {"action": "PREPARE_SE"}, None, SPEC, "15m")
        self.assertTrue(any("longe do preço analisado" in w for w in moved["warnings"]))
        near = ticket_mt5.build(_plan(4300.0, 4290.0, 4310.0, 4320.0), self.signal, None, SPEC, "15m")
        self.assertFalse(any("longe do preço analisado" in w for w in near["warnings"]))

    def test_sell_uses_bid_and_warns_about_min_lot_and_stops_level(self):
        tight = ticket_mt5.build(_plan(4300.0, 4300.1, 4299.9, 4299.8, "VENDA"), self.signal, None,
                                 {**SPEC, "balance": 10.0}, "15m")
        self.assertEqual(tight["entry"], 4300.00)
        self.assertTrue(any("distância mínima" in w for w in tight["warnings"]))
        self.assertTrue(any("spread" in w.lower() for w in tight["warnings"]))
        big = ticket_mt5.build(_plan(4300.0, 4250.0, 4350.0, 4400.0), self.signal, None, {**SPEC, "balance": 100.0}, "15m")
        self.assertEqual(big["quantity"], 0.01)
        self.assertTrue(any("lote mínimo" in w for w in big["warnings"]))


class SourceConsistencyTests(unittest.TestCase):
    """Uma análise nunca mistura o XAUUSD da corretora com o GC=F do Yahoo."""

    def setUp(self):
        _reset()
        self.addCleanup(_reset)

    def _snap(self, source, tf="15m"):
        from app.config import TIMEFRAMES
        return market_data.Snapshot("GC=F", TIMEFRAMES[tf], make_candles(n=120), "Ouro", time.time(), source)

    def test_mt5_ticket_only_with_mt5_candles(self):
        from app import pipeline
        from app.ticket import build_ticket
        mt5_snap, yahoo_snap = self._snap("MetaTrader 5 · X (XAUUSD, tempo real)"), self._snap("Yahoo Finance")
        with mock.patch.object(market_data, "USE_MT5", True), mock.patch.object(mt5_source, "mt5", FakeMT5()), \
                mock.patch.object(mt5_source, "symbol_spec", return_value=SPEC):
            self.assertEqual(pipeline.exchange_for("GC=F", mt5_snap), {"mt5": SPEC})
            waiting = pipeline.exchange_for("GC=F", yahoo_snap)
            self.assertIn("mt5_waiting", waiting)
            self.assertIsNone(pipeline.exchange_for("PETR4.SA", yahoo_snap))  # B3 sem MT5 continua com a boleta da XP
            self.assertEqual(pipeline.zone_inputs("GC=F", yahoo_snap), {"native": None, "user": []})
        ticket = build_ticket("GC=F", "futures", _plan(4300.0, 4290.0, 4310.0, 4320.0), {"action": "AGUARDE"},
                              "Yahoo Finance", date(2026, 9, 16), **waiting)
        self.assertFalse(ticket["available"])
        self.assertIn("MetaTrader 5", ticket["reason"])

    def test_context_from_another_source_is_dropped(self):
        from app import pipeline

        def yahoo(symbol, tf, force=False):
            return self._snap("Yahoo Finance", tf)

        with mock.patch.object(pipeline, "get_candles", side_effect=yahoo):
            self.assertEqual(pipeline.context_snaps("GC=F", "15m", self._snap("MetaTrader 5 · X (XAUUSD)")), (None, None))
            htf, _ = pipeline.context_snaps("BTC-USD", "15m", self._snap("Binance (tempo real)"))
        self.assertIsNotNone(htf)  # Binance x Yahoo no cripto: mesmo preço, pode usar


class NativeAndUserZonesTests(unittest.TestCase):
    def test_native_weekly_candle_is_known_only_when_next_one_starts(self):
        idx = pd.date_range("2026-01-04 22:00", periods=10, freq="7D", tz="UTC")  # semanas do servidor (domingo)
        weekly = pd.DataFrame({"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5}, index=idx)
        frame = zones._periods(pd.DataFrame(), "W", weekly)
        self.assertEqual(frame["end"].iloc[0], idx[1])
        self.assertEqual(frame["end"].iloc[-1], idx[-1] + pd.DateOffset(weeks=1))

    def test_reads_drawn_zones_and_applies_them_only_after_first_seen(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "zonas.csv"
        path.write_text("#AURUM-ZONAS,Broker Teste,BrokerTeste-Live\r\n"
                        "XAUUSD.m,retangulo,Resistência semanal,4320.50,4310.00,Resistencia semanal\r\n"
                        "XAUUSD.m,retangulo,copia,4310.00,4320.50,Resistencia semanal\r\n"
                        "XAUUSD.m,linha,Suporte,4250.00,4250.00,\r\n"
                        "EURUSDm,linha,x,1.1000,1.1000,\r\n", encoding="latin-1")
        seen_at = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)
        with mock.patch.object(user_zones, "SEEN_FILE", Path(tmp.name) / "vistas.json"):
            gold = user_zones.for_symbol("GC=F", "XAUUSD.m", path, seen_at)
            later = user_zones.for_symbol("GC=F", None, path, datetime(2026, 9, 17, tzinfo=timezone.utc))
        self.assertEqual(len(gold), 2)  # retângulo repetido em dois gráficos conta uma vez
        self.assertEqual((gold[0].low, gold[0].high), (4310.0, 4320.5))
        self.assertIn("Resistencia semanal", gold[0].label)
        self.assertEqual(later[0].since, pd.Timestamp(seen_at))  # continua valendo desde a primeira vez que foi vista

        # Uma zona desenhada só existe para candles que fecharam depois de vista.
        daily = make_candles(n=600, seed=11, freq="1D", start="2024-06-01 00:00")
        m15 = add_indicators(make_candles(n=400, seed=4, freq="15min", start="2026-01-10 00:00"))
        mid = float(m15["close"].iloc[-1])
        since = m15.index[300]
        drawn = [zones.UserZone(mid - 0.05, mid + 0.05, since)]
        feat = zones.zone_features(m15, 900, daily, None, drawn)
        labels_before = set(zones.zone_features(m15.iloc[:290], 900, daily, None, drawn).attrs["zones_now"].label)
        self.assertNotIn("Sua zona (MT5)", labels_before)
        self.assertIn("Sua zona (MT5)", feat.attrs["zones_now"].label)
        self.assertEqual(float(feat.attrs["zones_now"].weight[-1]), float(zones.USER_WEIGHT))


if __name__ == "__main__":
    unittest.main()
