import copy
import tempfile
import time
import unittest
from dataclasses import replace
from datetime import timedelta

from engine.bridge import TradingBridge
from engine.config import Settings
from engine.domain import InvalidData, stamp, utcnow
from engine.isolated_mt5 import SnapshotMT5
from engine.mt5 import MT5Result
from engine.mt5_evidence import PROTOCOL, validate_snapshot, validate_order_request
from engine.service import Application
from engine.webhook import SymbolMapper, TradingViewWebhookService, WebhookAuthenticator


def ok(data): return {'ok':True,'code':'MT5_OK','message':'read only','data':data}


def fixture():
    now=time.time()
    return {
        'protocol':PROTOCOL,'operation':'snapshot','captured_at':stamp(),
        'status':{'ok':True,'code':'MT5_CONNECTED','message':'connected','data':None},
        'terminal_info':ok({'connected':True,'trade_allowed':False}),
        'account_info':ok({'login':900001,'server':'DEMO-SERVER','currency':'USD','trade_mode':0,'trade_allowed':True,
                           'balance':500,'equity':500,'margin':0,'margin_free':500,'margin_level':0,'leverage':500}),
        'symbol_info':ok({'name':'EURUSD.a','visible':True,'trade_mode':4,'trade_contract_size':1000,'volume_min':.01,
                          'volume_max':100,'volume_step':.01,'point':.00001,'digits':5,'trade_stops_level':0,
                          'trade_freeze_level':0,'filling_mode':1}),
        'symbol_info_tick':ok({'time':now,'time_msc':int(now*1000),'bid':1.1000,'ask':1.1001}),
        'positions_get':ok({'items':[]}),'orders_get':ok({'items':[]}),'recent_deals':ok({'items':[]}),'recent_orders':ok({'items':[]}),
    }


class Checker:
    def __init__(self, app=None, ok_result=True): self.app=app;self.calls=[];self.ok_result=ok_result
    def read_only_order_check(self,request):
        if self.app is not None:
            assert getattr(self.app.store._transactions,'connection',None) is None
        self.calls.append(request)
        return MT5Result(self.ok_result,'MT5_ORDER_CHECK_PASSED' if self.ok_result else 'MT5_ORDER_CHECK_FAILED','check',{'non_submitting':True})


class EvidenceValidationTests(unittest.TestCase):
    signal={'mt5_symbol':'EURUSD.a'}
    def validate(self,value): return validate_snapshot(value,self.signal,'900001','DEMO-SERVER')
    def rejected(self,mutate,code):
        value=fixture();mutate(value)
        with self.assertRaisesRegex(InvalidData,code): self.validate(value)

    def test_complete_fixture_is_verified_and_redaction_safe(self):
        result=self.validate(fixture());self.assertTrue(result['demo_account_proven']);self.assertNotIn('900001',str(result));self.assertNotIn('DEMO-SERVER',str(result))
    def test_stale_and_live_account_fail(self):
        self.rejected(lambda x:x.update(captured_at=(utcnow()-timedelta(minutes=2)).isoformat()),'MT5_EVIDENCE_SNAPSHOT_STALE')
        self.rejected(lambda x:x['account_info']['data'].update(trade_mode=2),'MT5_ACCOUNT_NOT_CONFIRMED_DEMO')
    def test_identity_disconnect_and_autotrading_fail(self):
        self.rejected(lambda x:x['account_info']['data'].update(login=2),'MT5_ACCOUNT_MISMATCH')
        self.rejected(lambda x:x['terminal_info']['data'].update(connected=False),'MT5_TERMINAL_DISCONNECTED')
        self.rejected(lambda x:x['terminal_info']['data'].update(trade_allowed=True),'MT5_TERMINAL_AUTOTRADING_NOT_PROVEN_OFF')
    def test_current_and_recent_exposure_fail(self):
        self.rejected(lambda x:x['positions_get']['data']['items'].append({'ticket':1}),'MT5_EXTERNAL_EXPOSURE')
        self.rejected(lambda x:x['recent_deals']['data']['items'].append({'ticket':2}),'MT5_EXTERNAL_EXPOSURE')
    def test_symbol_tick_and_microstructure_fail_closed(self):
        self.rejected(lambda x:x['symbol_info']['data'].update(name='GBPUSD'),'MT5_SYMBOL_IDENTITY_UNVERIFIED')
        self.rejected(lambda x:x['symbol_info']['data'].pop('filling_mode'),'MT5_SYMBOL_PROPERTIES_INVALID')
        self.rejected(lambda x:x['symbol_info_tick']['data'].update(time=time.time()-60,time_msc=int((time.time()-60)*1000)),'MT5_TICK_STALE_OR_FUTURE')
    def test_exact_request_obeys_grid_precision_and_stop_level(self):
        value=fixture();value['symbol_info']['data']['trade_stops_level']=20
        valid={'type':'BUY','volume':'0.01','price':'1.10010','sl':'1.09800','tp':'1.10450'}
        self.assertTrue(validate_order_request(value,valid))
        with self.assertRaisesRegex(InvalidData,'VOLUME_GRID'): validate_order_request(value,{**valid,'volume':'0.015'})
        with self.assertRaisesRegex(InvalidData,'STOP_LEVEL'): validate_order_request(value,{**valid,'sl':'1.10000'})
    def test_worker_source_has_no_submission_operation(self):
        from pathlib import Path
        source=(Path(__file__).parent.parent/'engine'/'mt5_worker.py').read_text()
        self.assertNotIn('order_'+'send',source)


class VerifiedProposalIntegrationTests(unittest.TestCase):
    def test_verified_real_snapshot_creates_proposal_and_check_runs_outside_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            settings=replace(Settings(database_path=folder+'/db.sqlite3',webhook_secret='long-local-test-secret',default_account_profile='ACCOUNT_LIVE'),
                demo_trade_proposals_enabled=True,demo_trade_proposal_kill_switch=False,demo_expected_account_login='900001',demo_expected_broker_server='DEMO-SERVER')
            app=Application(settings.database_path,settings=settings); checker=Checker(app)
            bridge=TradingBridge(app,TradingViewWebhookService(SymbolMapper({'EURUSD':['EURUSD.a']}),300),WebhookAuthenticator(settings.webhook_secret),SnapshotMT5(fixture()),settings,checker,fixture())
            payload={'alert_id':'verified-1','symbol':'EURUSD','side':'BUY','timeframe':'H1','strategy':'test breakout','timestamp':stamp(),'entry':'1.1001','stop_loss':'1.0980','take_profit':'1.1045','metadata':{'strategy_type':'breakout'}}
            result=bridge.ingest(payload,settings.webhook_secret)
            self.assertEqual(result['decision'],'DEMO_TRADE_PROPOSAL');self.assertFalse(result['order_sent']);self.assertEqual(len(checker.calls),1)
            proposal=result['proposal'];self.assertEqual(proposal['evidence']['evidence_source'],'VERIFIED_READ_ONLY_MT5');self.assertFalse(proposal['execution_implemented'])
            self.assertNotIn('900001',str(app.snapshot()));self.assertNotIn('DEMO-SERVER',str(app.snapshot()))


if __name__=='__main__': unittest.main()
