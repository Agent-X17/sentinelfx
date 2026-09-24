import tempfile
import unittest
from datetime import timedelta

from engine.bridge import TradingBridge
from engine.config import Settings
from engine.domain import stamp, utcnow
from engine.mt5 import MT5Service, MockMT5Service
from engine.service import Application
from engine.webhook import SymbolMapper, TradingViewWebhookService, WebhookAuthenticator


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings(
            mode="SIMULATION",
            database_path=self.temp.name + "/bridge.sqlite3",
            webhook_secret="test-secret",
            default_account_profile="ACCOUNT_LIVE",
        )
        self.app = Application(self.settings.database_path, settings=self.settings)
        self.mapper = SymbolMapper({"EURUSD": ["EURUSD.a"]})
        self.webhook = TradingViewWebhookService(self.mapper, 300)
        self.mt5 = MockMT5Service(
            account={"balance": 400, "equity": 400, "margin_free": 400, "leverage": 500, "trade_allowed": True},
            symbols={"EURUSD.a": {"trade_contract_size": 1000, "volume_min": .01, "volume_max": 100, "volume_step": .01, "point": .00001, "digits": 5, "trade_mode": 1}},
            ticks={"EURUSD.a": {"bid": 1.1000, "ask": 1.1001, "time": 1}},
        )
        self.bridge = TradingBridge(self.app, self.webhook, WebhookAuthenticator("test-secret"), self.mt5, self.settings)

    def tearDown(self):
        self.temp.cleanup()

    def payload(self, **updates):
        payload = {
            "alert_id": "alert-1",
            "symbol": "OANDA:EUR/USD",
            "side": "BUY",
            "timeframe": "H1",
            "strategy": "test breakout",
            "timestamp": stamp(),
            "entry": "1.1001",
            "stop_loss": "1.0980",
            "take_profit": "1.1045",
            "metadata": {"strategy_type": "breakout"},
        }
        payload.update(updates)
        return payload

    def test_complete_bridge_candidate(self):
        result = self.bridge.ingest(self.payload(), "test-secret")
        self.assertTrue(result["accepted"])
        self.assertEqual(result["decision"]["final_decision"], "BUY")
        self.assertGreater(float(result["decision"]["position_size_result"]), 0)
        self.assertEqual(str(self.mt5.order_check_requests[0]["volume"]), str(result["decision"]["position_size_result"]))
        state = self.app.snapshot()
        self.assertEqual(len(state["raw_webhooks"]), 1)
        self.assertEqual(len(state["normalized_signals"]), 1)
        self.assertEqual(len(state["risk_checks"]), 1)

    def test_authentication_failure(self):
        result = self.bridge.ingest(self.payload(), "wrong")
        self.assertEqual(result["decision"], "NO_TRADE")
        self.assertEqual(result["reason"], "WEBHOOK_AUTH_FAILED")

    def test_duplicate_webhook(self):
        self.bridge.ingest(self.payload(), "test-secret")
        result = self.bridge.ingest(self.payload(), "test-secret")
        self.assertEqual(result["status"], "duplicate")
        self.assertEqual(result["decision"], "NO_TRADE")

    def test_stale_webhook(self):
        old = (utcnow() - timedelta(minutes=20)).isoformat()
        result = self.bridge.ingest(self.payload(timestamp=old), "test-secret")
        self.assertEqual(result["status"], "stale")

    def test_unmapped_symbol(self):
        result = self.bridge.ingest(self.payload(symbol="XAUUSD"), "test-secret")
        self.assertEqual(result["status"], "unmapped")

    def test_missing_stop(self):
        payload = self.payload()
        payload.pop("stop_loss")
        result = self.bridge.ingest(payload, "test-secret")
        self.assertEqual(result["status"], "malformed")

    def test_order_check_failure(self):
        self.mt5.order_check_ok = False
        result = self.bridge.ingest(self.payload(), "test-secret")
        self.assertEqual(result["reason"], "MT5_ORDER_CHECK_FAILED")

    def test_trusted_adapter_requires_exact_mt5_checked_volume(self):
        self.bridge.ingest(self.payload(), "test-secret")
        self.assertEqual(len(self.mt5.order_check_requests), 1)
        checked = self.mt5.order_check_requests[0]["volume"]
        self.assertNotEqual(str(checked), "0.01")

    def test_mt5_disconnected(self):
        bridge = TradingBridge(self.app, self.webhook, WebhookAuthenticator("test-secret"), MT5Service(enabled=False), self.settings)
        result = bridge.ingest(self.payload(), "test-secret")
        self.assertEqual(result["reason"], "MT5_DISABLED")

    def test_account_restricted(self):
        self.mt5.account["trade_allowed"] = False
        result = self.bridge.ingest(self.payload(), "test-secret")
        self.assertEqual(result["reason"], "BROKER_VALIDATION_FAILED")
        self.assertIn("restricted", result["mt5"]["message"])

    def test_live_mode_cannot_be_constructed(self):
        with self.assertRaisesRegex(Exception, "not implemented"):
            Settings(mode="LIVE_GATED", live_execution_enabled=True).validate()

    def test_order_send_always_denied(self):
        result = self.mt5.order_send({"symbol": "EURUSD.a"})
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "LIVE_EXECUTION_NOT_IMPLEMENTED")


class SymbolMapperTests(unittest.TestCase):
    def test_exchange_prefix_and_slash(self):
        result = SymbolMapper({"EURUSD": ["EURUSD.a"]}).map("OANDA:EUR/USD")
        self.assertTrue(result["ok"])
        self.assertEqual(result["mt5_symbol"], "EURUSD.a")

    def test_ambiguous_mapping_fails(self):
        result = SymbolMapper({"EURUSD": ["EURUSD", "EURUSD.a"]}).map("EURUSD")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "SYMBOL_AMBIGUOUS")


if __name__ == "__main__":
    unittest.main()
