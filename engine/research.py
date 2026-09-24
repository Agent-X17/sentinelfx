"""Cost-aware, chronological backtesting of supplied entry/exit observations.
No automatic strategy discovery or real-money endorsement.
"""
from decimal import Decimal
from math import sqrt
from .domain import decimal,number,date,InvalidData,PROFILES,Policy

class PerformanceAnalyticsService:
    @staticmethod
    def summarize(pnls,starting_equity):
        values=[decimal(x) for x in pnls]; n=len(values); wins=[x for x in values if x>0]; losses=[-x for x in values if x<0]
        peak=equity=decimal(starting_equity); max_dd=Decimal(0)
        curve=[str(equity)]
        for x in values:
            equity+=x; peak=max(peak,equity); max_dd=max(max_dd,(peak-equity)/peak);curve.append(str(equity))
        z=1.96;p=len(wins)/n if n else 0; den=1+z*z/n if n else 1
        center=(p+z*z/(2*n))/den if n else 0
        half=z*sqrt(p*(1-p)/n+z*z/(4*n*n))/den if n else 0
        return dict(trade_count=n,net_pnl=sum(values,Decimal(0)),ending_equity=equity,max_drawdown=max_dd,
                    win_rate=p,win_rate_95_interval=[max(0,center-half),min(1,center+half)] if n else None,
                    average_win=sum(wins,Decimal(0))/len(wins) if wins else 0,average_loss=sum(losses,Decimal(0))/len(losses) if losses else 0,
                    profit_factor=sum(wins,Decimal(0))/sum(losses,Decimal(0)) if losses else None,equity_curve=curve,
                    sample_warning='Small sample; no evidence of skill' if n<100 else 'Independent out-of-sample review still required',sharpe=None,sortino=None)

class BacktestingService:
    @staticmethod
    def run(payload):
        profile=payload.get('account_profile')
        if profile not in PROFILES: raise InvalidData('Unknown account profile')
        trades=payload.get('trades')
        if not isinstance(trades,list) or not 1<=len(trades)<=10000: raise InvalidData('Supply 1–10000 chronological trade observations')
        split=date(payload.get('out_of_sample_start')); previous=None; groups={'in_sample':[],'out_of_sample':[]}; gross=[];costs=[];curve=[]
        for t in trades:
            when=date(t.get('timestamp'))
            if previous and when<previous: raise InvalidData('Trades must be chronological')
            previous=when
            pnl=number(t,'gross_pnl');cost=sum((number(t,k,0) for k in ('spread_cost','commission_cost','slippage_cost','swap_cost')),Decimal(0))
            groups['out_of_sample' if when>=split else 'in_sample'].append(pnl-cost);gross.append(pnl);costs.append(cost);curve.append(pnl-cost)
        start=PROFILES[profile]
        return dict(account_profile=profile,summary=PerformanceAnalyticsService.summarize(curve,start),
                    in_sample=PerformanceAnalyticsService.summarize(groups['in_sample'],start),
                    out_of_sample=PerformanceAnalyticsService.summarize(groups['out_of_sample'],start),
                    gross_pnl=sum(gross,Decimal(0)),transaction_costs=sum(costs,Decimal(0)),
                    sensitivity_double_cost=PerformanceAnalyticsService.summarize([g-2*c for g,c in zip(gross,costs)],start),
                    edge_validated=False,limitations='Analyzes user-supplied trade observations; does not validate fills, market data, strategy rules, parameter stability or survivorship bias. No promotion to live recommendations.')

