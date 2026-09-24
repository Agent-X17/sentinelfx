#!/usr/bin/env python3
"""Local-only dashboard/API. Run: python3 server.py"""
import argparse
import json
import os
import secrets
import sqlite3
import errno
import sys
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4
from engine.service import Application
from engine.domain import MODE,InvalidData,Policy,stamp,encode
from engine.seed import sample_signal
from engine.storage import Store,dumps
from engine.research import BacktestingService,CandleBacktestingService
from engine.config import Settings
from engine.mt5 import MT5Service
from engine.webhook import TradingViewWebhookService, WebhookAuthenticator, SymbolMapper
from engine.bridge import TradingBridge
from engine.redaction import redact

ROOT=Path(__file__).resolve().parent

def make_server(app,port=8765,bridge=None,mt5=None,settings=None):
    token=secrets.token_urlsafe(32)
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)
        def log_message(self,fmt,*args): pass
        def send(self,status,body,content_type='application/json'):
            data=body if isinstance(body,bytes) else dumps(body).encode()
            self.send_response(status);self.send_header('Content-Type',content_type);self.send_header('Content-Length',str(len(data)))
            self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers();self.wfile.write(data)
        def valid_host(self,webhook=False):
            host=(self.headers.get('Host') or '').lower()
            local=(f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}')
            allowed=tuple(settings.webhook_allowed_hosts) if webhook and settings else ()
            return host in local or host in allowed
        def do_GET(self):
            if not self.valid_host(): return self.send(403,{'error':'Invalid host'})
            path=urlparse(self.path).path
            try:
                if path=='/api/state': return self.send(200,dict(app.snapshot(),csrf_token=token,mt5=(mt5.status().to_dict() if mt5 else None),settings=(settings.public() if settings else None)))
                if path=='/api/health': return self.send(200,{'ok':True,'mode':settings.mode if settings else 'SIMULATION','live_execution_enabled':False,'mt5':mt5.status().to_dict() if mt5 else None})
                if path=='/api/mt5/status': return self.send(200,mt5.status().to_dict() if mt5 else {'ok':False,'code':'MT5_UNCONFIGURED'})
                if path=='/api/candle-sample': return self.send(200,json.loads((ROOT/'examples/synthetic-candles.json').read_text()))
                if path=='/api/sample':
                    s,m=sample_signal();return self.send(200,{'account_profile':'ACCOUNT_A','broker_id':'demo-cent','signal':s,'market':m})
                files={'/':('index.html','text/html; charset=utf-8'),'/app.js':('app.js','text/javascript; charset=utf-8'),'/style.css':('style.css','text/css; charset=utf-8')}
                if path in files:
                    f,mime=files[path];return self.send(200,(ROOT/'static'/f).read_bytes(),mime)
                self.send(404,{'error':'Not found'})
            except Exception:
                self.send(503,{'error':'Storage unavailable. No decision can be made.'})
        def do_POST(self):
            path=urlparse(self.path).path
            is_webhook=path=='/api/webhook/tradingview'
            if not self.valid_host(is_webhook): return self.send(403,{'error':'Invalid host'})
            if not is_webhook and self.headers.get('X-CSRF-Token')!=token:
                return self.send(403,{'error':'Local session token required'})
            origin=self.headers.get('Origin')
            if not is_webhook and origin and origin not in (f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}'):
                return self.send(403,{'error':'Cross-origin request refused'})
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=2_000_000: return self.send(413,{'error':'Invalid body size'})
                raw=self.rfile.read(length)
                try: payload=json.loads(raw,parse_constant=lambda x: (_ for _ in ()).throw(ValueError('Nonfinite JSON number')))
                except ValueError:
                    with app.store.transaction() as db: Store.audit(db,'MALFORMED_REQUEST',{'bytes':length,'reason':'Invalid JSON'})
                    return self.send(400,{'decision':'NO_TRADE','error':'Malformed JSON','live_execution_enabled':False})
                if not isinstance(payload,dict): raise InvalidData('JSON object required')
                if not is_webhook:
                    payload=redact(payload, (settings.webhook_secret,) if settings else ())
                if is_webhook:
                    if bridge is None: return self.send(503,{'error':'Webhook bridge unavailable','decision':'NO_TRADE'})
                    body_secret=payload.pop('secret',None)
                    supplied_secret=self.headers.get('X-Webhook-Secret') or body_secret
                    result=bridge.ingest(payload,supplied_secret,self.headers.get('Idempotency-Key'))
                    status = 401 if result.get('reason') == 'WEBHOOK_AUTH_FAILED' else {'malformed':400,'stale':422,'unmapped':422,'duplicate':409}.get(result.get('status'),200)
                    return self.send(status,result)
                elif path=='/api/evaluate': result=app.evaluate(payload)
                elif path=='/api/scenario':
                    if payload.get('scenario') not in ('safe','standard','stale','news','spread','missing-stop','correlation'): raise InvalidData('Unknown scenario')
                    s,m=sample_signal(payload['scenario'],payload.get('symbol','EURUSD'))
                    result=app.evaluate({'account_profile':payload.get('account_profile','ACCOUNT_A'),'broker_id':'demo-standard' if payload['scenario']=='standard' else 'demo-cent','signal':s,'market':m})
                elif path=='/api/close': result=app.close_position(payload.get('position_id'),payload.get('outcome'))
                elif path=='/api/review': result=app.review(payload.get('kind'),payload.get('id'),payload.get('notes'))
                elif path=='/api/withdrawals': result=app.withdrawal(payload)
                elif path=='/api/research':
                    kind=payload.get('kind'); record=payload.get('record')
                    if kind not in ('broker_profiles','providers','strategies') or not isinstance(record,dict): raise InvalidData('Invalid research record')
                    key=record.get('id')
                    if not isinstance(key,str) or not key or key.startswith('demo-'): raise InvalidData('Synthetic fixtures cannot be edited')
                    # Research edits never grant execution privileges or verification.
                    if kind=='providers': record.update(status='UNVERIFIED',verified_status=False,live_status='UNKNOWN')
                    if kind=='broker_profiles': record.update(synthetic=False,verification_status='UNCERTAIN',tanzania_availability='UNCERTAIN')
                    if kind=='strategies': record.update(edge_validated=False,status='RESEARCH_TEMPLATE')
                    with app.store.transaction() as db:
                        Store.put(db,kind,key,record);Store.audit(db,'RESEARCH_UPDATED',{'kind':kind,'record':record})
                    result=record
                elif path=='/api/backtest':
                    result=(CandleBacktestingService if 'candles' in payload else BacktestingService).run(payload);result.update(id=str(uuid4()),created_at=stamp(),strategy_id=payload.get('strategy_id','manual'))
                    with app.store.transaction() as db:
                        db.execute('INSERT INTO backtests VALUES(?,?,?)',(result['id'],result['strategy_id'],dumps(result)));Store.audit(db,'BACKTEST_RECORDED',result)
                else: return self.send(404,{'error':'No such operation; live execution is unavailable'})
                self.send(200,result)
            except (InvalidData,ValueError,TypeError,KeyError) as exc:
                try:
                    with app.store.transaction() as db: Store.audit(db,'REQUEST_REFUSED',{'path':urlparse(self.path).path,'reason':str(exc)})
                    self.send(400,{'error':str(exc),'decision':'NO_TRADE','live_execution_enabled':False})
                except Exception:
                    self.send(503,{'error':'Audit unavailable; operation not approved.','decision':'NO_TRADE'})
            except Exception:
                self.send(503,{'error':'Operation failed and was rolled back. No live order was sent.','decision':'NO_TRADE'})
    return ThreadingHTTPServer(('127.0.0.1',port),Handler)

