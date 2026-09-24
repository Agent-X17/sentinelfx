"""Deterministic risk calculations. All money is USD, including cent accounts.
Decimal strings cross the API boundary. No provider-supplied lot size is trusted.
"""
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_FLOOR, InvalidOperation
from typing import Protocol

MODE = 'RESEARCH_AND_SIMULATION_ONLY'
PROFILES = {'ACCOUNT_A': '50', 'ACCOUNT_B': '100', 'ACCOUNT_C': '150', 'ACCOUNT_LIVE': '300'}
FORBIDDEN = {'martingale', 'grid', 'grid_recovery', 'no_stop_loss', 'aggressive_averaging_down',
             'lot_size_escalation', 'extreme_leverage_dependence', 'hidden_floating_losses'}
SYMBOLS = {'EURUSD': ('0.0001', 'USD'), 'GBPUSD': ('0.0001', 'USD'), 'USDJPY': ('0.01', 'JPY')}

class InvalidData(ValueError):
    pass

def decimal(value):
    if isinstance(value, bool) or value is None:
        raise InvalidData('Missing or invalid numeric value')
    try:
        n = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise InvalidData('Invalid numeric value')
    if not n.is_finite() or abs(n) > Decimal('1000000000000'):
        raise InvalidData('Numeric value outside safe bounds')
    return n

def number(obj, key, minimum=None, maximum=None):
    n = decimal(obj.get(key))
    if minimum is not None and n < decimal(minimum):
        raise InvalidData(key + ' below minimum')
    if maximum is not None and n > decimal(maximum):
        raise InvalidData(key + ' above maximum')
    return n

def utcnow():
    return datetime.now(timezone.utc)

def stamp():
    return utcnow().isoformat()

def date(value):
    if not isinstance(value, str):
        raise InvalidData('Timestamp missing')
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if result.tzinfo is None:
            raise ValueError()
        return result.astimezone(timezone.utc)
    except ValueError:
        raise InvalidData('Timezone-aware ISO timestamp required')

def fresh(value, now, seconds):
    age = (now - date(value)).total_seconds()
    return 0 <= age <= seconds

def encode(value):
    if isinstance(value, Decimal):
        return format(value, 'f')
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(str(type(value)))

@dataclass(frozen=True)
class Policy:
    risk_fraction: str = '0.005'
    daily_fraction: str = '0.02'
    weekly_fraction: str = '0.04'
    max_positions: int = 1
    max_spread_pips: str = '2'
    max_cost_fraction: str = '0.20'
    min_reward_risk: str = '1.5'
    max_margin_fraction: str = '0.20'
    min_margin_level: str = '500'
    max_news_risk: str = '0.3'
    max_correlation: str = '0.5'
    max_volatility: str = '0.7'
    min_execution_quality: str = '0.7'
    min_score: str = '0.65'
    max_provider_drawdown: str = '0.15'
    min_provider_trades: int = 100
    min_provider_days: int = 180
    max_signal_age_seconds: int = 300
    max_market_age_seconds: int = 30
    max_account_age_seconds: int = 300
    max_active_providers: int = 3
    provider_daily_fraction: str = '0.01'
    provider_weekly_fraction: str = '0.02'
    provider_allocation_fraction: str = '0.20'
    provider_circuit_breaker_losses: int = 5
    score_weights: dict = field(default_factory=lambda: {'provider':'.20','technical':'.20','regime':'.15','macro':'.10','execution':'.20','reward_risk':'.15','news_penalty':'.20','correlation_penalty':'.15','volatility_penalty':'.10','spread_penalty':'.10'})

    def validate(self):
        for key in ('risk_fraction','daily_fraction','weekly_fraction','max_cost_fraction','max_margin_fraction',
                    'max_news_risk','max_correlation','max_volatility','min_execution_quality','min_score',
                    'max_provider_drawdown','provider_daily_fraction','provider_weekly_fraction','provider_allocation_fraction'):
            number(asdict(self), key, '0.000001', '1')
        # Configuration can tighten the conservative baseline, never silently loosen it.
        for key, cap in [('risk_fraction','.005'),('daily_fraction','.02'),('weekly_fraction','.04')]:
            if decimal(getattr(self, key)) > decimal(cap):
                raise InvalidData('Risk limit increase requires a separately reviewed implementation')
        for key in ('max_positions','min_provider_trades','min_provider_days','max_signal_age_seconds',
                    'max_market_age_seconds','max_account_age_seconds','max_active_providers','provider_circuit_breaker_losses'):
            value = getattr(self,key)
            if type(value) is not int or value < 1:
                raise InvalidData('Invalid policy integer: ' + key)
        if self.max_positions > 1:
            raise InvalidData('This release enforces one open position per account')
        number(asdict(self),'max_spread_pips','0.000001','10')
        number(asdict(self),'min_reward_risk','1','100')
        number(asdict(self),'min_margin_level','500','100000')
        expected={'provider','technical','regime','macro','execution','reward_risk','news_penalty','correlation_penalty','volatility_penalty','spread_penalty'}
        if not isinstance(self.score_weights,dict) or set(self.score_weights)!=expected: raise InvalidData('Invalid score weight keys')
        for key in expected:number(self.score_weights,key,0,1)
        if sum((decimal(self.score_weights[k]) for k in ('provider','technical','regime','macro','execution','reward_risk')),Decimal(0))!=1: raise InvalidData('Evidence weights must sum to 1')
        return self

