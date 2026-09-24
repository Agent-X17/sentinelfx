#!/usr/bin/env python3
"""Single-command local release acceptance gate for SentinelFX."""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime,timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request,urlopen

ROOT=Path(__file__).resolve().parent.parent
STAMP=datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
RUN_DIR=Path(tempfile.gettempdir())/('sentinelfx-acceptance-'+STAMP)
DB=RUN_DIR/('demo-'+STAMP+'.sqlite3')
SECRET='acceptance-only-secret-abcdefghijklmnopqrstuvwxyz'
RUN_DIR.mkdir()
server=None

def passed(label,detail=''):
    print('PASS  '+label+(' — '+detail if detail else ''),flush=True)

def fail(label,detail):
    raise RuntimeError(label+' — '+detail)

def get_json(url):
    with urlopen(url,timeout=10) as response:return response.status,json.load(response)

def webhook(base,case):
    command=[sys.executable,'-B',str(ROOT/'scripts/test_webhook.py'),'--url',base+'/api/webhook/tradingview','--secret',SECRET,'--case',case]
    result=subprocess.run(command,cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=20)
    if result.returncode: fail('webhook '+case,result.stderr.strip() or 'helper failed')
    return json.loads(result.stdout)

try:
    env=dict(os.environ,SYSTEM_MODE='SIMULATION',MT5_DIAGNOSTIC_MODE='mock',MT5_ENABLED='false',MT5_TERMINAL_PATH='',
        LIVE_EXECUTION_ENABLED='false',TRADINGVIEW_WEBHOOK_SECRET=SECRET,WEBHOOK_ALLOWED_HOSTS='',WEBHOOK_MAX_AGE_SECONDS='300',
        DEFAULT_ACCOUNT_PROFILE='ACCOUNT_LIVE',EXPLICIT_ORDER_CONFIRMATION_REQUIRED='true',
        DEMO_TRADE_PROPOSALS_ENABLED='false',DEMO_TRADE_PROPOSAL_KILL_SWITCH='true',
        DEMO_EXPECTED_ACCOUNT_LOGIN='',DEMO_EXPECTED_BROKER_SERVER='')
    command=[sys.executable,'-B','server.py','--demo','--db',str(DB),'--port','0']
    server=subprocess.Popen(command,cwd=ROOT,env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    lines=[];base=None
    deadline=time.time()+15
    while time.time()<deadline:
        line=server.stdout.readline()
        if line:
            lines.append(line.rstrip());print('SERVER '+line.rstrip())
            match=re.search(r'Dashboard:\s+(http://127\.0\.0\.1:\d+)/',line)
            if match:base=match.group(1)
            if line.strip().startswith('4. Inspect'):break
        elif server.poll() is not None:break
    startup='\n'.join(lines)
    if not base:fail('server startup','dashboard URL was not printed')
    for expected in (str(DB.resolve()),'System mode:     SIMULATION','MT5 diagnostics: mock','Live execution:  DISABLED',base,
                     'Webhook secret:  configured','Allowed hosts:   localhost only','python3 -B scripts/test_webhook.py'):
        if expected not in startup:fail('startup output','missing '+expected)
    if not DB.exists():fail('fresh database','timestamped database was not created')
    passed('fresh timestamped demo database',str(DB))
    passed('startup truth','port, URLs, database, SIMULATION, mock diagnostics, and live-disabled state printed')

    status_code,status_html=None,None
    with urlopen(base+'/status',timeout=10) as response:status_code=response.status;status_html=response.read().decode()
    if status_code!=200 or 'SentinelFX operator status' not in status_html:fail('/status','unexpected response')
    passed('/status','HTTP 200')
    code,health=get_json(base+'/api/health')
    if code!=200 or not health.get('ok') or health.get('mode')!='SIMULATION' or health.get('live_execution_enabled') is not False:fail('/api/health',str(health))
    passed('/api/health','healthy SIMULATION state; live_execution_enabled=false')

    expected={
        'valid':(200,'REQUIRED_EXTERNAL_EVIDENCE_UNVERIFIED'),
        'bad-secret':(401,'WEBHOOK_AUTH_FAILED'),'stale':(422,'Alert timestamp is stale or in the future'),
        'duplicate':(409,'DUPLICATE_WEBHOOK'),'missing-stop':(400,'stop_loss is required'),
        'unmapped':(422,'SYMBOL_UNMAPPED'),
    }
    for case,(http_status,reason) in expected.items():
        result=webhook(base,case)
        if result.get('http_status')!=http_status or result.get('reason')!=reason or result.get('decision')!='NO_TRADE':fail('webhook '+case,str(result))
        if result.get('order_sent') not in (False,None):fail('webhook '+case,'order_sent was not false')
        passed('webhook '+case,f'HTTP {http_status}; {reason}; NO_TRADE')

    try:
        urlopen(Request(base+'/api/webhook/tradingview',data=b'{}',headers={'Host':'attacker.invalid','Content-Type':'application/json'}),timeout=10)
        fail('invalid host','request unexpectedly succeeded')
    except HTTPError as exc:
        if exc.code!=403:fail('invalid host','expected 403, got '+str(exc.code))
    passed('invalid webhook host','HTTP 403')

    _,state=get_json(base+'/api/state')
    if len(state.get('decisions',[]))<3 or len(state.get('raw_webhooks',[]))<6 or len(state.get('risk_checks',[]))<2 or not state.get('audit_integrity'):fail('dashboard decision/audit state','state is incomplete')
    with urlopen(base+'/',timeout=10) as response:dashboard=response.read().decode()
    if 'app.js' not in dashboard:fail('dashboard','application shell did not load')
    passed('dashboard state',f"{len(state['raw_webhooks'])} webhook records, {len(state['risk_checks'])} risk checks, valid audit chain")

    for name,overrides,message in (
        ('live-enabled',{'LIVE_EXECUTION_ENABLED':'true'},'Live execution is not implemented'),
        ('LIVE_GATED',{'SYSTEM_MODE':'LIVE_GATED','LIVE_EXECUTION_ENABLED':'false'},'LIVE_GATED is reserved')):
        refusal_env=dict(env,**overrides)
        result=subprocess.run([sys.executable,'-B','server.py','--db',str(RUN_DIR/(name+'.sqlite3')),'--port','0'],cwd=ROOT,env=refusal_env,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=10)
        if result.returncode==0 or message not in result.stdout:fail(name+' startup refusal',result.stdout.strip() or 'startup unexpectedly succeeded')
        passed(name+' startup refusal','exit '+str(result.returncode)+'; '+message)
    refusal=subprocess.run([sys.executable,'-B','-c',"from engine.mt5 import MT5Service\nassert MT5Service().order_send({}).code=='LIVE_EXECUTION_NOT_IMPLEMENTED'"],cwd=ROOT)
    if refusal.returncode:fail('order_send refusal','refusal failed')
    token=state['csrf_token']
    try:urlopen(Request(base+'/api/execute',data=b'{}',headers={'Content-Type':'application/json','X-CSRF-Token':token}),timeout=10);fail('live HTTP route','route unexpectedly exists')
    except HTTPError as exc:
        if exc.code!=404:fail('live HTTP route','expected 404, got '+str(exc.code))
    passed('live execution impossible','both startup modes refuse, order_send refuses, /api/execute is 404')

    checks=[
        ['node','--check','static/app.js'],
        [sys.executable,'-B','-m','unittest','discover','-s','tests','-q'],
    ]
    check_env=dict(os.environ,PYTHONPYCACHEPREFIX='/tmp/sentinelfx-pycache')
    for check in checks:
        result=subprocess.run(check,cwd=ROOT,env=check_env,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
        if result.returncode:fail('final checks',result.stdout[-4000:])
        if 'unittest' in check: summary='; '.join(line.strip() for line in result.stdout.splitlines() if line.startswith('Ran ') or line=='OK')
        else: summary='JavaScript syntax valid'
        passed('final '+('test suite' if 'unittest' in check else 'frontend syntax'),summary)
    print('\nACCEPTANCE RESULT: PASS')
    print('Database retained at: '+str(DB))
except Exception as exc:
    print('\nACCEPTANCE RESULT: FAIL — '+str(exc),file=sys.stderr)
    sys.exit(1)
finally:
    if server is not None and server.poll() is None:
        server.terminate()
        try:server.wait(timeout=5)
        except subprocess.TimeoutExpired:server.kill()
