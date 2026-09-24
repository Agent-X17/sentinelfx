"""MetaTrader 5 boundary. Real order submission is deliberately unavailable."""
from dataclasses import dataclass, asdict
from typing import Any, Optional


@dataclass(frozen=True)
class MT5Result:
    ok: bool
    code: str
    message: str
    data: Optional[dict] = None

    def to_dict(self):
        return asdict(self)


class MT5Service:
    """Small adapter around the optional MetaTrader5 package.

    Import and connection failures are data, not crashes. The adapter exposes
    read/check operations only; order_send always refuses in this release.
    """

    def __init__(self, enabled=False, terminal_path="", module=None):
        self.enabled = enabled
        self.terminal_path = terminal_path
        self._module = module
        self._connected = False

    def _load(self):
        if self._module is not None:
            return self._module
        try:
            import MetaTrader5 as mt5  # type: ignore
            self._module = mt5
            return mt5
        except Exception:
            return None

    def initialize(self):
        self._connected = False
        if not self.enabled:
            return MT5Result(False, "MT5_DISABLED", "MT5 integration is disabled")
        module = self._load()
        if module is None:
            return MT5Result(False, "MT5_PACKAGE_UNAVAILABLE", "MetaTrader5 package is unavailable on this host")
        kwargs = {"path": self.terminal_path} if self.terminal_path else {}
        try:
            if not module.initialize(**kwargs):
                return MT5Result(False, "MT5_INITIALIZE_FAILED", str(module.last_error()))
            self._connected = True
            return MT5Result(True, "MT5_CONNECTED", "MT5 initialized")
        except Exception as exc:
            return MT5Result(False, "MT5_INITIALIZE_ERROR", str(exc))

    def shutdown(self):
        if self._connected and self._module is not None:
            try:
                self._module.shutdown()
            except Exception:
                pass
        self._connected = False

    def status(self):
        if not self.enabled:
            return MT5Result(False, "MT5_DISABLED", "MT5 integration is disabled")
        if not self._connected:
            return MT5Result(False, "MT5_NOT_CONNECTED", "MT5 is not connected")
        try:
            terminal = self._record(self._module.terminal_info())
            if not terminal or terminal.get('connected') is not True:
                return MT5Result(False, 'MT5_DISCONNECTED', 'Terminal connection is unavailable')
        except Exception:
            return MT5Result(False, 'MT5_DISCONNECTED', 'Cannot verify terminal connection')
        return MT5Result(True, "MT5_CONNECTED", "MT5 is connected")

    @staticmethod
    def _record(value: Any):
        if value is None:
            return None
        if hasattr(value, "_asdict"):
            return dict(value._asdict())
        if isinstance(value, dict):
            return dict(value)
        return {name: getattr(value, name) for name in dir(value) if not name.startswith("_") and not callable(getattr(value, name))}

    def _call(self, name, *args, **kwargs):
        state = self.status()
        if not state.ok:
            return state
        try:
            value = getattr(self._module, name)(*args, **kwargs)
            if value is None:
                return MT5Result(False, "MT5_LOOKUP_FAILED", f"{name} returned no data")
            if hasattr(value, '_asdict'):
                data = self._record(value)
            elif isinstance(value, (bool, int, float, str)):
                data = {'value': value}
                if value is False:
                    return MT5Result(False, 'MT5_OPERATION_FAILED', name + ' returned false', data)
            elif isinstance(value, (list, tuple)):
                data = {"items": [self._record(item) for item in value]}
            else:
                data = self._record(value)
            return MT5Result(True, "MT5_OK", name + " succeeded", data)
        except Exception as exc:
            return MT5Result(False, "MT5_CALL_ERROR", f"{name}: {exc}")

    def account_info(self): return self._call("account_info")
    def terminal_info(self): return self._call("terminal_info")
    def symbol_info(self, symbol): return self._call("symbol_info", symbol)
    def symbol_select(self, symbol, enable=True): return self._call("symbol_select", symbol, enable)
    def symbol_info_tick(self, symbol): return self._call("symbol_info_tick", symbol)
    def positions_get(self, **kwargs): return self._call("positions_get", **kwargs)
    def orders_get(self, **kwargs): return self._call("orders_get", **kwargs)
    def history_deals_get(self, date_from, date_to, **kwargs): return self._call("history_deals_get", date_from, date_to, **kwargs)
    def history_orders_get(self, date_from, date_to, **kwargs): return self._call("history_orders_get", date_from, date_to, **kwargs)
    def copy_rates_range(self, symbol, timeframe, date_from, date_to): return self._call("copy_rates_range", symbol, timeframe, date_from, date_to)
    def order_calc_margin(self, action, symbol, volume, price): return self._call("order_calc_margin", action, symbol, volume, price)
    def order_calc_profit(self, action, symbol, volume, price_open, price_close): return self._call("order_calc_profit", action, symbol, volume, price_open, price_close)
    def order_check(self, request):
        result = self._call('order_check', request)
        if result.ok and (not isinstance(result.data, dict) or type(result.data.get('retcode')) is not int or result.data['retcode'] != 0):
            return MT5Result(False, 'MT5_ORDER_CHECK_FAILED', 'Broker rejected or did not confirm order check', result.data)
        return result

    def order_send(self, request):
        return MT5Result(False, "LIVE_EXECUTION_NOT_IMPLEMENTED", "Order submission is disabled in this release")


class MockMT5Service(MT5Service):
    """Deterministic broker-truth adapter for tests and paper workflows."""

    def __init__(self, account=None, symbols=None, ticks=None, order_check_ok=True):
        super().__init__(enabled=True)
        self._connected = True
        self.account = account or {}
        self.symbols = symbols or {}
        self.ticks = ticks or {}
        self.order_check_ok = order_check_ok
        self.order_check_requests = []

    def account_info(self):
        return MT5Result(True, "MT5_OK", "mock account", dict(self.account))

    def status(self):
        return MT5Result(True, 'MT5_SYNTHETIC', 'Synthetic test fixture; no terminal connected')

    def symbol_info(self, symbol):
        value = self.symbols.get(symbol)
        return MT5Result(bool(value), "MT5_OK" if value else "MT5_SYMBOL_MISSING", "mock symbol lookup", dict(value) if value else None)

    def symbol_info_tick(self, symbol):
        value = self.ticks.get(symbol)
        return MT5Result(bool(value), "MT5_OK" if value else "MT5_TICK_MISSING", "mock tick lookup", dict(value) if value else None)

    def positions_get(self, **kwargs):
        return MT5Result(True, "MT5_OK", "mock positions", {"items": []})

    def orders_get(self, **kwargs):
        return MT5Result(True, "MT5_OK", "mock orders", {"items": []})

    def order_check(self, request):
        self.order_check_requests.append(dict(request))
        return MT5Result(self.order_check_ok, "MT5_OK" if self.order_check_ok else "MT5_ORDER_CHECK_FAILED", "mock order check", {"request": request})
