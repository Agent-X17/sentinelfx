import json
import tempfile
import threading
import unittest
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from engine.service import Application
from engine.seed import sample_signal
from server import make_server

class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.app=Application(cls.temp.name+'/http.sqlite3')
        cls.server=make_server(cls.app,0);cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.url='http://127.0.0.1:'+str(cls.server.server_port)
        with urlopen(cls.url+'/api/state') as r: cls.token=json.load(r)['csrf_token']
    @classmethod
    def tearDownClass(cls): cls.server.shutdown();cls.server.server_close();cls.thread.join();cls.temp.cleanup()
    def request(self,path,payload=None,headers=None):
        h={'Content-Type':'application/json','X-CSRF-Token':self.token};h.update(headers or {})
        data=None if payload is None else json.dumps(payload).encode()
        return urlopen(Request(self.url+path,data=data,headers=h),timeout=5)
    def test_health(self):
        with self.request('/api/health') as r:
            h=json.load(r);self.assertFalse(h['live_execution_enabled']);self.assertEqual(h['mode'],'SIMULATION')
    def test_static_csp(self):
        with self.request('/') as r:
            self.assertIn("frame-ancestors 'none'",r.headers['Content-Security-Policy']);self.assertIn('SENTINELFX',r.read().decode())
    def test_no_csrf(self):
        with self.assertRaises(HTTPError) as ctx:self.request('/api/evaluate',{}, {'X-CSRF-Token':''})
        self.assertEqual(ctx.exception.code,403)
    def test_bad_origin(self):
        with self.assertRaises(HTTPError) as ctx:self.request('/api/evaluate',{}, {'Origin':'https://attacker.invalid'})
        self.assertEqual(ctx.exception.code,403)
    def test_bad_host(self):
        with self.assertRaises(HTTPError) as ctx:self.request('/api/state',headers={'Host':'attacker.invalid'})
        self.assertEqual(ctx.exception.code,403)
    def test_bad_webhook_host_is_audited(self):
        before=len(self.app.snapshot()['audit'])
        with self.assertRaises(HTTPError) as ctx:self.request('/api/webhook/tradingview',{}, {'Host':'attacker.invalid','X-CSRF-Token':''})
        self.assertEqual(ctx.exception.code,403)
        audit=self.app.snapshot()['audit']
        self.assertEqual(len(audit),before+1);self.assertEqual(audit[0]['event'],'WEBHOOK_HOST_REJECTED')
    def test_no_live_endpoint(self):
        with self.assertRaises(HTTPError) as ctx:self.request('/api/execute',{})
        self.assertEqual(ctx.exception.code,404)
    def test_path_traversal(self):
        with self.assertRaises(HTTPError) as ctx:self.request('/../server.py')
        self.assertEqual(ctx.exception.code,404)
    def test_malformed_json_is_logged(self):
        before=len(self.app.snapshot()['audit'])
        req=Request(self.url+'/api/evaluate',data=b'{invalid',headers={'X-CSRF-Token':self.token,'Content-Type':'application/json'})
        with self.assertRaises(HTTPError) as ctx:urlopen(req)
        self.assertEqual(ctx.exception.code,400);self.assertEqual(len(self.app.snapshot()['audit']),before+1)
    def test_invalid_signal_shape_logged(self):
        with self.request('/api/evaluate',{'signal':{'symbol':[]}}) as r:self.assertEqual(json.load(r)['decision'],'NO_TRADE')
    def test_ui_to_api_to_storage(self):
        with self.request('/api/scenario',{'scenario':'safe','account_profile':'ACCOUNT_C'}) as r: result=json.load(r)
        self.assertEqual(result['decision'],'APPROVED_SIMULATED_TRADE')
        self.assertIsInstance(result['calculations']['estimated_max_loss'],str)
        with self.request('/api/close',{'position_id':result['position_id'],'outcome':'stop'}) as r: outcome=json.load(r)
        with self.request('/api/state') as r:snap=json.load(r)
        a=next(a for a in snap['accounts'] if a['id']=='ACCOUNT_C')
        self.assertLess(float(a['equity']),150);self.assertEqual(a['open_positions'],0);self.assertTrue(snap['audit_integrity'])
    def test_research_cannot_grant_execution(self):
        with self.request('/api/research',{'kind':'providers','record':{'id':'external','name':'External','verified_status':True,'status':'ACTIVE'}}) as r:p=json.load(r)
        self.assertFalse(p['verified_status']);self.assertEqual(p['status'],'UNVERIFIED')

if __name__=='__main__': unittest.main()
