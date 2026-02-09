"""Hotstreak detection and pattern analysis."""

import logging
from typing import List, Optional

import numpy as np

logger = logging.getLogger(__name__)

# ── Thresholds ──────────────────────────────────────────────────────
HOTSTREAK_WEAK_PCT = 0.65
HOTSTREAK_STRONG_PCT = 0.75
HOTSTREAK_MIN_WINDOW = 10
HOTSTREAK_MAX_WINDOW = 15
COLD_STREAK_LENGTH = 5
HISTORY_SIZE = 50


class HotstreakTracker:
    """Detects hotstreaks and cold streaks in multiplier history."""

    def __init__(self):
        self.recent: List[float] = []
        self.current_hotstreak: Optional[dict] = None
        self.last_hotstreak: Optional[dict] = None
        self.hotstreak_end_round = 0
        self.current_round = 0
        self.rounds_after_hotstreak = 0
        self.cold_streak_occurred = False
        self._cold_count = 0
        self.last_signal_round = 0

    def add_multiplier(self, multiplier: float):
        self.current_round += 1
        self.recent.append(multiplier)
        if len(self.recent) > HISTORY_SIZE:
            self.recent.pop(0)
        self._detect_hotstreak()
        self._track_cold(multiplier)

    # ── Queries ─────────────────────────────────────────────────────

    def get_last_n(self, n: int) -> List[float]:
        return self.recent[-n:] if len(self.recent) >= n else []

    def in_hotstreak(self) -> bool:
        return self.current_hotstreak is not None

    def just_ended_hotstreak(self) -> bool:
        return self.last_hotstreak is not None and self.rounds_after_hotstreak <= 15

    def mark_signal(self):
        self.last_signal_round = self.current_round

    # ── Detection ───────────────────────────────────────────────────

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
                    self.current_hotstreak = {
                        "type": stype,
                        "length": ws,
                        "average": avg,
                        "start_round": self.current_round - ws + 1,
                        "multipliers": window.copy(),
                    }
                    logger.info("%s hotstreak detected!", stype.capitalize())
                else:
                    self.current_hotstreak.update(
                        type=stype, length=ws, average=avg, multipliers=window.copy()
                    )
                return

        if self.current_hotstreak is not None:
            self.last_hotstreak = self.current_hotstreak.copy()
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


# ── Signal analysis helpers ─────────────────────────────────────────

def analyze_window(window: List[float], window_size: int) -> List[str]:
    """Analyze a multiplier window and return list of detected signal names."""
    signals = []
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
    """Check chain patterns after hotstreak ends. Returns signal names."""
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
                if ls["type"] == "strong" and ls["average"] > 6.0:
                    signals.append("dead_ass_chain")
                else:
                    signals.append("possible_chain")

    if ra == 15 and not tracker.cold_streak_occurred:
        signals.append("rule_of_17")

    return signals
