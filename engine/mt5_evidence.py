"""Strict validation for the versioned, read-only MT5 proposal evidence protocol."""
from decimal import Decimal

from .domain import InvalidData, date, decimal, utcnow


PROTOCOL = "sentinelfx.mt5.readonly-evidence.v1"
MAX_AGE_SECONDS = 30


def _result(snapshot, name):
    value = snapshot.get(name)
    if not isinstance(value, dict) or value.get("ok") is not True or not isinstance(value.get("data"), dict):
        raise InvalidData("MT5_EVIDENCE_" + name.upper() + "_MISSING")
    return value["data"]


def _number(record, key, positive=False, nonnegative=False):
    try:
        value = decimal(record.get(key))
    except (InvalidData, TypeError) as exc:
        raise InvalidData("MT5_EVIDENCE_INVALID_" + key.upper()) from exc
    if positive and value <= 0:
        raise InvalidData("MT5_EVIDENCE_INVALID_" + key.upper())
    if nonnegative and value < 0:
        raise InvalidData("MT5_EVIDENCE_INVALID_" + key.upper())
    return value


def _fresh(value, code, now):
    try:
        age = (now - date(value)).total_seconds()
    except InvalidData as exc:
        raise InvalidData(code) from exc
    if not 0 <= age <= MAX_AGE_SECONDS:
        raise InvalidData(code)
    return age


def validate_snapshot(snapshot, signal, expected_login, expected_server, now=None):
    """Return a redaction-safe summary or raise a stable fail-closed code."""
    now = now or utcnow()
    if not isinstance(snapshot, dict) or snapshot.get("protocol") != PROTOCOL or snapshot.get("operation") != "snapshot":
        raise InvalidData("MT5_EVIDENCE_PROTOCOL_INVALID")
    snapshot_age = _fresh(snapshot.get("captured_at"), "MT5_EVIDENCE_SNAPSHOT_STALE", now)
    status = snapshot.get("status")
    if not isinstance(status, dict) or status.get("ok") is not True:
        raise InvalidData((status or {}).get("code", "MT5_EVIDENCE_STATUS_MISSING"))

    terminal = _result(snapshot, "terminal_info")
    if terminal.get("connected") is not True:
        raise InvalidData("MT5_TERMINAL_DISCONNECTED")
    # A proposal collector does not need terminal AutoTrading. Fail closed if it is on.
    if terminal.get("trade_allowed") is not False:
        raise InvalidData("MT5_TERMINAL_AUTOTRADING_NOT_PROVEN_OFF")

    account = _result(snapshot, "account_info")
    if not expected_login or not expected_server:
        raise InvalidData("MT5_EXPECTED_DEMO_ACCOUNT_NOT_CONFIGURED")
    login = account.get("login")
    server = account.get("server")
    if type(login) is not int or login <= 0 or not isinstance(server, str) or not server:
        raise InvalidData("MT5_ACCOUNT_IDENTITY_UNVERIFIED")
    if str(login) != str(expected_login) or server != expected_server:
        raise InvalidData("MT5_ACCOUNT_MISMATCH")
    if account.get("trade_mode") != 0:
        raise InvalidData("MT5_ACCOUNT_NOT_CONFIRMED_DEMO")
    if account.get("currency") != "USD":
        raise InvalidData("MT5_ACCOUNT_CURRENCY_UNSUPPORTED_OR_UNKNOWN")
    if account.get("trade_allowed") is not True:
        raise InvalidData("MT5_ACCOUNT_RESTRICTED_OR_UNKNOWN")
    for key in ("balance", "equity"):
        _number(account, key, positive=True)
    for key in ("margin", "margin_free", "margin_level"):
        _number(account, key, nonnegative=True)
    _number(account, "leverage", positive=True)

    symbol = _result(snapshot, "symbol_info")
    if symbol.get("name") != signal.get("mt5_symbol"):
        raise InvalidData("MT5_SYMBOL_IDENTITY_UNVERIFIED")
    if symbol.get("visible") is not True or symbol.get("trade_mode") != 4:
        raise InvalidData("MT5_SYMBOL_TRADE_MODE_RESTRICTED_OR_UNKNOWN")
    for key in ("trade_contract_size", "volume_min", "volume_max", "volume_step", "point"):
        _number(symbol, key, positive=True)
    for key in ("trade_stops_level", "trade_freeze_level", "filling_mode"):
        if type(symbol.get(key)) is not int or symbol[key] < 0:
            raise InvalidData("MT5_SYMBOL_PROPERTIES_INVALID")
    digits = symbol.get("digits")
    if type(digits) is not int or not 0 <= digits <= 10 or _number(symbol, "point") != Decimal(10) ** -digits:
        raise InvalidData("MT5_SYMBOL_PROPERTIES_INVALID")
    minimum, maximum, step = (_number(symbol, key) for key in ("volume_min", "volume_max", "volume_step"))
    if minimum > maximum or step > maximum:
        raise InvalidData("MT5_SYMBOL_PROPERTIES_INVALID")

    tick = _result(snapshot, "symbol_info_tick")
    tick_stamp = tick.get("time_msc")
    try:
        tick_time = float(tick_stamp) / 1000 if type(tick_stamp) in (int, float) else float(tick.get("time"))
        tick_fresh=0 <= now.timestamp() - tick_time <= MAX_AGE_SECONDS
    except (TypeError,ValueError,OverflowError):
        tick_fresh=False
    if not tick_fresh:
        raise InvalidData("MT5_TICK_STALE_OR_FUTURE")
    bid, ask = _number(tick, "bid", positive=True), _number(tick, "ask", positive=True)
    if ask < bid:
        raise InvalidData("MT5_QUOTE_INVALID")

    for name in ("positions_get", "orders_get", "recent_deals", "recent_orders"):
        items = _result(snapshot, name).get("items")
        if not isinstance(items, list):
            raise InvalidData("MT5_EXPOSURE_UNKNOWN")
        if items:
            raise InvalidData("MT5_EXTERNAL_EXPOSURE")

    return {
        "protocol": PROTOCOL,
        "snapshot_age_seconds": round(snapshot_age, 3),
        "identity_match": True,
        "demo_account_proven": True,
        "terminal_connected": True,
        "terminal_autotrading": False,
        "account_currency": "USD",
        "positions_count": 0,
        "pending_orders_count": 0,
        "recent_external_exposure_count": 0,
        "symbol_mapping_exact": True,
        "symbol": signal["mt5_symbol"],
        "tick_fresh": True,
        "trade_session_available": True,
        "volume_grid": {"minimum": str(minimum), "maximum": str(maximum), "step": str(step)},
        "price_format": {"digits": digits, "point": str(symbol["point"])},
        "stops": {"stop_level": symbol["trade_stops_level"], "freeze_level": symbol["trade_freeze_level"]},
        "filling_mode": symbol["filling_mode"],
    }


