#!/usr/bin/env python3
"""Local-only dashboard/API. Run: python3 server.py"""
import argparse
import json
import os
import secrets
import sqlite3
import errno
import sys
import importlib.util
from datetime import datetime, timezone
from html import escape
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
from engine.isolated_mt5 import IsolatedMT5Service,MockDiagnosticMT5Service
from engine.webhook import TradingViewWebhookService, WebhookAuthenticator, SymbolMapper
from engine.bridge import TradingBridge
from engine.redaction import redact

ROOT=Path(__file__).resolve().parent

def seed_demo_activity(application):
    """Add clearly labelled synthetic decisions to a brand-new demo database."""
    for profile,scenario in (('ACCOUNT_A','safe'),('ACCOUNT_B','standard'),('ACCOUNT_C','stale')):
        signal,market=sample_signal(scenario)
        application.evaluate({'account_profile':profile,'broker_id':'demo-standard' if scenario=='standard' else 'demo-cent','signal':signal,'market':market})

def status_document(settings,mt5,port):
    state=mt5.status().to_dict() if mt5 else {'ok':False,'code':'MT5_UNCONFIGURED','message':'MT5 status unavailable'}
    operational=mt5.operational_state() if mt5 and hasattr(mt5,'operational_state') else {}
    allowed=', '.join(settings.webhook_allowed_hosts) or 'localhost only'
    rows=(('System mode',settings.mode),('Database',str(Path(settings.database_path).resolve())),('Dashboard',f'http://127.0.0.1:{port}/'),
          ('TradingView webhook',f'http://127.0.0.1:{port}/api/webhook/tradingview'),('Webhook secret','configured' if settings.webhook_secret else 'not configured'),
          ('Accepted external hosts',allowed),('MT5 diagnostic mode',settings.mt5_diagnostic_mode),('MT5 status',state['code']),
          ('Last diagnostic',operational.get('last_diagnostic_at') or 'none'),('Drift latch','ACTIVE' if operational.get('drift_latched') else 'clear'),
          ('Paper-connected eligibility','NOT ELIGIBLE'),('Live execution','DISABLED — not implemented'))
    body=''.join(f'<tr><th>{escape(k)}</th><td>{escape(str(v))}</td></tr>' for k,v in rows)
    return ("<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
            "<title>SentinelFX status</title><link rel='stylesheet' href='/style.css'><main class='content'><section class='card'><h1>SentinelFX operator status</h1>"
            "<p><strong>Live order submission is disabled.</strong> This page reports local simulation and diagnostic state.</p><table>"+body+
            "</table><p><a href='/'>Open dashboard</a> · <a href='/api/health'>JSON health</a></p></section></main></html>").encode()

def strict_object(pairs):
    result={}
    for key,value in pairs:
        if key in result: raise ValueError('Duplicate JSON field')
        result[key]=value
    return result

