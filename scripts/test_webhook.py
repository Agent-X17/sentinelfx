#!/usr/bin/env python3
"""Send one fresh TradingView-style alert to SentinelFX using only stdlib."""
import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

parser=argparse.ArgumentParser(description='Send a safe diagnostic alert; this cannot place an order.')
parser.add_argument('--url',default='http://127.0.0.1:8765/api/webhook/tradingview')
parser.add_argument('--secret',default='')
parser.add_argument('--symbol',default='OANDA:EUR/USD')
parser.add_argument('--case',choices=('valid','bad-secret','stale','duplicate','missing-stop','unmapped'),default='valid')
parser.add_argument('--alert-id',default='')
args=parser.parse_args()
now=datetime.now(timezone.utc)
payload={
    'alert_id':args.alert_id or 'local-test-'+str(uuid4()),'symbol':args.symbol,'side':'BUY','timeframe':'H1',
    'strategy':'SentinelFX local diagnostic','timestamp':now.isoformat(),
    'expires_at':(now+timedelta(minutes=4)).isoformat(),'entry':'1.1001',
    'stop_loss':'1.0980','take_profit':'1.1045',
    'metadata':{'purpose':'synthetic local webhook test'},
}
if args.case=='stale': payload['timestamp']=(now-timedelta(minutes=20)).isoformat()
if args.case=='missing-stop': payload.pop('stop_loss')
if args.case=='unmapped': payload['symbol']='UNMAPPED:NOT_A_PAIR'
headers={'Content-Type':'application/json'}
supplied='deliberately-wrong-secret' if args.case=='bad-secret' else args.secret
if supplied: headers['X-Webhook-Secret']=supplied

def send():
    request=Request(args.url,data=json.dumps(payload).encode(),headers=headers,method='POST')
    try:
        with urlopen(request,timeout=10) as response:return response.status,json.load(response)
    except HTTPError as exc:
        try:return exc.code,json.loads(exc.read().decode())
        except Exception:return exc.code,{'error':'Non-JSON error response'}

try:
    if args.case=='duplicate': send()
    status,result=send();result['http_status']=status;result['test_case']=args.case
    print(json.dumps(result,indent=2,sort_keys=True));sys.exit(0)
except URLError as exc:
    print('Cannot reach SentinelFX: '+str(exc.reason),file=sys.stderr);sys.exit(2)
