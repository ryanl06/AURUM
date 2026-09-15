"""Testes de B3/XP: vencimentos, ticks, boleta e conector MetaTrader 5 (simulado, sem terminal real)."""

from __future__ import annotations

import time
import types
import unittest
from datetime import date
from unittest import mock

import numpy as np

from app import assets, b3, mt5_source
from app.ticket import build_ticket


class ContractTests(unittest.TestCase):
    def test_wdo_rolls_before_first_business_day(self):
        self.assertEqual(b3.current_contract("WDOFUT", date(2026, 9, 14))["code"], "WDOV26")
        self.assertEqual(b3.current_contract("WDOFUT", date(2026, 9, 30))["code"], "WDOX26")
        self.assertEqual(b3.current_contract("WDOFUT", date(2026, 12, 20))["code"], "WDOF27")

    def test_win_even_months_wednesday_near_15(self):
        c = b3.current_contract("WINFUT", date(2026, 9, 14))
        self.assertEqual(c["code"], "WINV26")
        self.assertEqual(date.fromisoformat(c["expiry"]).weekday(), 2)
        self.assertEqual(b3.current_contract("WINFUT", date(2026, 10, 13))["code"], "WINZ26")

    def test_resolve_and_classify(self):
        for raw in ("wdo", "WDOFUT", "WDOV26", "mini dolar"):
            self.assertEqual(assets.resolve_symbol(raw), "WDOFUT")
        self.assertEqual(assets.resolve_symbol("winz26"), "WINFUT")
        self.assertEqual(assets.classify("WINFUT"), "b3fut")
        self.assertEqual(assets.resolve_symbol("PETR4"), "PETR4.SA")

    def test_round_to_tick(self):
        self.assertEqual(b3.round_to_tick(5143.74, 0.5), 5143.5)
        self.assertEqual(b3.round_to_tick(5143.26, 0.5, "up"), 5143.5)
        self.assertEqual(b3.round_to_tick(186033.3, 5, "down"), 186030)
        self.assertEqual(b3.round_to_tick(186031.0, 5, "up"), 186035)


def _plan(entry, stop, t1, t2, side="COMPRA", capital=50_000, risk_pct=1):
    return {"side": side, "entry": entry, "stop": stop, "target1": t1, "target2": t2, "capital": capital, "risk_pct": risk_pct}


