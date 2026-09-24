import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from engine.bridge import TradingBridge
from engine.config import Settings
from engine.domain import stamp
from engine.isolated_mt5 import IsolatedMT5Service,MockDiagnosticMT5Service
from engine.service import Application
from engine.webhook import SymbolMapper,TradingViewWebhookService,WebhookAuthenticator
from server import seed_demo_activity,status_document,webhook_diagnostics,mt5_host_capabilities


class OperatorTests(unittest.TestCase):
    def test_diagnostic_mode_configuration(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{'MT5_DIAGNOSTIC_MODE':'mock'},clear=True):
            settings=Settings.from_env(Path(folder))
            self.assertEqual(settings.mt5_diagnostic_mode,'mock')
            self.assertTrue(settings.mt5_enabled)
            self.assertFalse(settings.live_execution_enabled)
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{'MT5_DIAGNOSTIC_MODE':'invalid'},clear=True):
            with self.assertRaisesRegex(Exception,'disabled, mock, or real'): Settings.from_env(Path(folder))

    def test_mock_diagnostic_is_evidence_gated(self):
        with tempfile.TemporaryDirectory() as folder:
            settings=Settings(database_path=folder+'/db',webhook_secret='test-secret',mt5_enabled=True,mt5_diagnostic_mode='mock')
            app=Application(settings.database_path,settings=settings)
            with app.store.connect() as db:mappings=__import__('engine.storage',fromlist=['Store']).Store.symbol_mappings(db)
            bridge=TradingBridge(app,TradingViewWebhookService(SymbolMapper(mappings)),WebhookAuthenticator('test-secret'),MockDiagnosticMT5Service(),settings)
            payload={'alert_id':'operator-mock-1','symbol':'OANDA:EUR/USD','side':'BUY','timeframe':'H1','strategy':'diagnostic test','timestamp':stamp(),'entry':'1.1001','stop_loss':'1.0980','take_profit':'1.1045'}
            result=bridge.ingest(payload,'test-secret')
            self.assertEqual(result['decision'],'NO_TRADE')
            self.assertEqual(result['reason'],'REQUIRED_EXTERNAL_EVIDENCE_UNVERIFIED')
            self.assertEqual(result['block_source'],'MISSING_EXTERNAL_EVIDENCE')
            self.assertFalse(result['order_sent'])
            self.assertEqual(len(app.snapshot()['risk_checks']),1)

    def test_drift_reset_clears_identity_and_latch(self):
        service=IsolatedMT5Service(True);service._identity=(1,'demo','USD');service._drift=True
        result=service.reset_drift()
        self.assertTrue(result.ok);self.assertTrue(result.data['was_latched'])
        self.assertFalse(service._drift);self.assertIsNone(service._identity)
        self.assertFalse(service.operational_state()['drift_latched'])

    def test_demo_seed_and_human_status(self):
        with tempfile.TemporaryDirectory() as folder:
            settings=Settings(database_path=folder+'/demo.sqlite3')
            app=Application(settings.database_path,settings=settings);seed_demo_activity(app)
            snapshot=app.snapshot()
            self.assertEqual(len(snapshot['decisions']),3)
            page=status_document(settings,MockDiagnosticMT5Service(),8765).decode()
            self.assertIn('SentinelFX operator status',page)
            self.assertIn('Live execution',page)
            self.assertIn('/api/webhook/tradingview',page)

    def test_webhook_diagnostics_explain_validation(self):
        snapshot={'raw_webhooks':[{'received_at':'2026-01-01T00:00:00+00:00','status':'rejected','reason':'WEBHOOK_AUTH_FAILED'}],
                  'audit':[{'timestamp':'2026-01-01T00:01:00+00:00','event':'WEBHOOK_DUPLICATE','payload':'{}'}]}
        rows=webhook_diagnostics(snapshot)
        self.assertEqual(rows[0]['replay_status'],'DUPLICATE')
        self.assertEqual(rows[1]['secret_validation'],'FAILED')

    def test_host_capabilities_never_claim_real_support_without_both_requirements(self):
        capabilities=mt5_host_capabilities()
        self.assertEqual(capabilities['real_diagnostics_prerequisites_met'],capabilities['package_available'] and capabilities['windows_supported_host'])
        self.assertIn('demo-only login',capabilities['recommended_host'])

    def test_tradingview_template_is_valid_and_contains_no_broker_credentials(self):
        path=Path(__file__).resolve().parents[1]/'examples'/'tradingview-alert-message.json'
        raw=path.read_text()
        template=json.loads(raw)
        self.assertEqual(template['symbol'],'{{exchange}}:{{ticker}}')
        self.assertEqual(template['timestamp'],'{{timenow}}')
        self.assertEqual(template['entry'],'{{close}}')
        self.assertTrue({'secret','alert_id','side','stop_loss','take_profit'} <= set(template))
        self.assertFalse({'login','password','broker_password','api_key'} & set(template))


if __name__=='__main__': unittest.main()