class SignalProvider(Protocol):
    def signals(self): ...

class ManualSignalProvider:
    def __init__(self, payloads): self.payloads = payloads
    def signals(self): return list(self.payloads)

class DisabledExternalProvider:
    def signals(self):
        raise InvalidData('External adapter is unconfigured; no fabricated market data')

class SignalNormalizer:
    @staticmethod
    def normalize(raw):
        if not isinstance(raw, dict): raise InvalidData('Signal must be an object')
        s = dict(raw)
        if isinstance(s.get('symbol'), str): s['symbol'] = s['symbol'].replace('/','').upper()
        if isinstance(s.get('direction'), str): s['direction'] = s['direction'].upper()
        return s

class SignalValidationService:
    @staticmethod
    def validate(s, now, policy):
        reasons = []
        for k in ('signal_id','provider','strategy','source','timeframe','timestamp','expires_at'):
            if not isinstance(s.get(k), str) or not s[k].strip(): reasons.append('SIGNAL_MISSING_' + k.upper())
        if s.get('symbol') not in SYMBOLS: reasons.append('UNSUPPORTED_SYMBOL')
        if s.get('direction') not in ('BUY','SELL'): reasons.append('INVALID_DIRECTION')
        try:
            if not fresh(s.get('timestamp'),now,policy.max_signal_age_seconds): reasons.append('STALE_OR_FUTURE_SIGNAL')
            if date(s.get('expires_at')) <= now or date(s['expires_at']) <= date(s['timestamp']): reasons.append('EXPIRED_SIGNAL')
        except InvalidData: reasons.append('INVALID_SIGNAL_TIMESTAMP')
        try:
            entry, stop, target = [number(s,k,'0.00000001') for k in ('entry','stop_loss','take_profit')]
            if not (stop < entry < target if s.get('direction') == 'BUY' else target < entry < stop):
                reasons.append('INVALID_STOP_OR_TARGET')
            if stop == entry or abs(target-entry) / abs(entry-stop) < decimal(policy.min_reward_risk):
                reasons.append('INSUFFICIENT_REWARD_RISK')
        except (InvalidData, ZeroDivisionError): reasons.append('MISSING_OR_INVALID_STOP_PRICES')
        return reasons

class BrokerResearchService:
    WEIGHTS = {'small_account':25,'withdrawals':25,'entity':15,'minimum_size':10,'costs':10,'automation':5,'copy':5,'protections':5}
    @classmethod
    def score(cls,b):
        scores=b.get('evidence_scores',{})
        if b.get('verification_status') != 'VERIFIED' or any(k not in scores for k in cls.WEIGHTS): return None
        return sum(number(scores,k,0,1)*w for k,w in cls.WEIGHTS.items())
    @staticmethod
    def validate(b):
        reasons=[]
        # A synthetic profile is explicitly valid only for simulation fixtures.
        if b.get('synthetic') is not True:
            for k in ('brand','entity','country','regulator','license_number','legal_url','account_type','spread_model','commission_model','withdrawal_notes','evidence_reference'):
                if not isinstance(b.get(k),str) or not b[k].strip(): reasons.append('BROKER_MISSING_'+k.upper())
            if b.get('verification_status')!='VERIFIED': reasons.append('BROKER_UNVERIFIED')
            if b.get('tanzania_availability')!='YES': reasons.append('TANZANIA_ENTITY_UNCONFIRMED')
            if b.get('withdrawal_status') not in ('VERIFIED','TEST_APPROVED'): reasons.append('WITHDRAWAL_UNVERIFIED')
            try:
                if not fresh(b.get('last_checked_at'),utcnow(),86400*90): reasons.append('BROKER_EVIDENCE_STALE')
            except InvalidData: reasons.append('BROKER_EVIDENCE_MISSING')
        for k in ('contract_size','minimum_lot','lot_increment','maximum_lot','leverage'):
            try: number(b,k,'0.00000001')
            except InvalidData: reasons.append('BROKER_INVALID_'+k.upper())
        try:
            if decimal(b['minimum_lot']) > decimal(b['maximum_lot']): reasons.append('BROKER_INVALID_LOT_RANGE')
        except (KeyError,InvalidData): pass
        return reasons

