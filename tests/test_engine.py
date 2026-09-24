import copy
import json
import random
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
from engine.domain import *
from engine.seed import sample_signal
from engine.service import Application
from engine.storage import Store,dumps,loads
from engine.research import BacktestingService

class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=self.temp.name+'/db.sqlite3';self.app=Application(self.path)
        self.s,self.m=sample_signal(); snap=self.app.snapshot()
        self.a=snap['accounts'][0];self.b=next(b for b in snap['brokers'] if b['id']=='demo-cent');self.p=snap['providers'][0]
    def tearDown(self): self.temp.cleanup()
    def result(self,profile='ACCOUNT_A'):
        return self.app.engine.evaluate(profile,self.a,self.b,self.p,self.s,self.m)
    def submit(self,s=None,**kw):
        payload=dict(account_profile='ACCOUNT_A',broker_id='demo-cent',signal=s or self.s,market=self.m);payload.update(kw)
        return self.app.evaluate(payload)
    def reject(self,reason):
        r=self.result();self.assertEqual(r['decision'],'NO_TRADE');self.assertIn(reason,r['blocking_factors']);return r

class DomainTests(Fixture):
    def test_safe_cent(self):
        r=self.result();self.assertEqual(r['decision'],'APPROVED_SIMULATED_TRADE');self.assertFalse(r['live_execution_enabled'])
        c=r['calculations'];self.assertEqual(c['position_size'],Decimal('.11'));self.assertEqual(c['estimated_max_loss'],Decimal('.2365'))
    def test_all_profiles(self):
        for i,(profile,equity) in enumerate(PROFILES.items()):
            self.a.update(equity=equity,free_margin=equity)
            c=self.result(profile)['calculations'];self.assertLessEqual(c['estimated_max_loss'],Decimal(equity)*Decimal('.005'))
    def test_standard_rejected(self):
        self.b['contract_size']='100000';self.reject('BELOW_BROKER_MINIMUM')
    def test_pip_value_eurusd(self): self.assertEqual(self.result()['calculations']['pip_value_per_lot'],Decimal('.1'))
    def test_pip_value_usdjpy(self):
        self.s,self.m=sample_signal(symbol='USDJPY');self.assertEqual(self.result()['calculations']['pip_value_per_lot'],Decimal('1000')*Decimal('.01')/Decimal('150'))
    def test_margin(self): self.assertEqual(MarginCalculator.calculate('EURUSD',Decimal('1.1'),Decimal(1000),Decimal('.03'),Decimal(30)),Decimal('1.1'))
    def test_margin_jpy(self): self.assertEqual(MarginCalculator.calculate('USDJPY',Decimal(150),Decimal(1000),Decimal('.03'),Decimal(30)),Decimal(1))
    def test_round_down(self):
        self.b['lot_increment']='0.03';self.b['minimum_lot']='0.02';c=self.result()['calculations'];self.assertEqual(c['position_size'],Decimal('.11'))
        self.assertLessEqual(c['position_size'],c['calculated_safe_position_size'])
    def test_missing_stop(self): self.s.pop('stop_loss');self.reject('MISSING_OR_INVALID_STOP_PRICES')
    def test_wrong_stop(self): self.s['stop_loss']='1.101';self.reject('INVALID_STOP_OR_TARGET')
    def test_short_valid(self):
        self.s.update(direction='SELL',stop_loss='1.102',take_profit='1.096');self.assertEqual(self.result()['decision'],'APPROVED_SIMULATED_TRADE')
    def test_reward_risk(self): self.s['take_profit']='1.1005';self.reject('INSUFFICIENT_REWARD_RISK')
    def test_stale(self): self.s['timestamp']=(utcnow()-timedelta(minutes=6)).isoformat();self.reject('STALE_OR_FUTURE_SIGNAL')
    def test_expired(self): self.s['expires_at']=(utcnow()-timedelta(seconds=1)).isoformat();self.reject('EXPIRED_SIGNAL')
    def test_future(self): self.s['timestamp']=(utcnow()+timedelta(seconds=1)).isoformat();self.reject('STALE_OR_FUTURE_SIGNAL')
    def test_timezone_required(self): self.s['timestamp']='2026-09-21T13:00:00';self.reject('INVALID_SIGNAL_TIMESTAMP')
    def test_missing_expiry(self): self.s.pop('expires_at');self.reject('SIGNAL_MISSING_EXPIRES_AT')
    def test_missing_signal_id(self): self.s.pop('signal_id');self.reject('SIGNAL_MISSING_SIGNAL_ID')
    def test_bad_symbol(self): self.s['symbol']='XAUUSD';self.reject('UNSUPPORTED_SYMBOL')
    def test_news(self): self.m['news_risk']='.9';self.reject('NEWS_RISK_VETO')
    def test_correlation(self): self.m['correlation']='.9';self.reject('CORRELATION_VETO')
    def test_volatility(self): self.m['volatility']='.9';self.reject('VOLATILITY_VETO')
    def test_execution(self): self.m['execution_quality']='.1';self.reject('EXECUTION_QUALITY_VETO')
    def test_spread(self): self.m['spread_pips']='3';self.reject('SPREAD_PIPS_VETO')
    def test_costs(self): self.m['commission_per_lot']='1';self.reject('TRANSACTION_COST_VETO')
    def test_swap_and_slippage_counted(self):
        self.m.update(swap_per_lot='.1',commission_per_lot='.02');c=self.result()['calculations']
        self.assertEqual(c['estimated_total_transaction_cost'],sum(c[k] for k in ['estimated_spread_cost','estimated_commission_cost','estimated_slippage_cost','estimated_swap_cost']))
    def test_margin_unsafe(self): self.b['leverage']='2';self.reject('MARGIN_UNSAFE')
    def test_daily_stop(self): self.a['daily_loss']='1';self.reject('DAILY_LOSS_STOP')
    def test_weekly_stop(self): self.a['weekly_loss']='2';self.reject('WEEKLY_LOSS_STOP')
    def test_provider_budget(self): self.a['provider_daily_loss']='.5';self.reject('RISK_BUDGET_EXHAUSTED')
    def test_open_position(self): self.a['open_positions']=1;self.reject('MAX_OPEN_POSITIONS')
    def test_reserved_risk(self): self.a['reserved_risk']='.4';c=self.result()['calculations'];self.assertLessEqual(c['estimated_max_loss'],Decimal('.1'))
    def test_copy_size_ignored(self):
        c=self.result()['calculations'];self.s['metadata']['provider_lot_size']='10000';self.assertEqual(self.result()['calculations'],c)
    def test_suspended_provider(self): self.p['status']='SUSPENDED';self.reject('PROVIDER_UNAVAILABLE_OR_SUSPENDED')
    def test_forbidden_flags(self):
        for flag in FORBIDDEN:
            self.p['risk_flags']=[flag];self.reject('PROVIDER_FORBIDDEN_BEHAVIOR')
    def test_drawdown(self): self.p['current_drawdown']='.15';self.reject('PROVIDER_DRAWDOWN_BREAKER')
    def test_sample_size(self): self.p['trade_count']=20;self.reject('PROVIDER_SAMPLE_TOO_SMALL')
    def test_provider_history(self): self.p['age_days']=10;self.reject('PROVIDER_HISTORY_TOO_SHORT')
    def test_no_edge(self): self.m['edge_validated']=False;self.reject('NO_VALIDATED_EDGE')
    def test_conflict(self): self.m['conflicting_signals']=True;self.reject('CONFLICTING_OR_UNKNOWN_EVIDENCE')
    def test_market_stale(self): self.m['timestamp']=(utcnow()-timedelta(seconds=31)).isoformat();self.reject('MARKET_DATA_STALE')
    def test_context_stale(self): self.m['context_timestamp']=(utcnow()-timedelta(minutes=6)).isoformat();self.reject('NEWS_MACRO_CONTEXT_STALE')
    def test_market_unknown(self): self.m.pop('news_risk');self.reject('UNKNOWN_NEWS_RISK')
    def test_uncertain_account(self): self.a['state_certain']=False;self.reject('ACCOUNT_STATE_UNCERTAIN')
    def test_account_stale(self): self.a['updated_at']=(utcnow()-timedelta(minutes=6)).isoformat();self.reject('ACCOUNT_STATE_STALE')
    def test_broker_unknown(self): self.b['synthetic']=False;self.reject('BROKER_UNVERIFIED')
    def test_numeric_invalid(self):
        for val in ('NaN','Infinity','-Infinity',None,True,{},'hello'):
            self.s['entry']=val;self.assertEqual(self.result()['decision'],'NO_TRADE')
    def test_policy_guard(self):
        with self.assertRaises(InvalidData): Policy(risk_fraction='.1').validate()
    def test_live_adapter_disabled(self):
        with self.assertRaises(PermissionError): SafetyController.live_order()
    def test_nonfinite_budget(self):
        self.a['equity']='NaN';self.assertEqual(self.result()['decision'],'NO_TRADE')
    def test_randomized_loss_invariant(self):
        rng=random.Random(2026)
        for i in range(1500):
            s,m=sample_signal();b=copy.deepcopy(self.b)
            stop=Decimal(rng.randint(5,100))*Decimal('.0001');s['stop_loss']=str(Decimal(s['entry'])-stop);s['take_profit']=str(Decimal(s['entry'])+stop*2)
            b['contract_size']=str(rng.choice([100,1000,100000]));b['lot_increment']=rng.choice(['.001','.01','.03']);b['minimum_lot']=b['lot_increment']
            m['spread_pips']=str(rng.uniform(.1,2));m['slippage_pips']=str(rng.uniform(0,3));m['commission_per_lot']=str(rng.uniform(0,1));m['swap_per_lot']=str(rng.uniform(0,.2))
            budget=Decimal(rng.randint(1,750))/1000
            c=PositionSizer.calculate(s,b,m,budget,Policy())
            self.assertLessEqual(c['estimated_max_loss'],budget)
            self.assertLessEqual(c['position_size'],c['calculated_safe_position_size'])
            if c['position_size']:
                self.assertEqual((c['position_size']-Decimal(b['minimum_lot']))%Decimal(b['lot_increment']),0)

