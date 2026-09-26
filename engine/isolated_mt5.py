"""Bounded native diagnostics for the server; no in-process native calls."""
import json
import subprocess
import sys
import threading
import time
from datetime import datetime,timezone
from pathlib import Path
from .mt5 import MT5Result, MT5Service
from .domain import InvalidData
from .mt5_evidence import validate_order_check


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
    def terminal_info(self): return self._get('terminal_info')
    def recent_deals(self): return self._get('recent_deals')
    def recent_orders(self): return self._get('recent_orders')


class IsolatedMT5Service(MT5Service):
    diagnostic_mode='real'
    def __init__(self, enabled=False, terminal_path='', timeout=5):
        super().__init__(enabled,terminal_path)
        self.timeout=timeout
        self._lock=threading.Lock()
        self._identity=None
        self._drift=False
        self._last_diagnostic_at=None
        self._last_failure_reason=None

    def _remember(self,data):
        status=data.get('status',{}) if isinstance(data,dict) else {}
        self._last_diagnostic_at=datetime.now(timezone.utc).isoformat()
        self._last_failure_reason=None if status.get('ok') is True else status.get('code','MT5_DIAGNOSTIC_INVALID')
        return data

    def operational_state(self):
        return {'diagnostic_mode':'real' if self.enabled else 'disabled','last_diagnostic_at':self._last_diagnostic_at,
                'last_failure_reason':self._last_failure_reason,'drift_latched':self._drift,
                'account_reconciled':False,'snapshot_freshness':'VALIDATED_PER_REQUEST','external_evidence_ready':False,
                'paper_connected_eligible':False}

    def _worker(self,payload):
        response=subprocess.run([sys.executable,'-B','-m','engine.mt5_worker'],
            input=json.dumps({'terminal_path':self.terminal_path,**payload}),
            cwd=str(Path(__file__).resolve().parent.parent),text=True,
            stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=self.timeout,check=True)
        data=json.loads(response.stdout)
        if not isinstance(data,dict) or not isinstance(data.get('status'),dict):
            raise ValueError('Invalid worker output')
        return data

    def snapshot(self,symbol=None, timestamp_reads=False):
        def failure(code,message):
            return self._remember({'status':MT5Result(False,code,message).to_dict()})
        if not self.enabled:
            return failure('MT5_DISABLED','MT5 integration is disabled')
        if not self._lock.acquire(blocking=False):
            return failure('MT5_BUSY','Another diagnostic read is in progress; retry later')
        try:
            if self._drift:
                return failure('MT5_ACCOUNT_CHANGED','Account identity drift is latched; review and restart required')
            data=self._worker({'operation':'snapshot','symbol':symbol, 'timestamp_reads':timestamp_reads})
            account=(data.get('account_info') or {}).get('data') or {}
            identity=(account.get('login'),account.get('server'),account.get('currency'))
            if data['status'].get('code') == 'MT5_ACCOUNT_CHANGED' or (
                    data['status'].get('ok') is True and self._identity is not None and symbol is not None and identity != self._identity):
                self._drift=True
                return failure('MT5_ACCOUNT_CHANGED','Account identity changed; review and restart required')
            if type(identity[0]) is int and identity[0]>0 and isinstance(identity[1],str) and identity[1].strip() and identity[2]=='USD':
                self._identity=identity
            return self._remember(data)
        except subprocess.TimeoutExpired:
            return failure('MT5_TIMEOUT','Diagnostic worker exceeded its deadline and was terminated')
        except (subprocess.SubprocessError,OSError,ValueError,TypeError,AttributeError):
            return failure('MT5_WORKER_FAILED','Diagnostic worker failed; no account state imported')
        finally:
            self._lock.release()

    def read_only_order_check(self,request):
        """Run one isolated order_check and return validated non-submitting evidence."""
        if not self.enabled:
            return MT5Result(False,'MT5_DISABLED','MT5 integration is disabled')
        if not self._lock.acquire(blocking=False):
            return MT5Result(False,'MT5_BUSY','Another read-only MT5 operation is in progress')
        try:
            if self._drift or self._identity is None:
                return MT5Result(False,'MT5_ACCOUNT_IDENTITY_UNVERIFIED','A verified snapshot is required before order_check')
            data=self._worker({'operation':'order_check','request':request})
            identity=data.get('account_identity') or {}
            if (identity.get('login'),identity.get('server'),identity.get('currency')) != self._identity:
                self._drift=True
                return MT5Result(False,'MT5_ACCOUNT_CHANGED','Account identity changed before order_check')
            safe=validate_order_check(data,request.get('volume'))
            return MT5Result(True,'MT5_ORDER_CHECK_PASSED','Broker accepted the non-submitting order check',safe)
        except subprocess.TimeoutExpired:
            return MT5Result(False,'MT5_TIMEOUT','Read-only order check exceeded its deadline')
        except InvalidData as exc:
            return MT5Result(False,str(exc),'Read-only order check evidence was rejected')
        except (subprocess.SubprocessError,OSError,ValueError,TypeError,AttributeError):
            return MT5Result(False,'MT5_WORKER_FAILED','Read-only order check worker failed')
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
    def __init__(self): super().__init__(enabled=True);self._last_diagnostic_at=None
    def snapshot(self,symbol=None):
        now=time.time(); name=symbol or 'EURUSD.a'
        ok=lambda code,message,data=None: MT5Result(True,code,message,data).to_dict()
        data={
            'status':ok('MT5_MOCK_DIAGNOSTIC','Synthetic diagnostic fixture; no terminal connected',{'synthetic_diagnostic':True}),
            'account_info':ok('MT5_OK','synthetic diagnostic account',{'login':900001,'server':'SENTINELFX-MOCK','currency':'USD','balance':300.0,'equity':300.0,'margin_free':300.0,'margin_level':1000.0,'trade_allowed':False,'synthetic_diagnostic':True}),
            'symbol_info':ok('MT5_OK','synthetic diagnostic symbol',{'name':name,'visible':True,'trade_mode':4,'volume_min':0.01,'volume_step':0.01,'volume_max':100.0,'trade_contract_size':100000.0,'point':0.00001,'digits':5,'synthetic_diagnostic':True}),
            'symbol_info_tick':ok('MT5_OK','synthetic diagnostic quote',{'time':now,'bid':1.0999,'ask':1.1001,'synthetic_diagnostic':True}),
            'positions_get':ok('MT5_OK','synthetic diagnostic positions',{'items':[],'synthetic_diagnostic':True}),
            'orders_get':ok('MT5_OK','synthetic diagnostic orders',{'items':[],'synthetic_diagnostic':True}),
        }
        self._last_diagnostic_at=datetime.now(timezone.utc).isoformat()
        return data
    def initialize(self): return self.status()
    def status(self): return SnapshotMT5(self.snapshot()).status()
    def shutdown(self): pass
    def reset_drift(self): return MT5Result(True,'MT5_DIAGNOSTIC_RESET','Mock diagnostics have no drift latch',{'was_latched':False})
    def operational_state(self):
        return {'diagnostic_mode':'mock','last_diagnostic_at':self._last_diagnostic_at,'last_failure_reason':None,
                'drift_latched':False,'account_reconciled':False,'snapshot_freshness':'SYNTHETIC_ONLY',
                'external_evidence_ready':False,'paper_connected_eligible':False}
