"""Widget flutuante: leitura da análise, contagem até o fechamento do candle, atalho e a janela (sem internet)."""

from __future__ import annotations

import copy
import socket
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from widget import client
from widget.client import (ApiError, AurumApi, WidgetConfig, build_view, countdown, fmt_num, next_close,
                           next_refresh)
from widget.hotkey import parse_hotkey, pretty

MT5_ANALYSIS = {
    "symbol": "GC=F", "asset": {"name": "Ouro (XAU/USD)", "broker_symbol": "XAUUSD", "kind": "futures"},
    "timeframe": {"key": "15m", "label": "15 minutos", "seconds": 900}, "decimals": 2, "price": 4340.16,
    "score": -18.0, "market": {"open": True},
    "candle": {"start": "2026-09-16T09:30:00-03:00", "close_at": "2026-09-16T09:45:00-03:00", "forming": True},
    "data_provider": "mt5", "source_warning": None,
    "signal": {"action": "ENTRAR_AGORA", "title": "ENTRAR AGORA", "side": "COMPRA", "setup": "ROMPIMENTO + PULLBACK",
               "confidence": 42, "confidence_info": {"breakeven": 33.3}, "headline": "Compra confirmada",
               "simple": "Compre.", "warnings": ["Volatilidade acima do normal: reduza o tamanho da posição."],
               "window": {"label": "Entre até 09:45 (fechamento do candle atual)"}},
    "plan": {"side": "COMPRA", "entry": 4340.16, "stop": 4325.35, "target1": 4354.97, "target2": 4369.77,
             "stop_pct": 0.341, "target2_pct": 0.682, "reward_ratio": 2.0, "risk_pct": 1.0, "risk_amount": 88.86,
             "pips": {"label": "pontos", "stop": 1481, "target2": 2962, "lots": 0.06}},
    "ticket": {"available": True, "kind": "mt5", "mode": "entry", "entry": 4340.30, "stop": 4325.49,
               "target2": 4369.91, "quantity": 0.06, "qty_decimals": 2, "unit": "lote", "risk_money": 88.86,
               "reward_money": 177.66, "currency_symbol": "US$", "balance": 10000.0},
    "context": {"reliability": {"level": "ALTA"}}, "position": None,
}


class FormatTests(unittest.TestCase):
    def test_brazilian_numbers(self):
        self.assertEqual(fmt_num(4340.3, 2), "4.340,30")
        self.assertEqual(fmt_num(0.06, 2), "0,06")
        self.assertEqual(fmt_num(None), "—")

    def test_countdown_and_refresh_follow_the_candle_close(self):
        now = datetime(2026, 9, 16, 12, 40, 30, tzinfo=timezone.utc)
        close = datetime(2026, 9, 16, 12, 45, tzinfo=timezone.utc)
        self.assertEqual(countdown(close, "15m", now), "04:30")
        self.assertAlmostEqual(next_refresh(close, "15m", now), 20)  # antes: o intervalo normal do painel
        self.assertAlmostEqual(next_refresh(close, "15m", now + timedelta(minutes=4, seconds=20)), 14)  # fecha +4 s
        # Candle da fonte atrasado: a contagem vai para o próximo fechamento, não fica parada em zero.
        self.assertEqual(next_close(close, "15m", now + timedelta(minutes=20)), close + timedelta(minutes=30))  # 13:00:30 → 13:15
        self.assertEqual(countdown(None), "—")
        self.assertEqual(countdown(close, "1d", close - timedelta(hours=5, minutes=3)), "5h03")


