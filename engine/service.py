from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from uuid import uuid4
from decimal import Decimal
from dataclasses import replace
from .domain import *
from .storage import Store,dumps,loads
from .seed import seed,sample_signal
from .redaction import redact

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

    def preview_demo_proposal(self,payload):
        """Run the normal engine with stricter demo-proposal limits, without persistence."""
        if not isinstance(payload,dict): payload={'malformed_payload':payload}
        profile=payload.get('account_profile','ACCOUNT_LIVE'); raw=payload.get('signal',{}); market=payload.get('market',{})
        provider_id=raw.get('provider','') if isinstance(raw,dict) else ''
        broker_id=payload.get('broker_id','')
        with self.store.transaction() as db:
            a=self.refresh(db,profile,provider_id) if profile in PROFILES else {}
            b=Store.get(db,'broker_profiles',broker_id) or {}; p=Store.get(db,'providers',provider_id) or {}
            risk=str(decimal(self.settings.demo_max_risk_per_trade_pct)/100)
            daily=str(decimal(self.settings.demo_max_daily_loss_pct)/100)
            policy=replace(self.engine.policy,risk_fraction=risk,daily_fraction=daily,max_positions=self.settings.demo_max_open_positions)
            return DecisionEngine(policy).evaluate(profile,a,b,p,raw,market)

    @staticmethod
    def _proposal_row(row):
        value=loads(row['payload']); value.update(id=row['id'],status=row['status'],created_at=row['created_at'],updated_at=row['updated_at'],expires_at=row['expires_at'])
        return value

    def _proposal_history(self,db,proposal_id,action,old,new,reason,payload=None):
        secrets=(self.settings.webhook_secret,self.settings.demo_expected_account_login,self.settings.demo_expected_broker_server) if self.settings else ()
        safe=redact(payload or {},secrets); reason=redact(reason,secrets)
        db.execute('INSERT INTO demo_trade_proposal_history VALUES(?,?,?,?,?,?,?,?,?)',
                   (str(uuid4()),proposal_id,action,old,new,'LOCAL_OPERATOR' if action!='CREATED' else 'SYSTEM',reason,dumps(safe),stamp()))
        Store.audit(db,'DEMO_PROPOSAL_'+action,{'proposal_id':proposal_id,'from_status':old,'to_status':new,'reason':reason,'details':safe,'order_sent':False})

    def _expire_demo_proposals(self,db):
        now=utcnow()
        rows=db.execute("SELECT * FROM demo_trade_proposals WHERE status IN ('PENDING_LOCAL_REVIEW','APPROVED_FOR_FUTURE_DEMO_EXECUTION')").fetchall()
        for row in rows:
            if date(row['expires_at']) <= now:
                value=self._proposal_row(row); old=row['status']; value.update(status='EXPIRED',order_sent=False)
                db.execute('UPDATE demo_trade_proposals SET status=?,updated_at=?,payload=? WHERE id=?',('EXPIRED',stamp(),dumps(value),row['id']))
                self._proposal_history(db,row['id'],'EXPIRED',old,'EXPIRED','Automatic short-lived expiry')

    def create_demo_proposal(self,raw_webhook_id,signal,decision,account_snapshot,evidence,evaluation_payload=None,checked_volume=None):
        """Persist a review artifact only. This method has no MT5 dependency or send path."""
        settings=self.settings
        if not settings or not settings.demo_trade_proposals_enabled:
            raise InvalidData('DEMO_TRADE_PROPOSALS_DISABLED')
        if settings.mode!='SIMULATION':
            raise InvalidData('DEMO_TRADE_PROPOSALS_REQUIRE_SIMULATION_MODE')
        if settings.demo_trade_proposal_kill_switch:
            raise InvalidData('DEMO_TRADE_PROPOSAL_KILL_SWITCH_ACTIVE')
        if evaluation_payload is not None:
            profile=evaluation_payload.get('account_profile','ACCOUNT_LIVE'); raw=evaluation_payload.get('signal',{}); market=evaluation_payload.get('market',{})
            with self.store.transaction() as db:
                a=self.refresh(db,profile,raw.get('provider','')) if profile in PROFILES else {}
                b=Store.get(db,'broker_profiles',evaluation_payload.get('broker_id','')) or {}; p=Store.get(db,'providers',raw.get('provider','')) or {}
                policy=replace(self.engine.policy,risk_fraction=str(decimal(settings.demo_max_risk_per_trade_pct)/100),daily_fraction=str(decimal(settings.demo_max_daily_loss_pct)/100),max_positions=settings.demo_max_open_positions)
                decision=DecisionEngine(policy).evaluate(profile,a,b,p,raw,market)
                calculated=(decision.get('calculations') or {}).get('position_size')
                try:
                    exact=decimal(calculated)==decimal(checked_volume)
                except (InvalidData,TypeError):
                    exact=False
                if not exact: raise InvalidData('MT5_ORDER_CHECK_VOLUME_MISMATCH')
        if decision.get('decision')!='APPROVED_SIMULATED_TRADE' or decision.get('blocking_factors'):
            raise InvalidData('RISK_ENGINE_DID_NOT_APPROVE_PROPOSAL')
        calculations=decision.get('calculations') or {}
        required=('position_size','estimated_max_loss','reward_risk')
        if any(calculations.get(key) is None for key in required):
            raise InvalidData('PROPOSAL_CALCULATION_INCOMPLETE')
        now=utcnow(); expires=now+timedelta(minutes=settings.demo_proposal_expiry_minutes); proposal_id=str(uuid4())
        safe_account={key:account_snapshot.get(key) for key in ('balance','equity','margin','margin_free','margin_level','currency','trade_mode','trade_allowed') if key in account_snapshot}
        proposal={
            'proposal_type':'DEMO_TRADE_PROPOSAL','signal_id':signal['signal_id'],'alert_id':signal.get('alert_id') or signal['signal_id'],
            'symbol':signal['mt5_symbol'],'canonical_symbol':signal['symbol'],'direction':signal['direction'],
            'entry_reference_price':decision.get('entry'),'stop_loss':decision.get('stop_loss'),'take_profit':decision.get('take_profit'),
            'exact_volume':calculations['position_size'],'monetary_risk':calculations['estimated_max_loss'],'risk_reward':calculations['reward_risk'],
            'account_snapshot':safe_account,'risk_vetoes':list(decision.get('blocking_factors') or []),
            'warnings':['DEMO ORDER NOT SENT — EXECUTION IS NOT IMPLEMENTED.','Approval records intent only and cannot submit an MT5 order.'],
            'risk_validation':decision.get('validation_results',[]),'calculations':calculations,'evidence':evidence,
            'status':'PENDING_LOCAL_REVIEW','order_sent':False,'execution_implemented':False,
            'created_at':now.isoformat(),'expires_at':expires.isoformat(),
        }
        proposal=redact(proposal,(settings.webhook_secret,settings.demo_expected_account_login,settings.demo_expected_broker_server))
        with self.store.transaction() as db:
            self._expire_demo_proposals(db)
            if db.execute("SELECT 1 FROM demo_trade_proposals WHERE status IN ('PENDING_LOCAL_REVIEW','APPROVED_FOR_FUTURE_DEMO_EXECUTION')").fetchone():
                raise InvalidData('ONE_ACTIVE_DEMO_PROPOSAL_LIMIT')
            today=now.date().isoformat()
            approved=db.execute("SELECT COUNT(*) FROM demo_trade_proposal_history WHERE action='APPROVED' AND substr(created_at,1,10)=?",(today,)).fetchone()[0]
            if approved >= settings.demo_max_trades_per_day:
                raise InvalidData('DEMO_MAX_TRADES_PER_DAY_REACHED')
            db.execute('INSERT INTO demo_trade_proposals VALUES(?,?,?,?,?,?,?,?,?)',
                       (proposal_id,raw_webhook_id,signal['signal_id'],proposal['alert_id'],'PENDING_LOCAL_REVIEW',proposal['created_at'],proposal['created_at'],proposal['expires_at'],dumps(proposal)))
            self._proposal_history(db,proposal_id,'CREATED',None,'PENDING_LOCAL_REVIEW','All proposal-only checks passed',{'signal_id':signal['signal_id'],'evidence':evidence})
        proposal['id']=proposal_id
        return proposal

    def review_demo_proposal(self,proposal_id,action,reason):
        mapping={'approve':'APPROVED_FOR_FUTURE_DEMO_EXECUTION','reject':'REJECTED','cancel':'CANCELLED','expire':'EXPIRED'}
        if action not in mapping or not isinstance(proposal_id,str) or not proposal_id:
            raise InvalidData('Invalid proposal review action')
        if not isinstance(reason,str) or len(reason.strip())<3 or len(reason)>500:
            raise InvalidData('A short local review reason is required')
        settings=self.settings
        reason=redact(reason,(settings.webhook_secret,settings.demo_expected_account_login,settings.demo_expected_broker_server)).strip()
        if action=='approve' and (not settings.demo_trade_proposals_enabled or settings.demo_trade_proposal_kill_switch or settings.mode!='SIMULATION'):
            raise InvalidData('DEMO_PROPOSAL_APPROVAL_DISABLED')
        with self.store.transaction() as db:
            self._expire_demo_proposals(db)
            row=db.execute('SELECT * FROM demo_trade_proposals WHERE id=?',(proposal_id,)).fetchone()
            if not row: raise InvalidData('Proposal not found')
            old=row['status']; allowed=('PENDING_LOCAL_REVIEW',) if action in ('approve','reject','expire') else ('PENDING_LOCAL_REVIEW','APPROVED_FOR_FUTURE_DEMO_EXECUTION')
            if old not in allowed: raise InvalidData('Proposal is no longer eligible for this action')
            if action=='approve':
                approved=db.execute("SELECT COUNT(*) FROM demo_trade_proposal_history WHERE action='APPROVED' AND substr(created_at,1,10)=?",(utcnow().date().isoformat(),)).fetchone()[0]
                if approved >= settings.demo_max_trades_per_day: raise InvalidData('DEMO_MAX_TRADES_PER_DAY_REACHED')
            new=mapping[action]; value=self._proposal_row(row)
            value.update(status=new,review_reason=reason,reviewed_at=stamp(),order_sent=False,execution_implemented=False)
            db.execute('UPDATE demo_trade_proposals SET status=?,updated_at=?,payload=? WHERE id=?',(new,value['reviewed_at'],dumps(value),proposal_id))
            self._proposal_history(db,proposal_id,action.upper() if action!='expire' else 'EXPIRED',old,new,reason,{'order_sent':False})
            return value

    def demo_proposals(self,db=None):
        if db is None:
            with self.store.transaction() as connection: return self.demo_proposals(connection)
        self._expire_demo_proposals(db)
        proposals=[self._proposal_row(row) for row in db.execute('SELECT * FROM demo_trade_proposals ORDER BY created_at DESC LIMIT 100')]
        history=[dict(row) for row in db.execute('SELECT * FROM demo_trade_proposal_history ORDER BY created_at DESC LIMIT 300')]
        return proposals,history

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
            result['order_sent']=False
            result['evidence_source']='SYNTHETIC_SIMULATION' if market.get('synthetic') is True else 'UNVERIFIED'
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
            secrets=(self.settings.webhook_secret,self.settings.demo_expected_account_login,self.settings.demo_expected_broker_server) if self.settings else ()
            def safe_rows(query,fields=('payload',)):
                rows=[]
                for source in db.execute(query):
                    row=dict(source)
                    for field in fields:
                        if isinstance(row.get(field),str):
                            try: row[field]=dumps(redact(loads(row[field]),secrets))
                            except (ValueError,TypeError): row[field]=redact(row[field],secrets)
                    rows.append(row)
                return rows
            brokers=docs('broker_profiles')
            for b in brokers: b['weighted_score']=BrokerResearchService.score(b)
            operating_mode=self.settings.mode if self.settings else 'SIMULATION'
            proposals,proposal_history=self.demo_proposals(db)
            return dict(mode=operating_mode,legacy_mode=MODE,live_execution_enabled=False,policy=asdict(self.engine.policy),accounts=accounts,brokers=brokers,
                providers=docs('providers'),provider_metrics=docs('provider_metrics'),provider_daily_performance=docs('provider_daily_performance'),strategies=docs('strategies'),withdrawals=docs('withdrawal_tests'),backtests=docs('backtests'),
                open_positions=[loads(r['payload']) for r in db.execute("SELECT payload FROM positions WHERE status='OPEN'")],
                decisions=[loads(r['payload']) for r in db.execute('SELECT payload FROM decisions ORDER BY rowid DESC LIMIT 100')],
                signals=safe_rows('SELECT * FROM signals ORDER BY rowid DESC LIMIT 100',('raw_payload','normalized_payload')),
                outcomes=[loads(r['payload']) for r in db.execute('SELECT payload FROM signal_outcomes ORDER BY rowid DESC LIMIT 100')],
                raw_webhooks=safe_rows('SELECT * FROM raw_webhooks ORDER BY received_at DESC LIMIT 100'),
                normalized_signals=safe_rows('SELECT * FROM normalized_signals ORDER BY created_at DESC LIMIT 100'),
                symbol_mappings=[dict(r) for r in db.execute('SELECT * FROM symbol_mappings ORDER BY canonical_symbol,mt5_symbol')],
                risk_checks=safe_rows('SELECT * FROM risk_checks ORDER BY created_at DESC LIMIT 100'),
                demo_trade_proposals=proposals,demo_trade_proposal_history=proposal_history,
                audit=safe_rows('SELECT * FROM audit_logs ORDER BY id DESC LIMIT 100'),audit_integrity=Store.verify_audit(db))
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
