"""Conservative diagnostic checks. Passing these never authorizes execution."""
from .domain import decimal, InvalidData, utcnow
from decimal import Decimal, InvalidOperation


def validate_diagnostics(signal, symbol, tick, account, now=None):
    now = now or utcnow()
    failures = []
    if not all(isinstance(x, dict) for x in (symbol, tick, account)):
        return ['MT5_DIAGNOSTIC_DATA_MISSING']
    try:
        timestamp = decimal(Decimal(str(tick['time_msc'])) / 1000) if 'time_msc' in tick else decimal(tick.get('time'))
        age = decimal(now.timestamp()) - timestamp
        if not 0 <= age <= 30:
            failures.append('MT5_TICK_STALE_OR_FUTURE')
        bid, ask = decimal(tick.get('bid')), decimal(tick.get('ask'))
        if not 0 < bid <= ask:
            failures.append('MT5_QUOTE_INVALID')
    except (InvalidData, InvalidOperation):
        failures.append('MT5_TICK_INVALID')
    if symbol.get('name') != signal['mt5_symbol']:
        failures.append('MT5_SYMBOL_IDENTITY_UNVERIFIED')
    try:
        for key in ('trade_contract_size', 'volume_min', 'volume_max', 'volume_step', 'point'):
            if decimal(symbol.get(key)) <= 0:
                raise InvalidData(key)
        if decimal(symbol['volume_min']) > decimal(symbol['volume_max']) or decimal(symbol['volume_step']) > decimal(symbol['volume_max']):
            raise InvalidData('volume range')
        if 'digits' in symbol and (type(symbol['digits']) is not int or not 0 <= symbol['digits'] <= 10 or decimal(symbol['point']) != Decimal(10) ** -symbol['digits']):
            raise InvalidData('precision mismatch')
    except InvalidData:
        failures.append('MT5_SYMBOL_PROPERTIES_INVALID')
    if symbol.get('trade_mode') != 4:
        failures.append('MT5_SYMBOL_TRADE_MODE_RESTRICTED_OR_UNKNOWN')
    if account.get('currency') != 'USD':
        failures.append('MT5_ACCOUNT_CURRENCY_UNSUPPORTED_OR_UNKNOWN')
    if type(account.get('login')) is not int or account['login'] <= 0 or not isinstance(account.get('server'),str) or not account['server'].strip():
        failures.append('MT5_ACCOUNT_IDENTITY_UNVERIFIED')
    if account.get('trade_allowed') is not True:
        failures.append('MT5_ACCOUNT_RESTRICTED_OR_UNKNOWN')
    try:
        if decimal(account.get('equity')) <= 0 or decimal(account.get('margin_free')) < 0:
            raise InvalidData('account values')
    except InvalidData:
        failures.append('MT5_ACCOUNT_VALUES_INVALID')
    # A fresh read does not establish reconciled account state or snapshot age.
    failures.append('MT5_ACCOUNT_RECONCILIATION_UNVERIFIED')
    return failures
