"""Configuration loading and validation."""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = "./bot_config.json"


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
class SecondaryStrategyConfig:
    base_bet: float = 1000
    auto_cashout: float = 2.0
    max_consecutive_losses: int = 20
    bet_multiplier: float = 2.0
    enabled: bool = False


@dataclass
class TertiaryStrategyConfig:
    base_bet: float = 1000
    auto_cashout: float = 2.0
    max_consecutive_losses: int = 10
    max_losses_in_window: int = 7
    loss_check_window: int = 10
    enabled: bool = False


@dataclass
class BotConfig:
    username: str = ""
    password: str = ""
    game_url: str = ""
    max_loss: float = 100_000_000
    import_recent_on_new_session: bool = True
    strategies: List[PrimaryStrategyConfig] = field(default_factory=list)
    secondary_strategy: Optional[SecondaryStrategyConfig] = None
    tertiary_strategy: Optional[TertiaryStrategyConfig] = None

    @classmethod
    def from_file(cls, path: str = DEFAULT_CONFIG_PATH) -> "BotConfig":
        with open(path, "r") as f:
            raw = json.load(f)
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "BotConfig":
        strategies = [
            PrimaryStrategyConfig(**s) for s in raw.get("strategies", [])
        ]

        secondary = None
        if "secondary_strategy" in raw and raw["secondary_strategy"].get("enabled"):
            sec = raw["secondary_strategy"].copy()
            sec.pop("enabled", None)
            secondary = SecondaryStrategyConfig(**sec, enabled=True)

        tertiary = None
        if "tertiary_strategy" in raw and raw["tertiary_strategy"].get("enabled"):
            ter = raw["tertiary_strategy"].copy()
            ter.pop("enabled", None)
            tertiary = TertiaryStrategyConfig(**ter, enabled=True)

        return cls(
            username=raw.get("username", ""),
            password=raw.get("password", ""),
            game_url=raw.get("game_url", ""),
            max_loss=float(raw.get("max_loss", 100_000_000)),
            import_recent_on_new_session=raw.get("import_recent_on_new_session", True),
            strategies=strategies,
            secondary_strategy=secondary,
            tertiary_strategy=tertiary,
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
            result["strategies"].append({
                "name": s.name,
                "base_bet": s.base_bet,
                "auto_cashout": s.auto_cashout,
                "trigger_threshold": s.trigger_threshold,
                "trigger_count": s.trigger_count,
                "max_consecutive_losses": s.max_consecutive_losses,
                "bet_multiplier": s.bet_multiplier,
                "enabled": s.enabled,
            })
        if self.secondary_strategy:
            result["secondary_strategy"] = {
                "base_bet": self.secondary_strategy.base_bet,
                "auto_cashout": self.secondary_strategy.auto_cashout,
                "max_consecutive_losses": self.secondary_strategy.max_consecutive_losses,
                "bet_multiplier": self.secondary_strategy.bet_multiplier,
                "enabled": self.secondary_strategy.enabled,
            }
        if self.tertiary_strategy:
            result["tertiary_strategy"] = {
                "base_bet": self.tertiary_strategy.base_bet,
                "auto_cashout": self.tertiary_strategy.auto_cashout,
                "max_consecutive_losses": self.tertiary_strategy.max_consecutive_losses,
                "max_losses_in_window": self.tertiary_strategy.max_losses_in_window,
                "loss_check_window": self.tertiary_strategy.loss_check_window,
                "enabled": self.tertiary_strategy.enabled,
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
