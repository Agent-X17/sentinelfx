"""Local demo telemetry only. Never a source of trade authorization."""
import json
import math
import os
import time
from pathlib import Path


def monitor_path():
    default = Path.home() / 'Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/users/user/AppData/Roaming/MetaQuotes/Terminal/Common/Files/SentinelFX/demo-status.json'
    return Path(os.environ.get('MT5_DEMO_MONITOR_FILE', str(default)))


def read_monitor(path=None, now=None):
    now = time.time() if now is None else now
    result = {'ok': False, 'code': 'MT5_MONITOR_WAITING', 'order_submission_available': False}
    try:
        with (Path(path) if path else monitor_path()).open('rb') as handle:
            raw = handle.read(16385)
        if len(raw) > 16384:
            raise ValueError('Oversized snapshot')
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get('schema') != 1:
            raise ValueError('Invalid schema')
        generated = data['generated_at']
        if type(generated) not in (int, float) or not math.isfinite(generated):
            raise ValueError('Invalid timestamp')
        if not -2 <= now - generated <= 10:
            return dict(result, code='MT5_MONITOR_STALE')
        if data.get('demo') is not True:
            return dict(result, code='MT5_MONITOR_DEMO_REQUIRED')
        if data.get('connected') is not True:
            return dict(result, code='MT5_MONITOR_DISCONNECTED')
        for key in ('balance', 'equity', 'free_margin', 'positions', 'orders'):
            value = data[key]
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError('Invalid account metric')
        if not isinstance(data.get('currency'), str) or not isinstance(data.get('symbol'), str):
            raise ValueError('Invalid symbol or currency')
        safe = {key: data[key] for key in ('generated_at', 'currency', 'balance', 'equity', 'free_margin', 'positions', 'orders', 'symbol')}
        if data.get('tick_available') is True:
            for key in ('bid', 'ask', 'tick_server_time'):
                if type(data.get(key)) not in (int, float) or not math.isfinite(data[key]):
                    raise ValueError('Invalid quote')
            if data['bid'] <= 0 or data['ask'] < data['bid']:
                raise ValueError('Invalid quote prices')
            safe.update({key: data[key] for key in ('bid', 'ask', 'tick_server_time')})
        return dict(result, ok=True, code='MT5_DEMO_DIAGNOSTICS', data=safe,
                    message='Local demo telemetry only; quote freshness and execution are not validated.')
    except FileNotFoundError:
        return result
    except (OSError, ValueError, KeyError, TypeError):
        return dict(result, code='MT5_MONITOR_INVALID')