def bind_server(application, port, bridge, mt5, settings, fallback=False):
    try:
        return make_server(application,port,bridge,mt5,settings)
    except OSError as exc:
        if exc.errno != errno.EADDRINUSE:
            raise
        if not fallback:
            raise InvalidData(f'Port {port} is already in use. Use --port 0 for an available port, or --port-fallback.') from exc
        print(f'Port {port} is occupied; choosing an available localhost port.', flush=True)
        return make_server(application,0,bridge,mt5,settings)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8765);parser.add_argument('--db');parser.add_argument('--policy');parser.add_argument('--port-fallback',action='store_true')
    args=parser.parse_args()
    settings=Settings.from_env(ROOT,args.db)
    policy=Policy(**json.loads(Path(args.policy).read_text())) if args.policy else Policy()
    application=Application(settings.database_path,policy,settings)
    mt5=MT5Service(settings.mt5_enabled,settings.mt5_terminal_path)
    if settings.mt5_enabled: mt5.initialize()
    with application.store.connect() as db: mappings=Store.symbol_mappings(db)
    webhook=TradingViewWebhookService(SymbolMapper(mappings),settings.webhook_max_age_seconds)
    bridge=TradingBridge(application,webhook,WebhookAuthenticator(settings.webhook_secret),mt5,settings)
    server=bind_server(application,args.port,bridge,mt5,settings,args.port_fallback)
    print(f'SentinelFX • http://127.0.0.1:{server.server_port} • {settings.mode}',flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close();mt5.shutdown()


if __name__=='__main__':
    try:
        main()
    except (InvalidData, OSError, ValueError) as exc:
        print(f'SentinelFX could not start: {exc}', file=sys.stderr)
        sys.exit(1)
