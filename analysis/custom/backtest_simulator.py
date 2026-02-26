#!/usr/bin/env python3
"""
Crasher Bot Backtesting & Simulation Script

This script analyzes historical session data, generates signals using the custom strategy logic,
and backtests different configurations to find optimal settings through reverse engineering.

Usage:
    python backtest_simulator.py [--db path/to/crasher_data.db]
"""

import argparse
import itertools
import json
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION SPACE - Define parameter ranges to test
# ═══════════════════════════════════════════════════════════════════════════════

CONFIG_GRID = {
    "base_bet": [1000],
    "auto_cashout": [2.0, 3.0, 5.0, 10.0],
    "max_consecutive_losses": [2, 4, 6, 8, 10],
    "max_losses_in_window": [2, 4, 7, 10],
    "loss_check_window": [5, 10, 12, 15],
    "bet_multiplier": [1.0, 1.5],
    "stop_profit_count": [0, 2, 3],
    "cooldown_after_win": [0, 3, 5, 10],
    "cooldown_after_loss": [0, 3, 5, 10],
    "signal_confirm_threshold": [1.5, 2.0, 3.0],
    "signal_confirm_count": [2, 3, 4],
    "signal_confirm_window": [3, 4, 5],
    "signal_monitor_rounds": [10, 20],
    "activate_on_strong_hotstreak": [True],
    "activate_on_weak_hotstreak": [True, False],
    "activate_on_rule_of_17": [True, False],
    "activate_on_pre_streak_pattern": [True, False],
    "activate_on_possible_chain": [True, False],
    "activate_on_high_deviation_10": [True, False],
    "activate_on_high_deviation_15": [False, False],
}
# Reduced grid for faster initial testing
CONFIG_GRID_FAST = {
    "base_bet": [1000],
    "auto_cashout": [2.0, 3.0, 5.0, 10.0],
    "max_consecutive_losses": [2, 4, 6, 8, 10],
    "max_losses_in_window": [2, 4, 7, 10],
    "loss_check_window": [5, 10, 12, 15],
    "bet_multiplier": [1.0, 1.5],
    "stop_profit_count": [0, 2, 3],
    "cooldown_after_win": [0, 3, 5, 10],
    "cooldown_after_loss": [0, 3, 5, 10],
    "signal_confirm_threshold": [1.5, 2.0, 3.0],
    "signal_confirm_count": [2, 3, 4],
    "signal_confirm_window": [3, 4, 5],
    "signal_monitor_rounds": [10, 20],
    "activate_on_strong_hotstreak": [True],
    "activate_on_weak_hotstreak": [True, False],
    "activate_on_rule_of_17": [True, False],
    "activate_on_pre_streak_pattern": [True, False],
    "activate_on_possible_chain": [True, False],
    "activate_on_high_deviation_10": [True, False],
    "activate_on_high_deviation_15": [False, False],
}

# ═══════════════════════════════════════════════════════════════════════════════
# HOTSTREAK DETECTION (mirrors crasher_bot/core/hotstreak.py)
# ═══════════════════════════════════════════════════════════════════════════════

HOTSTREAK_WEAK_PCT = 0.65
HOTSTREAK_STRONG_PCT = 0.75
HOTSTREAK_MIN_WINDOW = 10
HOTSTREAK_MAX_WINDOW = 15
COLD_STREAK_LENGTH = 5
HISTORY_SIZE = 50


@dataclass
class HotstreakInfo:
    type: str  # "strong", "weak", or ""
    length: int = 0
    average: float = 0.0
    start_round: int = 0
    multipliers: List[float] = field(default_factory=list)


