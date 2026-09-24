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
args=parser.parse_args()
now=datetime.now(timezone.utc)
payload={
    'alert_id':'local-test-'+str(uuid4()),'symbol':args.symbol,'side':'BUY','timeframe':'H1',
    'strategy':'SentinelFX local diagnostic','timestamp':now.isoformat(),
    'expires_at':(now+timedelta(minutes=4)).isoformat(),'entry':'1.1001',
    'stop_loss':'1.0980','take_profit':'1.1045',
    'metadata':{'purpose':'synthetic local webhook test'},
}
headers={'Content-Type':'application/json'}
if args.secret: headers['X-Webhook-Secret']=args.secret
request=Request(args.url,data=json.dumps(payload).encode(),headers=headers,method='POST')
try:
    with urlopen(request,timeout=10) as response:
        print(json.dumps(json.load(response),indent=2,sort_keys=True));sys.exit(0)
except HTTPError as exc:
    print(exc.read().decode(),file=sys.stderr);sys.exit(1)
except URLError as exc:
    print('Cannot reach SentinelFX: '+str(exc.reason),file=sys.stderr);sys.exit(2)
