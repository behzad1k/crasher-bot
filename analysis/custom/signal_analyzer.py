#!/usr/bin/env python3
"""
Signal Outcome Analyzer

This script analyzes what happens AFTER each signal fires, helping you understand
which signals are most predictive of winning conditions.

Usage:
    python signal_analyzer.py [--db path/to/crasher_data.db]
"""

import argparse
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import json

import numpy as np

# Import shared logic from backtest_simulator
from backtest_simulator import (
    HotstreakTracker,
    analyze_window,
    check_chain_patterns,
    HOTSTREAK_MIN_WINDOW,
)


@dataclass
class SignalEvent:
    """Records a signal and what happened after it."""
    session_id: int
    round_index: int
    signal_type: str
    mult_at_signal: float
    window_avg: float
    window_std: float
    
    # What happened in the next N rounds
    next_5: List[float] = field(default_factory=list)
    next_10: List[float] = field(default_factory=list)
    next_20: List[float] = field(default_factory=list)
    
    def analyze_outcome(self, cashout_target: float) -> Dict:
        """Analyze if betting after this signal would have been profitable."""
        results = {}
        
        for window_name, window in [("next_5", self.next_5), ("next_10", self.next_10), ("next_20", self.next_20)]:
            if not window:
                continue
            
            # Simulate martingale betting
            total_profit = 0
            base_bet = 1000
            current_bet = base_bet
            bets = 0
            wins = 0
            max_streak = 0
            current_streak = 0
            
            for mult in window:
                bets += 1
                if mult >= cashout_target:
                    # Win
                    profit = current_bet * (cashout_target - 1)
                    total_profit += profit
                    wins += 1
                    current_streak = 0
                    current_bet = base_bet  # Reset
                else:
                    # Loss
                    total_profit -= current_bet
                    current_streak += 1
                    max_streak = max(max_streak, current_streak)
                    current_bet *= 2  # Martingale
            
            results[window_name] = {
                "rounds": len(window),
                "wins": wins,
                "win_rate": wins / len(window) if window else 0,
                "total_profit": total_profit,
                "max_loss_streak": max_streak,
                "above_target": sum(1 for m in window if m >= cashout_target),
                "avg_mult": np.mean(window),
            }
        
        return results


@dataclass
class SessionData:
    session_id: int
    multipliers: List[float]


def load_sessions(db_path: str) -> List[SessionData]:
    """Load session data from SQLite database."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT s.id, COUNT(m.id)
        FROM sessions s
        LEFT JOIN multipliers m ON s.id = m.session_id
        GROUP BY s.id
        ORDER BY s.id ASC
    """)
    sessions_info = cursor.fetchall()
    
    sessions = []
    for sid, count in sessions_info:
        if count == 0:
            continue
        
        cursor.execute("""
            SELECT multiplier FROM multipliers 
            WHERE session_id = ? 
            ORDER BY id ASC
        """, (sid,))
        mults = [row[0] for row in cursor.fetchall()]
        
        sessions.append(SessionData(session_id=sid, multipliers=mults))
    
    conn.close()
    return sessions