class CandleBacktestingService:
    """One transparent strategy: previous-bar MA crossover, next-bar open entry.
    Prior-window range determines stop; pessimistic stop-first intrabar fills.
    All candidate positions pass the same deterministic risk engine.
    """
    @staticmethod
    def run(payload):
        from datetime import timedelta
        from .domain import DecisionEngine,MODE,stamp
        from .seed import sample_signal
        profile=payload.get('account_profile','ACCOUNT_A')
        if profile not in PROFILES: raise InvalidData('Unknown account profile')
        symbol=payload.get('symbol','EURUSD')
        if symbol not in ('EURUSD','USDJPY','GBPUSD'): raise InvalidData('Unsupported pair')
        fast=payload.get('fast_period',3);slow=payload.get('slow_period',8)
        if type(fast) is not int or type(slow) is not int or not 2<=fast<slow<=200: raise InvalidData('Require 2 <= fast < slow <= 200')
        candles=payload.get('candles')
        if not isinstance(candles,list) or not slow+2<=len(candles)<=10000: raise InvalidData('Insufficient candles or more than 10000 rows')
        split=date(payload.get('out_of_sample_start'));previous=None;bars=[]
        for raw in candles:
            t=date(raw.get('timestamp'));o,h,l,c=[number(raw,k,'0.00000001') for k in ('open','high','low','close')]
            if previous and t<=previous: raise InvalidData('Candle timestamps must be strictly increasing')
            if not l<=min(o,c)<=max(o,c)<=h: raise InvalidData('Invalid OHLC bounds')
            bars.append(dict(t=t,open=o,high=h,low=l,close=c));previous=t
        costs=payload.get('costs',{})
        for k in ('spread_pips','slippage_pips','commission_per_lot','swap_per_lot'): number(costs,k,0)
        contract=number(payload,'contract_size','1');minimum=number(payload,'minimum_lot','.000001');step=number(payload,'lot_increment','.000001');leverage=number(payload,'leverage','1','100')
        broker=dict(synthetic=True,contract_size=contract,minimum_lot=minimum,lot_increment=step,maximum_lot='10',leverage=leverage)
        provider=dict(name='Synthetic MA research',status='ACTIVE',verified_status=True,live_status='SYNTHETIC',risk_flags=[],trade_count=1000,age_days=365,current_drawdown='0',consecutive_losses=0,data_quality='.9')
        engine=DecisionEngine();equity=Decimal(PROFILES[profile]);position=None;trades=[];rejected=[];decisions=[];suspended=False;daily_stop=None
        pip=Decimal('.01' if symbol=='USDJPY' else '.0001')
        def settle(bar,exit_price,why):
            nonlocal position,equity
            p=position;direction=1 if p['direction']=='BUY' else -1
            # Convert JPY realized PnL using exit, not the entry conversion.
            conversion=Decimal(1)/exit_price if symbol=='USDJPY' else Decimal(1)
            gross=(exit_price-p['entry'])*contract*p['lots']*direction*conversion
            cost=p['cost'];net=gross-cost;equity+=net
            trades.append(dict(timestamp=bar['t'].isoformat(),entry_timestamp=p['timestamp'],direction=p['direction'],simulated_entry=p['entry'],simulated_exit=exit_price,position_size=p['lots'],gross_pnl=gross,net_pnl=net,estimated_max_loss=p['risk'],gap_exceeded_estimate=(-net>p['risk']),exit_reason=why,**p['cost_breakdown']))
            position=None
        for i in range(slow+1,len(bars)):
            bar=bars[i];day=bar['t'].date();monday=day-timedelta(days=day.weekday())
            if position:
                stop=position['stop'];target=position['target'];buy=position['direction']=='BUY'
                if (bar['open']<=stop if buy else bar['open']>=stop): settle(bar,bar['open'],'GAP_STOP')
                elif (bar['low']<=stop if buy else bar['high']>=stop): settle(bar,stop,'STOP_FIRST')
                elif (bar['high']>=target if buy else bar['low']<=target): settle(bar,target,'TARGET')
            if position: continue
            def mean(start,end):return sum((b['close'] for b in bars[start:end]),Decimal(0))/(end-start)
            now_fast=mean(i-fast,i);now_slow=mean(i-slow,i);old_fast=mean(i-fast-1,i-1);old_slow=mean(i-slow-1,i-1)
            buy=old_fast<=old_slow and now_fast>now_slow;sell=old_fast>=old_slow and now_fast<now_slow
            if not buy and not sell: continue
            daily=sum((max(Decimal(0),-t['net_pnl']) for t in trades if date(t['timestamp']).date()==day),Decimal(0))
            weekly=sum((max(Decimal(0),-t['net_pnl']) for t in trades if date(t['timestamp']).date()>=monday),Decimal(0))
            base=min(equity,Decimal(PROFILES[profile]))
            if weekly>=base*Decimal('.04'): suspended=True
            if daily>=base*Decimal('.02'):daily_stop=day
            if suspended or daily_stop==day or equity<=0:
                rejected.append(dict(timestamp=bar['t'].isoformat(),reason='ACCOUNT_LOSS_STOP'));continue
            distance=max(pip*5,sum((b['high']-b['low'] for b in bars[i-slow:i]),Decimal(0))/slow)
            entry=bar['open'];direction='BUY' if buy else 'SELL';stop=entry-distance if buy else entry+distance;target=entry+2*distance if buy else entry-2*distance
            s,m=sample_signal(symbol=symbol);s.update(timestamp=bar['t'].isoformat(),expires_at=(bar['t']+timedelta(minutes=5)).isoformat(),entry=entry,stop_loss=stop,take_profit=target,direction=direction,strategy='Prior-bar MA crossover')
            m.update(costs);m.update(price=entry,timestamp=bar['t'].isoformat(),context_timestamp=bar['t'].isoformat())
            account=dict(equity=equity,currency='USD',state_certain=True,status='ACTIVE',daily_loss=daily,weekly_loss=weekly,provider_daily_loss=daily,provider_weekly_loss=weekly,reserved_risk=0,open_positions=0,used_margin=0,free_margin=equity,updated_at=bar['t'].isoformat())
            result=engine.evaluate(profile,account,broker,provider,s,m,bar['t']);decisions.append(result)
            if result['decision']!='APPROVED_SIMULATED_TRADE':rejected.append(dict(timestamp=bar['t'].isoformat(),reason=result['reason']));continue
            calc=result['calculations'];position=dict(entry=entry,stop=stop,target=target,direction=direction,lots=calc['position_size'],cost=calc['estimated_total_transaction_cost'],risk=calc['estimated_max_loss'],timestamp=bar['t'].isoformat(),cost_breakdown={k:calc['estimated_'+k] for k in ('spread_cost','commission_cost','slippage_cost','swap_cost')})
            # The entry bar can touch both levels; stop always wins.
            if (bar['low']<=stop if buy else bar['high']>=stop):settle(bar,stop,'ENTRY_BAR_STOP_FIRST')
            elif (bar['high']>=target if buy else bar['low']<=target):settle(bar,target,'ENTRY_BAR_TARGET')
        if position:settle(bars[-1],bars[-1]['close'],'END_OF_DATA')
        summary=PerformanceAnalyticsService.summarize([t['net_pnl'] for t in trades],PROFILES[profile])
        groups={name:PerformanceAnalyticsService.summarize([t['net_pnl'] for t in trades if (date(t['timestamp'])>=split)==out],PROFILES[profile]) for name,out in [('in_sample',False),('out_of_sample',True)]}
        return dict(account_profile=profile,summary=summary,**groups,trades=trades,rejections=rejected,decision_count=len(decisions),
                    decisions=decisions,gross_pnl=sum((t['gross_pnl'] for t in trades),Decimal(0)),edge_validated=False,
                    mode=MODE,rules='Previous completed-bar fast/slow MA crossover. Entry at next open; prior slow-window average range stop; target 2R; stop first if both touched; gaps at open.',
                    assumptions='Synthetic neutral news/macro/liquidity context; prices supplied by user; costs charged once per completed trade; fixed swap allowance is not a holding-time swap model; no real-data provenance validation.',
                    limitations='Research only. Gap losses can exceed pre-trade estimates. No strategy endorsement or live recommendation. Regime analysis and parameter stability require independent research.')
