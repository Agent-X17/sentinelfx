"""TradingView webhook validation and canonical signal normalization."""
import hashlib
import hmac
from dataclasses import dataclass, asdict
from datetime import timedelta
from uuid import uuid4

from .domain import InvalidData, date, decimal, stamp, utcnow


@dataclass(frozen=True)
class WebhookResult:
    accepted: bool
    status: str
    reason: str
    alert_id: str
    idempotency_key: str
    signal: dict = None

    def to_dict(self):
        return asdict(self)


class WebhookAuthenticator:
    def __init__(self, secret=""):
        self.secret = secret

    def verify(self, supplied):
        if not self.secret:
            return True
        return isinstance(supplied, str) and hmac.compare_digest(supplied.encode('utf-8'), self.secret.encode('utf-8'))


class SymbolMapper:
    def __init__(self, mappings=None):
        self.mappings = mappings or {}

    @staticmethod
    def canonical(value):
        if not isinstance(value, str) or not value.strip():
            raise InvalidData("Missing TradingView symbol")
        raw = value.strip().upper()
        if ":" in raw:
            raw = raw.split(":", 1)[1]
        return raw.replace("/", "").replace("-", "").replace("_", "")

    def map(self, value):
        canonical = self.canonical(value)
        candidates = self.mappings.get(canonical, [])
        if isinstance(candidates, str):
            candidates = [candidates]
        if len(candidates) != 1:
            return {"ok": False, "canonical_symbol": canonical, "mt5_symbol": None, "reason": "SYMBOL_UNMAPPED" if not candidates else "SYMBOL_AMBIGUOUS"}
        return {"ok": True, "canonical_symbol": canonical, "mt5_symbol": candidates[0], "reason": None}


class TradingViewWebhookService:
    REQUIRED = ("symbol", "side", "timeframe", "strategy", "timestamp")

    def __init__(self, mapper, max_age_seconds=300):
        self.mapper = mapper
        self.max_age_seconds = max_age_seconds

    @staticmethod
    def idempotency_key(payload, header_key=None):
        if isinstance(payload.get("alert_id"), str) and payload["alert_id"].strip():
            return "alert:" + payload["alert_id"].strip()[:194]
        # Delivery headers are transport metadata, never a replay escape hatch.
        # Without an alert ID, the stable signal identity determines uniqueness.
        material = "|".join(str(payload.get(key, "")) for key in ("alert_id", "symbol", "side", "strategy", "timeframe", "timestamp"))
        return hashlib.sha256(material.encode()).hexdigest()

    def validate(self, payload, header_key=None, now=None):
        now = now or utcnow()
        if not isinstance(payload, dict):
            alert_id = str(uuid4())
            key = self.idempotency_key({}, header_key)
            return WebhookResult(False, "malformed", "JSON object required", alert_id, key)
        alert_id = str(payload.get("alert_id") or uuid4())
        key = self.idempotency_key(payload, header_key)
        if payload.get('alert_id') is not None and (not isinstance(payload['alert_id'],str) or not 1 <= len(payload['alert_id'].strip()) <= 194):
            return WebhookResult(False, 'malformed', 'alert_id must be 1 to 194 characters', alert_id, key)
        missing = [name for name in self.REQUIRED if not isinstance(payload.get(name), str) or not payload[name].strip()]
        if missing:
            return WebhookResult(False, "malformed", "Missing fields: " + ", ".join(missing), alert_id, key)
        try:
            event_time = date(payload["timestamp"])
            age = (now - event_time).total_seconds()
            if age < 0 or age > self.max_age_seconds:
                return WebhookResult(False, "stale", "Alert timestamp is stale or in the future", alert_id, key)
        except InvalidData as exc:
            return WebhookResult(False, "malformed", str(exc), alert_id, key)
        side = payload["side"].strip().upper()
        if side not in {"BUY", "SELL"}:
            return WebhookResult(False, "malformed", "side must be BUY or SELL", alert_id, key)
        mapping = self.mapper.map(payload["symbol"])
        if not mapping["ok"]:
            return WebhookResult(False, "unmapped", mapping["reason"], alert_id, key)
        for field in ("entry", "stop_loss", "take_profit"):
            if payload.get(field) is not None:
                try:
                    decimal(payload[field])
                except InvalidData:
                    return WebhookResult(False, "malformed", field + " must be numeric", alert_id, key)
        if payload.get("stop_loss") is None:
            return WebhookResult(False, "malformed", "stop_loss is required", alert_id, key)
        expires = payload.get("expires_at") or (event_time + timedelta(seconds=self.max_age_seconds)).isoformat()
        try:
            expiry = date(expires)
            if expiry <= now or expiry <= event_time:
                return WebhookResult(False, 'stale', 'Alert has expired', alert_id, key)
        except InvalidData:
            return WebhookResult(False, 'malformed', 'Invalid expires_at', alert_id, key)
        signal = {
            "signal_id": alert_id,
            "symbol": mapping["canonical_symbol"],
            "mt5_symbol": mapping["mt5_symbol"],
            "direction": side,
            "entry": payload.get("entry"),
            "stop_loss": payload.get("stop_loss"),
            "take_profit": payload.get("take_profit"),
            "timestamp": event_time.isoformat(),
            "expires_at": expires,
            "provider": "tradingview",
            "strategy": payload["strategy"].strip(),
            "source": payload.get("source", "TradingView"),
            "timeframe": payload["timeframe"].strip(),
            "metadata": payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
        }
        return WebhookResult(True, "waiting_for_validation", "Webhook validated and mapped", alert_id, key, signal)
