"""Testes de B3/XP: vencimentos, ticks e boletas (XP e Binance)."""

from __future__ import annotations

import unittest
from datetime import date

from app import assets, b3
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


if __name__ == "__main__":
    unittest.main()