class PersistenceTests(Fixture):
    def test_duplicate_persists_after_restart(self):
        r=self.submit();self.app=Application(self.path);r2=self.submit();self.assertIn('DUPLICATE_SIGNAL',r2['blocking_factors']);self.assertEqual(len(self.app.snapshot()['open_positions']),1)
    def test_concurrent_duplicate(self):
        with ThreadPoolExecutor(max_workers=4) as pool: results=list(pool.map(lambda _:self.submit(),range(4)))
        self.assertEqual(sum(r['decision']=='APPROVED_SIMULATED_TRADE' for r in results),1)
    def test_concurrent_different_signals(self):
        signals=[sample_signal()[0] for _ in range(4)]
        with ThreadPoolExecutor(max_workers=4) as pool: results=list(pool.map(self.submit,signals))
        self.assertEqual(sum(r['decision']=='APPROVED_SIMULATED_TRADE' for r in results),1)
    def test_approval_reserves_risk(self):
        r=self.submit();a=self.app.snapshot()['accounts'][0];self.assertEqual(Decimal(a['reserved_risk']),r['calculations']['estimated_max_loss'])
    def test_close_stop_updates_equity(self):
        r=self.submit();o=self.app.close_position(r['position_id'],'stop');self.assertEqual(o['net_pnl'],-r['calculations']['estimated_max_loss'])
        a=self.app.snapshot()['accounts'][0];self.assertEqual(Decimal(a['daily_loss']),-o['net_pnl']);self.assertEqual(a['open_positions'],0)
    def test_double_close_refused(self):
        r=self.submit();self.app.close_position(r['position_id'],'stop')
        with self.assertRaises(InvalidData):self.app.close_position(r['position_id'],'stop')
    def test_profit_does_not_erase_loss(self):
        r=self.submit();loss=self.app.close_position(r['position_id'],'stop')['net_pnl'];s,_=sample_signal();r=self.submit(s);self.app.close_position(r['position_id'],'target')
        self.assertEqual(Decimal(self.app.snapshot()['accounts'][0]['daily_loss']),-loss)
    def test_audit_integrity(self): self.submit();self.assertTrue(self.app.snapshot()['audit_integrity'])
    def test_audit_append_only(self):
        with self.app.store.transaction() as db:
            with self.assertRaises(sqlite3.IntegrityError):db.execute("DELETE FROM audit_logs")
    def test_failed_audit_rolls_back_trade(self):
        with patch.object(Store,'audit',side_effect=sqlite3.OperationalError('disk full')):
            with self.assertRaises(sqlite3.OperationalError):self.submit()
        self.assertEqual(len(self.app.snapshot()['open_positions']),0)
        self.assertEqual(len(self.app.snapshot()['decisions']),0)
    def test_live_flags_rejected(self): self.assertIn('LIVE_EXECUTION_DISABLED',self.submit(live_execution_enabled=True)['blocking_factors'])
    def test_client_account_state_ignored(self):
        r=self.submit(account_equity='100000',max_allowed_loss_usd='5000');self.assertLessEqual(r['calculations']['estimated_max_loss'],Decimal('.25'))
    def test_real_broker_rejected(self): self.assertEqual(self.submit(broker_id='hfm')['decision'],'NO_TRADE')
    def test_provider_suspension_persists(self):
        with self.app.store.transaction() as db:
            p=Store.get(db,'providers','demo-provider');p['risk_flags']=['martingale'];Store.put(db,'providers',p['id'],p)
        self.submit();self.app=Application(self.path);self.assertEqual(self.app.snapshot()['providers'][0]['status'],'SUSPENDED')
        with self.assertRaises(InvalidData):self.app.review('providers','demo-provider','Reviewed but martingale remains unresolved')
    def test_malformed_logged(self):
        r=self.app.evaluate({});self.assertEqual(r['decision'],'NO_TRADE');self.assertEqual(len(self.app.snapshot()['decisions']),1)
    def test_weekly_stop_requires_review(self):
        with self.app.store.transaction() as db:
            a=Store.get(db,'accounts','ACCOUNT_A');a['status']='SUSPENDED';Store.put(db,'accounts','ACCOUNT_A',a)
        self.app=Application(self.path);self.assertEqual(self.app.snapshot()['accounts'][0]['status'],'SUSPENDED')
        self.app.review('accounts','ACCOUNT_A','New week and all account conditions reviewed')
        self.assertEqual(self.app.snapshot()['accounts'][0]['status'],'ACTIVE')
    def test_withdrawal_requires_evidence(self):
        raw=dict(broker_profile_id='hfm',entity='Unknown',account_type='Test',deposit_method='Card',withdrawal_method='Card',deposited_amount='5',withdrawn_amount='2',fees='0',conversion_cost='0',notes='test',result='SUCCESSFUL')
        with self.assertRaises(InvalidData):self.app.withdrawal(raw)
        raw.update(result='PENDING');self.app.withdrawal(raw);self.assertEqual(len(self.app.snapshot()['withdrawals']),1)

