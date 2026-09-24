"""Isolated diagnostic process. No configurable module, mock or send operation."""
import json
import sys
from .mt5 import MT5Service
from .domain import stamp


def collect(adapter, symbol=None):
    results = {'status': adapter.status().to_dict()}
    if not results['status']['ok'] or symbol is None:
        return results
    for name, args in [('account_info',()),('symbol_info',(symbol,)),
                       ('symbol_info_tick',(symbol,)),('positions_get',()),('orders_get',())]:
        results[name] = getattr(adapter,name)(*args).to_dict()
    # Detect identity changes during this diagnostic sequence.
    after = adapter.account_info()
    first = results['account_info'].get('data') or {}
    last = after.data or {}
    if not after.ok or any(first.get(k) != last.get(k) for k in ('login','server','currency')):
        results['status'] = {'ok':False,'code':'MT5_ACCOUNT_CHANGED','message':'Account identity changed during diagnostics','data':None}
    elif not adapter.status().ok:
        results['status'] = {'ok':False,'code':'MT5_DISCONNECTED','message':'Terminal disconnected during diagnostics','data':None}
    results['observed_at'] = stamp()
    return results


if __name__ == '__main__':
    request = json.loads(sys.stdin.read())
    adapter = MT5Service(True,request.get('terminal_path',''))
    try:
        initial = adapter.initialize()
        result = collect(adapter,request.get('symbol')) if initial.ok else {'status':initial.to_dict()}
        print(json.dumps(result,allow_nan=False))
    finally:
        adapter.shutdown()
