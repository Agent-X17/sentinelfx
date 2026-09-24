"""Runtime configuration with fail-closed execution defaults."""
from dataclasses import dataclass, asdict
from pathlib import Path
import os

from .domain import InvalidData, PROFILES

SYSTEM_MODES = {"DISCONNECTED", "SIMULATION", "PAPER_TRADING", "LIVE_DISABLED", "LIVE_GATED"}
MT5_DIAGNOSTIC_MODES = {"disabled", "mock", "real"}


def _bool(value: str, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise InvalidData("Invalid boolean configuration value")


@dataclass(frozen=True)
class Settings:
    mode: str = "SIMULATION"
    database_path: str = "data/engine.sqlite3"
    webhook_secret: str = ""
    webhook_allowed_hosts: tuple = ()
    webhook_max_age_seconds: int = 300
    mt5_terminal_path: str = ""
    mt5_enabled: bool = False
    mt5_diagnostic_mode: str = "disabled"
    live_execution_enabled: bool = False
    explicit_order_confirmation_required: bool = True
    default_account_profile: str = "ACCOUNT_LIVE"
    demo_trade_proposals_enabled: bool = False
    demo_trade_proposal_kill_switch: bool = True
    demo_proposal_expiry_minutes: int = 10
    demo_max_risk_per_trade_pct: str = "0.25"
    demo_max_daily_loss_pct: str = "1.0"
    demo_max_open_positions: int = 1
    demo_max_trades_per_day: int = 2
    demo_expected_account_login: str = ""
    demo_expected_broker_server: str = ""

    @classmethod
    def from_env(cls, root: Path, database_override: str = None):
        mode = os.environ.get("SYSTEM_MODE", "SIMULATION").strip().upper()
        if mode not in SYSTEM_MODES:
            raise InvalidData("Unknown SYSTEM_MODE")
        legacy_mt5_enabled = _bool(os.environ.get("MT5_ENABLED"), False)
        diagnostic_mode = os.environ.get("MT5_DIAGNOSTIC_MODE", "real" if legacy_mt5_enabled else "disabled").strip().lower()
        settings = cls(
            mode=mode,
            database_path=database_override or os.environ.get("DATABASE_PATH", str(root / "data" / "engine.sqlite3")),
            webhook_secret=os.environ.get("TRADINGVIEW_WEBHOOK_SECRET", ""),
            webhook_allowed_hosts=tuple(item.strip().lower() for item in os.environ.get("WEBHOOK_ALLOWED_HOSTS", "").split(",") if item.strip()),
            webhook_max_age_seconds=int(os.environ.get("WEBHOOK_MAX_AGE_SECONDS", "300")),
            mt5_terminal_path=os.environ.get("MT5_TERMINAL_PATH", ""),
            mt5_enabled=diagnostic_mode != "disabled",
            mt5_diagnostic_mode=diagnostic_mode,
            live_execution_enabled=_bool(os.environ.get("LIVE_EXECUTION_ENABLED"), False),
            explicit_order_confirmation_required=_bool(os.environ.get("EXPLICIT_ORDER_CONFIRMATION_REQUIRED"), True),
            default_account_profile=os.environ.get("DEFAULT_ACCOUNT_PROFILE", "ACCOUNT_LIVE"),
            demo_trade_proposals_enabled=_bool(os.environ.get("DEMO_TRADE_PROPOSALS_ENABLED"), False),
            demo_trade_proposal_kill_switch=_bool(os.environ.get("DEMO_TRADE_PROPOSAL_KILL_SWITCH"), True),
            demo_proposal_expiry_minutes=int(os.environ.get("DEMO_PROPOSAL_EXPIRY_MINUTES", "10")),
            demo_max_risk_per_trade_pct=os.environ.get("DEMO_MAX_RISK_PER_TRADE_PCT", "0.25"),
            demo_max_daily_loss_pct=os.environ.get("DEMO_MAX_DAILY_LOSS_PCT", "1.0"),
            demo_max_open_positions=int(os.environ.get("DEMO_MAX_OPEN_POSITIONS", "1")),
            demo_max_trades_per_day=int(os.environ.get("DEMO_MAX_TRADES_PER_DAY", "2")),
            demo_expected_account_login=os.environ.get("DEMO_EXPECTED_ACCOUNT_LOGIN", "").strip(),
            demo_expected_broker_server=os.environ.get("DEMO_EXPECTED_BROKER_SERVER", "").strip(),
        )
        settings.validate()
        return settings

    def validate(self):
        if self.default_account_profile not in PROFILES:
            raise InvalidData('Unknown DEFAULT_ACCOUNT_PROFILE')
        if self.mode not in SYSTEM_MODES:
            raise InvalidData('Unknown SYSTEM_MODE')
        if self.mt5_diagnostic_mode not in MT5_DIAGNOSTIC_MODES:
            raise InvalidData('MT5_DIAGNOSTIC_MODE must be disabled, mock, or real')
        if self.webhook_allowed_hosts and not self.webhook_secret:
            raise InvalidData('External webhook hosts require a secret')
        if self.webhook_allowed_hosts and len(self.webhook_secret.strip()) < 32:
            raise InvalidData('External webhook hosts require a secret of at least 32 characters')
        if self.webhook_max_age_seconds < 1 or self.webhook_max_age_seconds > 3600:
            raise InvalidData("WEBHOOK_MAX_AGE_SECONDS must be between 1 and 3600")
        if self.live_execution_enabled:
            raise InvalidData("Live execution is not implemented in this release")
        if self.mode == "LIVE_GATED":
            raise InvalidData("LIVE_GATED is reserved for a separately reviewed future release")
        if self.mode in {"PAPER_TRADING", "LIVE_DISABLED"} and not self.mt5_enabled:
            raise InvalidData("The selected mode requires MT5_ENABLED=true")
        from .domain import decimal
        if not 1 <= self.demo_proposal_expiry_minutes <= 60:
            raise InvalidData("DEMO_PROPOSAL_EXPIRY_MINUTES must be between 1 and 60")
        if not 0 < decimal(self.demo_max_risk_per_trade_pct) <= decimal("0.25"):
            raise InvalidData("DEMO_MAX_RISK_PER_TRADE_PCT cannot exceed 0.25")
        if not 0 < decimal(self.demo_max_daily_loss_pct) <= decimal("1.0"):
            raise InvalidData("DEMO_MAX_DAILY_LOSS_PCT cannot exceed 1.0")
        if self.demo_max_open_positions != 1 or not 1 <= self.demo_max_trades_per_day <= 2:
            raise InvalidData("Demo proposal position/trade limits cannot be loosened")
        return self

    def public(self):
        data = asdict(self)
        data["webhook_secret_configured"] = bool(data.pop("webhook_secret"))
        data["mt5_terminal_path_configured"] = bool(data.pop("mt5_terminal_path"))
        data["demo_expected_account_login_configured"] = bool(data.pop("demo_expected_account_login"))
        data["demo_expected_broker_server_configured"] = bool(data.pop("demo_expected_broker_server"))
        return data