class BacktestTests(unittest.TestCase):
    def test_costs_and_split(self):
        p=dict(account_profile='ACCOUNT_A',out_of_sample_start='2026-02-01T00:00:00Z',trades=[dict(timestamp='2026-01-01T00:00:00Z',gross_pnl='1',spread_cost='.1',commission_cost='.1',slippage_cost='.1',swap_cost='.1'),dict(timestamp='2026-02-02T00:00:00Z',gross_pnl='-.5',spread_cost='.1',commission_cost='0',slippage_cost='0',swap_cost='0')])
        r=BacktestingService.run(p);self.assertEqual(r['summary']['net_pnl'],Decimal('0'));self.assertEqual(r['out_of_sample']['trade_count'],1);self.assertFalse(r['edge_validated'])
        self.assertEqual(r['sensitivity_double_cost']['net_pnl'],Decimal('-.5'))


class CandleTests(unittest.TestCase):
    def payload(self):
        from pathlib import Path
        return json.loads((Path(__file__).parent.parent/'examples/synthetic-candles.json').read_text())
    def test_candle_backtest_has_actual_trades(self):
        from engine.research import CandleBacktestingService
        r=CandleBacktestingService.run(self.payload())
        self.assertGreater(r['summary']['trade_count'],0);self.assertFalse(r['edge_validated'])
        for d in r['decisions']:
            if d['decision']=='APPROVED_SIMULATED_TRADE':self.assertLessEqual(d['calculations']['estimated_max_loss'],d['calculations']['risk_budget'])
        self.assertEqual(r['summary']['trade_count'],len(r['trades']))
    def test_invalid_ohlc_rejected(self):
        from engine.research import CandleBacktestingService
        p=self.payload();p['candles'][0]['high']='.1'
        with self.assertRaises(InvalidData):CandleBacktestingService.run(p)
    def test_no_lookahead_in_decisions(self):
        from engine.research import CandleBacktestingService
        p=self.payload();full=CandleBacktestingService.run(p);p['candles']=p['candles'][:90];prefix=CandleBacktestingService.run(p)
        until=date(p['candles'][-1]['timestamp'])
        decisions=[d for d in full['decisions'] if date(d['timestamp'])<=until]
        self.assertEqual([d['calculations'] for d in decisions],[d['calculations'] for d in prefix['decisions']])
    def test_reverse_dates_rejected(self):
        from engine.research import CandleBacktestingService
        p=self.payload();p['candles'].reverse()
        with self.assertRaises(InvalidData):CandleBacktestingService.run(p)

