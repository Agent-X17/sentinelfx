import json
import sqlite3
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from tests import test_bridge
from engine.domain import stamp
from engine.storage import Store
from engine.mt5 import MT5Service
from engine.isolated_mt5 import IsolatedMT5Service
from engine.mt5_worker import collect
from engine.redaction import redact
from engine.diagnostics import validate_diagnostics
from server import strict_object


class IntakeTests(unittest.TestCase):
    setUp=test_bridge.BridgeTests.setUp
    tearDown=test_bridge.BridgeTests.tearDown
    payload=test_bridge.BridgeTests.payload

    def test_invalid_id_cannot_poison_truncated_valid_id(self):
        self.assertEqual(self.bridge.ingest(self.payload(alert_id='a'*195),'test-secret')['status'],'malformed')
        self.assertNotEqual(self.bridge.ingest(self.payload(alert_id='a'*194),'test-secret')['status'],'duplicate')

    def test_invalid_payload_does_not_poison_corrected_delivery(self):
        self.bridge.ingest(self.payload(stop_loss=None),'test-secret')
        self.assertTrue(self.bridge.ingest(self.payload(),'test-secret')['accepted'])

    def test_redaction_does_not_collapse_distinct_identities(self):
        first=self.payload(alert_id='test-secret')
        second=self.payload(alert_id='[REDACTED]')
        self.bridge.ingest(first,'test-secret')
        result=self.bridge.ingest(second,'test-secret')
        self.assertNotEqual(result['status'],'duplicate')
        self.assertNotIn('test-secret',str(self.app.snapshot()['raw_webhooks']))

    def test_header_and_body_secret_values_are_both_filtered(self):
        self.bridge.ingest(self.payload(secret='body-only-value',metadata={'note':'body-only-value'}),'test-secret')
        self.assertNotIn('body-only-value',str(self.app.snapshot()['raw_webhooks']))
        self.assertNotIn('body-only-value',str(self.app.snapshot()['audit']))

    def test_id_whitespace_normalizes_and_replays(self):
        self.bridge.ingest(self.payload(alert_id='  abc  '),'test-secret')
        self.assertEqual(self.bridge.ingest(self.payload(alert_id='abc'),'test-secret')['status'],'duplicate')

    def test_legacy_identity_is_still_recognized(self):
        with self.app.store.transaction() as db:
            self.bridge._persist_raw(db,'old','alert:alert-1',{},'blocked','test')
        self.assertEqual(self.bridge.ingest(self.payload(),'test-secret')['status'],'duplicate')

    def test_canonical_fallback_prevents_formatting_replay(self):
        payload=self.payload(alert_id=None)
        self.bridge.ingest(payload,'test-secret')
        payload.update(symbol='EURUSD',side=' buy ')
        self.assertEqual(self.bridge.ingest(payload,'test-secret')['status'],'duplicate')

    def test_empty_expiry_is_not_defaulted(self):
        for expiry in ('',None,False,0):
            self.assertFalse(self.webhook.validate(self.payload(expires_at=expiry)).accepted)

    def test_invalid_mapping_fails_closed(self):
        for mapping in ([None],[''],42,{'name':'EURUSD'}):
            self.mapper.mappings['EURUSD']=mapping
            self.assertFalse(self.webhook.validate(self.payload()).accepted)

    def test_mock_cannot_overwrite_placeholder_equity(self):
        self.bridge.ingest(self.payload(),'test-secret')
        account=next(a for a in self.app.snapshot()['accounts'] if a['id']=='ACCOUNT_LIVE')
        self.assertEqual(account['equity'],'300')
        self.assertEqual(account['starting_equity'],'300')

    def test_real_reads_happen_outside_write_transaction(self):
        real=MT5Service(True)
        for name in ('status','symbol_info','symbol_info_tick','account_info','positions_get','orders_get'):
            setattr(real,name,getattr(self.mt5,name))
        original=real.symbol_info
        def inspect(symbol):
            self.assertIsNone(getattr(self.app.store._transactions,'connection',None))
            with self.app.store.transaction() as db: Store.audit(db,'CONCURRENT_DIAGNOSTIC_TEST',{})
            return original(symbol)
        real.symbol_info=inspect;self.bridge.mt5=real
        self.assertEqual(self.bridge.ingest(self.payload(),'test-secret')['reason'],'REQUIRED_EXTERNAL_EVIDENCE_UNVERIFIED')
        self.assertEqual(self.app.snapshot()['open_positions'],[])

    def test_identity_change_during_collection_is_blocked(self):
        original=self.mt5.account_info
        count=[0]
        def account():
            result=original();count[0]+=1
            result.data.update(login=count[0],server='demo',currency='USD')
            return result
        self.mt5.account_info=account
        self.assertEqual(collect(self.mt5,'EURUSD.a')['status']['code'],'MT5_ACCOUNT_CHANGED')


