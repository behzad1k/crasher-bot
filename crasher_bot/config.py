"""Configuration loading and validation."""

import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


DEFAULT_CONFIG_PATH = None  # Resolved lazily


def get_default_config_path() -> str:
    """Resolve config path, checking multiple locations for bundled apps."""
    config_name = "bot_config.json"

    if getattr(sys, "frozen", False):
        user_config = Path.home() / ".crasher_bot" / config_name
        if user_config.exists():
            return str(user_config)

        bundled = Path(getattr(sys, "_MEIPASS", ".")) / config_name
        if bundled.exists():
            user_config.parent.mkdir(parents=True, exist_ok=True)
            import shutil

            shutil.copy2(bundled, user_config)
            return str(user_config)

        return str(user_config)
    else:
        return str(Path(".") / config_name)


DEFAULT_CONFIG_PATH = ""


@dataclass
class PrimaryStrategyConfig:
    name: str
    base_bet: float
    auto_cashout: float
    trigger_threshold: float
    trigger_count: int
    max_consecutive_losses: int = 20
    bet_multiplier: float = 2.0
    enabled: bool = True


@dataclass
class CustomStrategyConfig:
    base_bet: float = 1000
    auto_cashout: float = 2.0
    max_consecutive_losses: int = 10
    max_losses_in_window: int = 7
    loss_check_window: int = 10
    bet_multiplier: float = 2.0
    stop_profit_count: int = 0
    cooldown_after_win: int = 0
    enabled: bool = False
    # Activation triggers
    activate_on_strong_hotstreak: bool = True
    activate_on_weak_hotstreak: bool = False
    activate_on_rule_of_17: bool = True
    activate_on_pre_streak_pattern: bool = True
    activate_on_high_deviation_10: bool = False
    activate_on_high_deviation_15: bool = False
    # Signal confirmation
    signal_confirm_threshold: float = 2.0
    signal_confirm_count: int = 3
    signal_confirm_window: int = 5
    signal_monitor_rounds: int = 20


@dataclass
class BotConfig:
    username: str = ""
    password: str = ""
    game_url: str = ""
    max_loss: float = 100_000_000
    import_recent_on_new_session: bool = True
    strategies: List[PrimaryStrategyConfig] = field(default_factory=list)
    custom_strategy: Optional[CustomStrategyConfig] = None

    @classmethod
    def from_file(cls, path: str = DEFAULT_CONFIG_PATH) -> "BotConfig":
        with open(path, "r") as f:
            raw = json.load(f)
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "BotConfig":
        strategies = [PrimaryStrategyConfig(**s) for s in raw.get("strategies", [])]

        custom = None
        if "custom_strategy" in raw and raw["custom_strategy"].get("enabled"):
            cst = raw["custom_strategy"].copy()
            cst.pop("enabled", None)
            custom = CustomStrategyConfig(**cst, enabled=True)

        return cls(
            username=raw.get("username", ""),
            password=raw.get("password", ""),
            game_url=raw.get("game_url", ""),
            max_loss=float(raw.get("max_loss", 100_000_000)),
            import_recent_on_new_session=raw.get("import_recent_on_new_session", True),
            strategies=strategies,
            custom_strategy=custom,
        )

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "username": self.username,
            "password": self.password,
            "game_url": self.game_url,
            "max_loss": self.max_loss,
            "import_recent_on_new_session": self.import_recent_on_new_session,
            "strategies": [],
        }
        for s in self.strategies:
            result["strategies"].append(
                {
                    "name": s.name,
                    "base_bet": s.base_bet,
                    "auto_cashout": s.auto_cashout,
                    "trigger_threshold": s.trigger_threshold,
                    "trigger_count": s.trigger_count,
                    "max_consecutive_losses": s.max_consecutive_losses,
                    "bet_multiplier": s.bet_multiplier,
                    "enabled": s.enabled,
                }
            )
        if self.custom_strategy:
            cs = self.custom_strategy
            result["custom_strategy"] = {
                "base_bet": cs.base_bet,
                "auto_cashout": cs.auto_cashout,
                "max_consecutive_losses": cs.max_consecutive_losses,
                "max_losses_in_window": cs.max_losses_in_window,
                "loss_check_window": cs.loss_check_window,
                "bet_multiplier": cs.bet_multiplier,
                "stop_profit_count": cs.stop_profit_count,
                "cooldown_after_win": cs.cooldown_after_win,
                "enabled": cs.enabled,
                "activate_on_strong_hotstreak": cs.activate_on_strong_hotstreak,
                "activate_on_weak_hotstreak": cs.activate_on_weak_hotstreak,
                "activate_on_rule_of_17": cs.activate_on_rule_of_17,
                "activate_on_pre_streak_pattern": cs.activate_on_pre_streak_pattern,
                "activate_on_high_deviation_10": cs.activate_on_high_deviation_10,
                "activate_on_high_deviation_15": cs.activate_on_high_deviation_15,
                "signal_confirm_threshold": cs.signal_confirm_threshold,
                "signal_confirm_count": cs.signal_confirm_count,
                "signal_confirm_window": cs.signal_confirm_window,
                "signal_monitor_rounds": cs.signal_monitor_rounds,
            }
        return result

    def save(self, path: str = DEFAULT_CONFIG_PATH):
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    def validate(self) -> List[str]:
        errors = []
        if not self.username:
            errors.append("Username is required")
        if not self.password:
            errors.append("Password is required")
        if not self.game_url:
            errors.append("Game URL is required")
        if not self.strategies:
            errors.append("At least one primary strategy is required")
        for s in self.strategies:
            if s.base_bet <= 0:
                errors.append(f"[{s.name}] Base bet must be positive")
            if s.auto_cashout <= 1.0:
                errors.append(f"[{s.name}] Auto cashout must be > 1.0")
        return errors
