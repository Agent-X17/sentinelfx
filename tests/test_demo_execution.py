"""Deterministic DEMO-only normalization and one-shot execution boundary tests."""
import copy
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from engine.config import Settings
from engine.demo_execution_worker import MAGIC, run
from engine.domain import InvalidData
from engine.hfm_demo_time import (POLICY_REVISION, SCHEMA, account_fingerprint,
                                  evaluate)
from engine.hfm_readonly_time import seasonal_offset
from engine.mt5 import MT5Result
from engine.mt5_time import server_fingerprint
from test_mt5_evidence import fixture

UTC = timezone.utc


def evidence_batch(now, offset=None, age=5, auto=True, balance=500):
    offset = seasonal_offset(now) if offset is None else offset
    samples=[]
    for index in range(3):
        sample=fixture(); call=now.timestamp()-4+index
        sample['captured_at']=datetime.fromtimestamp(call,UTC).isoformat()
        sample['clock_observation']={'wall_start':call,'wall_end':call,'monotonic_elapsed':0}
        for observation in sample['call_observations'].values():
            observation.update(utc_before=call,utc_after=call,monotonic_elapsed=0)
        sample['terminal_info']['data']['trade_allowed']=auto
        sample['account_info']['data'].update(balance=balance,equity=balance)
        sample['runtime_info']['python_version']='3.14.7'
        raw=round((now.timestamp()-age+index+offset)*1000)
        sample['symbol_info_tick']['data'].update(time=raw//1000,time_msc=raw)
        sample['tick_crosscheck']={}
        for method in ('copy_ticks_from','copy_ticks_range'):
            sample['call_observations'][method]={'utc_before':call,'utc_after':call,'monotonic_elapsed':0}
            sample['tick_crosscheck'][method]={'matched':True,
                'utc_from':datetime.fromtimestamp(call-30,UTC).isoformat(),
                'utc_to':datetime.fromtimestamp(call,UTC).isoformat()}
        samples.append(sample)
    policy={'schema':SCHEMA,'revision':POLICY_REVISION,'scope':'DEMO_ONLY_VALIDATION','year':2026,
            'server_fingerprint':server_fingerprint('DEMO-SERVER'),
            'account_fingerprint':account_fingerprint('900001','DEMO-SERVER'),'symbol':'EURUSD.a',
            'package_version':'5.0.6180','terminal_build':5000,'python_version':'3.14.7',
            'summer_offset_seconds':10800,'winter_offset_seconds':7200,
            'dst_start':'LAST_SUNDAY_OF_MARCH','dst_end':'LAST_SUNDAY_OF_OCTOBER',
            'hfm_evidence_date':'2026-09-26','mql5_evidence_url':'https://www.mql5.com/en/forum/516531#comment_60465455'}
    return samples,policy


class DemoLevel1Tests(unittest.TestCase):
    def test_defaults_remain_disabled(self):
        settings=Settings()
        self.assertFalse(settings.demo_execution_gated)
        self.assertFalse(settings.live_execution_enabled)
        self.assertFalse(settings.demo_trade_proposals_enabled)
        self.assertTrue(settings.demo_trade_proposal_kill_switch)
        self.assertEqual(settings.mode,'SIMULATION')

    def test_summer_and_winter_normalize_without_external_certificate(self):
        for month,offset in ((1,7200),(7,10800)):
            now=datetime(2026,month,15,12,tzinfo=UTC); samples,policy=evidence_batch(now)
            result=evaluate(samples,policy,now,'900001','DEMO-SERVER','EURUSD.a',
                            require_autotrading=True)
            self.assertEqual(result['offset_seconds'],offset)
            self.assertEqual(result['result'],'PASS_DEMO_LEVEL1_ONLY')
            self.assertFalse(result['externally_clock_certified'])
            self.assertEqual(result['samples'][0]['age_seconds'],5)

    def test_market_closed_stale_offset_identity_version_and_balance_block(self):
        now=datetime(2026,7,15,12,tzinfo=UTC)
        cases=[]
        samples,policy=evidence_batch(now)
        for row in samples[1:]: row['symbol_info_tick']['data']=copy.deepcopy(samples[0]['symbol_info_tick']['data'])
        cases.append((samples,policy,'MARKET_CLOSED'))
        cases.append((*evidence_batch(now,age=31),'STALE_OR_FUTURE'))
        cases.append((*evidence_batch(now,offset=7200),'STALE_OR_FUTURE'))
        samples,policy=evidence_batch(now); samples[0]['runtime_info']['terminal_build']=6183
        cases.append((samples,policy,'RUNTIME_IDENTITY'))
        cases.append((*evidence_batch(now,balance=501),'CEILING'))
        for samples,policy,code in cases:
            with self.subTest(code=code),self.assertRaisesRegex(InvalidData,code):
                evaluate(samples,policy,now,'900001','DEMO-SERVER','EURUSD.a',require_autotrading=True)

    def test_fall_transition_guard_and_time_msc_disagreement(self):
        now=datetime(2026,10,25,12,tzinfo=UTC)
        samples,policy=evidence_batch(datetime(2026,10,20,12,tzinfo=UTC))
        with self.assertRaisesRegex(InvalidData,'DST_UNCERTAIN'):
            evaluate(samples,policy,now,'900001','DEMO-SERVER','EURUSD.a',require_autotrading=True)
        now=datetime(2026,7,15,12,tzinfo=UTC); samples,policy=evidence_batch(now)
        samples[0]['symbol_info_tick']['data']['time']+=1
        with self.assertRaisesRegex(InvalidData,'FIELDS_INCONSISTENT'):
            evaluate(samples,policy,now,'900001','DEMO-SERVER','EURUSD.a',require_autotrading=True)

    def test_spring_transition_missing_policy_future_and_host_clock_drift(self):
        transition=datetime(2026,3,29,12,tzinfo=UTC)
        samples,policy=evidence_batch(datetime(2026,3,20,12,tzinfo=UTC))
        with self.assertRaisesRegex(InvalidData,'DST_UNCERTAIN'):
            evaluate(samples,policy,transition,'900001','DEMO-SERVER','EURUSD.a',require_autotrading=True)
        now=datetime(2026,7,15,12,tzinfo=UTC); samples,policy=evidence_batch(now)
        with self.assertRaisesRegex(InvalidData,'POLICY_MISSING'):
            evaluate(samples,None,now,'900001','DEMO-SERVER','EURUSD.a',require_autotrading=True)
        samples,policy=evidence_batch(now,age=-1)
        with self.assertRaisesRegex(InvalidData,'STALE_OR_FUTURE'):
            evaluate(samples,policy,now,'900001','DEMO-SERVER','EURUSD.a',require_autotrading=True)
        samples,policy=evidence_batch(now); samples[0]['clock_observation']['wall_start']-=3
        with self.assertRaisesRegex(InvalidData,'CLOCK_DRIFT'):
            evaluate(samples,policy,now,'900001','DEMO-SERVER','EURUSD.a',require_autotrading=True)

    def test_gated_settings_require_all_explicit_controls(self):
        safe=replace(Settings(),demo_execution_gated=True,mt5_diagnostic_mode='real',mt5_enabled=True,
            demo_trade_proposals_enabled=True,demo_trade_proposal_kill_switch=False,
            demo_expected_account_login='900001',demo_expected_broker_server='DEMO-SERVER',
            demo_account_tag='DEMO_ONLY',hfm_demo_policy_path='policy.json')
        self.assertIs(safe.validate(),safe)
        for changed in (replace(safe,demo_account_tag=''),replace(safe,demo_trade_proposal_kill_switch=True),
                        replace(safe,live_execution_enabled=True),replace(safe,webhook_allowed_hosts=('public.example',))):
            with self.assertRaises(InvalidData): changed.validate()


class DemoWorkerTests(unittest.TestCase):
    def adapter(self, confirmed=True):
        result=SimpleNamespace(retcode=10009,deal=44,order=55,volume=.01,price=1.1001,request_id=7)
        module=SimpleNamespace(TRADE_ACTION_DEAL=1,ORDER_TYPE_BUY=0,ORDER_TYPE_SELL=1,
            ORDER_TIME_GTC=0,ORDER_FILLING_FOK=0,ORDER_FILLING_IOC=1,ORDER_FILLING_RETURN=2,
            order_send=Mock(return_value=result))
        adapter=SimpleNamespace(_module=module)
        adapter._record=lambda value: vars(value)
        adapter.order_check=Mock(return_value=MT5Result(True,'OK','ok',{'retcode':0}))
        account={'login':900001,'server':'DEMO-SERVER','currency':'USD','trade_mode':0}
        adapter.account_info=Mock(return_value=MT5Result(True,'OK','ok',account))
        position={'magic':MAGIC,'comment':'SFXDEMO-attempt-','ticket':1}
        adapter.positions_get=Mock(return_value=MT5Result(True,'OK','ok',{'items':[position] if confirmed else []}))
        adapter.shutdown=Mock()
        return adapter

    def payload(self):
        return {'symbol':'EURUSD.a','direction':'BUY','volume':'0.01','sl':'1.09800','tp':'1.10450',
                'attempt_id':'attempt-12345678','expected_login':'900001','expected_server':'DEMO-SERVER',
                'account_tag':'DEMO_ONLY','policy':{}}

    @patch('engine.demo_execution_worker.collect',return_value={})
    @patch('engine.demo_execution_worker.evaluate')
    def test_one_confirmed_send_only(self,evaluator,collector):
        sample=fixture(); sample['terminal_info']['data']['trade_allowed']=True
        evaluator.return_value={'normalized_snapshot':sample,'result':'PASS_DEMO_LEVEL1_ONLY','samples':[]}
        adapter=self.adapter(); result=run(self.payload(),adapter,sleeper=lambda _:None)
        self.assertEqual(result['status'],'CONFIRMED_DEMO_EXECUTED')
        self.assertTrue(result['order_sent']); adapter._module.order_send.assert_called_once()
        self.assertEqual(collector.call_count,3)

    @patch('engine.demo_execution_worker.collect',return_value={})
    @patch('engine.demo_execution_worker.evaluate',side_effect=InvalidData('HFM_DEMO_ACCOUNT_MISMATCH'))
    def test_failed_gate_never_sends(self,evaluator,collector):
        adapter=self.adapter(); result=run(self.payload(),adapter,sleeper=lambda _:None)
        self.assertEqual(result['status'],'BLOCKED_NO_TRADE')
        adapter._module.order_send.assert_not_called()

    def test_missing_demo_tag_never_connects_or_sends(self):
        adapter=self.adapter(); payload=self.payload(); payload['account_tag']=''
        result=run(payload,adapter,sleeper=lambda _:None)
        self.assertEqual(result['status'],'BLOCKED_NO_TRADE')
        adapter._module.order_send.assert_not_called()


if __name__=='__main__': unittest.main()