class ProviderScoringService:
    @staticmethod
    def validate(p,policy):
        reasons=[]
        if p.get('status')!='ACTIVE' or p.get('verified_status') is not True: reasons.append('PROVIDER_UNAVAILABLE_OR_SUSPENDED')
        if p.get('live_status') not in ('LIVE','SYNTHETIC'): reasons.append('PROVIDER_HISTORY_NOT_LIVE')
        flags=p.get('risk_flags')
        if not isinstance(flags,list): reasons.append('PROVIDER_FLAGS_UNKNOWN')
        elif FORBIDDEN.intersection(flags): reasons.append('PROVIDER_FORBIDDEN_BEHAVIOR')
        try:
            if number(p,'trade_count',0) < policy.min_provider_trades: reasons.append('PROVIDER_SAMPLE_TOO_SMALL')
            if number(p,'age_days',0) < policy.min_provider_days: reasons.append('PROVIDER_HISTORY_TOO_SHORT')
            if number(p,'current_drawdown',0,1) >= decimal(policy.max_provider_drawdown): reasons.append('PROVIDER_DRAWDOWN_BREAKER')
            if number(p,'consecutive_losses',0) >= policy.provider_circuit_breaker_losses: reasons.append('PROVIDER_LOSS_BREAKER')
            if number(p,'data_quality',0,1) < decimal('.7'): reasons.append('PROVIDER_DATA_UNRELIABLE')
        except InvalidData: reasons.append('PROVIDER_METRICS_UNKNOWN')
        return reasons

class ExecutionValidator:
    @staticmethod
    def validate(m,s,now,policy):
        reasons=[]
        try:
            if not fresh(m.get('timestamp'),now,policy.max_market_age_seconds): reasons.append('MARKET_DATA_STALE')
            if not fresh(m.get('context_timestamp'),now,policy.max_signal_age_seconds): reasons.append('NEWS_MACRO_CONTEXT_STALE')
        except InvalidData: reasons.append('MARKET_CONTEXT_TIMESTAMP_MISSING')
        for key in ('news_risk','correlation','volatility','execution_quality','technical_score','regime_score','macro_score'):
            try: number(m,key,0,1)
            except InvalidData: reasons.append('UNKNOWN_'+key.upper())
        for key in ('spread_pips','slippage_pips','commission_per_lot','swap_per_lot'):
            try: number(m,key,0)
            except InvalidData: reasons.append('UNKNOWN_'+key.upper())
        if m.get('symbol')!=s.get('symbol'): reasons.append('QUOTE_SYMBOL_MISMATCH')
        if m.get('session_open') is not True or m.get('liquid') is not True: reasons.append('SESSION_OR_LIQUIDITY_UNSAFE')
        if m.get('edge_validated') is not True: reasons.append('NO_VALIDATED_EDGE')
        if m.get('conflicting_signals') is not False: reasons.append('CONFLICTING_OR_UNKNOWN_EVIDENCE')
        for key,limit,above in [('news_risk',policy.max_news_risk,True),('correlation',policy.max_correlation,True),
                               ('volatility',policy.max_volatility,True),('spread_pips',policy.max_spread_pips,True),
                               ('execution_quality',policy.min_execution_quality,False)]:
            try:
                n=decimal(m.get(key)); bad=n>decimal(limit) if above else n<decimal(limit)
                if bad: reasons.append(key.upper()+'_VETO')
            except InvalidData: pass
        try:
            pip=decimal(SYMBOLS[s['symbol']][0]); quote=number(m,'price','0.00000001')
            if abs(quote-decimal(s['entry']))/pip > decimal('2'): reasons.append('ENTRY_DEVIATION')
        except (KeyError,InvalidData): reasons.append('INVALID_MARKET_PRICE')
        return reasons