class ViewTests(unittest.TestCase):
    def test_mt5_signal_uses_the_ticket_numbers(self):
        v = build_view(MT5_ANALYSIS)
        self.assertEqual((v.title, v.symbol, v.symbol_key, v.timeframe), ("ENTRAR AGORA", "XAUUSD", "GC=F", "15m"))
        self.assertEqual((v.entry, v.stop, v.target), ("4.340,30", "4.325,49", "4.369,91"))  # boleta: ask do MT5
        self.assertEqual(v.size, "0,06 lotes")
        self.assertEqual(v.risk, "0,89% da conta")
        self.assertIn("US$ 88,86", v.risk_detail)
        self.assertEqual(v.gain, "US$ 177,66")
        self.assertEqual(v.confidence, "42%")
        self.assertIn("empate 33%", v.confidence_detail)
        self.assertIn("1.481 pontos", v.stop_detail)
        self.assertEqual(v.color, client.BUY)
        self.assertFalse(v.simulated)
        self.assertEqual(v.bar_start, int(datetime(2026, 9, 16, 12, 30, tzinfo=timezone.utc).timestamp()))

    def test_open_position_shows_its_own_size_and_result(self):
        a = copy.deepcopy(MT5_ANALYSIS)
        a["signal"].update(action="SAIR_AGORA", title="SAIR AGORA", confidence=None, confidence_info=None)
        a["position"] = {"side": "COMPRA", "quantity": 0.03, "pnl_pct": 1.24}
        a["ticket"] = {"available": False, "reason": "Boleta em lotes aguardando os preços do MetaTrader 5"}
        a["source_warning"] = "Preços do Yahoo Finance, não do seu MetaTrader 5."
        v = build_view(a)
        self.assertEqual(v.size, "0,03 · registrada")
        self.assertEqual((v.gain, v.gain_detail), ("+1,24%", "resultado agora"))
        self.assertEqual(v.color, client.EXIT)
        self.assertTrue(v.position)
        self.assertTrue(v.warnings[0].startswith("Preços do Yahoo"))  # aviso de fonte sempre em primeiro
        self.assertEqual(v.confidence, "18/100")  # sem chance histórica: força do mercado
        self.assertIn("vendedora", v.confidence_detail)

    def test_pause_and_wait_are_simulations(self):
        a = copy.deepcopy(MT5_ANALYSIS)
        a["signal"].update(action="PAUSA", title="PAUSA", side=None, explanation="Perda do dia atingiu o limite.")
        v = build_view(a)
        self.assertTrue(v.simulated)
        self.assertEqual(v.color, client.PAUSE)
        self.assertIn("Perda do dia atingiu o limite.", v.warnings)
        self.assertEqual(v.side_text, "COMPRA ↑")  # lado do plano de simulação
        closed = copy.deepcopy(MT5_ANALYSIS)
        closed["market"]["open"] = False
        self.assertIsNone(build_view(closed).close_at)  # mercado fechado: sem contagem


class HotkeyTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_hotkey("ctrl+alt+a"), (0x0002 | 0x0001, ord("A")))
        self.assertEqual(parse_hotkey("Ctrl + Shift + F9"), (0x0002 | 0x0004, 0x78))
        for bad in ("a", "ctrl+", "hyper+a", "ctrl+??"):
            with self.assertRaises(ValueError):
                parse_hotkey(bad)
        self.assertEqual(pretty("ctrl+alt+a"), "Ctrl+Alt+A")


class ApiAndConfigTests(unittest.TestCase):
    def test_offline_aurum_is_reported_quickly(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]  # porta livre: ninguém escutando
        with self.assertRaises(ApiError) as ctx:
            AurumApi(f"http://127.0.0.1:{port}").health()
        self.assertTrue(ctx.exception.offline)

    def test_config_roundtrip_and_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "widget.json"
            WidgetConfig(symbol="EURUSD=X", timeframe="1h", opacity=0.7, x=10, y=20).save(path)
            cfg = WidgetConfig.load(path)
            self.assertEqual((cfg.symbol, cfg.timeframe, cfg.opacity, cfg.x), ("EURUSD=X", "1h", 0.7, 10))
            path.write_text('{"opacity": 0.05, "timeframe": "7m", "desconhecido": 1}', encoding="utf-8")
            cfg = WidgetConfig.load(path)
            self.assertEqual((cfg.opacity, cfg.timeframe), (0.3, "15m"))  # nunca some da tela


class WindowTests(unittest.TestCase):
    """Monta a janela de verdade (sem rede e sem atalho global) e desenha um sinal."""

    def test_renders_signal_and_toggles(self):
        import tkinter as tk
        from widget.ui import AurumWidget
        try:
            root = tk.Tk()
        except tk.TclError:
            self.skipTest("sem tela disponível")

        def close():
            try:
                root.destroy()
            except tk.TclError:
                pass  # o próprio teste já fechou

        self.addCleanup(close)
        with tempfile.TemporaryDirectory() as tmp:
            w = AurumWidget(root, AurumApi("http://127.0.0.1:9"), WidgetConfig(), config_path=Path(tmp) / "w.json",
                            use_hotkey=False, autostart=False)
            w.render(build_view(MT5_ANALYSIS))
            root.update()
            self.assertEqual(w.action_label.cget("text"), "ENTRAR AGORA")
            self.assertEqual(w.cells["entry"][0].cget("text"), "4.340,30")
            self.assertEqual(w.cells["size"][0].cget("text"), "0,06 lotes")
            self.assertEqual(w.plan_tag.cget("text"), "ENVIAR AGORA")
            self.assertIn("Volatilidade", w.warn_label.cget("text"))
            w.set_compact(True)
            root.update()
            self.assertFalse(w.body.winfo_ismapped())
            w.set_opacity(0.5)
            self.assertAlmostEqual(float(root.attributes("-alpha")), 0.5, places=2)
            w.toggle_visible()
            self.assertTrue(w.hidden)
            w.toggle_visible()
            self.assertFalse(w.hidden)
            w.quit()


if __name__ == "__main__":
    unittest.main()
