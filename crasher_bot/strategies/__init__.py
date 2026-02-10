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
        return self.base_bet * (self.bet_multiplier**self.consecutive_losses)


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
        return self.base_bet * (self.bet_multiplier**self.consecutive_losses)


@dataclass
class TertiaryState:
    """Runtime state for the tertiary strategy."""

    base_bet: float
    auto_cashout: float
    max_consecutive_losses: int
    max_losses_in_window: int
    loss_check_window: int
    bet_multiplier: float
    stop_profit_count: int = 0
    name: str = "Tertiary"

    current_bet: float = 0.0
    consecutive_losses: int = 0
    total_profit: float = 0.0
    total_wins: int = 0
    waiting_for_result: bool = False
    is_active: bool = False
    recent_outcomes: List[str] = field(default_factory=list)

    def __post_init__(self):
        if self.current_bet == 0.0:
            self.current_bet = self.base_bet

    def reset(self):
        self.current_bet = self.base_bet
        self.consecutive_losses = 0
        self.waiting_for_result = False
        self.is_active = False

    def full_reset(self):
        """Reset everything including win counter and outcomes."""
        self.reset()
        self.total_wins = 0
        self.recent_outcomes = []

    def record_outcome(self, outcome: str):
        self.recent_outcomes.append(outcome)
        if len(self.recent_outcomes) > self.loss_check_window:
            self.recent_outcomes.pop(0)

    def losses_in_window(self) -> int:
        return sum(1 for o in self.recent_outcomes if o == "loss")

    def should_stop_for_window_losses(self) -> bool:
        if len(self.recent_outcomes) < self.loss_check_window:
            return False
        return self.losses_in_window() >= self.max_losses_in_window

    def should_stop_for_profit(self) -> bool:
        """Returns True if stop_profit_count is set and wins have reached it."""
        return self.stop_profit_count > 0 and self.total_wins >= self.stop_profit_count

    def next_bet(self) -> float:
        if self.consecutive_losses == 0:
            return self.base_bet
        return self.base_bet * (self.bet_multiplier**self.consecutive_losses)