class ExtendedSafetyTests(Fixture):
    def test_existing_database_receives_new_account_profiles(self):
        with tempfile.TemporaryDirectory() as directory:
            path=directory+'/upgrade.sqlite3'
            app=Application(path)
            with app.store.transaction() as db:
                db.execute("DELETE FROM accounts WHERE id='ACCOUNT_LIVE'")
            upgraded=Application(path)
            self.assertIn('ACCOUNT_LIVE',[account['id'] for account in upgraded.snapshot()['accounts']])

    def test_jpy_loss_conversion_is_conservative(self):
        self.s,self.m=sample_signal(symbol='USDJPY');r=self.submit();o=self.app.close_position(r['position_id'],'stop')
        self.assertLessEqual(-o['net_pnl'],r['calculations']['risk_budget'])
    def test_normalized_pair_settles(self):
        self.s['symbol']='EUR/USD';r=self.submit();self.assertEqual(r['decision'],'APPROVED_SIMULATED_TRADE');self.app.close_position(r['position_id'],'stop')
    def test_migration_upgrade(self):
        with self.app.store.connect() as db:self.assertEqual([r[0] for r in db.execute('SELECT version FROM schema_migrations ORDER BY version')],[1,2,3,4])
    def test_loss_caps_across_days(self):
        yesterday=(utcnow()-timedelta(days=1)).isoformat()
        with self.app.store.transaction() as db:
            db.execute('INSERT INTO signal_outcomes VALUES(?,?,?,?,?)',('old','old','ACCOUNT_A','CLOSED',dumps(dict(closed_at=yesterday,net_pnl='-0.2',provider_id='demo-provider'))))
        a=self.app.snapshot()['accounts'][0];self.assertEqual(a['daily_loss'],'0')
    def test_daily_stop_latch(self):
        from engine.service import TZ
        with self.app.store.transaction() as db:
            a=Store.get(db,'accounts','ACCOUNT_A');a['daily_stop_date']=utcnow().astimezone(TZ).date().isoformat();Store.put(db,'accounts','ACCOUNT_A',a)
        self.assertIn('DAILY_LOSS_STOP_LATCHED',self.submit()['blocking_factors'])
    def test_provider_allocation(self):
        self.app.engine=DecisionEngine(Policy(provider_allocation_fraction='.01'))
        self.assertIn('PROVIDER_ALLOCATION_LIMIT',self.result()['blocking_factors'])
    def test_low_score_watchlist_cannot_open(self):
        self.m.update(technical_score='.1',regime_score='.1',macro_score='.1');r=self.submit()
        self.assertEqual(r['decision'],'WATCHLIST');self.assertEqual(len(self.app.snapshot()['open_positions']),0)