class HotstreakTracker:
    """Detects hotstreaks and cold streaks in multiplier history."""

    def __init__(self):
        self.recent: List[float] = []
        self.current_hotstreak: Optional[HotstreakInfo] = None
        self.last_hotstreak: Optional[HotstreakInfo] = None
        self.hotstreak_end_round = 0
        self.current_round = 0
        self.rounds_after_hotstreak = 0
        self.cold_streak_occurred = False
        self._cold_count = 0

    def add_multiplier(self, multiplier: float):
        self.current_round += 1
        self.recent.append(multiplier)
        if len(self.recent) > HISTORY_SIZE:
            self.recent.pop(0)
        self._detect_hotstreak()
        self._track_cold(multiplier)

    def get_last_n(self, n: int) -> List[float]:
        return self.recent[-n:] if len(self.recent) >= n else list(self.recent)

    def in_hotstreak(self) -> bool:
        return self.current_hotstreak is not None

    def just_ended_hotstreak(self) -> bool:
        return self.last_hotstreak is not None and self.rounds_after_hotstreak <= 15

    def _detect_hotstreak(self):
        for ws in range(HOTSTREAK_MAX_WINDOW, HOTSTREAK_MIN_WINDOW - 1, -1):
            if len(self.recent) < ws:
                continue
            window = self.recent[-ws:]
            above = sum(1 for m in window if m >= 2.0)
            pct = above / ws
            if pct >= HOTSTREAK_WEAK_PCT:
                avg = sum(window) / ws
                stype = "strong" if pct >= HOTSTREAK_STRONG_PCT else "weak"
                if self.current_hotstreak is None:
                    self.current_hotstreak = HotstreakInfo(
                        type=stype,
                        length=ws,
                        average=avg,
                        start_round=self.current_round - ws + 1,
                        multipliers=window.copy(),
                    )
                else:
                    self.current_hotstreak.type = stype
                    self.current_hotstreak.length = ws
                    self.current_hotstreak.average = avg
                    self.current_hotstreak.multipliers = window.copy()
                return

        if self.current_hotstreak is not None:
            self.last_hotstreak = HotstreakInfo(
                type=self.current_hotstreak.type,
                length=self.current_hotstreak.length,
                average=self.current_hotstreak.average,
                start_round=self.current_hotstreak.start_round,
                multipliers=self.current_hotstreak.multipliers.copy(),
            )
            self.hotstreak_end_round = self.current_round - 1
            self.rounds_after_hotstreak = 0
            self.cold_streak_occurred = False
            self._cold_count = 0
            self.current_hotstreak = None

    def _track_cold(self, multiplier: float):
        if self.last_hotstreak is not None:
            self.rounds_after_hotstreak = self.current_round - self.hotstreak_end_round
        if multiplier < 2.0:
            self._cold_count += 1
            if self._cold_count >= COLD_STREAK_LENGTH:
                self.cold_streak_occurred = True
        else:
            self._cold_count = 0


def analyze_window(window: List[float], window_size: int) -> List[str]:
    """Analyze a multiplier window and return list of detected signal names."""
    signals = []
    if len(window) < window_size:
        return signals

    avg = np.mean(window)
    std = np.std(window)
    mx = np.max(window)
    above_2x = sum(1 for m in window if m >= 2.0)

    if window_size == 10 and avg > 3.75 and above_2x >= 4 and std > 12 and mx > 7.16:
        signals.append("pre_streak")

    if std > 25:
        signals.append("high_stddev")

    return signals


def check_chain_patterns(tracker: HotstreakTracker) -> List[str]:
    """Check chain patterns after hotstreak ends."""
    if tracker.last_hotstreak is None:
        return []

    signals = []
    ra = tracker.rounds_after_hotstreak

    if ra == 10:
        last_10 = tracker.get_last_n(10)
        if len(last_10) == 10:
            avg = np.mean(last_10)
            above = sum(1 for m in last_10 if m >= 2.0)
            if avg > 2.0 and above > 4 and not tracker.cold_streak_occurred:
                ls = tracker.last_hotstreak
                if ls.type == "strong" and ls.average > 6.0:
                    signals.append("dead_ass_chain")
                else:
                    signals.append("possible_chain")

    if ra == 15 and not tracker.cold_streak_occurred:
        signals.append("rule_of_17")

    return signals


# ═══════════════════════════════════════════════════════════════════════════════
# CUSTOM STRATEGY STATE (mirrors crasher_bot/strategies/__init__.py)
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class CustomConfig:
    base_bet: float = 1000
    auto_cashout: float = 2.0
    max_consecutive_losses: int = 10
    max_losses_in_window: int = 7
    loss_check_window: int = 10
    bet_multiplier: float = 2.0
    stop_profit_count: int = 0
    cooldown_after_win: int = 0
    cooldown_after_loss: int = 0

    activate_on_strong_hotstreak: bool = True
    activate_on_weak_hotstreak: bool = False
    activate_on_rule_of_17: bool = True
    activate_on_pre_streak_pattern: bool = True
    activate_on_possible_chain: bool = False
    activate_on_high_deviation_10: bool = False
    activate_on_high_deviation_15: bool = False

    signal_confirm_threshold: float = 2.0
    signal_confirm_count: int = 3
    signal_confirm_window: int = 5
    signal_monitor_rounds: int = 20