class TicketTests(unittest.TestCase):
    signal = {"action": "ENTRAR_AGORA", "window": {"label": "Entre até 10:15"}}

    def test_wdo_contracts_from_risk_and_ticks(self):
        t = build_ticket("WDOFUT", "b3fut", _plan(5412.3, 5398.8, 5425.8, 5439.3), self.signal,
                         "MetaTrader 5 · XP (WDO$N, tempo real)", date(2026, 9, 14))
        self.assertTrue(t["available"])
        self.assertEqual(t["code"], "WDOV26")
        self.assertEqual(t["entry"], 5412.5)
        self.assertEqual(t["stop"], 5398.5)  # stop arredondado para longe da entrada
        self.assertEqual(t["target2"], 5439.0)  # alvo arredondado para perto da entrada
        self.assertEqual(t["stop_limit"], 5397.5)
        # risco 1% de 50 mil = R$ 500; 14 pontos x R$10 = R$140 por contrato -> 3 contratos
        self.assertEqual(t["quantity"], 3)
        self.assertAlmostEqual(t["risk_money"], 420.0)
        self.assertFalse(t["approximate"])
        self.assertIn("WDOV26", t["text"])

    def test_win_sell_rounds_opposite_way(self):
        t = build_ticket("WINFUT", "b3fut", _plan(186030, 187012, 185048, 184066, side="VENDA", capital=100_000),
                         self.signal, "Yahoo Finance · referência aproximada (x)", date(2026, 9, 14))
        self.assertEqual(t["stop"], 187015)
        self.assertEqual(t["target2"], 184070)
        self.assertEqual(t["side"], "VENDA")
        self.assertTrue(t["approximate"])
        self.assertTrue(any("aproximados" in w for w in t["warnings"]))

    def test_single_contract_over_budget_warns(self):
        t = build_ticket("WINFUT", "b3fut", _plan(186000, 184000, 188000, 190000, capital=10_000), self.signal,
                         "MetaTrader 5", date(2026, 9, 14))
        self.assertEqual(t["quantity"], 1)
        self.assertTrue(any("acima do seu limite" in w for w in t["warnings"]))

    def test_stock_lots_and_fractional(self):
        lot = build_ticket("PETR4.SA", "b3", _plan(49.11, 48.24, 49.98, 50.84, capital=20_000), self.signal,
                           "Yahoo Finance", date(2026, 9, 14), asset_name="Petrobras PN")
        self.assertEqual(lot["code"], "PETR4")
        self.assertEqual(lot["quantity"] % 100, 0)
        small = build_ticket("PETR4.SA", "b3", _plan(49.11, 48.24, 49.98, 50.84, capital=2_000), self.signal,
                             "Yahoo Finance", date(2026, 9, 14))
        self.assertEqual(small["code"], "PETR4F")
        self.assertLess(small["quantity"], 100)
        self.assertEqual(small["quantity"], 22)  # 1% de 2 mil = R$20 ÷ R$0,87 de risco por ação
        self.assertIn("22 ações", small["text"])

    def test_xp_unity_orders_and_short_stock_warning(self):
        buy = build_ticket("PETR4.SA", "b3", _plan(49.11, 48.24, 49.98, 50.84, capital=20_000), self.signal,
                           "Yahoo Finance", date(2026, 9, 14), timeframe="1h")
        entry, protect = buy["xp_orders"]
        self.assertEqual({f["label"]: f.get("value") for f in entry["fields"]}["Tipo de ordem"], "Limitada")
        labels = {f["label"]: f for f in protect["fields"]}
        self.assertEqual(labels["Operação"]["value"], "VENDA")  # proteção de uma compra é uma venda
        self.assertEqual(labels["Tipo de ordem"]["value"], "Stop Loss")
        self.assertEqual(labels["Preço disparo"]["key"], "stop")
        self.assertIn("Até cancelar", buy["validity"])
        self.assertFalse(any("descoberto" in w for w in buy["warnings"]))
        sell = build_ticket("PETR4.SA", "b3", _plan(49.11, 49.98, 48.24, 47.37, side="VENDA", capital=20_000),
                            self.signal, "Yahoo Finance", date(2026, 9, 14), timeframe="15m")
        self.assertTrue(any("descoberto" in w for w in sell["warnings"]))
        self.assertEqual(sell["validity"], "Hoje")

    def test_binance_spot_ticket_respects_pair_rules(self):
        info = {"pair": "BTCUSDT", "base": "BTC", "quote": "USDT", "status": "TRADING", "tick": 0.01,
                "step": 0.00001, "min_qty": 0.00001, "min_notional": 5.0, "oco": True}
        plan = _plan(76_900.123, 76_450.555, 77_349.9, 77_799.99, capital=10_000, risk_pct=1)
        t = build_ticket("BTC-USD", "crypto", plan, self.signal, "Binance (tempo real)", date(2026, 9, 15),
                         binance=info, brl_per_usd=5.0)
        self.assertTrue(t["available"])
        self.assertEqual(t["exchange"], "Binance")
        self.assertEqual(t["entry"], 76_900.12)
        self.assertEqual(t["stop"], 76_450.55)  # arredondado para longe da entrada
        self.assertEqual(t["target2"], 77_799.99)
        # capital R$10.000 / 5 = US$2.000; risco de 1% pediria 0,04448 BTC, mas o dinheiro só compra 0,02548
        self.assertAlmostEqual(t["quantity"], 0.02548, places=5)
        wide = build_ticket("BTC-USD", "crypto", _plan(76_900, 73_000, 80_800, 84_700, capital=10_000, risk_pct=1),
                            self.signal, "Binance", date(2026, 9, 15), binance=info, brl_per_usd=5.0)
        self.assertAlmostEqual(wide["quantity"], 0.00512, places=5)  # stop largo: limitado pelo risco (US$20/3.900)
        steps = t["quantity"] / 0.00001
        self.assertAlmostEqual(steps, round(steps), places=6)  # múltiplo exato do step do par
        self.assertLessEqual(t["risk_money"], 20.0)
        orders = t["xp_orders"]
        self.assertEqual({f["label"]: f.get("value") for f in orders[1]["fields"]}["Tipo"], "OCO")
        self.assertIn("BINANCE", t["text"])

    def test_binance_minimum_and_spot_sell_warnings(self):
        info = {"pair": "BTCUSDT", "base": "BTC", "quote": "USDT", "status": "TRADING", "tick": 0.01,
                "step": 0.00001, "min_qty": 0.00001, "min_notional": 5.0, "oco": True}
        tiny = build_ticket("BTC-USD", "crypto", _plan(76_900, 76_450, 77_350, 77_800, capital=20, risk_pct=1),
                            self.signal, "Binance", date(2026, 9, 15), binance=info, brl_per_usd=5.0)
        self.assertTrue(any("mínimo da Binance" in w for w in tiny["warnings"]))
        sell = build_ticket("BTC-USD", "crypto", _plan(76_900, 77_350, 76_450, 76_000, side="VENDA"),
                            self.signal, "Binance", date(2026, 9, 15), binance=info, brl_per_usd=5.0)
        self.assertTrue(any("Spot você só vende" in w for w in sell["warnings"]))
        missing = build_ticket("BTC-USD", "crypto", _plan(1, 0.9, 1.1, 1.2), self.signal, "Binance", date(2026, 9, 15))
        self.assertFalse(missing["available"])

    def test_exit_ticket_inverts_side(self):
        position = {"side": "COMPRA", "quantity": 2, "entry_price": 5400}
        t = build_ticket("WDOFUT", "b3fut", _plan(5400, 5386, 5414, 5428), {"action": "SAIR_AGORA", "headline": "STOP"},
                         "MetaTrader 5", date(2026, 9, 14), position=position)
        self.assertEqual(t["mode"], "exit")
        self.assertEqual(t["side"], "VENDA")
        self.assertEqual(t["quantity"], 2)

    def test_forex_suggests_wdo(self):
        t = build_ticket("EURUSD=X", "forex", _plan(1.1, 1.09, 1.11, 1.12), self.signal, "Yahoo", date(2026, 9, 14))
        self.assertFalse(t["available"])
        self.assertEqual(t["suggest"], "WDOFUT")


