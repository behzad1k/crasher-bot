"""Strategy state objects for runtime tracking."""

from dataclasses import dataclass, field
from typing import List


@dataclass
class StrategyState:
    """Runtime state for a primary strategy."""

    name: str
    base_bet: float
    auto_cashout: float
    trigger_threshold: float
    trigger_count: int
    max_consecutive_losses: int
    bet_multiplier: float

    current_bet: float = 0.0
    consecutive_losses: int = 0
    total_profit: float = 0.0
    waiting_for_result: bool = False
    is_active: bool = False

    def __post_init__(self):
        if self.current_bet == 0.0:
            self.current_bet = self.base_bet

    def reset(self):
        self.current_bet = self.base_bet
        self.consecutive_losses = 0
        self.waiting_for_result = False
        self.is_active = False

    def next_bet(self) -> float:
        if self.consecutive_losses == 0:
            return self.base_bet
        return self.base_bet * (self.bet_multiplier ** self.consecutive_losses)


@dataclass
class SecondaryState:
    """Runtime state for the secondary (signal-monitoring) strategy."""

    base_bet: float
    auto_cashout: float
    max_consecutive_losses: int
    bet_multiplier: float
    name: str = "Secondary"

    current_bet: float = 0.0
    consecutive_losses: int = 0
    total_profit: float = 0.0
    waiting_for_result: bool = False
    is_active: bool = False
    monitoring: bool = False
    rounds_monitored: int = 0
    monitoring_history: List[float] = field(default_factory=list)

    def __post_init__(self):
        if self.current_bet == 0.0:
            self.current_bet = self.base_bet

    def reset(self):
        self.current_bet = self.base_bet
        self.consecutive_losses = 0
        self.waiting_for_result = False
        self.is_active = False

    def stop_monitoring(self):
        self.monitoring = False
        self.rounds_monitored = 0
        self.monitoring_history = []

    def start_monitoring(self, initial: List[float] | None = None):
        self.monitoring = True
        self.rounds_monitored = 0
        self.monitoring_history = list(initial) if initial else []

    def next_bet(self) -> float:
        if self.consecutive_losses == 0:
            return self.base_bet
        return self.base_bet * (self.bet_multiplier ** self.consecutive_losses)