@dataclass
class SimulationState:
    """Tracks state during backtesting simulation."""

    config: CustomConfig

    # Betting state
    current_bet: float = 0.0
    consecutive_losses: int = 0
    total_profit: float = 0.0
    total_wins: int = 0
    total_bets: int = 0
    is_active: bool = False
    waiting_for_result: bool = False
    recent_outcomes: List[str] = field(default_factory=list)

    # Monitoring state
    monitoring: bool = False
    rounds_monitored: int = 0
    monitoring_history: List[float] = field(default_factory=list)
    pending_signal_reason: Optional[str] = None

    # Cooldown state
    cooldown_remaining: int = 0
    cooldown_type: str = ""

    # Stats tracking
    max_drawdown: float = 0.0
    peak_profit: float = 0.0
    win_streak: int = 0
    max_win_streak: int = 0
    loss_streak: int = 0
    max_loss_streak: int = 0
    signals_fired: List[Tuple[int, str]] = field(default_factory=list)
    bets_placed: List[Tuple[int, float, float, str]] = field(
        default_factory=list
    )  # (round, bet, mult, outcome)

    def __post_init__(self):
        if self.current_bet == 0.0:
            self.current_bet = self.config.base_bet

    def reset(self):
        self.current_bet = self.config.base_bet
        self.consecutive_losses = 0
        self.waiting_for_result = False
        self.is_active = False

    def full_reset(self):
        self.reset()
        self.total_wins = 0
        self.recent_outcomes = []
        self.stop_monitoring()
        self.cooldown_remaining = 0
        self.cooldown_type = ""

    def enter_cooldown_reset(self):
        self.current_bet = self.config.base_bet
        self.consecutive_losses = 0
        self.waiting_for_result = False
        self.is_active = False
        self.recent_outcomes = []
        self.stop_monitoring()

    def start_monitoring(self, reason: str, initial: Optional[List[float]] = None):
        self.monitoring = True
        self.rounds_monitored = 0
        self.monitoring_history = list(initial) if initial else []
        self.pending_signal_reason = reason

    def stop_monitoring(self):
        self.monitoring = False
        self.rounds_monitored = 0
        self.monitoring_history = []
        self.pending_signal_reason = None

    def in_cooldown(self) -> bool:
        return self.cooldown_remaining > 0

    def tick_cooldown(self):
        if self.cooldown_remaining > 0:
            self.cooldown_remaining -= 1

    def start_win_cooldown(self):
        if self.config.cooldown_after_win > 0:
            self.cooldown_remaining = self.config.cooldown_after_win
            self.cooldown_type = "win"
            self.stop_monitoring()

    def start_loss_cooldown(self):
        if self.config.cooldown_after_loss > 0:
            self.cooldown_remaining = self.config.cooldown_after_loss
            self.cooldown_type = "loss"
            self.stop_monitoring()

    def record_outcome(self, outcome: str):
        self.recent_outcomes.append(outcome)
        if len(self.recent_outcomes) > self.config.loss_check_window:
            self.recent_outcomes.pop(0)

    def losses_in_window(self) -> int:
        return sum(1 for o in self.recent_outcomes if o == "loss")

    def should_stop_for_window_losses(self) -> bool:
        if len(self.recent_outcomes) < self.config.loss_check_window:
            return False
        return self.losses_in_window() >= self.config.max_losses_in_window

    def should_stop_for_profit(self) -> bool:
        return (
            self.config.stop_profit_count > 0
            and self.total_wins >= self.config.stop_profit_count
        )

    def next_bet(self) -> float:
        if self.consecutive_losses == 0:
            return self.config.base_bet
        return self.config.base_bet * (
            self.config.bet_multiplier**self.consecutive_losses
        )

    def should_activate_on_signal(self, signal_name: str) -> bool:
        mapping = {
            "pre_streak": self.config.activate_on_pre_streak_pattern,
            "rule_of_17": self.config.activate_on_rule_of_17,
            "possible_chain": self.config.activate_on_possible_chain,
            "dead_ass_chain": False,
        }
        return bool(mapping.get(signal_name, False))

    def should_activate_on_high_stddev(self, window_size: int) -> bool:
        if window_size == 10:
            return self.config.activate_on_high_deviation_10
        elif window_size == 15:
            return self.config.activate_on_high_deviation_15
        return False

    def should_activate_on_hotstreak(self, hotstreak_type: str) -> bool:
        if hotstreak_type == "strong":
            return self.config.activate_on_strong_hotstreak
        elif hotstreak_type == "weak":
            return self.config.activate_on_weak_hotstreak
        return False

    def check_confirmation(self, recent_mults: List[float]) -> bool:
        if len(recent_mults) < self.config.signal_confirm_window:
            return False
        window = recent_mults[-self.config.signal_confirm_window :]
        above = sum(1 for m in window if m >= self.config.signal_confirm_threshold)
        return above >= self.config.signal_confirm_count

    def update_stats(self, profit: float):
        self.total_profit += profit
        if self.total_profit > self.peak_profit:
            self.peak_profit = self.total_profit
        drawdown = self.peak_profit - self.total_profit
        if drawdown > self.max_drawdown:
            self.max_drawdown = drawdown


# ═══════════════════════════════════════════════════════════════════════════════
# BACKTESTING ENGINE
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class SessionData:
    session_id: int
    multipliers: List[float]
    start_time: str
    end_time: Optional[str]