class CircuitBreakerTests(Fixture):
    def test_fifth_loss_suspends_and_review_is_explicit(self):
        with self.app.store.transaction() as db:
            p=Store.get(db,'providers','demo-provider');p['consecutive_losses']=4;Store.put(db,'providers',p['id'],p)
        r=self.submit();self.app.close_position(r['position_id'],'stop')
        self.assertEqual(self.app.snapshot()['providers'][0]['status'],'SUSPENDED')
        self.app=Application(self.path)
        self.assertEqual(self.app.snapshot()['providers'][0]['status'],'SUSPENDED')
        self.app.review('providers','demo-provider','Reviewed five losses in synthetic test data; restart research only.')
        self.assertEqual(self.app.snapshot()['providers'][0]['status'],'ACTIVE')
    def test_invalid_score_weights_fail_closed(self):
        with self.assertRaises(InvalidData):Policy(score_weights={'provider':'1'}).validate()
    def test_withdrawal_zero_amount_cannot_be_success(self):
        raw=dict(broker_profile_id='hfm',entity='Test entity',account_type='Test',deposit_method='Card',withdrawal_method='Card',deposited_amount='5',withdrawn_amount='0',fees='0',conversion_cost='0',notes='test',result='SUCCESSFUL',completed_date=stamp(),completed_steps=list(range(1,10)),evidence_notes='No money received')
        with self.assertRaises(InvalidData):self.app.withdrawal(raw)

if __name__=='__main__': unittest.main()
