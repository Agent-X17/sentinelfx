"""Isolated diagnostic process. No configurable module, mock or send operation."""
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from .mt5 import MT5Service
from .domain import stamp
from .mt5_evidence import PROTOCOL


def collect(adapter, symbol=None):
    wall_start, monotonic_start = time.time(), time.monotonic()
    results = {'protocol':PROTOCOL,'operation':'snapshot','status': adapter.status().to_dict()}
    if not results['status']['ok'] or symbol is None:
        return results
    for name, args in [('terminal_info',()),('account_info',()),('symbol_info',(symbol,)),
                       ('symbol_info_tick',(symbol,)),('positions_get',()),('orders_get',())]:
        results[name] = getattr(adapter,name)(*args).to_dict()
    now=datetime.now(timezone.utc); since=now-timedelta(hours=24)
    results['recent_deals']=adapter.history_deals_get(since,now).to_dict()
    results['recent_orders']=adapter.history_orders_get(since,now).to_dict()
    # Detect identity changes during this diagnostic sequence.
    after = adapter.account_info()
    first = results['account_info'].get('data') or {}
    last = after.data or {}
    if not after.ok or any(first.get(k) != last.get(k) for k in ('login','server','currency')):
        results['status'] = {'ok':False,'code':'MT5_ACCOUNT_CHANGED','message':'Account identity changed during diagnostics','data':None}
    elif not adapter.status().ok:
        results['status'] = {'ok':False,'code':'MT5_DISCONNECTED','message':'Terminal disconnected during diagnostics','data':None}
    wall_end = time.time()
    results['clock_observation'] = {
        'wall_start': wall_start, 'wall_end': wall_end,
        'monotonic_elapsed': time.monotonic() - monotonic_start,
    }
    results['captured_at'] = datetime.fromtimestamp(wall_end, timezone.utc).isoformat()
    return results


def check(adapter, request):
    """Perform one broker preflight. This worker has no submission operation."""
    allowed={'symbol','type','volume','price','sl','tp','filling_mode'}
    if not isinstance(request,dict) or set(request)-allowed or request.get('type') not in ('BUY','SELL'):
        return {'protocol':PROTOCOL,'operation':'order_check','status':{'ok':False,'code':'MT5_ORDER_CHECK_REQUEST_INVALID','message':'Invalid read-only check request','data':None}}
    before=adapter.account_info()
    if not before.ok:
        return {'protocol':PROTOCOL,'operation':'order_check','status':before.to_dict()}
    module=adapter._module
    native={
        'action':getattr(module,'TRADE_ACTION_DEAL'),
        'symbol':request['symbol'],'volume':float(request['volume']),'price':float(request['price']),
        'sl':float(request['sl']),'tp':float(request['tp']),
        'type':getattr(module,'ORDER_TYPE_BUY') if request['type']=='BUY' else getattr(module,'ORDER_TYPE_SELL'),
        'type_time':getattr(module,'ORDER_TIME_GTC'),
        'type_filling':(getattr(module,'ORDER_FILLING_FOK') if int(request['filling_mode']) & 1 else
                        getattr(module,'ORDER_FILLING_IOC') if int(request['filling_mode']) & 2 else
                        getattr(module,'ORDER_FILLING_RETURN')),
    }
    result=adapter.order_check(native)
    after=adapter.account_info()
    first=before.data or {}; last=after.data or {}
    identity={key:first.get(key) for key in ('login','server','currency')}
    if not after.ok or any(first.get(key)!=last.get(key) for key in identity):
        result=type(result)(False,'MT5_ACCOUNT_CHANGED','Account identity changed during order_check',None)
    return {'protocol':PROTOCOL,'operation':'order_check','status':result.to_dict(),'checked_volume':str(request['volume']),'account_identity':identity,'captured_at':stamp()}


if __name__ == '__main__':
    request = json.loads(sys.stdin.read())
    adapter = MT5Service(True,request.get('terminal_path',''))
    try:
        initial = adapter.initialize()
        if not initial.ok:
            result={'protocol':PROTOCOL,'operation':request.get('operation','snapshot'),'status':initial.to_dict()}
        elif request.get('operation','snapshot')=='snapshot':
            result=collect(adapter,request.get('symbol'))
        elif request.get('operation')=='order_check':
            result=check(adapter,request.get('request'))
        else:
            result={'protocol':PROTOCOL,'operation':'invalid','status':{'ok':False,'code':'MT5_WORKER_OPERATION_INVALID','message':'Unsupported read-only worker operation','data':None}}
        print(json.dumps(result,allow_nan=False))
    finally:
        adapter.shutdown()
