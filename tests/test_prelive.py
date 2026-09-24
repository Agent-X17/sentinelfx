import json
import threading
import unittest
from datetime import timedelta
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from tests import test_bridge
from tests import test_hardening
from engine.domain import stamp, utcnow, InvalidData
from engine.diagnostics import validate_diagnostics
from engine.mt5 import MT5Service
from engine.storage import Store
from server import make_server, bind_server


class PreliveTests(unittest.TestCase):
    setUp = test_bridge.BridgeTests.setUp
    tearDown = test_bridge.BridgeTests.tearDown
    payload = test_bridge.BridgeTests.payload

    def test_nested_credentials_never_reach_persistence(self):
        payload = self.payload(metadata={'strategy_type':'breakout','nested':[{'api_key':'private-value', 'Authorization':'bearer-value'}], 'note':'test-secret'})
        result = self.bridge.ingest(payload, 'test-secret')
        self.assertTrue(result['accepted'])
        with self.app.store.connect() as db:
            for table in ('raw_webhooks','normalized_signals','audit_logs','signals','decisions','journal_entries'):
                rows = str([tuple(r) for r in db.execute('SELECT * FROM '+table)])
                for secret in ('private-value','bearer-value','test-secret'):
                    self.assertNotIn(secret, rows)
        self.assertEqual(payload['metadata']['nested'][0]['api_key'], 'private-value')

    def test_failed_auth_payload_is_redacted(self):
        self.bridge.ingest(self.payload(metadata={'password':'private-value'}), 'wrong')
        self.assertNotIn('private-value', str(self.app.snapshot()['raw_webhooks']))

    def test_header_cannot_collide_with_alert_namespace(self):
        self.bridge.ingest(self.payload(alert_id=None), 'test-secret', 'alert:alert-1')
        result = self.bridge.ingest(self.payload(), 'test-secret')
        self.assertNotEqual(result['status'], 'duplicate')

    def test_changed_header_without_alert_id_cannot_replay(self):
        payload = self.payload(alert_id=None)
        self.bridge.ingest(payload,'test-secret','first')
        self.assertEqual(self.bridge.ingest(payload,'test-secret','second')['status'],'duplicate')

    def test_expired_and_invalid_expiry_rejected_before_adapter(self):
        for expiry in ((utcnow()-timedelta(seconds=1)).isoformat(), 'not-a-date'):
            with self.subTest(expiry=expiry):
                check = self.webhook.validate(self.payload(expires_at=expiry))
                self.assertFalse(check.accepted)

    def test_blocked_candidate_has_signal_and_risk_record(self):
        self.bridge.mt5 = MT5Service(enabled=False)
        result = self.bridge.ingest(self.payload(), 'test-secret')
        self.assertFalse(result['order_sent'])
        state = self.app.snapshot()
        self.assertEqual(state['normalized_signals'][0]['status'], 'NO_TRADE')
        self.assertEqual(json.loads(state['risk_checks'][0]['payload'])['reason'], 'MT5_DISABLED')

    def test_live_flags_refused_independently(self):
        from engine.config import Settings
        for settings in (Settings(live_execution_enabled=True), Settings(mode='LIVE_GATED')):
            with self.assertRaises(InvalidData): settings.validate()

    def test_false_retcode_cannot_be_mistaken_for_zero(self):
        adapter=test_hardening.AdapterTests().adapter(False)
        self.assertFalse(adapter.order_check({}).ok)

    def test_port_conflict_explicit_and_fallback_safe(self):
        server = make_server(self.app,0)
        try:
            with self.assertRaisesRegex(InvalidData, 'already in use'):
                bind_server(self.app,server.server_port,None,None,None)
            fallback = bind_server(self.app,server.server_port,None,None,None,True)
            try: self.assertNotEqual(fallback.server_port,server.server_port)
            finally: fallback.server_close()
        finally: server.server_close()

    def test_http_webhook_status_semantics(self):
        server=make_server(self.app,0,self.bridge,self.mt5,self.settings)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def send(payload, secret):
            req=Request(f'http://127.0.0.1:{server.server_port}/api/webhook/tradingview',data=json.dumps(payload).encode(),headers={'X-Webhook-Secret':secret})
            try:
                with urlopen(req,timeout=5) as response: return response.status,json.load(response)
            except HTTPError as response: return response.code,json.load(response)
        try:
            self.assertEqual(send(self.payload(),'wrong')[0],401)
            self.assertEqual(send(self.payload(),'test-secret')[0],200)
            self.assertEqual(send(self.payload(),'test-secret')[0],409)
            self.assertEqual(send(self.payload(alert_id='missing',stop_loss=None),'test-secret')[0],400)
            self.assertEqual(send(self.payload(alert_id='stale',timestamp='2000-01-01T00:00:00Z'),'test-secret')[0],422)
        finally: server.shutdown();server.server_close();thread.join()


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.signal={'mt5_symbol':'EURUSD.a'}
        self.symbol={'name':'EURUSD.a','trade_contract_size':1000,'volume_min':.01,'volume_max':100,'volume_step':.01,'point':.00001,'trade_mode':4}
        self.tick={'bid':1.1,'ask':1.1001,'time':utcnow().timestamp()}
        self.account={'login':123,'server':'demo','currency':'USD','equity':300,'margin_free':300,'trade_allowed':True}
    def check(self): return validate_diagnostics(self.signal,self.symbol,self.tick,self.account)
    def test_fresh_diagnostics_still_require_reconciliation(self):
        self.assertEqual(self.check(),['MT5_ACCOUNT_RECONCILIATION_UNVERIFIED'])
    def test_stale_tick_is_visible(self):
        self.tick['time']=1
        self.assertIn('MT5_TICK_STALE_OR_FUTURE',self.check())
    def test_future_millisecond_tick_is_visible(self):
        self.tick['time_msc']=(utcnow().timestamp()+60)*1000
        self.assertIn('MT5_TICK_STALE_OR_FUTURE',self.check())
    def test_missing_and_nonfinite_tick_fail(self):
        for value in (None,'NaN',True):
            self.tick['time']=value
            self.assertIn('MT5_TICK_INVALID',self.check())
    def test_symbol_mismatch_and_invalid_grid(self):
        self.symbol.update(name='GBPUSD',volume_step=0)
        self.assertIn('MT5_SYMBOL_IDENTITY_UNVERIFIED',self.check())
        self.assertIn('MT5_SYMBOL_PROPERTIES_INVALID',self.check())
    def test_currency_and_account_identity_unknown(self):
        self.account={}
        self.assertIn('MT5_ACCOUNT_IDENTITY_UNVERIFIED',self.check())
        self.assertIn('MT5_ACCOUNT_CURRENCY_UNSUPPORTED_OR_UNKNOWN',self.check())
    def test_missing_diagnostic_record(self):
        self.account=None
        self.assertEqual(self.check(),['MT5_DIAGNOSTIC_DATA_MISSING'])
