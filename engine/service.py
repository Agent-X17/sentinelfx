from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from uuid import uuid4
from decimal import Decimal
from .domain import *
from .storage import Store,dumps,loads
from .seed import seed,sample_signal

TZ=ZoneInfo('Africa/Dar_es_Salaam')

class Application:
    def __init__(self,path,policy=None,settings=None):
        self.store=Store(path); self.engine=DecisionEngine(policy); self.settings=settings; seed(self.store)
        with self.store.transaction() as db: Store.audit(db,'POLICY_LOADED',asdict(self.engine.policy))
    def refresh(self,db,profile,provider='demo-provider'):
        a=Store.get(db,'accounts',profile)
        if not a: raise InvalidData('Unknown account')
        today=utcnow().astimezone(TZ).date(); monday=today-timedelta(days=today.weekday())
        daily=weekly=pd=pw=Decimal(0)
        for r in db.execute('SELECT payload FROM signal_outcomes WHERE account_profile=? AND status=?',(profile,'CLOSED')):
            o=loads(r['payload']); day=date(o['closed_at']).astimezone(TZ).date(); loss=max(Decimal(0),-decimal(o['net_pnl']))
            if day>=monday: weekly+=loss
            if day==today: daily+=loss
            if o['provider_id']==provider:
                if day>=monday: pw+=loss
                if day==today: pd+=loss
        positions=[loads(r['payload']) for r in db.execute("SELECT payload FROM positions WHERE account_profile=? AND status='OPEN'",(profile,))]
        a.update(daily_loss=str(daily),weekly_loss=str(weekly),provider_daily_loss=str(pd),provider_weekly_loss=str(pw),
                 reserved_risk=str(sum((decimal(p['calculations']['estimated_max_loss']) for p in positions),Decimal(0))),
                 used_margin=str(sum((decimal(p['calculations']['margin_required']) for p in positions),Decimal(0))),open_positions=len(positions),updated_at=stamp())
        a['free_margin']=str(max(Decimal(0),decimal(a['equity'])-decimal(a['used_margin'])))
        baseline=min(decimal(a['equity']),decimal(a['starting_equity']))
        if weekly>=baseline*decimal(self.engine.policy.weekly_fraction):
            if a['status']!='SUSPENDED': Store.audit(db,'ACCOUNT_SUSPENDED',{'account':profile,'reason':'Weekly loss cap','human_review_required':True})
            a['status']='SUSPENDED'
        if daily>=baseline*decimal(self.engine.policy.daily_fraction): a['daily_stop_date']=today.isoformat()
        Store.put(db,'accounts',profile,a)
        return a
    def preview(self,payload):
        """Calculate a decision without creating a decision or position record.

        The trading bridge uses this only to obtain the exact conservative lot
        size that MT5 must preflight. ``evaluate`` repeats every check before it
        persists anything, so the preview can never authorize a trade.
        """
        if not isinstance(payload,dict): payload={'malformed_payload':payload}
        profile=payload.get('account_profile','ACCOUNT_A'); raw=payload.get('signal',{}); market=payload.get('market',{})
        if not isinstance(raw,dict): raw={}
        if not isinstance(market,dict): market={}
        provider_id=raw.get('provider',''); broker_id=payload.get('broker_id','')
        if not all(isinstance(v,str) for v in (profile,provider_id,broker_id)): profile='INVALID';provider_id='';broker_id=''
        with self.store.transaction() as db:
            a=self.refresh(db,profile,provider_id) if profile in PROFILES else {}
            b=Store.get(db,'broker_profiles',broker_id) or {}; p=Store.get(db,'providers',provider_id) or {}
            return self.engine.evaluate(profile,a,b,p,raw,market)

    def evaluate(self,payload,trusted_adapter=False,checked_volume=None):
        if not isinstance(payload,dict): payload={'malformed_payload':payload}
        profile=payload.get('account_profile','ACCOUNT_A'); raw=payload.get('signal',{}); market=payload.get('market',{})
        if not isinstance(raw,dict): raw={}
        if not isinstance(market,dict): market={}
        provider_id=raw.get('provider',''); broker_id=payload.get('broker_id','')
        if not all(isinstance(v,str) for v in (profile,provider_id,broker_id)): profile='INVALID';provider_id='';broker_id=''
        with self.store.transaction() as db:
            a=self.refresh(db,profile,provider_id) if profile in PROFILES else {}
            b=Store.get(db,'broker_profiles',broker_id) or {}; p=Store.get(db,'providers',provider_id) or {}
            result=self.engine.evaluate(profile,a,b,p,raw,market)
            reasons=result['blocking_factors']
            if trusted_adapter and not reasons:
                calculated=result.get('calculations',{}).get('position_size')
                if checked_volume is None:
                    reasons.append('MT5_ORDER_CHECK_REQUIRED')
                else:
                    try:
                        if decimal(checked_volume) != decimal(calculated): reasons.append('MT5_ORDER_CHECK_VOLUME_MISMATCH')
                    except (InvalidData,TypeError):
                        reasons.append('MT5_ORDER_CHECK_VOLUME_MISMATCH')
            if not Store.verify_audit(db): reasons.append('AUDIT_INTEGRITY_FAILED')
            sid=raw.get('signal_id'); sid=sid if isinstance(sid,str) and len(sid)<=200 else str(uuid4())
            duplicate=db.execute('SELECT 1 FROM signals WHERE id=?',(sid,)).fetchone() is not None
            if duplicate: reasons.append('DUPLICATE_SIGNAL')
            operating_mode = self.settings.mode if self.settings else 'SIMULATION'
            supplied_mode = payload.get('system_mode', operating_mode)
            if payload.get('live_execution_enabled') is True or supplied_mode not in (operating_mode, MODE): reasons.append('LIVE_EXECUTION_DISABLED')
            if not trusted_adapter and (market.get('synthetic') is not True or b.get('synthetic') is not True or p.get('live_status')!='SYNTHETIC'):
                reasons.append('REAL_DATA_ADAPTER_NOT_CONFIGURED')
            if operating_mode in ('DISCONNECTED','LIVE_GATED'): reasons.append('SYSTEM_MODE_BLOCKED')
            if a.get('daily_stop_date')==utcnow().astimezone(TZ).date().isoformat(): reasons.append('DAILY_LOSS_STOP_LATCHED')
            symbol=raw.get('symbol')
            if not isinstance(symbol,str) or symbol.replace('/','').upper() not in b.get('supported_symbols',[]): reasons.append('BROKER_SYMBOL_UNSUPPORTED')
            if len([r for r in db.execute("SELECT payload FROM providers") if loads(r['payload']).get('status')=='ACTIVE'])>self.engine.policy.max_active_providers:
                reasons.append('ACTIVE_PROVIDER_LIMIT')
            if any(x in reasons for x in ('PROVIDER_FORBIDDEN_BEHAVIOR','PROVIDER_DRAWDOWN_BREAKER','PROVIDER_LOSS_BREAKER','PROVIDER_DATA_UNRELIABLE','PROVIDER_METRICS_UNKNOWN','PROVIDER_FLAGS_UNKNOWN')) and p:
                p.update(status='SUSPENDED',suspension_reason='; '.join(reasons),human_review_required=True,suspended_at=stamp())
                Store.put(db,'providers',provider_id,p)
                db.execute('INSERT INTO provider_status VALUES(?,?,?)',(str(uuid4()),provider_id,dumps(p)))
                Store.audit(db,'PROVIDER_SUSPENDED',p)
            if reasons:
                result['decision']='WATCHLIST' if reasons==['INSUFFICIENT_EVIDENCE'] else 'NO_TRADE'
                result['reason']='; '.join(reasons)
            result['decision_id']=str(uuid4()); result['signal_id']=sid
            result['operating_mode']=operating_mode
            result['validation_results'].append({'stage':'Persistent safety controls','passed':not reasons,'reasons':list(reasons)})
            if not duplicate:
                db.execute('INSERT INTO signals VALUES(?,?,?,?,?,?,?,?)',(sid,provider_id,profile,stamp(),dumps(raw),dumps(SignalNormalizer.normalize(raw)),result['decision'],result['reason']))
            if result['decision']=='APPROVED_SIMULATED_TRADE':
                pos=dict(id=str(uuid4()),account_profile=profile,provider_id=provider_id,signal_id=sid,signal=SignalNormalizer.normalize(raw),
                         broker=b,market=market,calculations=result['calculations'],opened_at=stamp(),status='OPEN')
                db.execute('INSERT INTO positions VALUES(?,?,?,?,?,?)',(pos['id'],profile,provider_id,sid,'OPEN',dumps(pos)))
                result['position_id']=pos['id']; self.refresh(db,profile,provider_id)
                db.execute('INSERT INTO paper_trades(id,decision_id,status,payload,created_at) VALUES(?,?,?,?,?)',(pos['id'],result['decision_id'],'OPEN',dumps(pos),stamp()))
                Store.audit(db,'SIMULATED_POSITION_OPENED',pos)
            for reason in reasons:
                db.execute('INSERT INTO veto_reasons VALUES(?,?,?,?,?)',(str(uuid4()),result['decision_id'],reason,result['reason'],stamp()))
            db.execute('INSERT INTO decisions VALUES(?,?,?,?,?)',(result['decision_id'],sid,profile,stamp(),dumps(result)))
            Store.audit(db,'DECISION',{'account':a,'broker':b,'provider':p,'raw_signal':raw,'raw_request':payload,'market':market,'result':result})
            return result
    def close_position(self,position_id,outcome):
        if outcome not in ('stop','target'): raise InvalidData('Use stop or target for the deterministic simulation')
        with self.store.transaction() as db:
            row=db.execute('SELECT * FROM positions WHERE id=?',(position_id,)).fetchone()
            if not row or row['status']!='OPEN': raise InvalidData('Position is not open')
            p=loads(row['payload']); c=p['calculations']; s=p['signal']; lots=decimal(c['position_size'])
            exit_price=decimal(s['stop_loss'] if outcome=='stop' else s['take_profit'])
            conversion=decimal('1')/exit_price if s['symbol']=='USDJPY' else decimal('1')
            gross=(exit_price-decimal(s['entry']))*decimal(p['broker']['contract_size'])*lots*conversion*(1 if s['direction']=='BUY' else -1)
            costs=decimal(c['estimated_total_transaction_cost']); net=gross-costs
            o=dict(id=str(uuid4()),signal_id=p['signal_id'],account_profile=p['account_profile'],provider_id=p['provider_id'],simulated_entry=s['entry'],simulated_exit=exit_price,
                   position_size=lots,gross_pnl=gross,net_pnl=net,spread_cost=c['estimated_spread_cost'],commission_cost=c['estimated_commission_cost'],
                   slippage_cost=c['estimated_slippage_cost'],swap_cost=c['estimated_swap_cost'],r_multiple=net/decimal(c['estimated_max_loss']),outcome_status='CLOSED',closed_at=stamp())
            db.execute('UPDATE positions SET status=? WHERE id=?',('CLOSED',position_id))
            db.execute('UPDATE paper_trades SET status=?,closed_at=? WHERE id=?',('CLOSED',stamp(),position_id))
            db.execute('INSERT INTO signal_outcomes VALUES(?,?,?,?,?)',(o['id'],p['signal_id'],p['account_profile'],'CLOSED',dumps(o)))
            a=Store.get(db,'accounts',p['account_profile']); a['equity']=str(decimal(a['equity'])+net); Store.put(db,'accounts',p['account_profile'],a)
            provider=Store.get(db,'providers',p['provider_id']); provider['consecutive_losses']=provider.get('consecutive_losses',0)+1 if net<0 else 0
            if provider['consecutive_losses']>=self.engine.policy.provider_circuit_breaker_losses:
                provider.update(status='SUSPENDED',human_review_required=True,suspension_reason='Consecutive-loss circuit breaker',suspended_at=stamp())
                Store.audit(db,'PROVIDER_SUSPENDED',provider)
                db.execute('INSERT INTO provider_status VALUES(?,?,?)',(str(uuid4()),provider['id'],dumps(provider)))
            Store.put(db,'providers',p['provider_id'],provider)
            self.refresh(db,p['account_profile'],p['provider_id'])
            day=utcnow().astimezone(TZ).date().isoformat()
            daily_outcomes=[loads(r['payload']) for r in db.execute('SELECT payload FROM signal_outcomes WHERE account_profile=?',(p['account_profile'],))]
            daily_outcomes=[r for r in daily_outcomes if r['provider_id']==p['provider_id'] and date(r['closed_at']).astimezone(TZ).date().isoformat()==day]
            record=dict(provider_id=p['provider_id'],account_profile=p['account_profile'],date=day,daily_pnl=sum((decimal(r['net_pnl']) for r in daily_outcomes),Decimal(0)),number_of_closed_trades=len(daily_outcomes),source='FOLLOWER_SIMULATION')
            key=p['provider_id']+':'+p['account_profile']+':'+day
            db.execute('INSERT INTO provider_daily_performance VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',(key,p['provider_id'],day,dumps(record)))
            Store.audit(db,'SIMULATED_POSITION_CLOSED',o)
            return o
    def snapshot(self):
        with self.store.transaction() as db:
            accounts=[self.refresh(db,k) for k in PROFILES]
            def docs(table): return [dict(id=r['id'],**{k:v for k,v in loads(r['payload']).items() if k!='id'}) for r in db.execute('SELECT id,payload FROM '+table)]
            brokers=docs('broker_profiles')
            for b in brokers: b['weighted_score']=BrokerResearchService.score(b)
            operating_mode=self.settings.mode if self.settings else 'SIMULATION'
            return dict(mode=operating_mode,legacy_mode=MODE,live_execution_enabled=False,policy=asdict(self.engine.policy),accounts=accounts,brokers=brokers,
                providers=docs('providers'),provider_metrics=docs('provider_metrics'),provider_daily_performance=docs('provider_daily_performance'),strategies=docs('strategies'),withdrawals=docs('withdrawal_tests'),backtests=docs('backtests'),
                open_positions=[loads(r['payload']) for r in db.execute("SELECT payload FROM positions WHERE status='OPEN'")],
                decisions=[loads(r['payload']) for r in db.execute('SELECT payload FROM decisions ORDER BY rowid DESC LIMIT 100')],
                signals=[dict(r) for r in db.execute('SELECT * FROM signals ORDER BY rowid DESC LIMIT 100')],
                outcomes=[loads(r['payload']) for r in db.execute('SELECT payload FROM signal_outcomes ORDER BY rowid DESC LIMIT 100')],
                raw_webhooks=[dict(r) for r in db.execute('SELECT * FROM raw_webhooks ORDER BY received_at DESC LIMIT 100')],
                normalized_signals=[dict(r) for r in db.execute('SELECT * FROM normalized_signals ORDER BY created_at DESC LIMIT 100')],
                symbol_mappings=[dict(r) for r in db.execute('SELECT * FROM symbol_mappings ORDER BY canonical_symbol,mt5_symbol')],
                risk_checks=[dict(r) for r in db.execute('SELECT * FROM risk_checks ORDER BY created_at DESC LIMIT 100')],
                audit=[dict(r) for r in db.execute('SELECT * FROM audit_logs ORDER BY id DESC LIMIT 100')],audit_integrity=Store.verify_audit(db))
    def review(self,kind,key,notes):
        if kind not in ('providers','accounts') or not isinstance(notes,str) or len(notes.strip())<20: raise InvalidData('A review explanation of at least 20 characters is required')
        with self.store.transaction() as db:
            obj=Store.get(db,kind,key)
            if not obj: raise InvalidData('Record missing')
            if kind=='providers':
                if FORBIDDEN.intersection(obj.get('risk_flags',[])) or decimal(obj.get('current_drawdown','1'))>=decimal(self.engine.policy.max_provider_drawdown): raise InvalidData('Underlying provider risk must be resolved before review')
                obj['consecutive_losses']=0
            else:
                self.refresh(db,key);obj=Store.get(db,kind,key)
                if decimal(obj['weekly_loss'])>=min(decimal(obj['equity']),decimal(obj['starting_equity']))*decimal(self.engine.policy.weekly_fraction): raise InvalidData('Weekly budget is still exhausted')
            obj.update(status='ACTIVE',human_review_required=False,review_notes=notes,reviewed_at=stamp())
            Store.put(db,kind,key,obj); Store.audit(db,'HUMAN_REVIEW',{'kind':kind,'record':obj})
            return obj
    def withdrawal(self,raw):
        with self.store.transaction() as db:
            broker=Store.get(db,'broker_profiles',raw.get('broker_profile_id',''))
            if not broker or broker.get('synthetic'): raise InvalidData('Choose a research broker')
            for key in ('deposit_method','withdrawal_method','entity','account_type','notes'):
                if not isinstance(raw.get(key),str) or not raw[key].strip(): raise InvalidData('Missing '+key)
            for key in ('deposited_amount','withdrawn_amount','fees','conversion_cost'): number(raw,key,0)
            if raw.get('result') not in ('PENDING','SUCCESSFUL','UNSUCCESSFUL'): raise InvalidData('Invalid result')
            for field in ('deposit_date','withdrawal_request_date'):
                if raw.get(field): date(raw[field])
            if raw['result']=='SUCCESSFUL':
                number(raw,'deposited_amount','0.00000001');number(raw,'withdrawn_amount','0.00000001')
                if len(raw.get('completed_steps',[]))!=9 or set(raw['completed_steps'])!=set(range(1,10)): raise InvalidData('All nine workflow steps are required')
                date(raw.get('completed_date'))
                if not raw.get('evidence_notes'): raise InvalidData('Successful withdrawal requires evidence notes')
            record=dict(raw,id=str(uuid4()),recorded_at=stamp(),funding_recommendation='NOT_APPROVED')
            db.execute('INSERT INTO withdrawal_tests VALUES(?,?,?)',(record['id'],broker['id'],dumps(record)))
            Store.audit(db,'WITHDRAWAL_TEST_RECORDED',record)
            return record