@dataclass
class BacktestResult:
    config: CustomConfig
    total_profit: float
    total_bets: int
    total_wins: int
    win_rate: float
    max_drawdown: float
    profit_factor: float  # gross profit / gross loss
    sharpe_ratio: float
    max_win_streak: int
    max_loss_streak: int
    signals_fired: int
    avg_bet_size: float
    roi: float  # Return on investment (profit / total wagered)

    def score(self) -> float:
        """Combined score for ranking configs."""
        # Balance profit, win rate, and risk management
        if self.total_bets == 0:
            return -float("inf")

        profit_score = self.total_profit / 100000  # Normalize
        winrate_score = (self.win_rate - 0.5) * 2  # Center around 50%
        drawdown_penalty = -self.max_drawdown / 100000
        roi_score = self.roi * 10

        return profit_score + winrate_score + drawdown_penalty + roi_score


class BacktestEngine:
    """Simulates the custom strategy against historical data."""

    def __init__(self, sessions: List[SessionData]):
        self.sessions = sessions
        self.verbose = False

    def run(self, config: CustomConfig) -> BacktestResult:
        """Run backtest with the given configuration."""
        all_profits = []
        total_wagered = 0.0
        gross_profit = 0.0
        gross_loss = 0.0

        state = SimulationState(config=config)

        for session in self.sessions:
            tracker = HotstreakTracker()

            # Reset state between sessions (simulate fresh start)
            # But keep cumulative stats
            state.reset()
            state.stop_monitoring()
            state.cooldown_remaining = 0

            for i, mult in enumerate(session.multipliers):
                round_num = i + 1
                tracker.add_multiplier(mult)

                # Tick cooldown each round
                if state.in_cooldown():
                    state.tick_cooldown()

                # Handle active bet result
                if state.waiting_for_result:
                    profit = self._handle_result(state, mult, round_num)
                    all_profits.append(profit)
                    total_wagered += (
                        state.bets_placed[-1][1] if state.bets_placed else 0
                    )
                    if profit > 0:
                        gross_profit += profit
                    else:
                        gross_loss += abs(profit)

                # Signal monitoring (if not in cooldown)
                if state.monitoring and not state.is_active and not state.in_cooldown():
                    self._monitor_round(state, mult, tracker, round_num)

                # Hotstreak activation (if not in cooldown)
                if not state.in_cooldown():
                    self._check_hotstreak_activation(state, tracker, round_num)

                # Signal analysis (if idle)
                if (
                    not state.is_active
                    and not state.waiting_for_result
                    and not state.in_cooldown()
                ):
                    self._analyze_signals(state, tracker, round_num)

        # Calculate metrics
        total_wins = state.total_wins
        total_bets = state.total_bets
        win_rate = total_wins / total_bets if total_bets > 0 else 0.0
        profit_factor = (
            gross_profit / gross_loss
            if gross_loss > 0
            else float("inf")
            if gross_profit > 0
            else 0.0
        )

        # Sharpe ratio approximation
        if len(all_profits) > 1:
            returns = np.array(all_profits)
            sharpe = np.mean(returns) / np.std(returns) if np.std(returns) > 0 else 0.0
        else:
            sharpe = 0.0

        roi = state.total_profit / total_wagered if total_wagered > 0 else 0.0
        avg_bet = total_wagered / total_bets if total_bets > 0 else 0.0

        return BacktestResult(
            config=config,
            total_profit=state.total_profit,
            total_bets=total_bets,
            total_wins=total_wins,
            win_rate=win_rate,
            max_drawdown=state.max_drawdown,
            profit_factor=profit_factor,
            sharpe_ratio=sharpe,
            max_win_streak=state.max_win_streak,
            max_loss_streak=state.max_loss_streak,
            signals_fired=len(state.signals_fired),
            avg_bet_size=avg_bet,
            roi=roi,
        )

    def _handle_result(self, state: SimulationState, mult: float, round_num: int):
        """Handle the result of an active bet."""
        bet_amount = state.current_bet

        if mult >= state.config.auto_cashout:
            # WIN
            profit = bet_amount * (state.config.auto_cashout - 1)
            state.total_wins += 1
            state.total_bets += 1
            state.record_outcome("win")
            state.update_stats(profit)
            state.bets_placed.append((round_num, bet_amount, mult, "win"))

            state.win_streak += 1
            state.loss_streak = 0
            if state.win_streak > state.max_win_streak:
                state.max_win_streak = state.win_streak

            state.consecutive_losses = 0
            state.current_bet = state.config.base_bet
            state.waiting_for_result = False

            # Check stop-profit
            if state.should_stop_for_profit():
                if state.config.cooldown_after_win > 0:
                    state.total_wins = 0
                    state.start_win_cooldown()
                    state.enter_cooldown_reset()
                else:
                    state.full_reset()
                return profit

            # Continue betting
            self._place_bet(state, round_num)
            return profit
        else:
            # LOSS
            loss = bet_amount
            state.total_bets += 1
            state.record_outcome("loss")
            state.update_stats(-loss)
            state.bets_placed.append((round_num, bet_amount, mult, "loss"))

            state.loss_streak += 1
            state.win_streak = 0
            if state.loss_streak > state.max_loss_streak:
                state.max_loss_streak = state.loss_streak

            state.consecutive_losses += 1

            # Check max consecutive losses
            if state.consecutive_losses >= state.config.max_consecutive_losses:
                if state.config.cooldown_after_loss > 0:
                    state.start_loss_cooldown()
                    state.enter_cooldown_reset()
                else:
                    state.full_reset()
                return -loss

            # Check window loss limit
            if state.should_stop_for_window_losses():
                if state.config.cooldown_after_loss > 0:
                    state.start_loss_cooldown()
                    state.enter_cooldown_reset()
                else:
                    state.full_reset()
                return -loss

            # Continue with martingale
            state.current_bet = state.next_bet()
            state.waiting_for_result = True
            return -loss

    def _place_bet(self, state: SimulationState, round_num: int):
        """Place a bet (simulate)."""
        state.is_active = True
        state.current_bet = state.next_bet()
        state.waiting_for_result = True

    def _check_hotstreak_activation(
        self, state: SimulationState, tracker: HotstreakTracker, round_num: int
    ):
        """Check for hotstreak-based activation."""
        if state.is_active or state.waiting_for_result or state.in_cooldown():
            return

        if tracker.current_hotstreak is None:
            return

        hs_type = tracker.current_hotstreak.type
        if state.should_activate_on_hotstreak(hs_type):
            state.signals_fired.append((round_num, f"{hs_type}_hotstreak"))
            state.stop_monitoring()
            self._place_bet(state, round_num)

    def _signal_triggered(
        self,
        state: SimulationState,
        tracker: HotstreakTracker,
        reason: str,
        round_num: int,
    ):
        """Handle a signal trigger."""
        if state.is_active or state.waiting_for_result or state.in_cooldown():
            return

        # Check immediate confirmation
        recent = tracker.get_last_n(state.config.signal_confirm_window)
        if state.check_confirmation(recent):
            state.signals_fired.append((round_num, f"{reason} (confirmed)"))
            self._place_bet(state, round_num)
        else:
            # Start monitoring
            state.start_monitoring(reason, initial=list(recent))

    def _monitor_round(
        self,
        state: SimulationState,
        mult: float,
        tracker: HotstreakTracker,
        round_num: int,
    ):
        """Process a round during signal monitoring."""
        state.rounds_monitored += 1
        state.monitoring_history.append(mult)

        # Check confirmation
        if len(state.monitoring_history) >= state.config.signal_confirm_window:
            last_n = state.monitoring_history[-state.config.signal_confirm_window :]
            above = sum(1 for m in last_n if m >= state.config.signal_confirm_threshold)
            if above >= state.config.signal_confirm_count:
                if not state.is_active and not state.in_cooldown():
                    reason = state.pending_signal_reason or "signal"
                    state.signals_fired.append((round_num, f"{reason} (confirmed)"))
                    state.stop_monitoring()
                    self._place_bet(state, round_num)
                    return

        # Max monitoring reached
        if state.rounds_monitored >= state.config.signal_monitor_rounds:
            state.stop_monitoring()

    def _analyze_signals(
        self, state: SimulationState, tracker: HotstreakTracker, round_num: int
    ):
        """Analyze for signal triggers."""
        if state.is_active or tracker.in_hotstreak() or state.in_cooldown():
            return

        triggered_signals = []

        for win_size, min_len in [(10, 10), (15, 15)]:
            window = tracker.get_last_n(win_size)
            if len(window) < min_len:
                continue
            signals = analyze_window(window, win_size)
            for sig in signals:
                if sig == "high_stddev":
                    if state.should_activate_on_high_stddev(win_size):
                        triggered_signals.append(f"{sig}_w{win_size}")
                elif state.should_activate_on_signal(sig):
                    triggered_signals.append(sig)

        if tracker.just_ended_hotstreak():
            for sig in check_chain_patterns(tracker):
                if state.should_activate_on_signal(sig):
                    triggered_signals.append(sig)

        if triggered_signals and not state.is_active:
            reason = ", ".join(triggered_signals)
            self._signal_triggered(state, tracker, reason, round_num)


