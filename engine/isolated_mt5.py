"""Bounded native diagnostics for the server; no in-process native calls."""
import json
import subprocess
import sys
import threading
from pathlib import Path
from .mt5 import MT5Result, MT5Service


class SnapshotMT5:
    """Frozen diagnostic results; never eligible for the mock approval path."""
    def __init__(self, data): self.data = data
    def _get(self, name):
        try:
            result = MT5Result(**self.data[name])
            if type(result.ok) is not bool:
                raise ValueError()
            return result
        except (KeyError,TypeError,ValueError):
            return MT5Result(False,'MT5_DIAGNOSTIC_INVALID','Diagnostic result is unavailable')
    def status(self): return self._get('status')
    def account_info(self): return self._get('account_info')
    def symbol_info(self, symbol): return self._get('symbol_info')
    def symbol_info_tick(self, symbol): return self._get('symbol_info_tick')
    def positions_get(self): return self._get('positions_get')
    def orders_get(self): return self._get('orders_get')


class IsolatedMT5Service(MT5Service):
    def __init__(self, enabled=False, terminal_path='', timeout=5):
        super().__init__(enabled,terminal_path)
        self.timeout=timeout
        self._lock=threading.Lock()
        self._identity=None
        self._drift=False

    def snapshot(self,symbol=None):
        def failure(code,message):
            return {'status':MT5Result(False,code,message).to_dict()}
        if not self.enabled:
            return failure('MT5_DISABLED','MT5 integration is disabled')
        if not self._lock.acquire(blocking=False):
            return failure('MT5_BUSY','Another diagnostic read is in progress; retry later')
        try:
            if self._drift:
                return failure('MT5_ACCOUNT_CHANGED','Account identity drift is latched; review and restart required')
            response=subprocess.run([sys.executable,'-B','-m','engine.mt5_worker'],
                input=json.dumps({'terminal_path':self.terminal_path,'symbol':symbol}),
                cwd=str(Path(__file__).resolve().parent.parent),text=True,
                stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=self.timeout,check=True)
            data=json.loads(response.stdout)
            if not isinstance(data,dict) or not isinstance(data.get('status'),dict):
                raise ValueError('Invalid worker output')
            account=(data.get('account_info') or {}).get('data') or {}
            identity=(account.get('login'),account.get('server'),account.get('currency'))
            if data['status'].get('code') == 'MT5_ACCOUNT_CHANGED' or (
                    data['status'].get('ok') is True and self._identity is not None and symbol is not None and identity != self._identity):
                self._drift=True
                return failure('MT5_ACCOUNT_CHANGED','Account identity changed; review and restart required')
            if type(identity[0]) is int and identity[0]>0 and isinstance(identity[1],str) and identity[1].strip() and identity[2]=='USD':
                self._identity=identity
            return data
        except subprocess.TimeoutExpired:
            return failure('MT5_TIMEOUT','Diagnostic worker exceeded its deadline and was terminated')
        except (subprocess.SubprocessError,OSError,ValueError,TypeError,AttributeError):
            return failure('MT5_WORKER_FAILED','Diagnostic worker failed; no account state imported')
        finally:
            self._lock.release()

    def initialize(self): return self.status()
    def status(self): return SnapshotMT5(self.snapshot()).status()
    def shutdown(self): pass  # Each worker owns and closes its own terminal connection.