def detect_all_signals(sessions: List[SessionData]) -> List[SignalEvent]:
    """Detect all signals in historical data and capture what follows."""
    all_signals = []
    
    for session in sessions:
        tracker = HotstreakTracker()
        mults = session.multipliers
        
        for i, mult in enumerate(mults):
            tracker.add_multiplier(mult)
            round_idx = i
            
            # Get following multipliers (within same session only)
            remaining = mults[i + 1:]
            next_5 = remaining[:5]
            next_10 = remaining[:10]
            next_20 = remaining[:20]
            
            window_10 = tracker.get_last_n(10)
            window_15 = tracker.get_last_n(15)
            
            # Check hotstreaks
            if tracker.current_hotstreak:
                hs = tracker.current_hotstreak
                if hs.length == HOTSTREAK_MIN_WINDOW:
                    sig = SignalEvent(
                        session_id=session.session_id,
                        round_index=round_idx,
                        signal_type=f"{hs.type}_hotstreak",
                        mult_at_signal=mult,
                        window_avg=hs.average,
                        window_std=np.std(hs.multipliers) if hs.multipliers else 0,
                        next_5=next_5,
                        next_10=next_10,
                        next_20=next_20,
                    )
                    all_signals.append(sig)
            
            # Check pattern signals (window 10)
            if len(window_10) >= 10:
                signals = analyze_window(window_10, 10)
                for sig_type in signals:
                    sig = SignalEvent(
                        session_id=session.session_id,
                        round_index=round_idx,
                        signal_type=f"{sig_type}_w10",
                        mult_at_signal=mult,
                        window_avg=np.mean(window_10),
                        window_std=np.std(window_10),
                        next_5=next_5,
                        next_10=next_10,
                        next_20=next_20,
                    )
                    all_signals.append(sig)
            
            # Check pattern signals (window 15)
            if len(window_15) >= 15:
                signals = analyze_window(window_15, 15)
                for sig_type in signals:
                    sig = SignalEvent(
                        session_id=session.session_id,
                        round_index=round_idx,
                        signal_type=f"{sig_type}_w15",
                        mult_at_signal=mult,
                        window_avg=np.mean(window_15),
                        window_std=np.std(window_15),
                        next_5=next_5,
                        next_10=next_10,
                        next_20=next_20,
                    )
                    all_signals.append(sig)
            
            # Check chain patterns
            if tracker.just_ended_hotstreak():
                chain_signals = check_chain_patterns(tracker)
                for sig_type in chain_signals:
                    sig = SignalEvent(
                        session_id=session.session_id,
                        round_index=round_idx,
                        signal_type=sig_type,
                        mult_at_signal=mult,
                        window_avg=np.mean(window_10) if window_10 else 0,
                        window_std=np.std(window_10) if window_10 else 0,
                        next_5=next_5,
                        next_10=next_10,
                        next_20=next_20,
                    )
                    all_signals.append(sig)
    
    return all_signals


def analyze_signal_effectiveness(signals: List[SignalEvent], cashout_targets: List[float] = None) -> Dict:
    """Analyze how effective each signal type is for different cashout targets."""
    if cashout_targets is None:
        cashout_targets = [2.0, 3.0, 4.0, 5.0]
    
    # Group signals by type
    by_type: Dict[str, List[SignalEvent]] = {}
    for sig in signals:
        if sig.signal_type not in by_type:
            by_type[sig.signal_type] = []
        by_type[sig.signal_type].append(sig)
    
    results = {}
    
    for sig_type, type_signals in by_type.items():
        results[sig_type] = {
            "count": len(type_signals),
            "targets": {},
        }
        
        for target in cashout_targets:
            target_results = {
                "next_5": {"total_profit": 0, "total_wins": 0, "total_rounds": 0},
                "next_10": {"total_profit": 0, "total_wins": 0, "total_rounds": 0},
                "next_20": {"total_profit": 0, "total_wins": 0, "total_rounds": 0},
            }
            
            for sig in type_signals:
                outcome = sig.analyze_outcome(target)
                for window_name in ["next_5", "next_10", "next_20"]:
                    if window_name in outcome:
                        o = outcome[window_name]
                        target_results[window_name]["total_profit"] += o["total_profit"]
                        target_results[window_name]["total_wins"] += o["wins"]
                        target_results[window_name]["total_rounds"] += o["rounds"]
            
            # Calculate averages
            for window_name in target_results:
                tr = target_results[window_name]
                tr["avg_profit_per_signal"] = tr["total_profit"] / len(type_signals) if type_signals else 0
                tr["win_rate"] = tr["total_wins"] / tr["total_rounds"] if tr["total_rounds"] > 0 else 0
            
            results[sig_type]["targets"][target] = target_results
    
    return results