# ═══════════════════════════════════════════════════════════════════════════════
# DATA LOADING
# ═══════════════════════════════════════════════════════════════════════════════


def load_sessions(db_path: str) -> List[SessionData]:
    """Load session data from SQLite database."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Get all sessions
    cursor.execute("""
        SELECT s.id, s.start_timestamp, s.end_timestamp, COUNT(m.id)
        FROM sessions s
        LEFT JOIN multipliers m ON s.id = m.session_id
        GROUP BY s.id
        ORDER BY s.id ASC
    """)
    sessions_info = cursor.fetchall()

    sessions = []
    for sid, start_ts, end_ts, count in sessions_info:
        if count == 0:
            continue

        # Get multipliers for this session in order
        cursor.execute(
            """
            SELECT multiplier FROM multipliers
            WHERE session_id = ?
            ORDER BY id ASC
        """,
            (sid,),
        )
        mults = [row[0] for row in cursor.fetchall()]

        sessions.append(
            SessionData(
                session_id=sid,
                multipliers=mults,
                start_time=start_ts or "",
                end_time=end_ts,
            )
        )

    conn.close()
    return sessions


def get_db_path() -> str:
    """Find the database path."""
    candidates = [
        Path.home() / ".crasher_bot" / "crasher_data.db",
        Path("./crasher_data.db"),
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    return str(candidates[0])


# ═══════════════════════════════════════════════════════════════════════════════
# GRID SEARCH & ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════


def generate_configs(grid: Dict, max_configs: int = 1000) -> List[CustomConfig]:
    """Generate config combinations from grid."""
    keys = list(grid.keys())
    values = [grid[k] for k in keys]

    configs = []
    for combo in itertools.product(*values):
        if len(configs) >= max_configs:
            break
        params = dict(zip(keys, combo))

        # Skip invalid combinations (e.g., both hotstreaks off AND all signals off)
        if not any(
            [
                params.get("activate_on_strong_hotstreak", False),
                params.get("activate_on_weak_hotstreak", False),
                params.get("activate_on_rule_of_17", False),
                params.get("activate_on_pre_streak_pattern", False),
                params.get("activate_on_possible_chain", False),
                params.get("activate_on_high_deviation_10", False),
                params.get("activate_on_high_deviation_15", False),
            ]
        ):
            continue

        configs.append(CustomConfig(**params))

    return configs


def run_grid_search(
    sessions: List[SessionData],
    grid: Dict,
    max_configs: int = 500,
    verbose: bool = True,
    sort_by: str = "profit",
) -> List[BacktestResult]:
    """Run backtests for all config combinations.

    Args:
        sort_by: "profit", "roi", "winrate", "score", "sharpe", "drawdown"
    """
    configs = generate_configs(grid, max_configs)
    if verbose:
        print(f"Testing {len(configs)} configurations...")

    engine = BacktestEngine(sessions)
    results = []

    for i, config in enumerate(configs):
        result = engine.run(config)
        results.append(result)

        if verbose and (i + 1) % 50 == 0:
            print(f"  Progress: {i + 1}/{len(configs)}")

    # Sort by specified metric
    sort_keys = {
        "profit": lambda r: r.total_profit,
        "roi": lambda r: r.roi,
        "winrate": lambda r: r.win_rate,
        "score": lambda r: r.score(),
        "sharpe": lambda r: r.sharpe_ratio,
        "drawdown": lambda r: -r.max_drawdown,  # Lower drawdown is better
        "profit_factor": lambda r: (
            r.profit_factor if r.profit_factor != float("inf") else 999999
        ),
    }

    sort_key = sort_keys.get(sort_by, sort_keys["profit"])
    results.sort(key=sort_key, reverse=True)

    if verbose:
        print(f"\nResults sorted by: {sort_by.upper()}")

    return results


def analyze_data(sessions: List[SessionData]) -> Dict:
    """Analyze the historical data for insights."""
    all_mults = []
    for s in sessions:
        all_mults.extend(s.multipliers)

    if not all_mults:
        return {"error": "No data"}

    arr = np.array(all_mults)

    return {
        "total_rounds": len(all_mults),
        "total_sessions": len(sessions),
        "mean": float(np.mean(arr)),
        "median": float(np.median(arr)),
        "std": float(np.std(arr)),
        "min": float(np.min(arr)),
        "max": float(np.max(arr)),
        "above_2x_pct": float(
            sum(1 for m in all_mults if m >= 2.0) / len(all_mults) * 100
        ),
        "above_3x_pct": float(
            sum(1 for m in all_mults if m >= 3.0) / len(all_mults) * 100
        ),
        "above_5x_pct": float(
            sum(1 for m in all_mults if m >= 5.0) / len(all_mults) * 100
        ),
        "above_10x_pct": float(
            sum(1 for m in all_mults if m >= 10.0) / len(all_mults) * 100
        ),
        "rounds_per_session_avg": len(all_mults) / len(sessions),
    }


def print_results(results: List[BacktestResult], top_n: int = 20):
    """Print formatted results."""
    print("\n" + "=" * 100)
    print(f"TOP {top_n} CONFIGURATIONS (sorted by highest profit)")
    print("=" * 100)

    for i, r in enumerate(results[:top_n]):
        print(f"\n{'─' * 100}")
        print(
            f"RANK #{i + 1}  |  💰 PROFIT: {r.total_profit:+,.0f}  |  ROI: {r.roi * 100:+.2f}%"
        )
        print(f"{'─' * 100}")
        print(
            f"  Win Rate: {r.win_rate * 100:.1f}%  |  Bets: {r.total_bets}  |  Wins: {r.total_wins}  |  Signals: {r.signals_fired}"
        )
        print(
            f"  Max Drawdown: {r.max_drawdown:,.0f}  |  Profit Factor: {r.profit_factor:.2f}  |  Sharpe: {r.sharpe_ratio:.3f}"
        )
        print(
            f"  Max Win Streak: {r.max_win_streak}  |  Max Loss Streak: {r.max_loss_streak}  |  Avg Bet: {r.avg_bet_size:,.0f}"
        )
        print(f"\n  Config:")
        print(
            f"    base_bet={r.config.base_bet}, auto_cashout={r.config.auto_cashout}x, bet_mult={r.config.bet_multiplier}"
        )
        print(
            f"    max_losses={r.config.max_consecutive_losses}, window_losses={r.config.max_losses_in_window}/{r.config.loss_check_window}"
        )
        print(
            f"    stop_profit={r.config.stop_profit_count}, cd_win={r.config.cooldown_after_win}, cd_loss={r.config.cooldown_after_loss}"
        )
        print(
            f"    confirm: {r.config.signal_confirm_count}/{r.config.signal_confirm_window} > {r.config.signal_confirm_threshold}x, monitor={r.config.signal_monitor_rounds}"
        )
        triggers = []
        if r.config.activate_on_strong_hotstreak:
            triggers.append("strong_hs")
        if r.config.activate_on_weak_hotstreak:
            triggers.append("weak_hs")
        if r.config.activate_on_rule_of_17:
            triggers.append("rule17")
        if r.config.activate_on_pre_streak_pattern:
            triggers.append("pre_streak")
        if r.config.activate_on_possible_chain:
            triggers.append("possible_chain")
        if r.config.activate_on_high_deviation_10:
            triggers.append("stddev10")
        if r.config.activate_on_high_deviation_15:
            triggers.append("stddev15")
        print(f"    triggers: [{', '.join(triggers)}]")

    # Summary statistics
    if results:
        profitable = [r for r in results if r.total_profit > 0]
        print(f"\n{'=' * 100}")
        print(f"SUMMARY: {len(profitable)}/{len(results)} configs were profitable")
        if profitable:
            print(f"  Best profit: {results[0].total_profit:+,.0f}")
            print(
                f"  Avg profit (profitable only): {sum(r.total_profit for r in profitable) / len(profitable):+,.0f}"
            )
        print("=" * 100)


def export_best_config(result: BacktestResult, output_path: str = "best_config.json"):
    """Export the best config to JSON format matching bot_config.json structure."""
    config = {
        "custom_strategy": {
            "enabled": True,
            "base_bet": result.config.base_bet,
            "auto_cashout": result.config.auto_cashout,
            "max_consecutive_losses": result.config.max_consecutive_losses,
            "max_losses_in_window": result.config.max_losses_in_window,
            "loss_check_window": result.config.loss_check_window,
            "bet_multiplier": result.config.bet_multiplier,
            "stop_profit_count": result.config.stop_profit_count,
            "cooldown_after_win": result.config.cooldown_after_win,
            "cooldown_after_loss": result.config.cooldown_after_loss,
            "activate_on_strong_hotstreak": result.config.activate_on_strong_hotstreak,
            "activate_on_weak_hotstreak": result.config.activate_on_weak_hotstreak,
            "activate_on_rule_of_17": result.config.activate_on_rule_of_17,
            "activate_on_pre_streak_pattern": result.config.activate_on_pre_streak_pattern,
            "activate_on_possible_chain": result.config.activate_on_possible_chain,
            "activate_on_high_deviation_10": result.config.activate_on_high_deviation_10,
            "activate_on_high_deviation_15": result.config.activate_on_high_deviation_15,
            "signal_confirm_threshold": result.config.signal_confirm_threshold,
            "signal_confirm_count": result.config.signal_confirm_count,
            "signal_confirm_window": result.config.signal_confirm_window,
            "signal_monitor_rounds": result.config.signal_monitor_rounds,
        },
        "backtest_results": {
            "total_profit": result.total_profit,
            "roi_percent": result.roi * 100,
            "win_rate_percent": result.win_rate * 100,
            "total_bets": result.total_bets,
            "max_drawdown": result.max_drawdown,
            "profit_factor": result.profit_factor,
            "score": result.score(),
        },
    }

    with open(output_path, "w") as f:
        json.dump(config, f, indent=2)

    print(f"\nBest config exported to: {output_path}")


# ═══════════════════════════════════════════════════════════════════════════════
# SIGNAL ANALYSIS MODE
# ═══════════════════════════════════════════════════════════════════════════════


def analyze_signals(sessions: List[SessionData], verbose: bool = True) -> Dict:
    """Analyze all signals that would have fired in historical data."""
    all_signals = []

    for session in sessions:
        tracker = HotstreakTracker()

        for i, mult in enumerate(session.multipliers):
            round_num = i + 1
            tracker.add_multiplier(mult)

            # Check hotstreaks
            if tracker.current_hotstreak:
                hs = tracker.current_hotstreak
                # Only record when hotstreak first detected
                if hs.length == HOTSTREAK_MIN_WINDOW:
                    all_signals.append(
                        {
                            "session_id": session.session_id,
                            "round": round_num,
                            "type": f"{hs.type}_hotstreak",
                            "mult": mult,
                            "window_avg": hs.average,
                        }
                    )

            # Check pattern signals
            for win_size, min_len in [(10, 10), (15, 15)]:
                window = tracker.get_last_n(win_size)
                if len(window) >= min_len:
                    signals = analyze_window(window, win_size)
                    for sig in signals:
                        all_signals.append(
                            {
                                "session_id": session.session_id,
                                "round": round_num,
                                "type": sig + f"_w{win_size}",
                                "mult": mult,
                                "window_avg": np.mean(window),
                            }
                        )

            # Check chain patterns
            if tracker.just_ended_hotstreak():
                for sig in check_chain_patterns(tracker):
                    all_signals.append(
                        {
                            "session_id": session.session_id,
                            "round": round_num,
                            "type": sig,
                            "mult": mult,
                            "window_avg": np.mean(tracker.get_last_n(10))
                            if tracker.get_last_n(10)
                            else 0,
                        }
                    )

    # Analyze signal success rates
    signal_stats = {}
    for sig in all_signals:
        sig_type = sig["type"]
        if sig_type not in signal_stats:
            signal_stats[sig_type] = {
                "count": 0,
                "total_following": 0,
                "following_above_2x": 0,
            }
        signal_stats[sig_type]["count"] += 1

    if verbose:
        print("\n" + "=" * 60)
        print("SIGNAL ANALYSIS")
        print("=" * 60)
        print(f"\nTotal signals detected: {len(all_signals)}")
        print("\nSignal frequency:")
        for sig_type, stats in sorted(
            signal_stats.items(), key=lambda x: x[1]["count"], reverse=True
        ):
            print(f"  {sig_type}: {stats['count']} occurrences")

    return {
        "total_signals": len(all_signals),
        "signal_types": signal_stats,
        "signals": all_signals,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════


def main():
    parser = argparse.ArgumentParser(description="Backtest Crasher Bot configurations")
    parser.add_argument("--db", default=None, help="Path to crasher_data.db")
    parser.add_argument(
        "--mode",
        choices=["backtest", "analyze", "signals", "all"],
        default="all",
        help="Mode: backtest, analyze data, analyze signals, or all",
    )
    parser.add_argument(
        "--fast", action="store_true", help="Use reduced config grid for faster testing"
    )
    parser.add_argument(
        "--max-configs", type=int, default=500, help="Maximum configs to test"
    )
    parser.add_argument(
        "--top", type=int, default=20, help="Number of top results to show"
    )
    parser.add_argument(
        "--export", action="store_true", help="Export best config to JSON"
    )
    parser.add_argument(
        "--sort-by",
        choices=[
            "profit",
            "roi",
            "winrate",
            "score",
            "sharpe",
            "drawdown",
            "profit_factor",
        ],
        default="profit",
        help="Sort results by metric (default: profit)",
    )
    args = parser.parse_args()

    # Find database
    db_path = args.db or get_db_path()
    print(f"Using database: {db_path}")

    if not Path(db_path).exists():
        print(f"ERROR: Database not found at {db_path}")
        print("Please specify the path with --db or run the bot first to collect data.")
        sys.exit(1)

    # Load data
    print("Loading session data...")
    sessions = load_sessions(db_path)
    if not sessions:
        print("ERROR: No session data found in database.")
        sys.exit(1)

    total_rounds = sum(len(s.multipliers) for s in sessions)
    print(f"Loaded {len(sessions)} sessions with {total_rounds} total rounds")

    # Run requested mode
    if args.mode in ["analyze", "all"]:
        print("\n" + "=" * 60)
        print("DATA ANALYSIS")
        print("=" * 60)
        stats = analyze_data(sessions)
        for k, v in stats.items():
            if isinstance(v, float):
                print(f"  {k}: {v:.4f}")
            else:
                print(f"  {k}: {v}")

    if args.mode in ["signals", "all"]:
        analyze_signals(sessions, verbose=True)

    if args.mode in ["backtest", "all"]:
        grid = CONFIG_GRID_FAST if args.fast else CONFIG_GRID
        results = run_grid_search(
            sessions, grid, max_configs=args.max_configs, sort_by=args.sort_by
        )
        print_results(results, top_n=args.top)

        if args.export and results:
            export_best_config(results[0])

    print("\n" + "=" * 60)
    print("BACKTEST COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    main()
