"""Runtime configuration with fail-closed execution defaults."""
from dataclasses import dataclass, asdict
from pathlib import Path
import os

from .domain import InvalidData, PROFILES

SYSTEM_MODES = {"DISCONNECTED", "SIMULATION", "PAPER_TRADING", "LIVE_DISABLED", "LIVE_GATED"}


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
    live_execution_enabled: bool = False
    explicit_order_confirmation_required: bool = True
    default_account_profile: str = "ACCOUNT_LIVE"

    @classmethod
    def from_env(cls, root: Path, database_override: str = None):
        mode = os.environ.get("SYSTEM_MODE", "SIMULATION").strip().upper()
        if mode not in SYSTEM_MODES:
            raise InvalidData("Unknown SYSTEM_MODE")
        settings = cls(
            mode=mode,
            database_path=database_override or os.environ.get("DATABASE_PATH", str(root / "data" / "engine.sqlite3")),
            webhook_secret=os.environ.get("TRADINGVIEW_WEBHOOK_SECRET", ""),
            webhook_allowed_hosts=tuple(item.strip().lower() for item in os.environ.get("WEBHOOK_ALLOWED_HOSTS", "").split(",") if item.strip()),
            webhook_max_age_seconds=int(os.environ.get("WEBHOOK_MAX_AGE_SECONDS", "300")),
            mt5_terminal_path=os.environ.get("MT5_TERMINAL_PATH", ""),
            mt5_enabled=_bool(os.environ.get("MT5_ENABLED"), False),
            live_execution_enabled=_bool(os.environ.get("LIVE_EXECUTION_ENABLED"), False),
            explicit_order_confirmation_required=_bool(os.environ.get("EXPLICIT_ORDER_CONFIRMATION_REQUIRED"), True),
            default_account_profile=os.environ.get("DEFAULT_ACCOUNT_PROFILE", "ACCOUNT_LIVE"),
        )
        settings.validate()
        return settings

    def validate(self):
        if self.default_account_profile not in PROFILES:
            raise InvalidData('Unknown DEFAULT_ACCOUNT_PROFILE')
        if self.mode not in SYSTEM_MODES:
            raise InvalidData('Unknown SYSTEM_MODE')
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
        return self

    def public(self):
        data = asdict(self)
        data["webhook_secret_configured"] = bool(data.pop("webhook_secret"))
        return data
