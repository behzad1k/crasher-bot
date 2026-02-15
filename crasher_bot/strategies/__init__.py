"""Strategy state objects for runtime tracking."""

from dataclasses import dataclass, field
from typing import List, Optional


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
class CustomState:
    """Runtime state for the custom strategy."""

    base_bet: float
    auto_cashout: float
    max_consecutive_losses: int
    max_losses_in_window: int
    loss_check_window: int
    bet_multiplier: float
    stop_profit_count: int = 0
    cooldown_after_win: int = 0
    cooldown_after_loss: int = 0
    name: str = "Custom"

    # Activation triggers
    activate_on_strong_hotstreak: bool = True
    activate_on_weak_hotstreak: bool = False
    activate_on_rule_of_17: bool = True
    activate_on_pre_streak_pattern: bool = True
    activate_on_possible_chain: bool = False
    activate_on_high_deviation_10: bool = False
    activate_on_high_deviation_15: bool = False

    # Signal confirmation
    signal_confirm_threshold: float = 2.0
    signal_confirm_count: int = 3
    signal_confirm_window: int = 5
    signal_monitor_rounds: int = 20

    # Betting state
    current_bet: float = 0.0
    consecutive_losses: int = 0
    total_profit: float = 0.0
    total_wins: int = 0
    waiting_for_result: bool = False
    is_active: bool = False
    recent_outcomes: List[str] = field(default_factory=list)

    # Signal monitoring state
    monitoring: bool = False
    rounds_monitored: int = 0
    monitoring_history: List[float] = field(default_factory=list)
    pending_signal_reason: Optional[str] = None

    # Cooldown state
    cooldown_remaining: int = 0
    _cooldown_type: str = ""  # "win" or "loss" — informational only

    def __post_init__(self):
        if self.current_bet == 0.0:
            self.current_bet = self.base_bet

    # ── Cooldown ───────────────────────────────────────────────────

    def start_cooldown(self):
        """Start the post-win cooldown period (triggered by stop-profit).
        Drops current signal/monitoring and suppresses signal checking for N rounds."""
        if self.cooldown_after_win > 0:
            self.cooldown_remaining = self.cooldown_after_win
            self._cooldown_type = "win"
            self.stop_monitoring()

    def start_loss_cooldown(self):
        """Start the post-loss cooldown period (triggered by max losses or window losses).
        Drops current signal/monitoring and suppresses signal checking for N rounds."""
        if self.cooldown_after_loss > 0:
            self.cooldown_remaining = self.cooldown_after_loss
            self._cooldown_type = "loss"
            self.stop_monitoring()

    def tick_cooldown(self):
        """Decrement cooldown by one round. Call once per round."""
        if self.cooldown_remaining > 0:
            self.cooldown_remaining -= 1

    def in_cooldown(self) -> bool:
        """Returns True if currently in a post-win or post-loss cooldown period."""
        return self.cooldown_remaining > 0

    @property
    def cooldown_type(self) -> str:
        """Returns the type of cooldown currently active ('win', 'loss', or '')."""
        if self.cooldown_remaining > 0:
            return self._cooldown_type
        return ""

    # ── Signal activation checks ───────────────────────────────────

    def should_activate_on_signal(self, signal_name: str) -> bool:
        """Check if a given signal name should trigger activation."""
        mapping = {
            "pre_streak": self.activate_on_pre_streak_pattern,
            "rule_of_17": self.activate_on_rule_of_17,
            "possible_chain": self.activate_on_possible_chain,
            "dead_ass_chain": False,
        }
        if signal_name in mapping:
            return bool(mapping[signal_name])
        return False

    def should_activate_on_high_stddev(self, window_size: int) -> bool:
        """Check if high_stddev signal should trigger for a given window size."""
        if window_size == 10:
            return self.activate_on_high_deviation_10
        elif window_size == 15:
            return self.activate_on_high_deviation_15
        return False

    def should_activate_on_hotstreak(self, hotstreak_type: str) -> bool:
        """Check if a hotstreak type should trigger activation."""
        if hotstreak_type == "strong":
            return self.activate_on_strong_hotstreak
        elif hotstreak_type == "weak":
            return self.activate_on_weak_hotstreak
        return False

    # ── Signal confirmation ────────────────────────────────────────

    def check_confirmation(self, recent_mults: List[float]) -> bool:
        """Check if recent multipliers meet the signal confirmation criteria."""
        if len(recent_mults) < self.signal_confirm_window:
            return False
        window = recent_mults[-self.signal_confirm_window :]
        above = sum(1 for m in window if m >= self.signal_confirm_threshold)
        return above >= self.signal_confirm_count

    def start_monitoring(self, reason: str, initial: Optional[List[float]] = None):
        """Start monitoring rounds after a signal to wait for confirmation."""
        self.monitoring = True
        self.rounds_monitored = 0
        self.monitoring_history = list(initial) if initial else []
        self.pending_signal_reason = reason

    def stop_monitoring(self):
        """Stop signal monitoring."""
        self.monitoring = False
        self.rounds_monitored = 0
        self.monitoring_history = []
        self.pending_signal_reason = None

    # ── Betting state ──────────────────────────────────────────────

    def reset(self):
        self.current_bet = self.base_bet
        self.consecutive_losses = 0
        self.waiting_for_result = False
        self.is_active = False

    def full_reset(self):
        """Reset everything including win counter, outcomes, and monitoring."""
        self.reset()
        self.total_wins = 0
        self.recent_outcomes = []
        self.stop_monitoring()
        self.cooldown_remaining = 0
        self._cooldown_type = ""

    def enter_cooldown_reset(self):
        """Reset betting state and drop signals, but preserve total_wins and total_profit
        for informational purposes. Used when entering cooldown (not a full disable)."""
        self.current_bet = self.base_bet
        self.consecutive_losses = 0
        self.waiting_for_result = False
        self.is_active = False
        self.recent_outcomes = []
        self.stop_monitoring()

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
