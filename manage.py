#!/usr/bin/env python3
"""Offline maintenance. All commands operate on simulation data only."""
import argparse,json
from pathlib import Path
from engine.service import Application
from engine.storage import dumps,Store
from engine.seed import sample_signal
from engine.research import CandleBacktestingService,BacktestingService
root=Path(__file__).resolve().parent
parser=argparse.ArgumentParser();parser.add_argument('command',choices=['init','sample','audit','backtest','backup','restore']);parser.add_argument('--db',default=str(root/'data/engine.sqlite3'));parser.add_argument('--input');parser.add_argument('--output');args=parser.parse_args()
if args.command in ('backup','restore'):
    if not args.output: parser.error('--output must be a new destination database path')
    try: print('Verified database copy: '+Store.copy_verified(args.db,args.output))
    except ValueError as exc: parser.exit(1,str(exc)+'\n')
elif args.command=='sample':
    s,m=sample_signal();print(dumps(dict(account_profile='ACCOUNT_A',broker_id='demo-cent',signal=s,market=m)))
elif args.command=='backtest':
    if not args.input:parser.error('--input required')
    p=json.loads(Path(args.input).read_text());print(dumps((CandleBacktestingService if 'candles' in p else BacktestingService).run(p)))
else:
    app=Application(args.db)
    if args.command=='init':print('Database migrated and seeded; simulation only.')
    else:
        with app.store.connect() as db:
            ok=Store.verify_audit(db);print('Audit chain: '+('VALID' if ok else 'FAILED'));raise SystemExit(0 if ok else 1)