class MarginCalculator:
    @staticmethod
    def calculate(symbol,entry,contract,lots,leverage):
        # Supported USD account pairs only. USDJPY base is already USD.
        notional=contract*lots*(decimal('1') if symbol=='USDJPY' else entry)
        return notional/leverage

class PositionSizer:
    @staticmethod
    def calculate(s,b,m,budget,policy):
        symbol=s['symbol']; entry=decimal(s['entry']); pip=decimal(SYMBOLS[symbol][0])
        contract=decimal(b['contract_size'])
        conversion=decimal('1') if SYMBOLS[symbol][1]=='USD' else decimal('1')/min(entry,decimal(s['stop_loss']))
        pip_value=contract*pip*conversion
        stop_pips=abs(entry-decimal(s['stop_loss']))/pip
        spread=decimal(m['spread_pips'])*pip_value
        slippage=decimal(m['slippage_pips'])*pip_value
        commission=decimal(m['commission_per_lot']); swap=decimal(m['swap_per_lot'])
        cost=spread+slippage+commission+swap
        unit_loss=stop_pips*pip_value+cost
        raw=budget/unit_loss
        step=decimal(b['lot_increment']); minimum=decimal(b['minimum_lot'])
        # Broker grid is minimum + n * increment, never nearest rounding.
        lots=decimal('0') if raw<minimum else minimum+((min(raw,decimal(b['maximum_lot']))-minimum)/step).to_integral_value(rounding=ROUND_FLOOR)*step
        margin=MarginCalculator.calculate(symbol,entry,contract,lots,decimal(b['leverage']))
        return dict(position_size=lots,calculated_safe_position_size=raw,stop_loss_pips=stop_pips,
                    pip_value_per_lot=contract*pip*(decimal('1') if SYMBOLS[symbol][1]=='USD' else decimal('1')/entry),risk_pip_value_per_lot=pip_value,estimated_spread_cost=spread*lots,estimated_commission_cost=commission*lots,
                    estimated_slippage_cost=slippage*lots,estimated_swap_cost=swap*lots,estimated_total_transaction_cost=cost*lots,
                    estimated_max_loss=unit_loss*lots,minimum_lot_loss=unit_loss*minimum,margin_required=margin,
                    risk_budget=budget,reward_risk=abs(decimal(s['take_profit'])-entry)/abs(entry-decimal(s['stop_loss'])))

class RiskManager:
    @staticmethod
    def budget(a,profile,policy):
        equity=number(a,'equity','0.00000001'); baseline=min(equity,decimal(PROFILES[profile]))
        reserved=number(a,'reserved_risk',0)
        daily=baseline*decimal(policy.daily_fraction)-number(a,'daily_loss',0)-reserved
        weekly=baseline*decimal(policy.weekly_fraction)-number(a,'weekly_loss',0)-reserved
        provider_daily=baseline*decimal(policy.provider_daily_fraction)-number(a,'provider_daily_loss',0)-reserved
        provider_weekly=baseline*decimal(policy.provider_weekly_fraction)-number(a,'provider_weekly_loss',0)-reserved
        return min(baseline*decimal(policy.risk_fraction),daily,weekly,provider_daily,provider_weekly),daily,weekly
    @staticmethod
    def veto(a,sizing,b,policy,daily,weekly):
        reasons=[]
        if a.get('status')!='ACTIVE': reasons.append('ACCOUNT_SUSPENDED')
        if daily<=0: reasons.append('DAILY_LOSS_STOP')
        if weekly<=0: reasons.append('WEEKLY_LOSS_STOP')
        if number(a,'open_positions',0)>=policy.max_positions: reasons.append('MAX_OPEN_POSITIONS')
        if sizing['position_size']<decimal(b['minimum_lot']): reasons.append('BELOW_BROKER_MINIMUM')
        if sizing['estimated_max_loss']>sizing['risk_budget'] or sizing['risk_budget']<=0: reasons.append('RISK_BUDGET_EXCEEDED')
        if sizing['estimated_total_transaction_cost']>sizing['risk_budget']*decimal(policy.max_cost_fraction): reasons.append('TRANSACTION_COST_VETO')
        equity=decimal(a['equity']); used=number(a,'used_margin',0)+sizing['margin_required']
        if used>equity*decimal(policy.max_margin_fraction) or sizing['margin_required']>number(a,'free_margin',0): reasons.append('MARGIN_UNSAFE')
        if sizing['margin_required']>equity*decimal(policy.provider_allocation_fraction): reasons.append('PROVIDER_ALLOCATION_LIMIT')
        stressed_equity=equity-sizing['estimated_max_loss']-number(a,'reserved_risk',0)
        if used and stressed_equity/used*100<decimal(policy.min_margin_level): reasons.append('MARGIN_LEVEL_UNSAFE')
        sizing['margin_level_projection']=None if not used else stressed_equity/used*100
        sizing['risk_percent']=sizing['estimated_max_loss']/equity*100
        return reasons

