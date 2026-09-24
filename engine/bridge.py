"""TradingView -> validation -> MT5 broker truth -> risk engine orchestration."""
from decimal import Decimal
from dataclasses import replace
from uuid import uuid4

from .domain import InvalidData, decimal, stamp
from .storage import Store, dumps, loads
from .mt5 import MockMT5Service
from .redaction import redact
from .diagnostics import validate_diagnostics
from .isolated_mt5 import SnapshotMT5
from .mt5_worker import collect


class TradingBridge:
    def __init__(self, application, webhook_service, authenticator, mt5, settings):
        self.application = application
        self.webhook_service = webhook_service
        self.authenticator = authenticator
        self.mt5 = mt5
        self.settings = settings

    def _persist_raw(self, db, raw_id, key, payload, status, reason):
        db.execute(
            "INSERT INTO raw_webhooks(id,idempotency_key,received_at,status,reason,payload) VALUES(?,?,?,?,?,?)",
            (raw_id, key, stamp(), status, reason, dumps(payload)),
        )

    @staticmethod
    def block_source(reason):
        if reason=='DUPLICATE_WEBHOOK': return 'DUPLICATE_DETECTION'
        if reason=='REQUIRED_EXTERNAL_EVIDENCE_UNVERIFIED': return 'MISSING_EXTERNAL_EVIDENCE'
        if str(reason).startswith(('MT5_','BROKER_')): return 'MT5_DIAGNOSTICS'
        if reason in ('SYSTEM_DISCONNECTED',): return 'SYSTEM_STATE'
        if reason in ('WEBHOOK_AUTH_FAILED','SYMBOL_UNMAPPED','SYMBOL_AMBIGUOUS') or 'timestamp' in str(reason).lower() or 'stop_loss' in str(reason): return 'WEBHOOK_VALIDATION'
        return 'RISK_ENGINE'

    def ingest(self, payload, secret=None, idempotency_key=None):
        # Real reads never authorize or reserve risk. Collect them without a DB
        # write lock, then validate and deduplicate again in the atomic commit.
        if type(self.mt5) is not MockMT5Service and not isinstance(self.mt5, SnapshotMT5):
            if self.authenticator.verify(secret):
                check=self.webhook_service.validate(payload,idempotency_key)
                if check.accepted and self.settings.mode != 'DISCONNECTED':
                    data = self.mt5.snapshot(check.signal['mt5_symbol']) if hasattr(self.mt5,'snapshot') else collect(self.mt5,check.signal['mt5_symbol'])
                    bridge=TradingBridge(self.application,self.webhook_service,self.authenticator,SnapshotMT5(data),self.settings)
                    return bridge.ingest(payload,secret,idempotency_key)
        # Serialize intake, reservation, journal and audit as one local unit.
        # Nested application transactions share this connection on this thread.
        with self.application.store.transaction():
            result = self._ingest(payload, secret, idempotency_key)
            result['order_sent'] = False
            return result

    def _ingest(self, payload, secret=None, idempotency_key=None):
        original = payload
        body_secret=original.get('secret') if isinstance(original,dict) else None
        sensitive=(secret,self.authenticator.secret,body_secret)
        payload = redact(payload, sensitive)
        if isinstance(payload, dict):
            payload = dict(payload)
            payload.pop('secret', None)
        raw_id = str(uuid4())
        if not self.authenticator.verify(secret):
            with self.application.store.transaction() as db:
                key = 'unauthenticated:' + raw_id
                self._persist_raw(db, raw_id, key, payload, "rejected", "WEBHOOK_AUTH_FAILED")
                Store.audit(db, "WEBHOOK_REJECTED", {"id": raw_id, "reason": "WEBHOOK_AUTH_FAILED"})
            return {"accepted": False, "status": "rejected", "decision": "NO_TRADE", "reason": "WEBHOOK_AUTH_FAILED", "block_source":"WEBHOOK_VALIDATION", "raw_webhook_id": raw_id}

        check = self.webhook_service.validate(original, idempotency_key)
        legacy_key = self.webhook_service.legacy_key(original) if check.accepted else ''
        check = replace(check, alert_id=check.idempotency_key,
                        signal=redact(check.signal,sensitive))
        if not check.accepted:
            check = replace(check,idempotency_key='rejected:' + raw_id)
        with self.application.store.transaction() as db:
            duplicate = db.execute("SELECT id FROM raw_webhooks WHERE idempotency_key IN (?,?)", (check.idempotency_key,legacy_key)).fetchone()
            if duplicate:
                Store.audit(db, "WEBHOOK_DUPLICATE", {"existing_id": duplicate["id"], "idempotency_key": check.idempotency_key})
                return {"accepted": False, "status": "duplicate", "decision": "NO_TRADE", "reason": "DUPLICATE_WEBHOOK", "block_source":"DUPLICATE_DETECTION", "raw_webhook_id": duplicate["id"]}
            self._persist_raw(db, raw_id, check.idempotency_key, payload, check.status, check.reason)
            Store.audit(db, "WEBHOOK_RECEIVED" if check.accepted else "WEBHOOK_REJECTED", {"id": raw_id, **check.to_dict()})
            if not check.accepted:
                return {"accepted": False, "status": check.status, "decision": "NO_TRADE", "reason": check.reason, "block_source":"WEBHOOK_VALIDATION", "raw_webhook_id": raw_id}

        if self.settings.mode == "DISCONNECTED":
            return self._blocked(raw_id, check.signal, "SYSTEM_DISCONNECTED")

        mt5_state = self.mt5.status()
        if not mt5_state.ok:
            return self._blocked(raw_id, check.signal, mt5_state.code)

        symbol_result = self.mt5.symbol_info(check.signal["mt5_symbol"])
        tick_result = self.mt5.symbol_info_tick(check.signal["mt5_symbol"])
        account_result = self.mt5.account_info()
        positions_result = self.mt5.positions_get()
        orders_result = self.mt5.orders_get()
        if not all(item.ok for item in (symbol_result, tick_result, account_result, positions_result, orders_result)):
            reason = next(item.code for item in (symbol_result, tick_result, account_result, positions_result, orders_result) if not item.ok)
            return self._blocked(raw_id, check.signal, reason, {"symbol": symbol_result.to_dict(), "tick": tick_result.to_dict(), "account": account_result.to_dict()})

        for exposure in (positions_result, orders_result):
            if not isinstance(exposure.data, dict) or not isinstance(exposure.data.get('items'), list):
                return self._blocked(raw_id, check.signal, 'MT5_EXPOSURE_UNKNOWN')
            if exposure.data['items']:
                return self._blocked(raw_id, check.signal, 'MT5_EXTERNAL_EXPOSURE')

        # Only the explicit in-process test adapter may use synthetic evidence.
        # No webhook field or environment flag can enable this fixture path.
        if type(self.mt5) is not MockMT5Service or self.settings.mode != 'SIMULATION':
            return self._blocked(raw_id, check.signal, 'REQUIRED_EXTERNAL_EVIDENCE_UNVERIFIED', {
                'account': account_result.to_dict(), 'symbol': symbol_result.to_dict(),
                'tick': tick_result.to_dict(),
                'diagnostic_failures': validate_diagnostics(check.signal, symbol_result.data, tick_result.data, account_result.data),
                'account_reconciled': False,
                'missing_evidence': ['broker_entity', 'provider_history', 'news', 'macro',
                                     'commission', 'swap', 'slippage', 'account_reconciliation'],
            })

        try:
            broker, market = self._broker_inputs(check.signal, symbol_result.data, tick_result.data, account_result.data)
        except InvalidData as exc:
            return self._blocked(raw_id, check.signal, "BROKER_VALIDATION_FAILED", {"message": str(exc)})
        profile = self.settings.default_account_profile
        with self.application.store.transaction() as db:
            account = Store.get(db, "accounts", profile)
            if account is None:
                raise InvalidData("Configured account profile is missing")
            # Mock terminal balances are diagnostic fixtures, never ledger input.
            Store.put(db, "broker_profiles", "mt5-runtime", broker)
            provider = Store.get(db, "providers", "tradingview")
            if provider is None:
                provider = {"id":"tradingview","name":"TradingView webhook","status":"ACTIVE","verified_status":True,"live_status":"SYNTHETIC" if self.settings.mode=="SIMULATION" else "LIVE","risk_flags":[],"trade_count":1000,"age_days":365,"current_drawdown":"0","consecutive_losses":0,"data_quality":"0.9"}
                Store.put(db, "providers", "tradingview", provider)
            db.execute("INSERT INTO normalized_signals(id,raw_webhook_id,status,payload,created_at) VALUES(?,?,?,?,?)", (check.signal["signal_id"], raw_id, "waiting_for_validation", dumps(check.signal), stamp()))
            db.execute("INSERT INTO broker_snapshots(id,raw_webhook_id,payload,created_at) VALUES(?,?,?,?)", (str(uuid4()), raw_id, dumps({"symbol":symbol_result.data,"tick":tick_result.data}), stamp()))
            db.execute("INSERT INTO account_snapshots(id,raw_webhook_id,payload,created_at) VALUES(?,?,?,?)", (str(uuid4()), raw_id, dumps(account_result.data), stamp()))
            Store.audit(db, "MT5_VALIDATION", {"raw_webhook_id":raw_id,"symbol":symbol_result.to_dict(),"tick":tick_result.to_dict(),"account":account_result.to_dict()})

        check.signal["provider"] = "tradingview"
        evaluation_payload = {"account_profile":profile,"broker_id":"mt5-runtime","signal":check.signal,"market":market,"system_mode":self.settings.mode}
        preview = self.application.preview(evaluation_payload)
        preview_volume = preview.get("calculations", {}).get("position_size")
        if preview.get("decision") != "APPROVED_SIMULATED_TRADE" or preview_volume is None:
            decision = self.application.evaluate(evaluation_payload, trusted_adapter=True, checked_volume=None)
            order_check = {"ok": False, "code": "NOT_RUN", "message": "Risk preview rejected the candidate", "data": None}
        else:
            order_check_result = self.mt5.order_check({
                "symbol": check.signal["mt5_symbol"],
                "type": check.signal["direction"],
                "volume": preview_volume,
                "price": market["price"],
                "sl": check.signal["stop_loss"],
                "tp": check.signal.get("take_profit"),
            })
            if not order_check_result.ok:
                return self._blocked(raw_id, check.signal, order_check_result.code, {"order_check": order_check_result.to_dict(), "checked_volume": preview_volume})
            order_check = order_check_result.to_dict()
            decision = self.application.evaluate(evaluation_payload, trusted_adapter=True, checked_volume=preview_volume)
        final = decision["decision"]
        if final == "APPROVED_SIMULATED_TRADE":
            final = check.signal["direction"]
        execution_decision = {
            "execution_mode": "SIMULATION",
            "evidence_source": "SYNTHETIC_TEST_FIXTURE",
            "order_sent": False,
            "final_decision": final,
            "direction": check.signal["direction"],
            "main_reason": decision["reason"],
            "block_source": None if decision["decision"]=='APPROVED_SIMULATED_TRADE' else 'RISK_ENGINE',
            "main_risk": decision["blocking_factors"][0] if decision["blocking_factors"] else "Mode remains simulation/paper only",
            "mt5_validation_result": order_check,
            "risk_check_result": decision["validation_results"],
            "position_size_result": decision.get("calculations", {}).get("position_size"),
            "beginner_explanation": "A TradingView alert becomes a trade candidate only after MT5 and risk checks agree.",
        }
        with self.application.store.transaction() as db:
            db.execute('UPDATE raw_webhooks SET status=?,reason=? WHERE id=?', ('processed', decision['reason'], raw_id))
            db.execute("UPDATE normalized_signals SET status=? WHERE id=?", (decision["decision"], check.signal["signal_id"]))
            db.execute("INSERT INTO risk_checks(id,signal_id,payload,created_at) VALUES(?,?,?,?)", (str(uuid4()), check.signal["signal_id"], dumps(decision), stamp()))
            journal = {
                "signal_id": check.signal["signal_id"],
                "setup_name": check.signal["strategy"],
                "strategy_type": check.signal["metadata"].get("strategy_type", "unknown"),
                "source_layer": "TradingView",
                "reason": decision["reason"],
                "risk_amount": decision.get("calculations", {}).get("risk_budget"),
                "position_size": decision.get("calculations", {}).get("position_size"),
                "stop_loss": check.signal["stop_loss"],
                "take_profit": check.signal.get("take_profit"),
                "decision": final,
                "beginner_lesson": execution_decision["beginner_explanation"],
                "professional_insight": "MT5 broker truth and risk sizing must agree before a candidate can progress.",
                "warning": "Simulation and paper results do not guarantee real fills or profit.",
            }
            db.execute("INSERT INTO journal_entries(id,decision_id,payload,created_at) VALUES(?,?,?,?)", (str(uuid4()), decision["decision_id"], dumps(journal), stamp()))
            Store.audit(db, "JOURNAL_ENTRY", journal)
        return {"accepted": True, "status": "processed", "raw_webhook_id": raw_id, "mt5": mt5_state.to_dict(), "decision": execution_decision, "decision_detail": decision}

    def _blocked(self, raw_id, signal, reason, mt5=None):
        result = {"accepted": False, "status": "blocked", "decision": "NO_TRADE", "reason": reason, "block_source":self.block_source(reason), "raw_webhook_id": raw_id, "signal": signal, "mt5": mt5, "order_sent": False}
        result['evidence_source']='DIAGNOSTIC_ONLY_UNVERIFIED'
        result=redact(result,(self.authenticator.secret,))
        with self.application.store.transaction() as db:
            if not db.execute('SELECT 1 FROM normalized_signals WHERE id=?', (signal['signal_id'],)).fetchone():
                db.execute('INSERT INTO normalized_signals(id,raw_webhook_id,status,payload,created_at) VALUES(?,?,?,?,?)', (signal['signal_id'],raw_id,'NO_TRADE',dumps(signal),stamp()))
            db.execute('INSERT INTO risk_checks(id,signal_id,payload,created_at) VALUES(?,?,?,?)', (str(uuid4()),signal['signal_id'],dumps(result),stamp()))
            db.execute('UPDATE raw_webhooks SET status=?,reason=? WHERE id=?', ('blocked', reason, raw_id))
            db.execute('UPDATE normalized_signals SET status=? WHERE raw_webhook_id=?', ('NO_TRADE', raw_id))
            Store.audit(db, "EXECUTION_BLOCKED", result)
        return result

    @staticmethod
    def _broker_inputs(signal, symbol, tick, account):
        required = ("trade_contract_size", "volume_min", "volume_max", "volume_step", "point")
        if any(symbol.get(name) is None for name in required):
            raise InvalidData("MT5 symbol specification is incomplete")
        bid, ask = decimal(tick.get("bid")), decimal(tick.get("ask"))
        point = decimal(symbol["point"])
        if ask <= bid or point <= 0:
            raise InvalidData("MT5 tick is invalid")
        if symbol.get("trade_mode") in (0, "DISABLED"):
            raise InvalidData("MT5 symbol is not tradable")
        if account.get("trade_allowed") is False:
            raise InvalidData("MT5 account trade mode is restricted")
        direction = signal["direction"]
        price = ask if direction == "BUY" else bid
        signal["entry"] = signal.get("entry") or str(price)
        digits = int(symbol.get("digits", 5))
        pip_size = Decimal("0.01") if digits in (2, 3) else Decimal("0.0001")
        spread_pips = (ask - bid) / pip_size
        broker = {
            "id":"mt5-runtime","brand":"MT5 connected broker","entity":"RUNTIME_UNVERIFIED","country":"UNVERIFIED","regulator":"UNVERIFIED","license_number":"UNVERIFIED","legal_url":"RUNTIME","account_type":"MT5 runtime","spread_model":"Runtime bid/ask","commission_model":"Configured allowance","withdrawal_notes":"Not execution-approved","evidence_reference":"MT5 runtime snapshot","synthetic":True,
            "contract_size":str(symbol["trade_contract_size"]),"minimum_lot":str(symbol["volume_min"]),"maximum_lot":str(symbol["volume_max"]),"lot_increment":str(symbol["volume_step"]),"leverage":str(account.get("leverage", 1)),"supported_symbols":[signal["symbol"]],
        }
        market = {
            "symbol":signal["symbol"],"price":str(price),"timestamp":stamp(),"context_timestamp":stamp(),"spread_pips":str(spread_pips),"slippage_pips":"0.5","commission_per_lot":"0","swap_per_lot":"0","session_open":True,"liquid":True,"edge_validated":True,"conflicting_signals":False,"news_risk":"0","correlation":"0","volatility":"0.1","execution_quality":"0.9","technical_score":"0.8","regime_score":"0.8","macro_score":"0.5","synthetic":True,
        }
        return broker, market