class HygieneTests(unittest.TestCase):
    def test_invalid_quotes_and_volume_are_explicit(self):
        result=validate_diagnostics({'mt5_symbol':'EURUSD'}, {'name':'GBPUSD','volume_step':-1},
            {'time':__import__('time').time(),'bid':1.2,'ask':1.1},{})
        for code in ('MT5_QUOTE_INVALID','MT5_SYMBOL_IDENTITY_UNVERIFIED','MT5_SYMBOL_PROPERTIES_INVALID'):
            self.assertIn(code,result)

    def test_missing_order_check_code_fails(self):
        from tests import test_hardening
        self.assertFalse(test_hardening.AdapterTests().adapter(None).order_check({}).ok)

    def test_secret_in_keys_and_nested_lists(self):
        output=redact({'actual-secret': [{'api-key':'other-secret'}]},('actual-secret',))
        self.assertNotIn('actual-secret',str(output));self.assertNotIn('other-secret',str(output))

    def test_duplicate_json_fields_rejected(self):
        with self.assertRaises(ValueError):json.loads('{"secret":"one","secret":"two"}',object_pairs_hook=strict_object)

    def test_pathological_depth_is_bounded(self):
        value={}
        for _ in range(100):value={'nested':value}
        self.assertIn('nesting limit',str(redact(value)))

    def test_invalid_account_identity_types(self):
        for login in (True,-1,'123',[]):
            failures=validate_diagnostics({'mt5_symbol':'EURUSD'},{},{},{'login':login,'server':' ','currency':'EUR'})
            self.assertIn('MT5_ACCOUNT_IDENTITY_UNVERIFIED',failures)
            self.assertIn('MT5_ACCOUNT_CURRENCY_UNSUPPORTED_OR_UNKNOWN',failures)


class WorkerTests(unittest.TestCase):
    def test_timeout_returns_failure_and_never_order(self):
        service=IsolatedMT5Service(True,timeout=.1)
        with patch('engine.isolated_mt5.subprocess.run',side_effect=subprocess.TimeoutExpired('worker',.1)):
            self.assertEqual(service.status().code,'MT5_TIMEOUT')
        self.assertEqual(service.order_send({}).code,'LIVE_EXECUTION_NOT_IMPLEMENTED')

    def test_worker_executable_cannot_be_selected_by_payload(self):
        service=IsolatedMT5Service(True)
        response=SimpleNamespace(stdout=json.dumps({'status':{'ok':False,'code':'TEST','message':'fixture','data':None}}))
        with patch('engine.isolated_mt5.subprocess.run',return_value=response) as run:
            service.snapshot('EURUSD')
            args,kw=run.call_args
            self.assertEqual(args[0][-1],'engine.mt5_worker')
            self.assertEqual(kw['timeout'],5)
            self.assertNotIn('shell',kw)

    def test_identity_drift_latches(self):
        service=IsolatedMT5Service(True)
        def response(login):
            return SimpleNamespace(stdout=json.dumps({'status':{'ok':True,'code':'MT5_CONNECTED','message':'','data':None},'account_info':{'data':{'login':login,'server':'demo','currency':'USD'}}}))
        with patch('engine.isolated_mt5.subprocess.run',side_effect=[response(1),response(2)]) as run:
            self.assertTrue(service.snapshot('EURUSD')['status']['ok'])
            self.assertEqual(service.snapshot('EURUSD')['status']['code'],'MT5_ACCOUNT_CHANGED')
            self.assertEqual(service.snapshot('EURUSD')['status']['code'],'MT5_ACCOUNT_CHANGED')
            self.assertEqual(run.call_count,2)

    def test_busy_worker_fails_without_waiting(self):
        service=IsolatedMT5Service(True)
        with service._lock:self.assertEqual(service.status().code,'MT5_BUSY')

    def test_disconnect_is_not_misreported_as_account_drift(self):
        service=IsolatedMT5Service(True);service._identity=(123,'demo','USD')
        response=SimpleNamespace(stdout=json.dumps({'status':{'ok':False,'code':'MT5_DISCONNECTED','message':'offline','data':None}}))
        with patch('engine.isolated_mt5.subprocess.run',return_value=response):
            self.assertEqual(service.snapshot('EURUSD')['status']['code'],'MT5_DISCONNECTED')
            self.assertFalse(service._drift)

    def test_invalid_worker_output_fails(self):
        with patch('engine.isolated_mt5.subprocess.run',return_value=SimpleNamespace(stdout='not-json')):
            self.assertEqual(IsolatedMT5Service(True).status().code,'MT5_WORKER_FAILED')


class DatabaseTests(unittest.TestCase):
    def test_tampered_audit_blocks_startup_and_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            from engine.service import Application
            source=Path(folder)/'source.db';app=Application(source)
            with sqlite3.connect(source) as db:
                db.execute('DROP TRIGGER audit_no_update')
                db.execute("UPDATE audit_logs SET hash='corrupt'")
            with self.assertRaisesRegex(ValueError,'Audit integrity'):Store(source)
            with self.assertRaisesRegex(ValueError,'NOT approved'):Store.copy_verified(source,Path(folder)/'bad-backup.db')

    def test_concurrent_initialization_preserves_migrations(self):
        with tempfile.TemporaryDirectory() as folder:
            path=str(Path(folder)/'db.sqlite3')
            with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(lambda _:Store(path),range(4)))
            with sqlite3.connect(path) as db:self.assertEqual(db.execute('SELECT version FROM schema_migrations').fetchall(),[(1,),(2,),(3,),(4,)])

    def test_backup_restore_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            from engine.service import Application
            source=Path(folder)/'source.db';backup=Path(folder)/'backup.db';restored=Path(folder)/'restored.db'
            app=Application(source)
            Store.copy_verified(source,backup);Store.copy_verified(backup,restored)
            self.assertTrue(Application(restored).snapshot()['audit_integrity'])
            with self.assertRaises(ValueError):Store.copy_verified(source,backup)

    def test_newer_schema_refuses_startup(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'db';Store(path)
            with sqlite3.connect(path) as db:db.execute("INSERT INTO schema_migrations VALUES(999,'future')")
            with self.assertRaisesRegex(ValueError,'newer'):Store(path)