class SafetyController:
    @staticmethod
    def live_order(*args,**kwargs):
        raise PermissionError('LIVE_EXECUTION_NOT_IMPLEMENTED')

class DecisionEngine:
    def __init__(self,policy=None): self.policy=(policy or Policy()).validate()
    def evaluate(self,profile,a,b,p,raw,m,now=None):
        now=now or utcnow(); checks=[]; reasons=[]; sizing={}; s={}
        def stage(name,items):
            checks.append({'stage':name,'passed':not items,'reasons':items}); reasons.extend(items)
        try:
            if profile not in PROFILES: raise InvalidData('Unknown account profile')
            for k in ('equity','daily_loss','weekly_loss','reserved_risk','open_positions','used_margin','free_margin','provider_daily_loss','provider_weekly_loss'): number(a,k,0)
            ar=[]
            if a.get('currency')!='USD' or a.get('state_certain') is not True: ar.append('ACCOUNT_STATE_UNCERTAIN')
            if not fresh(a.get('updated_at'),now,self.policy.max_account_age_seconds): ar.append('ACCOUNT_STATE_STALE')
            if a.get('status')!='ACTIVE': ar.append('ACCOUNT_SUSPENDED')
            stage('Account state',ar)
            stage('Broker constraints',BrokerResearchService.validate(b))
            s=SignalNormalizer.normalize(raw)
            stage('Signal validation',SignalValidationService.validate(s,now,self.policy))
            stage('Provider validation',ProviderScoringService.validate(p,self.policy))
            stage('Market, news, execution and evidence',ExecutionValidator.validate(m,s,now,self.policy))
            if not reasons:
                budget,daily,weekly=RiskManager.budget(a,profile,self.policy)
                if budget<=0:
                    stage('RiskManager final veto',(['DAILY_LOSS_STOP'] if daily<=0 else [])+(['WEEKLY_LOSS_STOP'] if weekly<=0 else [])+['RISK_BUDGET_EXHAUSTED'])
                else:
                    sizing=PositionSizer.calculate(s,b,m,budget,self.policy)
                    stage('RiskManager final veto',RiskManager.veto(a,sizing,b,self.policy,daily,weekly))
                    w={k:decimal(v) for k,v in self.policy.score_weights.items()}
                    score=(number(p,'data_quality',0,1)*w['provider']+number(m,'technical_score',0,1)*w['technical']+
                           number(m,'regime_score',0,1)*w['regime']+number(m,'macro_score',0,1)*w['macro']+
                           number(m,'execution_quality',0,1)*w['execution']+min(sizing['reward_risk']/3,decimal(1))*w['reward_risk']-
                           number(m,'news_risk',0,1)*w['news_penalty']-number(m,'correlation',0,1)*w['correlation_penalty']-
                           number(m,'volatility',0,1)*w['volatility_penalty']-number(m,'spread_pips',0)/10*w['spread_penalty'])
                    sizing['confidence_score']=max(decimal(0),score)
                    stage('Evidence score',[] if score>=decimal(self.policy.min_score) else ['INSUFFICIENT_EVIDENCE'])
        except (InvalidData,KeyError,TypeError,ArithmeticError) as exc:
            stage('Fail-closed input validation',['INVALID_OR_INCOMPLETE_DATA: '+str(exc)])
        decision='NO_TRADE' if reasons else 'APPROVED_SIMULATED_TRADE'
        if reasons==['INSUFFICIENT_EVIDENCE']: decision='WATCHLIST'
        return {'decision':decision,'system_mode':MODE,'live_execution_enabled':False,'account_profile':profile,
                'symbol':s.get('symbol'),'direction':s.get('direction'),'entry':s.get('entry'),'stop_loss':s.get('stop_loss'),
                'take_profit':s.get('take_profit'),'provider':p.get('name'),'strategy':s.get('strategy'),
                'reason':'; '.join(reasons) if reasons else 'All simulation checks passed; no live order can be submitted.',
                'blocking_factors':reasons,'validation_results':checks,'calculations':sizing,'timestamp':now.isoformat()}