def webhook_diagnostics(snapshot):
    items=[]
    for row in snapshot.get('raw_webhooks',[])[:20]:
        reason=row.get('reason') or 'UNKNOWN'
        items.append({'timestamp':row.get('received_at'),'status':row.get('status'),'reason':reason,
            'block_source':TradingBridge.block_source(reason),'host_validation':'MATCHED',
            'secret_validation':'FAILED' if reason=='WEBHOOK_AUTH_FAILED' else 'MATCHED',
            'required_fields':'FAILED' if 'Missing fields' in reason or 'stop_loss' in reason else 'PASSED_OR_NOT_REACHED',
            'symbol_mapping':'FAILED' if reason in ('SYMBOL_UNMAPPED','SYMBOL_AMBIGUOUS') else 'PASSED_OR_NOT_REACHED',
            'replay_status':'DUPLICATE' if reason=='DUPLICATE_WEBHOOK' else 'UNIQUE_OR_NOT_REACHED'})
    for row in snapshot.get('audit',[]):
        if row.get('event') not in ('WEBHOOK_DUPLICATE','WEBHOOK_HOST_REJECTED'): continue
        payload=json.loads(row['payload'])
        reason='DUPLICATE_WEBHOOK' if row['event']=='WEBHOOK_DUPLICATE' else 'WEBHOOK_HOST_REJECTED'
        items.append({'timestamp':row['timestamp'],'status':'duplicate' if reason=='DUPLICATE_WEBHOOK' else 'rejected','reason':reason,
            'block_source':'DUPLICATE_DETECTION' if reason=='DUPLICATE_WEBHOOK' else 'WEBHOOK_VALIDATION',
            'host_validation':'FAILED' if reason=='WEBHOOK_HOST_REJECTED' else 'MATCHED','secret_validation':'NOT_REACHED' if reason=='WEBHOOK_HOST_REJECTED' else 'MATCHED',
            'required_fields':'NOT_REACHED','symbol_mapping':'NOT_REACHED','replay_status':'DUPLICATE' if reason=='DUPLICATE_WEBHOOK' else 'NOT_REACHED'})
    return sorted(items,key=lambda item:item.get('timestamp') or '',reverse=True)[:20]

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
                runtime={'dashboard_url':f'http://127.0.0.1:{self.server.server_port}/','status_url':f'http://127.0.0.1:{self.server.server_port}/status','webhook_path':'/api/webhook/tradingview','local_webhook_url':f'http://127.0.0.1:{self.server.server_port}/api/webhook/tradingview'}
                mt5_status=mt5.status().to_dict() if mt5 else None
                operational=mt5.operational_state() if mt5 and hasattr(mt5,'operational_state') else {}
                if path=='/api/state':
                    snapshot=app.snapshot(); diagnostics=webhook_diagnostics(snapshot)
                    return self.send(200,dict(snapshot,csrf_token=token,mt5=mt5_status,mt5_operational=operational,settings=(settings.public() if settings else None),runtime=runtime,
                        webhook_diagnostics=diagnostics,last_webhook_result=diagnostics[0] if diagnostics else None,last_safe_state_at=snapshot['audit'][0]['timestamp'] if snapshot.get('audit_integrity') and snapshot.get('audit') else None,
                        readiness={'simulation':'READY','diagnostic':'SYNTHETIC' if settings and settings.mt5_diagnostic_mode=='mock' else 'DIAGNOSTIC_ONLY','account_reconciliation':'UNVERIFIED','snapshot_freshness':operational.get('snapshot_freshness','UNVERIFIED'),'external_evidence':'NOT_READY','paper_connected':'NOT_ELIGIBLE','live_execution':'UNAVAILABLE'}))
                if path=='/api/health': return self.send(200,{'ok':True,'mode':settings.mode if settings else 'SIMULATION','live_execution_enabled':False,'database_path':str(Path(settings.database_path).resolve()) if settings else None,'mt5_diagnostic_mode':settings.mt5_diagnostic_mode if settings else 'disabled','mt5':mt5_status,'mt5_operational':operational,'webhook':runtime})
                if path=='/api/mt5/status': return self.send(200,mt5.status().to_dict() if mt5 else {'ok':False,'code':'MT5_UNCONFIGURED'})
                if path=='/status': return self.send(200,status_document(settings,mt5,self.server.server_port),'text/html; charset=utf-8')
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
            if not self.valid_host(is_webhook):
                if is_webhook:
                    try:
                        with app.store.transaction() as db: Store.audit(db,'WEBHOOK_HOST_REJECTED',{'reason':'WEBHOOK_HOST_REJECTED','host_validation':'FAILED'})
                    except Exception: pass
                return self.send(403,{'error':'Invalid host','decision':'NO_TRADE','reason':'WEBHOOK_HOST_REJECTED','live_execution_enabled':False})
            if not is_webhook and self.headers.get('X-CSRF-Token')!=token:
                return self.send(403,{'error':'Local session token required'})
            origin=self.headers.get('Origin')
            if not is_webhook and origin and origin not in (f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}'):
                return self.send(403,{'error':'Cross-origin request refused'})
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=2_000_000: return self.send(413,{'error':'Invalid body size'})
                raw=self.rfile.read(length)
                try: payload=json.loads(raw,object_pairs_hook=strict_object,parse_constant=lambda x: (_ for _ in ()).throw(ValueError('Nonfinite JSON number')))
                except (ValueError,RecursionError):
                    with app.store.transaction() as db: Store.audit(db,'MALFORMED_REQUEST',{'bytes':length,'reason':'Invalid JSON'})
                    return self.send(400,{'decision':'NO_TRADE','error':'Malformed JSON','live_execution_enabled':False})
                if not isinstance(payload,dict): raise InvalidData('JSON object required')
                if not is_webhook:
                    payload=redact(payload, (settings.webhook_secret,) if settings else ())
                if is_webhook:
                    if bridge is None: return self.send(503,{'error':'Webhook bridge unavailable','decision':'NO_TRADE'})
                    body_secret=payload.get('secret')
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
                elif path=='/api/mt5/reset-drift':
                    if payload.get('confirm')!='RESET_DIAGNOSTIC_LATCH' or not hasattr(mt5,'reset_drift'): raise InvalidData('Explicit diagnostic reset confirmation required')
                    with app.store.transaction() as db: Store.audit(db,'MT5_DIAGNOSTIC_RESET_REQUESTED',{'mode':settings.mt5_diagnostic_mode,'reason':payload.get('reason','Operator review')})
                    result=mt5.reset_drift().to_dict()
                else: return self.send(404,{'error':'No such operation; live execution is unavailable'})
                self.send(200,result)
            except (InvalidData,ValueError,TypeError,KeyError) as exc:
                try:
                    # Parser/type errors may contain supplied strings. Do not
                    # echo or log their values as diagnostic explanations.
                    reason=str(exc) if isinstance(exc,InvalidData) else 'Invalid request value or structure'
                    reason=redact(reason,(settings.webhook_secret,) if settings else ())
                    with app.store.transaction() as db: Store.audit(db,'REQUEST_REFUSED',{'path':path,'reason':reason})
                    self.send(400,{'error':reason,'decision':'NO_TRADE','live_execution_enabled':False})
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
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8765);parser.add_argument('--db');parser.add_argument('--policy');parser.add_argument('--port-fallback',action='store_true');parser.add_argument('--demo',action='store_true',help='create a fresh database with synthetic example decisions')
    args=parser.parse_args()
    if args.demo:
        if args.db:
            demo_path=Path(args.db)
            if demo_path.exists(): raise InvalidData('--demo refuses to overwrite an existing database')
        else:
            demo_path=ROOT/'data'/('demo-'+datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')+'.sqlite3')
        args.db=str(demo_path)
    settings=Settings.from_env(ROOT,args.db)
    policy=Policy(**json.loads(Path(args.policy).read_text())) if args.policy else Policy()
    application=Application(settings.database_path,policy,settings)
    if args.demo: seed_demo_activity(application)
    mt5=MockDiagnosticMT5Service() if settings.mt5_diagnostic_mode=='mock' else IsolatedMT5Service(settings.mt5_diagnostic_mode=='real',settings.mt5_terminal_path)
    if settings.mt5_enabled: mt5.initialize()
    with application.store.connect() as db: mappings=Store.symbol_mappings(db)
    webhook=TradingViewWebhookService(SymbolMapper(mappings),settings.webhook_max_age_seconds)
    bridge=TradingBridge(application,webhook,WebhookAuthenticator(settings.webhook_secret),mt5,settings)
    server=bind_server(application,args.port,bridge,mt5,settings,args.port_fallback)
    print('\nSentinelFX is ready',flush=True)
    print(f'  Dashboard:       http://127.0.0.1:{server.server_port}/',flush=True)
    print(f'  Status page:     http://127.0.0.1:{server.server_port}/status',flush=True)
    print(f'  Database:        {Path(settings.database_path).resolve()}',flush=True)
    print(f'  System mode:     {settings.mode}',flush=True)
    print(f'  MT5 diagnostics: {settings.mt5_diagnostic_mode}',flush=True)
    native_available=importlib.util.find_spec('MetaTrader5') is not None
    print(f'  MT5 host check:  {"package detected" if native_available else "package unavailable on this host"}',flush=True)
    print('  Live execution:  DISABLED (not implemented)',flush=True)
    print(f'  Webhook:         http://127.0.0.1:{server.server_port}/api/webhook/tradingview\n',flush=True)
    print(f'  Webhook secret:  {"configured" if settings.webhook_secret else "NOT configured (localhost testing only)"}',flush=True)
    print('  Allowed hosts:   '+(', '.join(settings.webhook_allowed_hosts) if settings.webhook_allowed_hosts else 'localhost only; set exact tunnel host for external delivery'),flush=True)
    secret_arg=' --secret "$TRADINGVIEW_WEBHOOK_SECRET"' if settings.webhook_secret else ''
    print('\nNext steps:',flush=True)
    print(f'  1. Open dashboard: http://127.0.0.1:{server.server_port}/',flush=True)
    print(f'  2. Check status:   http://127.0.0.1:{server.server_port}/status',flush=True)
    print(f'  3. Test webhook:   python3 -B scripts/test_webhook.py --url http://127.0.0.1:{server.server_port}/api/webhook/tradingview{secret_arg}',flush=True)
    print('  4. Inspect Overview, Signal decisions, and Audit trail.\n',flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close();mt5.shutdown()


if __name__=='__main__':
    try:
        main()
    except (InvalidData, OSError, ValueError) as exc:
        print(f'SentinelFX could not start: {exc}', file=sys.stderr)
        sys.exit(1)
