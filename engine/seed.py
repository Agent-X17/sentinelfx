from datetime import timedelta
from uuid import uuid4
from .domain import PROFILES, stamp, utcnow
from .storage import Store,dumps

STRATEGIES=['Trend following','Momentum','Breakouts','Mean reversion','London session','New York session','Volatility breakout','Moving averages','RSI','ATR','Support and resistance','Market structure','Multi-timeframe confirmation','Carry','Macro and interest-rate regimes']

def seed(store):
    with store.transaction() as db:
        already_initialized=bool(db.execute('SELECT count(*) FROM accounts').fetchone()[0])
        for key,equity in PROFILES.items():
            if not Store.get(db,'accounts',key):
                Store.put(db,'accounts',key,dict(id=key,equity=equity,starting_equity=equity,currency='USD',status='ACTIVE',
                    state_certain=True,daily_loss='0',weekly_loss='0',reserved_risk='0',open_positions=0,used_margin='0',free_margin=equity,
                    provider_daily_loss='0',provider_weekly_loss='0',updated_at=stamp(),daily_stop_date=None))
        # Version upgrades may add account profiles. Preserve existing records
        # and insert only profiles that the older database does not contain.
        if already_initialized: return
        for brand in ['HFM','FP Markets','Exness','XM','IC Markets','Pepperstone','Deriv','FXTM']:
            key=brand.lower().replace(' ','-')
            Store.put(db,'broker_profiles',key,dict(id=key,brand=brand,entity=None,country=None,regulator=None,license_number=None,
                tanzania_availability='UNCERTAIN',account_type='UNCONFIRMED',verification_status='UNCERTAIN',
                withdrawal_status='UNCONFIRMED',withdrawal_notes='Written Tanzania-specific confirmation and withdrawal test required.',
                legal_url=None,last_checked_at=None,evidence_scores={},research_fields={},
                warning='ENTITY UNCONFIRMED — DO NOT FUND UNTIL WRITTEN CONFIRMATION',synthetic=False))
        for key,contract in [('demo-cent','1000'),('demo-standard','100000')]:
            Store.put(db,'broker_profiles',key,dict(id=key,brand='Synthetic '+('Cent' if contract=='1000' else 'Standard'),
                entity='SIMULATION FIXTURE — no broker',country='N/A',regulator='N/A',license_number='N/A',
                synthetic=True,tanzania_availability='NOT_APPLICABLE',account_type=key,contract_size=contract,
                minimum_lot='0.01',lot_increment='0.01',maximum_lot='10',leverage='30',spread_model='Synthetic variable',
                commission_model='Round turn per lot USD',verification_status='SYNTHETIC',withdrawal_status='NOT_APPLICABLE',
                withdrawal_notes='No deposits or withdrawals',supported_symbols=['EURUSD','USDJPY','GBPUSD']))
        provider=dict(id='demo-provider',name='Research fixture',platform='Synthetic',broker='No broker',source_url=None,
            live_status='SYNTHETIC',verified_status=True,status='ACTIVE',created_at=stamp(),updated_at=stamp(),risk_flags=[],
            trade_count=250,age_days=365,current_drawdown='0.02',max_drawdown='0.04',data_quality='0.95',consecutive_losses=0,
            notes='Invented metrics for deterministic tests. Not a real provider or evidence of profitability.')
        Store.put(db,'providers','demo-provider',provider)
        db.execute('INSERT INTO provider_metrics VALUES(?,?,?,?)',('demo-metrics','demo-provider',stamp()[:10],dumps(provider)))
        for i,name in enumerate(STRATEGIES):
            Store.put(db,'strategies',str(i+1),dict(id=str(i+1),name=name,status='RESEARCH_TEMPLATE',rules='',
                instruments=['EURUSD','USDJPY'],timeframe='H1',data_source=None,backtest_period=None,out_of_sample_period=None,
                trade_count=0,gross_return=None,net_return=None,max_drawdown=None,profit_factor=None,sharpe=None,sortino=None,
                weaknesses='Unvalidated hypothesis. Define rules and test realistic costs before use.',
                sample_size=0,edge_validated=False,account_suitability={'ACCOUNT_A':'UNPROVEN','ACCOUNT_B':'UNPROVEN','ACCOUNT_C':'UNPROVEN'}))
        Store.audit(db,'INITIALIZED',{'mode':'RESEARCH_AND_SIMULATION_ONLY','synthetic_data':True})

def sample_signal(scenario='safe',symbol='EURUSD'):
    now=utcnow(); entry,stop,target=('150','149.80','150.40') if symbol=='USDJPY' else ('1.1000','1.0980','1.1040')
    s=dict(signal_id=str(uuid4()),symbol=symbol,direction='BUY',entry=entry,stop_loss=stop,take_profit=target,
           timestamp=now.isoformat(),expires_at=(now+timedelta(minutes=5)).isoformat(),provider='demo-provider',provider_confidence='0.95',
           strategy='Synthetic risk-gate exercise',source='Manual simulation fixture',timeframe='H1',metadata={'provider_lot_size':'1.00'})
    m=dict(symbol=symbol,price=entry,timestamp=now.isoformat(),context_timestamp=now.isoformat(),spread_pips='1',slippage_pips='0.5',commission_per_lot='0',swap_per_lot='0',
           session_open=True,liquid=True,edge_validated=True,conflicting_signals=False,news_risk='0',correlation='0',volatility='0.1',
           execution_quality='0.95',technical_score='0.9',regime_score='0.9',macro_score='0.8',synthetic=True)
    if scenario=='stale': s['timestamp']=(now-timedelta(minutes=20)).isoformat()
    if scenario=='news': m['news_risk']='0.9'
    if scenario=='spread': m['spread_pips']='8'
    if scenario=='missing-stop': s.pop('stop_loss')
    if scenario=='correlation': m['correlation']='0.9'
    return s,m
