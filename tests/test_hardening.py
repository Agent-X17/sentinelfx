import tempfile
import unittest
from collections import namedtuple
from types import SimpleNamespace
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

from tests import test_bridge
from engine.mt5 import MT5Service, MT5Result
from engine.config import Settings
from engine.storage import Store


class HardenedBridgeTests(unittest.TestCase):
    setUp = test_bridge.BridgeTests.setUp
    tearDown = test_bridge.BridgeTests.tearDown
    payload = test_bridge.BridgeTests.payload
    def test_failed_auth_does_not_poison_alert(self):
        for _ in range(2):
            self.assertEqual(self.bridge.ingest(self.payload(), 'wrong')['reason'], 'WEBHOOK_AUTH_FAILED')
        self.assertTrue(self.bridge.ingest(self.payload(), 'test-secret')['accepted'])

    def test_secret_removed_from_persistence(self):
        self.bridge.ingest(self.payload(secret='must-not-persist'), 'test-secret')
        with self.app.store.connect() as db:
            for table in ('raw_webhooks', 'audit_logs'):
                self.assertNotIn('must-not-persist', str([tuple(r) for r in db.execute('SELECT * FROM '+table)]))

    def test_changed_header_cannot_replay_alert(self):
        self.bridge.ingest(self.payload(), 'test-secret', 'header-1')
        self.assertEqual(self.bridge.ingest(self.payload(), 'test-secret', 'header-2')['status'], 'duplicate')

    def test_concurrent_delivery_processed_once(self):
        payload = self.payload()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.bridge.ingest(payload, 'test-secret'), range(2)))
        self.assertEqual(sorted(r['status'] for r in results), ['duplicate', 'processed'])
        self.assertEqual(len(self.app.snapshot()['open_positions']), 1)

    def test_journal_failure_rolls_back_entire_candidate(self):
        original = Store.audit
        def audit(db, event, payload):
            if event == 'JOURNAL_ENTRY':
                raise RuntimeError('journal unavailable')
            return original(db, event, payload)
        with patch.object(Store, 'audit', side_effect=audit):
            with self.assertRaises(RuntimeError):
                self.bridge.ingest(self.payload(), 'test-secret')
        state = self.app.snapshot()
        self.assertEqual(state['open_positions'], [])
        self.assertEqual(state['raw_webhooks'], [])
        self.assertTrue(state['audit_integrity'])

    def test_external_position_any_symbol_blocks(self):
        self.mt5.positions_get = lambda **kw: MT5Result(True, 'OK', '', {'items':[{'symbol':'GBPUSD'}]})
        self.assertEqual(self.bridge.ingest(self.payload(), 'test-secret')['reason'], 'MT5_EXTERNAL_EXPOSURE')

    def test_pending_order_blocks(self):
        self.mt5.orders_get = lambda **kw: MT5Result(True, 'OK', '', {'items':[{'ticket':1}]})
        self.assertEqual(self.bridge.ingest(self.payload(), 'test-secret')['reason'], 'MT5_EXTERNAL_EXPOSURE')

    def test_unknown_exposure_blocks(self):
        self.mt5.positions_get = lambda **kw: MT5Result(True, 'OK', '', {})
        self.assertEqual(self.bridge.ingest(self.payload(), 'test-secret')['reason'], 'MT5_EXPOSURE_UNKNOWN')

    def test_real_adapter_cannot_use_fixture_evidence(self):
        real = MT5Service(enabled=True)
        for method in ('status','symbol_info','symbol_info_tick','account_info','positions_get','orders_get'):
            setattr(real, method, getattr(self.mt5, method))
        self.bridge.mt5 = real
        result = self.bridge.ingest(self.payload(metadata={'synthetic':True}), 'test-secret')
        self.assertEqual(result['reason'], 'REQUIRED_EXTERNAL_EVIDENCE_UNVERIFIED')
        state = self.app.snapshot()
        self.assertEqual(state['open_positions'], [])
        self.assertEqual(state['raw_webhooks'][0]['status'], 'blocked')
        self.assertFalse(any(p['id']=='tradingview' for p in state['providers']))

    def test_fixture_is_labelled(self):
        result=self.bridge.ingest(self.payload(), 'test-secret')
        self.assertEqual(result['decision']['evidence_source'], 'SYNTHETIC_TEST_FIXTURE')
        self.assertFalse(result['decision']['order_sent'])


class AdapterTests(unittest.TestCase):
    def adapter(self, retcode=0):
        result = namedtuple('Check', 'retcode comment')
        module = SimpleNamespace(
            terminal_info=lambda: SimpleNamespace(connected=True),
            order_check=lambda request: result(retcode, 'check'),
            account_info=lambda: namedtuple('Account', 'equity currency')(50, 'USD'),
            symbol_select=lambda *args: False,
            order_calc_margin=lambda *args: 1.25)
        adapter=MT5Service(enabled=True, module=module)
        adapter._connected=True
        return adapter

    def test_namedtuple_account_fields_survive(self):
        self.assertEqual(self.adapter().account_info().data, {'equity':50,'currency':'USD'})

    def test_failed_order_check_retcode_is_rejection(self):
        self.assertFalse(self.adapter(10019).order_check({}).ok)

    def test_successful_order_check_retcode(self):
        self.assertTrue(self.adapter().order_check({}).ok)

    def test_terminal_disconnect_detected_after_initialization(self):
        adapter=self.adapter()
        adapter._module.terminal_info=lambda: SimpleNamespace(connected=False)
        self.assertFalse(adapter.status().ok)
        self.assertFalse(adapter.account_info().ok)

    def test_numeric_and_false_results(self):
        adapter=self.adapter()
        self.assertEqual(adapter.order_calc_margin(0,'EURUSD',.01,1).data, {'value':1.25})
        self.assertFalse(adapter.symbol_select('EURUSD').ok)

    def test_external_host_requires_secret(self):
        with self.assertRaises(ValueError):
            Settings(webhook_allowed_hosts=('example.test',)).validate()
