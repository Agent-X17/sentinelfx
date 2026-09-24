"""Bounded native diagnostics for the server; no in-process native calls."""
import json
import subprocess
import sys
import threading
import time
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
    diagnostic_mode='real'
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

    def reset_drift(self):
        """Clear only the in-memory diagnostic identity latch after an audited review."""
        with self._lock:
            was_latched=self._drift
            self._drift=False
            self._identity=None
        return MT5Result(True,'MT5_DIAGNOSTIC_RESET','Diagnostic identity latch cleared',{'was_latched':was_latched})


class MockDiagnosticMT5Service(MT5Service):
    """Synthetic read-only diagnostics for local operator testing.

    This is deliberately a different type from MockMT5Service, so TradingBridge's
    real-evidence gate always returns NO_TRADE for its data.
    """
    diagnostic_mode='mock'
    def __init__(self): super().__init__(enabled=True)
    def snapshot(self,symbol=None):
        now=time.time(); name=symbol or 'EURUSD.a'
        ok=lambda code,message,data=None: MT5Result(True,code,message,data).to_dict()
        return {
            'status':ok('MT5_MOCK_DIAGNOSTIC','Synthetic diagnostic fixture; no terminal connected',{'synthetic_diagnostic':True}),
            'account_info':ok('MT5_OK','synthetic diagnostic account',{'login':900001,'server':'SENTINELFX-MOCK','currency':'USD','balance':300.0,'equity':300.0,'margin_free':300.0,'margin_level':1000.0,'trade_allowed':False,'synthetic_diagnostic':True}),
            'symbol_info':ok('MT5_OK','synthetic diagnostic symbol',{'name':name,'visible':True,'trade_mode':4,'volume_min':0.01,'volume_step':0.01,'volume_max':100.0,'trade_contract_size':100000.0,'point':0.00001,'digits':5,'synthetic_diagnostic':True}),
            'symbol_info_tick':ok('MT5_OK','synthetic diagnostic quote',{'time':now,'bid':1.0999,'ask':1.1001,'synthetic_diagnostic':True}),
            'positions_get':ok('MT5_OK','synthetic diagnostic positions',{'items':[],'synthetic_diagnostic':True}),
            'orders_get':ok('MT5_OK','synthetic diagnostic orders',{'items':[],'synthetic_diagnostic':True}),
        }
    def initialize(self): return self.status()
    def status(self): return SnapshotMT5(self.snapshot()).status()
    def shutdown(self): pass
    def reset_drift(self): return MT5Result(True,'MT5_DIAGNOSTIC_RESET','Mock diagnostics have no drift latch',{'was_latched':False})