def print_signal_report(analysis: Dict, signals: List[SignalEvent]):
    """Print a detailed report of signal effectiveness."""
    print("\n" + "=" * 100)
    print("SIGNAL EFFECTIVENESS REPORT")
    print("=" * 100)
    
    # Overall stats
    print(f"\nTotal signals detected: {len(signals)}")
    print("\nSignal frequency:")
    for sig_type in sorted(analysis.keys(), key=lambda x: analysis[x]["count"], reverse=True):
        print(f"  {sig_type}: {analysis[sig_type]['count']}")
    
    # Detailed analysis by target
    for target in [2.0, 3.0, 5.0]:
        print(f"\n{'─' * 100}")
        print(f"TARGET: {target}x CASHOUT")
        print(f"{'─' * 100}")
        
        # Rank signals by profitability
        signal_scores = []
        for sig_type, data in analysis.items():
            if target not in data["targets"]:
                continue
            t = data["targets"][target]
            # Use next_10 as primary metric
            if t["next_10"]["total_rounds"] > 0:
                score = t["next_10"]["avg_profit_per_signal"]
                win_rate = t["next_10"]["win_rate"]
                signal_scores.append((sig_type, score, win_rate, data["count"]))
        
        signal_scores.sort(key=lambda x: x[1], reverse=True)
        
        print(f"\n{'Signal Type':<30} {'Count':>8} {'Avg Profit':>12} {'Win Rate':>10}")
        print("-" * 70)
        for sig_type, score, win_rate, count in signal_scores:
            profit_str = f"{score:+,.0f}" if score != 0 else "0"
            print(f"{sig_type:<30} {count:>8} {profit_str:>12} {win_rate*100:>9.1f}%")
    
    # Best signal combinations
    print(f"\n{'=' * 100}")
    print("RECOMMENDED SIGNAL PRIORITIES")
    print("=" * 100)
    
    for target in [2.0, 3.0, 5.0]:
        print(f"\nFor {target}x cashout:")
        profitable = []
        for sig_type, data in analysis.items():
            if target not in data["targets"]:
                continue
            t = data["targets"][target]
            if t["next_10"]["avg_profit_per_signal"] > 0:
                profitable.append((sig_type, t["next_10"]["avg_profit_per_signal"]))
        
        profitable.sort(key=lambda x: x[1], reverse=True)
        if profitable:
            for i, (sig, prof) in enumerate(profitable[:5], 1):
                print(f"  {i}. {sig}: +{prof:,.0f} avg profit per signal")
        else:
            print("  No consistently profitable signals found")


def export_detailed_signals(signals: List[SignalEvent], output_path: str):
    """Export all signals with their outcomes to JSON for further analysis."""
    export_data = []
    
    for sig in signals:
        outcomes = sig.analyze_outcome(2.0)  # Default to 2x
        outcomes_3x = sig.analyze_outcome(3.0)
        outcomes_5x = sig.analyze_outcome(5.0)
        
        export_data.append({
            "session_id": sig.session_id,
            "round_index": sig.round_index,
            "signal_type": sig.signal_type,
            "mult_at_signal": sig.mult_at_signal,
            "window_avg": sig.window_avg,
            "window_std": sig.window_std,
            "next_5": sig.next_5,
            "next_10": sig.next_10,
            "next_20": sig.next_20,
            "outcome_2x": outcomes,
            "outcome_3x": outcomes_3x,
            "outcome_5x": outcomes_5x,
        })
    
    with open(output_path, "w") as f:
        json.dump(export_data, f, indent=2)
    
    print(f"\nDetailed signal data exported to: {output_path}")