def validate_order_check(envelope, expected_volume):
    if not isinstance(envelope, dict) or envelope.get("protocol") != PROTOCOL or envelope.get("operation") != "order_check":
        raise InvalidData("MT5_ORDER_CHECK_PROTOCOL_INVALID")
    status = envelope.get("status") or {}
    data = status.get("data") or {}
    if status.get("ok") is not True or type(data.get("retcode")) is not int or data["retcode"] != 0:
        raise InvalidData("MT5_ORDER_CHECK_FAILED")
    if decimal(envelope.get("checked_volume")) != decimal(expected_volume):
        raise InvalidData("MT5_ORDER_CHECK_VOLUME_MISMATCH")
    return {"passed": True, "retcode": 0, "exact_checked_volume": str(expected_volume), "non_submitting": True}


def validate_order_request(snapshot, request):
    """Validate the exact preflight request against the already verified symbol grid."""
    symbol=_result(snapshot,"symbol_info")
    volume=decimal(request.get("volume")); minimum=decimal(symbol.get("volume_min")); maximum=decimal(symbol.get("volume_max")); step=decimal(symbol.get("volume_step"))
    if volume < minimum or volume > maximum or (volume-minimum) % step != 0:
        raise InvalidData("MT5_ORDER_CHECK_VOLUME_GRID_INVALID")
    point=decimal(symbol.get("point")); digits=symbol.get("digits")
    values=[decimal(request.get(key)) for key in ("price","sl")]
    if request.get("tp") is not None: values.append(decimal(request.get("tp")))
    quantum=Decimal(10) ** -digits
    if any(value.quantize(quantum) != value for value in values):
        raise InvalidData("MT5_ORDER_CHECK_PRICE_PRECISION_INVALID")
    price,stop=values[0],values[1]; target=decimal(request.get("tp")) if request.get("tp") is not None else None
    minimum_distance=Decimal(symbol["trade_stops_level"])*point
    if request.get("type")=="BUY": distances=(price-stop,target-price if target is not None else minimum_distance)
    else: distances=(stop-price,price-target if target is not None else minimum_distance)
    if any(distance < minimum_distance for distance in distances):
        raise InvalidData("MT5_ORDER_CHECK_STOP_LEVEL_INVALID")
    return True