class FakeMT5(types.SimpleNamespace):
    TIMEFRAME_M15 = 15

    def __init__(self, server_offset=10800):
        super().__init__()
        self.server_offset = server_offset
        self.selected = []

    def initialize(self, **kwargs):
        return True

    def last_error(self):
        return (1, "ok")

    def terminal_info(self):
        return types.SimpleNamespace(name="MetaTrader 5", company="XP Investimentos")

    def account_info(self):
        return types.SimpleNamespace(company="XP Investimentos", server="XPMT5-DEMO")

    def symbol_info(self, name):
        return types.SimpleNamespace(name=name) if name == "WDO$N" else None

    def symbol_select(self, name, enable):
        self.selected.append(name)
        return True

    def symbol_info_tick(self, name):
        return types.SimpleNamespace(time=int(time.time()) + self.server_offset)

    def copy_rates_from_pos(self, name, tf, start, count):
        now = (int(time.time()) // 900) * 900 + self.server_offset
        times = np.arange(now - 899 * 900, now + 1, 900)
        dtype = [("time", "i8"), ("open", "f8"), ("high", "f8"), ("low", "f8"), ("close", "f8"),
                 ("tick_volume", "i8"), ("spread", "i4"), ("real_volume", "i8")]
        rates = np.zeros(len(times), dtype=dtype)
        rates["time"], rates["open"], rates["close"] = times, 5400.0, 5401.0
        rates["high"], rates["low"], rates["real_volume"] = 5402.0, 5399.0, 1000
        return rates


class HangingMT5(FakeMT5):
    """Simula o terminal aberto com diálogo na tela: initialize demora muito ("IPC timeout")."""

    def initialize(self, **kwargs):
        time.sleep(3)
        return False

    def last_error(self):
        return (-10005, "IPC timeout")


class Mt5SourceTests(unittest.TestCase):
    def setUp(self):
        mt5_source._state.update(connected=False, connecting=False, last_try=0.0, offset=None, terminal=None,
                                 error=None, failures=0, terminal_running=None, checked_at=0.0)
        mt5_source._symbol_cache.clear()

    def test_candidates_include_continuous_and_current_code(self):
        names = mt5_source.candidates("WDOFUT", date(2026, 9, 14))
        self.assertIn("WDO$N", names)
        self.assertIn("WDOV26", names)
        self.assertEqual(mt5_source.candidates("PETR4.SA"), ["PETR4"])

    def test_offset_measured_only_with_fresh_tick(self):
        now = 1_800_000_000
        self.assertEqual(mt5_source.measure_offset(now + 10800 + 20, now), 10800)
        mt5_source._state["offset"] = None
        self.assertEqual(mt5_source.measure_offset(now - 3 * 86400 - 777, now), 0)
        self.assertIsNone(mt5_source._state["offset"])

    def test_reads_never_wait_for_a_hanging_terminal(self):
        with mock.patch.object(mt5_source, "mt5", HangingMT5()), \
                mock.patch.object(mt5_source, "terminal_running", return_value=True), \
                mock.patch.object(mt5_source, "probe", return_value=(False, -10005, "IPC timeout")), \
                mock.patch.object(mt5_source, "request_connect"):
            started = time.time()
            self.assertIsNone(mt5_source.fetch("WDOFUT", "15m", 100))
            status = mt5_source.status()
            self.assertLess(time.time() - started, 0.5)  # leitura e status respondem na hora
            self.assertFalse(status["connected"])
            started = time.time()
            self.assertFalse(mt5_source.connect_now())  # o teste em outro processo falhou: nem chama initialize aqui
            self.assertLess(time.time() - started, 0.5)
            self.assertIn("não respondeu", mt5_source._state["error"])

    def test_no_terminal_means_no_initialize(self):
        fake = FakeMT5()
        fake.initialize = mock.Mock(return_value=True)
        with mock.patch.object(mt5_source, "mt5", fake), mock.patch.object(mt5_source, "terminal_running", return_value=False):
            self.assertFalse(mt5_source.connect_now())
        fake.initialize.assert_not_called()  # nunca abre um MT5 sozinho nem espera por IPC

    def test_fetch_converts_server_time_to_utc(self):
        fake = FakeMT5(server_offset=10800)
        with mock.patch.object(mt5_source, "mt5", fake), mock.patch.object(mt5_source, "terminal_running", return_value=True), \
                mock.patch.object(mt5_source, "probe", return_value=(True, 1, "Success")):
            self.assertTrue(mt5_source.connect_now())
            got = mt5_source.fetch("WDOFUT", "15m", 900)
        self.assertIsNotNone(got)
        df, source = got
        self.assertEqual(len(df), 900)
        self.assertIn("XP Investimentos", source)
        self.assertIn("tempo real", source)
        last = df.index[-1].timestamp()
        self.assertLess(abs(last - time.time()), 1800)  # sem o ajuste do fuso estaria 3h no futuro
        self.assertTrue((df["volume"] == 1000).all())

    def test_without_package_everything_falls_back(self):
        with mock.patch.object(mt5_source, "mt5", None):
            self.assertFalse(mt5_source.installed())
            self.assertIsNone(mt5_source.fetch("WDOFUT", "15m", 100))
            self.assertFalse(mt5_source.status()["connected"])


if __name__ == "__main__":
    unittest.main()
