"""Testes da API com banco temporário e dados sintéticos (sem internet)."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app import database, market_data, news
from app.main import app
from tests.helpers import make_snapshot


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        database.DATA_DIR = Path(cls.tmp.name)
        database.DB_PATH = Path(cls.tmp.name) / "test.db"
        database._initialized = False
        news.set_events([])  # sem internet: agenda vazia
        for tf in ("15m", "5m"):
            snap = make_snapshot(symbol="BTC-USD", timeframe=tf, n=450, seed=21)
            market_data._cache[("BTC-USD", tf)] = snap
            snap.fetched_at = time.time() + 10**6  # não expira durante o teste
        cls.client = TestClient(app, headers={"X-AURUM": "1"})  # sem "with": não inicia o scanner em segundo plano

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_local_only_protections(self):
        bare = TestClient(app)
        self.assertEqual(bare.get("/api/health").json()["app"], "AURUM")
        self.assertEqual(bare.put("/api/settings", json={"capital": "1"}).status_code, 403)  # sem cabeçalho X-AURUM
        self.assertEqual(bare.post("/api/system/shutdown").status_code, 403)
        evil = TestClient(app, base_url="http://evil.example.com", headers={"X-AURUM": "1"})
        self.assertEqual(evil.get("/api/meta").status_code, 403)  # DNS rebinding

    def test_meta_and_static(self):
        meta = self.client.get("/api/meta").json()
        self.assertIn("NÃO APOSTE", meta["disclaimer"])
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_analysis_by_alias(self):
        resp = self.client.get("/api/analysis", params={"symbol": "btc", "tf": "15m"})
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["symbol"], "BTC-USD")
        self.assertIn("signal", data)
        self.assertEqual(self.client.get("/api/analysis", params={"symbol": "btc", "tf": "7m"}).status_code, 422)

    def test_position_lifecycle(self):
        price = self.client.get("/api/analysis", params={"symbol": "BTC-USD", "tf": "5m"}).json()["price"]
        bad = self.client.post("/api/positions", json={"symbol": "BTC-USD", "side": "COMPRA", "entry_price": price,
                                                       "stop_price": price * 1.01})
        self.assertEqual(bad.status_code, 422)
        pos = self.client.post("/api/positions", json={"symbol": "BTC-USD", "side": "COMPRA", "entry_price": price,
                                                       "timeframe": "5m"}).json()
        self.assertEqual(pos["status"], "open")
        dup = self.client.post("/api/positions", json={"symbol": "BTC-USD", "side": "VENDA", "entry_price": price})
        self.assertEqual(dup.status_code, 409)
        analysis = self.client.get("/api/analysis", params={"symbol": "BTC-USD", "tf": "5m"}).json()
        self.assertIsNotNone(analysis["position"])
        self.assertIn(analysis["signal"]["action"], {"MANTENHA", "SAIR_AGORA"})
        closed = self.client.post(f"/api/positions/{pos['id']}/close", json={"exit_price": price * 1.02}).json()
        self.assertEqual(closed["status"], "closed")
        self.assertAlmostEqual(closed["pnl_pct"], 2.0, places=2)

    def test_settings_enforce_max_stop(self):
        self.assertEqual(self.client.put("/api/settings", json={"max_stop_pct": "5"}).status_code, 422)
        ok = self.client.put("/api/settings", json={"max_stop_pct": "2", "telegram_token": "123:abcdef"})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json()["max_stop_pct"], "2")
        self.assertTrue(ok.json()["telegram_token"].startswith("••••"))  # token nunca volta inteiro
        self.client.put("/api/settings", json={"max_stop_pct": "3"})

    def test_history_and_report(self):
        self.client.post("/api/analysis/record", params={"symbol": "BTC-USD", "tf": "15m"})
        rows = self.client.get("/api/history", params={"symbol": "BTC-USD"}).json()
        self.assertGreaterEqual(len(rows), 1)
        report = self.client.get("/api/report.txt", params={"symbol": "BTC-USD", "tf": "15m"})
        self.assertIn("RELATÓRIO AURUM", report.text)
        csv = self.client.get("/api/history.csv")
        self.assertIn("symbol", csv.text.splitlines()[0])


if __name__ == "__main__":
    unittest.main()