def find_optimal_confirmation(signals: List[SignalEvent]) -> Dict:
    """
    Find optimal signal confirmation parameters by testing different
    confirmation window sizes and thresholds.
    """
    print("\n" + "=" * 100)
    print("OPTIMAL CONFIRMATION ANALYSIS")
    print("=" * 100)
    
    # Test different confirmation parameters
    thresholds = [1.5, 2.0, 2.5, 3.0]
    counts = [2, 3, 4]
    windows = [3, 4, 5, 6]
    
    results = []
    
    for thresh in thresholds:
        for count in counts:
            for window in windows:
                if count > window:
                    continue
                
                # For each signal, check if confirmation would have predicted success
                predictions = []
                
                for sig in signals:
                    # Check if next_5 would have confirmed
                    confirm_window = sig.next_5[:window] if len(sig.next_5) >= window else []
                    if not confirm_window:
                        continue
                    
                    above = sum(1 for m in confirm_window if m >= thresh)
                    confirmed = above >= count
                    
                    # Check if betting after confirmation would succeed (using next rounds after confirmation)
                    betting_window = sig.next_5[window:] + sig.next_10[5:] if len(sig.next_5) > window else []
                    if not betting_window:
                        continue
                    
                    # Success = at least one 2x+ in next 5 rounds after confirmation
                    success = any(m >= 2.0 for m in betting_window[:5])
                    
                    predictions.append((confirmed, success))
                
                if not predictions:
                    continue
                
                # Calculate prediction accuracy
                confirmed_signals = [p for p in predictions if p[0]]
                if confirmed_signals:
                    true_positive = sum(1 for c, s in confirmed_signals if s)
                    precision = true_positive / len(confirmed_signals)
                else:
                    precision = 0
                
                all_success = [p for p in predictions if p[1]]
                if all_success:
                    true_positive = sum(1 for c, s in predictions if c and s)
                    recall = true_positive / len(all_success)
                else:
                    recall = 0
                
                f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
                
                results.append({
                    "threshold": thresh,
                    "count": count,
                    "window": window,
                    "precision": precision,
                    "recall": recall,
                    "f1": f1,
                    "confirmed_count": len(confirmed_signals),
                })
    
    # Sort by F1 score
    results.sort(key=lambda x: x["f1"], reverse=True)
    
    print(f"\n{'Threshold':>10} {'Count':>6} {'Window':>7} {'Precision':>10} {'Recall':>8} {'F1':>8} {'Confirmed':>10}")
    print("-" * 75)
    for r in results[:15]:
        print(f"{r['threshold']:>10.1f} {r['count']:>6} {r['window']:>7} {r['precision']:>10.2%} {r['recall']:>8.2%} {r['f1']:>8.3f} {r['confirmed_count']:>10}")
    
    if results:
        best = results[0]
        print(f"\nBest confirmation parameters:")
        print(f"  Threshold: {best['threshold']}x")
        print(f"  Count: {best['count']} rounds")
        print(f"  Window: {best['window']} rounds")
        print(f"  F1 Score: {best['f1']:.3f}")
    
    return {"results": results}


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


def main():
    parser = argparse.ArgumentParser(description="Analyze signal effectiveness")
    parser.add_argument("--db", default=None, help="Path to crasher_data.db")
    parser.add_argument("--export", default=None, help="Export detailed signals to JSON")
    parser.add_argument("--confirm-analysis", action="store_true", help="Run confirmation parameter optimization")
    args = parser.parse_args()
    
    db_path = args.db or get_db_path()
    print(f"Using database: {db_path}")
    
    if not Path(db_path).exists():
        print(f"ERROR: Database not found at {db_path}")
        sys.exit(1)
    
    print("Loading session data...")
    sessions = load_sessions(db_path)
    if not sessions:
        print("ERROR: No session data found.")
        sys.exit(1)
    
    total_rounds = sum(len(s.multipliers) for s in sessions)
    print(f"Loaded {len(sessions)} sessions with {total_rounds} total rounds")
    
    print("\nDetecting signals...")
    signals = detect_all_signals(sessions)
    print(f"Found {len(signals)} signals")
    
    print("\nAnalyzing signal effectiveness...")
    analysis = analyze_signal_effectiveness(signals)
    print_signal_report(analysis, signals)
    
    if args.confirm_analysis:
        find_optimal_confirmation(signals)
    
    if args.export:
        export_detailed_signals(signals, args.export)
    
    print("\n" + "=" * 60)
    print("ANALYSIS COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    main()
