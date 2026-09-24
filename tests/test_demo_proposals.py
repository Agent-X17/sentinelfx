import json
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from datetime import timedelta
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from engine.bridge import TradingBridge
from engine.config import Settings
from engine.domain import stamp, utcnow
from engine.mt5 import MockMT5Service, MT5Result
from engine.service import Application
from engine.webhook import SymbolMapper, TradingViewWebhookService, WebhookAuthenticator
from server import make_server


class TrackingMT5(MockMT5Service):
    def __init__(self, *args, positions=None, orders=None, **kwargs):
        super().__init__(*args, **kwargs); self.sent=0; self.positions=positions or []; self.orders=orders or []
    def positions_get(self, **kwargs): return MT5Result(True,'MT5_OK','positions',{'items':self.positions})
    def orders_get(self, **kwargs): return MT5Result(True,'MT5_OK','orders',{'items':self.orders})
    def order_send(self, request): self.sent+=1; return super().order_send(request)


class DemoProposalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.db=self.temp.name+'/db.sqlite3'
        self.base=Settings(mode='SIMULATION',database_path=self.db,webhook_secret='unit-secret-value',default_account_profile='ACCOUNT_LIVE')
    def tearDown(self): self.temp.cleanup()
    def settings(self, **changes):
        return replace(self.base,demo_trade_proposals_enabled=True,demo_trade_proposal_kill_switch=False,
                       demo_expected_account_login='900001',demo_expected_broker_server='SENTINELFX-DEMO-FIXTURE',**changes)
    def mt5(self, **changes):
        account={'login':900001,'server':'SENTINELFX-DEMO-FIXTURE','currency':'USD','trade_mode':0,'balance':500,'equity':500,'margin':0,'margin_free':500,'margin_level':0,'leverage':500,'trade_allowed':True,'snapshot_at':stamp()}
        account.update(changes.pop('account',{}))
        symbol={'name':'EURUSD.a','trade_contract_size':1000,'volume_min':.01,'volume_max':100,'volume_step':.01,'point':.00001,'digits':5,'trade_mode':4}
        tick={'bid':1.1000,'ask':1.1001,'time':time.time()}
        positions=changes.pop('positions',[]); orders=changes.pop('orders',[])
        adapter=MockMT5Service(account=account,symbols={'EURUSD.a':symbol},ticks={'EURUSD.a':tick},**changes)
        adapter.sent=0
        adapter.positions_get=lambda **kwargs: MT5Result(True,'MT5_OK','positions',{'items':positions})
        adapter.orders_get=lambda **kwargs: MT5Result(True,'MT5_OK','orders',{'items':orders})
        def refused_send(request):
            adapter.sent+=1
            return MT5Result(False,'LIVE_EXECUTION_NOT_IMPLEMENTED','Order submission is disabled')
        adapter.order_send=refused_send
        return adapter
    def payload(self, alert='proposal-1', **changes):
        p={'alert_id':alert,'symbol':'OANDA:EUR/USD','side':'BUY','timeframe':'H1','strategy':'test breakout','timestamp':stamp(),'entry':'1.1001','stop_loss':'1.0980','take_profit':'1.1045','metadata':{'strategy_type':'breakout'}}
        p.update(changes); return p
    def bridge(self, settings=None, mt5=None):
        settings=settings or self.settings(); app=Application(self.db,settings=settings); adapter=mt5 or self.mt5()
        bridge=TradingBridge(app,TradingViewWebhookService(SymbolMapper({'EURUSD':['EURUSD.a']}),300),WebhookAuthenticator(settings.webhook_secret),adapter,settings)
        return app,adapter,bridge

    def test_feature_disabled_by_default(self):
        app,adapter,bridge=self.bridge(self.base)
        result=bridge.ingest(self.payload(),'unit-secret-value')
        self.assertNotEqual(result.get('decision'),'DEMO_TRADE_PROPOSAL')
        self.assertEqual(app.snapshot()['demo_trade_proposals'],[])

    def test_valid_proposal_creation_has_exact_fields_and_no_paper_trade(self):
        app,adapter,bridge=self.bridge(); result=bridge.ingest(self.payload(),'unit-secret-value')
        self.assertEqual(result['decision'],'DEMO_TRADE_PROPOSAL'); self.assertFalse(result['order_sent']); self.assertEqual(adapter.sent,0)
        proposal=app.snapshot()['demo_trade_proposals'][0]
        for key in ('symbol','direction','entry_reference_price','stop_loss','take_profit','exact_volume','monetary_risk','risk_reward','account_snapshot','risk_vetoes','warnings'):
            self.assertIn(key,proposal)
        self.assertEqual(proposal['status'],'PENDING_LOCAL_REVIEW'); self.assertEqual(app.snapshot()['open_positions'],[])
        self.assertLessEqual(float(proposal['monetary_risk']),.75)

    def test_stale_account_snapshot_is_no_trade(self):
        old=(utcnow()-timedelta(minutes=2)).isoformat(); app,_,bridge=self.bridge(mt5=self.mt5(account={'snapshot_at':old}))
        result=bridge.ingest(self.payload(),'unit-secret-value')
        self.assertEqual(result['decision'],'NO_TRADE'); self.assertEqual(result['reason'],'MT5_ACCOUNT_SNAPSHOT_STALE_OR_FUTURE'); self.assertEqual(app.snapshot()['demo_trade_proposals'],[])

    def test_account_mismatch_is_no_trade(self):
        app,_,bridge=self.bridge(mt5=self.mt5(account={'login':123}))
        self.assertEqual(bridge.ingest(self.payload(),'unit-secret-value')['reason'],'MT5_ACCOUNT_MISMATCH')
        self.assertEqual(app.snapshot()['demo_trade_proposals'],[])

    def test_external_exposure_is_no_trade(self):
        app,_,bridge=self.bridge(mt5=self.mt5(positions=[{'ticket':1}]))
        self.assertEqual(bridge.ingest(self.payload(),'unit-secret-value')['reason'],'MT5_EXTERNAL_EXPOSURE')
        self.assertEqual(app.snapshot()['demo_trade_proposals'],[])

    def test_duplicate_webhook_cannot_create_second_proposal(self):
        app,_,bridge=self.bridge(); bridge.ingest(self.payload(),'unit-secret-value')
        result=bridge.ingest(self.payload(),'unit-secret-value')
        self.assertEqual(result['reason'],'DUPLICATE_WEBHOOK'); self.assertEqual(len(app.snapshot()['demo_trade_proposals']),1)

    def test_approval_only_changes_state_and_never_sends(self):
        app,adapter,bridge=self.bridge(); proposal=bridge.ingest(self.payload(),'unit-secret-value')['proposal']
        reviewed=app.review_demo_proposal(proposal['id'],'approve','Reviewed complete local evidence')
        self.assertEqual(reviewed['status'],'APPROVED_FOR_FUTURE_DEMO_EXECUTION'); self.assertFalse(reviewed['order_sent']); self.assertEqual(adapter.sent,0); self.assertEqual(app.snapshot()['open_positions'],[])

    def test_reject_cancel_and_expiry_are_audited(self):
        for index,action in enumerate(('reject','cancel')):
            path=self.temp.name+f'/db{index}.sqlite3'; settings=replace(self.settings(),database_path=path); app=Application(path,settings=settings)
            bridge=TradingBridge(app,TradingViewWebhookService(SymbolMapper({'EURUSD':['EURUSD.a']}),300),WebhookAuthenticator(settings.webhook_secret),self.mt5(),settings)
            p=bridge.ingest(self.payload(f'action-{index}'),'unit-secret-value')['proposal']; app.review_demo_proposal(p['id'],action,'Local test decision')
            self.assertEqual(app.snapshot()['demo_trade_proposals'][0]['status'],{'reject':'REJECTED','cancel':'CANCELLED'}[action])
        app,_,bridge=self.bridge(); p=bridge.ingest(self.payload('expiry'),'unit-secret-value')['proposal']
        with app.store.transaction() as db: db.execute("UPDATE demo_trade_proposals SET expires_at=? WHERE id=?",((utcnow()-timedelta(seconds=1)).isoformat(),p['id']))
        state=app.snapshot(); self.assertEqual(state['demo_trade_proposals'][0]['status'],'EXPIRED')
        events=[x['action'] for x in state['demo_trade_proposal_history']]; self.assertIn('EXPIRED',events)

    def test_one_active_proposal_limit(self):
        app,_,bridge=self.bridge(); bridge.ingest(self.payload('first'),'unit-secret-value')
        result=bridge.ingest(self.payload('second'),'unit-secret-value')
        self.assertEqual(result['decision'],'NO_TRADE'); self.assertEqual(result['reason'],'ONE_ACTIVE_DEMO_PROPOSAL_LIMIT'); self.assertEqual(len(app.snapshot()['demo_trade_proposals']),1)

    def test_kill_switch_blocks_creation_and_approval(self):
        killed=replace(self.settings(),demo_trade_proposal_kill_switch=True); app,_,bridge=self.bridge(killed)
        self.assertEqual(bridge.ingest(self.payload(),'unit-secret-value')['reason'],'DEMO_TRADE_PROPOSAL_KILL_SWITCH_ACTIVE')
        self.assertEqual(app.snapshot()['demo_trade_proposals'],[])
        app,_,bridge=self.bridge(); p=bridge.ingest(self.payload('approval-kill'),'unit-secret-value')['proposal']; app.settings=replace(app.settings,demo_trade_proposal_kill_switch=True)
        with self.assertRaisesRegex(Exception,'APPROVAL_DISABLED'): app.review_demo_proposal(p['id'],'approve','Should remain blocked')

    def test_audit_complete_and_secrets_redacted(self):
        app,_,bridge=self.bridge(); payload=self.payload(metadata={'note':'unit-secret-value','password':'hidden-password'})
        p=bridge.ingest(payload,'unit-secret-value')['proposal']; app.review_demo_proposal(p['id'],'reject','Local rejection reason')
        state=app.snapshot(); events=[r['event'] for r in state['audit']]
        self.assertIn('DEMO_PROPOSAL_CREATED',events); self.assertIn('DEMO_PROPOSAL_REJECT',events)
        rendered=str(state)
        for secret in ('unit-secret-value','hidden-password','900001','SENTINELFX-DEMO-FIXTURE'): self.assertNotIn(secret,rendered)
        with app.store.connect() as db:
            persisted=' '.join(str(tuple(row)) for table in ('raw_webhooks','account_snapshots','demo_trade_proposals','demo_trade_proposal_history','audit_logs') for row in db.execute('SELECT * FROM '+table))
        for secret in ('unit-secret-value','hidden-password','900001','SENTINELFX-DEMO-FIXTURE'): self.assertNotIn(secret,persisted)
        self.assertGreaterEqual(len(state['demo_trade_proposal_history']),2)


class DemoProposalHTTPTests(unittest.TestCase):
    def test_proposal_mutation_requires_csrf(self):
        with tempfile.TemporaryDirectory() as folder:
            settings=Settings(database_path=folder+'/db.sqlite3'); app=Application(settings.database_path,settings=settings)
            server=make_server(app,0,settings=settings); thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
            try:
                url='http://127.0.0.1:'+str(server.server_port)
                req=Request(url+'/api/demo-proposals/review',data=json.dumps({'proposal_id':'x','action':'approve','reason':'test'}).encode(),headers={'Content-Type':'application/json','X-CSRF-Token':''})
                with self.assertRaises(HTTPError) as caught: urlopen(req,timeout=5)
                self.assertEqual(caught.exception.code,403)
            finally: server.shutdown();server.server_close();thread.join()


if __name__=='__main__': unittest.main()
