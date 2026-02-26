#!/usr/bin/env python3
"""
Primary Strategy Optimizer

For each multiplier (2x–20x), searches every valid trigger_count and finds
the configuration that produced the highest historical profit while never
going bust in the recorded data.

How it works
────────────
  1. Load historical rounds from the database.
  2. For each cashout multiplier:
       a. Find max_streak  – longest run of consecutive rounds UNDER threshold.
       b. For each trigger_count from 1 → max_streak:
            • required_mr  = max_streak − trigger_count + 1
              (minimum martingale rounds needed to be bust-free in history)
            • base_bet     = min(
                floor_to_step( bank × (bm−1) / (bm^req_mr − 1) ),
                bank × max_base_pct / 100
              )
              The max_base_pct cap (default 1 %) keeps base_bet realistic and
              prevents the optimizer from recommending a single all-in bet.
            • Simulate the full history, count profit / busts
       c. Keep only bust-free configs; rank by total simulated profit.
       d. Report the best, plus a table of all viable alternatives.

Why the cap matters
───────────────────
  Without a cap, the optimizer always picks trigger_count = max_streak and
  bets the entire bank on a single round (1 fire in 36k rounds → highest raw
  profit but completely impractical).  The cap forces the optimizer to compare
  configs that fire regularly with realistic bet sizes.

Usage
─────
    python primary_strategy_optimizer.py --bank 2600000
    python primary_strategy_optimizer.py --bank 2600000 --max-base-pct 0.5
    python primary_strategy_optimizer.py --bank 1000000 --multipliers 2,3,4,5
    python primary_strategy_optimizer.py --bank 2600000 --top 5 --export
    python primary_strategy_optimizer.py --bank 2600000 --min-fired 10
"""

import argparse
import json
import math
import sqlite3
import sys
from pathlib import Path
from typing import List, Optional, Tuple

# ─────────────────────────────────────────────────────────────
# DATA LOADING
# ─────────────────────────────────────────────────────────────


def load_multipliers(db_path: str) -> List[float]:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT multiplier FROM multipliers ORDER BY id ASC")
    mults = [row[0] for row in cur.fetchall()]
    conn.close()
    return mults


# ─────────────────────────────────────────────────────────────
# STREAK ANALYSIS
# ─────────────────────────────────────────────────────────────


def streak_stats(mults: List[float], threshold: float) -> dict:
    """Count / percentile stats for consecutive runs under threshold."""
    lengths, current = [], 0
    for m in mults:
        if m < threshold:
            current += 1
        else:
            if current:
                lengths.append(current)
            current = 0
    if current:
        lengths.append(current)

    if not lengths:
        return {"count": 0, "mean": 0.0, "p90": 0, "p95": 0, "p99": 0, "max": 0}
    s = sorted(lengths)
    n = len(s)
    return {
        "count": n,
        "mean": round(sum(s) / n, 2),
        "p90": s[int(n * 0.90)],
        "p95": s[int(n * 0.95)],
        "p99": s[int(n * 0.99)],
        "max": s[-1],
    }


# ─────────────────────────────────────────────────────────────
# MATH HELPERS
# ─────────────────────────────────────────────────────────────


def bet_mult_for(cashout: float) -> float:
    """Minimum bet multiplier to cover all prior losses in one win: cashout/(cashout-1)."""
    return cashout / (cashout - 1)


def geo_sum(base: float, ratio: float, n: int) -> float:
    """Total cost of n consecutive martingale bets: base*(ratio^n-1)/(ratio-1)."""
    if ratio == 1.0:
        return base * n
    return base * (ratio**n - 1) / (ratio - 1)


def base_bet_exact(bank: float, bm: float, n: int) -> float:
    """
    Largest base_bet so that losing n consecutive martingale bets costs exactly bank.
    Inverse of geo_sum: base = bank * (bm-1) / (bm^n - 1)
    """
    if n <= 0:
        return bank
    if bm == 1.0:
        return bank / n
    return bank * (bm - 1) / (bm**n - 1)


def round_down_to_step(value: float, min_bet: float) -> float:
    """
    Round value DOWN to the nearest clean increment, then enforce min_bet floor.
      >= 10 000  -> nearest 500
      1 000 - 9 999  -> nearest 250
        100 -   999  -> nearest 50
         <  100      -> nearest 10
    """
    if value >= 10_000:
        step = 500
    elif value >= 1_000:
        step = 250
    elif value >= 100:
        step = 50
    else:
        step = 10
    rounded = math.floor(value / step) * step
    return max(min_bet, float(rounded))


def rounds_covered(bank: float, base: float, bm: float) -> int:
    """How many consecutive martingale losses fit inside bank at this base_bet?"""
    total, n, bet = 0.0, 0, base
    while total + bet <= bank:
        total += bet
        n += 1
        bet *= bm
    return n


# ─────────────────────────────────────────────────────────────
# SIMULATION
# ─────────────────────────────────────────────────────────────


def simulate(
    mults: List[float],
    cashout: float,
    threshold: float,
    trigger_count: int,
    base_bet: float,
    bet_mult: float,
    max_mr: int,
) -> Tuple[float, int, int]:
    """
    Replay the full round history with this strategy.

    Returns (total_profit, total_fired, busts).

    Mechanics
    ---------
    * Count consecutive rounds < threshold as the 'streak'.
    * When streak reaches exactly trigger_count, enter martingale:
        - Bet base_bet on the next round.
        - Win (round >= cashout): profit += bet*(cashout-1) - losses_so_far; streak resets.
        - Lose (round < cashout): losses accumulate; bet multiplied by bet_mult.
        - Exhaust max_mr without a win: bust; profit -= losses_so_far; streak resets.
    * Rounds consumed during a martingale sequence do NOT count towards the next streak.
    """
    total_profit = 0.0
    total_fired = 0
    busts = 0
    streak = 0
    i = 0
    n = len(mults)

    while i < n:
        m = mults[i]
        if m < threshold:
            streak += 1
        else:
            streak = 0
        i += 1

        if streak == trigger_count:
            # Trigger fires
            total_fired += 1
            bet = base_bet
            lost_so_far = 0.0
            won = False

            for _ in range(max_mr):
                if i >= n:
                    break
                m2 = mults[i]
                i += 1

                if m2 >= cashout:
                    total_profit += bet * (cashout - 1) - lost_so_far
                    won = True
                    streak = 0
                    break
                else:
                    lost_so_far += bet
                    bet *= bet_mult

            if not won:
                total_profit -= lost_so_far
                busts += 1
                streak = 0

    return total_profit, total_fired, busts


# ─────────────────────────────────────────────────────────────
# OPTIMIZER  (core search for one multiplier)
# ─────────────────────────────────────────────────────────────


def optimize_one(
    mults: List[float],
    cashout: int,
    bank: float,
    min_bet: float,
    max_base_pct: float,
    top_n: int,
    mr_factor: Optional[int] = None,
    min_fired: int = 1,
) -> dict:
    """
    Search all valid trigger_count values for this cashout multiplier.

    Two sizing modes, selected by mr_factor:

    DEFAULT mode  (mr_factor is None)
    ──────────────────────────────────
      For each trigger_count:
        required_mr = max_streak - trigger_count + 1
        base_bet    = min(
                        floor_to_step(bank x (bm-1) / (bm^req_mr - 1)),
                        bank x max_base_pct / 100
                      )
      base_bet varies per trigger_count.
      max_base_pct prevents the optimizer from recommending a single all-in bet.

    FIXED-MR mode  (mr_factor is an integer, e.g. 11)
    ───────────────────────────────────────────────────
      target_mr = cashout x mr_factor           (e.g. 4x -> 44 rounds)
      base_bet  = floor_to_step(bank x (bm-1) / (bm^target_mr - 1))
                  floored to min_bet if the exact value falls below it.
      base_bet is computed ONCE and held fixed across all trigger_counts.
      trigger_count is still searched 1 -> max_streak to maximise profit.
      Configs that bust in the simulation are excluded.

    min_fired filters out configs that triggered fewer than this many times
    in the full history (avoids recommending strategies that barely activated).

    Returns a dict with 'best' config and 'all_viable' sorted by profit.
    """
    threshold = cashout + 0.01
    bm = bet_mult_for(float(cashout))
    ss = streak_stats(mults, threshold)
    max_streak = ss["max"]

    meta: dict = {
        "max_base_pct": max_base_pct,
        "mr_factor": mr_factor,
        "min_fired": min_fired,
    }

    if max_streak == 0:
        return {
            "cashout": cashout,
            "bm": round(bm, 6),
            "max_streak": 0,
            "streak_stats": ss,
            "best": None,
            "all_viable": [],
            **meta,
        }

    n_rounds = len(mults)
    viable = []
    rounds_to_cover = max_streak + 1
    min_trigger_count = rounds_covered(bank, min_bet, bm)

    # -- Fixed-MR mode: compute base_bet once
    if mr_factor is not None:
        rounds_to_cover = cashout * mr_factor

    for tc in range(min_trigger_count, rounds_to_cover):
        if mr_factor is not None:
            req_mr = tc
            exact_base = base_bet_exact(bank, bm, rounds_to_cover - tc)
            base_bet = round_down_to_step(exact_base, min_bet)
            actual_mr = rounds_covered(bank, base_bet, bm)
        else:
            req_mr = tc + 1
            max_base = bank * max_base_pct / 100.0
            exact_base = min(base_bet_exact(bank, bm, req_mr), max_base)
            base_bet = round_down_to_step(exact_base, min_bet)
            actual_mr = rounds_covered(bank, base_bet, bm)

        profit, fired, busts = simulate(
            mults, float(cashout), threshold, tc, base_bet, bm, actual_mr
        )

        entry = {
            "trigger_count": tc,
            "base_bet": base_bet,
            "req_mr": req_mr,
            "actual_mr": actual_mr,
            "fired": fired,
            "busts": busts,
            "profit": round(profit, 2),
            "profit_per_1k": round(profit * 1000 / n_rounds, 2),
            "profit_per_fire": round(profit / fired, 2) if fired else 0.0,
            "worst_cost": round(geo_sum(base_bet, bm, actual_mr), 2),
            "bank_pct": round(geo_sum(base_bet, bm, actual_mr) / bank * 100, 2),
            "profit_per_win": round(base_bet * (float(cashout) - 1), 2),
        }

        if busts == 0 and fired >= min_fired:
            viable.append(entry)

    # Sort by total simulated profit (descending)
    viable.sort(key=lambda x: x["profit"], reverse=True)
    best = viable[0] if viable else None

    return {
        "cashout": cashout,
        "bm": round(bm, 6),
        "max_streak": max_streak,
        "streak_stats": ss,
        "best": best,
        "all_viable": viable[:top_n],
        **meta,
    }


# ─────────────────────────────────────────────────────────────
# OUTPUT
# ─────────────────────────────────────────────────────────────


def print_streak_table(mults: List[float], cashout_list: List[int]):
    print()
    print("=" * 76)
    print("  HISTORICAL STREAK SUMMARY  (consecutive rounds UNDER threshold)")
    print("=" * 76)
    print(
        f"  {'Mult':>6}  {'Thresh':>7}  {'Count':>6}  "
        f"{'Mean':>6}  {'p90':>5}  {'p95':>5}  {'p99':>5}  {'Max':>5}"
    )
    print(f"  {'─' * 72}")
    for co in cashout_list:
        ss = streak_stats(mults, co + 0.01)
        print(
            f"  {co:>5}x  {co + 0.01:>7.2f}  {ss['count']:>6}  "
            f"{ss['mean']:>6.1f}  {ss['p90']:>5}  {ss['p95']:>5}  "
            f"{ss['p99']:>5}  {ss['max']:>5}"
        )
    print()


def print_summary_table(results: List[dict], bank: float):
    print()
    print("=" * 120)
    print(
        f"  SIMULATION RESULTS  |  Bank: {bank:,.0f}  "
        f"  (best bust-free config per multiplier, ranked by total simulated profit)"
    )
    print("=" * 120)
    print(
        f"  {'#':>3}  {'Mult':>6}  {'TrigCnt':>7}  {'BaseBet':>10}  "
        f"{'BetMult':>9}  {'ReqMR':>6}  {'ActMR':>6}  "
        f"{'Fired':>6}  {'Profit1W':>10}  {'TotalProfit':>13}  "
        f"{'P/1k rnd':>10}  {'Bank%':>6}"
    )
    print(f"  {'─' * 116}")

    for i, r in enumerate(results):
        b = r["best"]
        if b is None:
            print(f"  {i + 1:>3}  {r['cashout']:>5}x  {'NO VIABLE CONFIG':>80}")
            continue
        print(
            f"  {i + 1:>3}  {r['cashout']:>5}x  "
            f"{b['trigger_count']:>7}  "
            f"{b['base_bet']:>10,.0f}  "
            f"{r['bm']:>9.6f}  "
            f"{b['req_mr']:>6}  "
            f"{b['actual_mr']:>6}  "
            f"{b['fired']:>6}  "
            f"{b['profit_per_win']:>10,.0f}  "
            f"{b['profit']:>13,.0f}  "
            f"{b['profit_per_1k']:>10,.1f}  "
            f"{b['bank_pct']:>5.1f}%"
        )

    print()
    print("=" * 120)
    print()
    print("  Column guide:")
    print(
        "    TrigCnt  = consecutive under-threshold rounds to wait before entering martingale"
    )
    print(
        "    BaseBet  = first martingale bet (sized so losing ReqMR rounds costs approximately bank)"
    )
    print(
        "    BetMult  = multiplier applied to bet after each loss (= cashout/(cashout-1))"
    )
    print("    ReqMR    = min martingale rounds needed to never bust in history")
    print("    ActMR    = actual rounds the bank covers at BaseBet (after rounding)")
    print("    Fired    = how many times the trigger fired in 36k rounds of history")
    print(
        "    Profit1W = profit if first martingale bet wins (= BaseBet x (cashout-1))"
    )
    print("    TotalProfit = net simulated profit over full history")
    print(
        "    P/1k rnd = TotalProfit normalised per 1 000 rounds (annualised rate proxy)"
    )
    print("    Bank%    = worst-case cost as % of bank (losing ALL ActMR bets)")
    print()


def print_alternatives(results: List[dict]):
    """Print all viable alternatives for each multiplier."""
    for r in results:
        co = r["cashout"]
        via = r["all_viable"]
        if not via:
            continue
        if len(via) <= 1:
            continue  # nothing extra to show
        print(f"  {co}x alternatives (bust-free, sorted by profit):")
        print(
            f"    {'tc':>4}  {'base_bet':>10}  {'req_mr':>7}  {'act_mr':>7}  "
            f"{'fired':>6}  {'profit1w':>10}  {'total_profit':>14}  {'p/1k':>10}"
        )
        for e in via:
            marker = " <- best" if e is r["best"] else ""
            print(
                f"    {e['trigger_count']:>4}  "
                f"{e['base_bet']:>10,.0f}  "
                f"{e['req_mr']:>7}  "
                f"{e['actual_mr']:>7}  "
                f"{e['fired']:>6}  "
                f"{e['profit_per_win']:>10,.0f}  "
                f"{e['profit']:>14,.0f}  "
                f"{e['profit_per_1k']:>10,.1f}{marker}"
            )
        print()


def export_configs(results: List[dict], bank: float, path: str):
    strategies = []
    for r in results:
        b = r["best"]
        co = r["cashout"]
        if b is None:
            continue
        strategies.append(
            {
                "name": f"{co}x_Strategy",
                "base_bet": b["base_bet"],
                "auto_cashout": float(co),
                "trigger_threshold": round(co + 0.01, 2),
                "trigger_count": b["trigger_count"],
                "max_consecutive_losses": b["actual_mr"],
                "bet_multiplier": r["bm"],
                "enabled": True,
            }
        )
    out = {
        "_meta": {
            "bank": bank,
            "total_strategies": len(strategies),
            "note": "Generated by primary_strategy_optimizer.py via simulation",
        },
        "strategies": strategies,
    }
    with open(path, "w") as f:
        json.dump(out, f, indent=2)
    print(f"Exported {len(strategies)} strategies -> {path}")


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────


def get_db_path() -> str:
    for p in [
        Path("./crasher_data.db"),
        Path.home() / ".crasher_bot" / "crasher_data.db",
    ]:
        if p.exists():
            return str(p)
    return "./crasher_data.db"


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Simulate every valid (trigger_count, base_bet) pair against historical "
            "data and find the most profitable bust-free config for each multiplier."
        )
    )
    parser.add_argument(
        "--bank",
        type=float,
        required=True,
        help="Your total bank/balance (e.g. 2600000)",
    )
    parser.add_argument("--db", default=None, help="Path to crasher_data.db")
    parser.add_argument(
        "--min-bet",
        type=float,
        default=1000,
        help="Minimum base bet floor (default: 1000)",
    )
    parser.add_argument(
        "--max-base-pct",
        type=float,
        default=100.0,
        help="Maximum base_bet as %% of bank (default: 1.0). "
        "Prevents optimizer from recommending all-in single bets. "
        "Lower = safer / more frequent triggers. "
        "Example: --max-base-pct 0.5  (cap at 0.5%% of bank). "
        "Ignored when --mr-factor is set.",
    )
    parser.add_argument(
        "--mr-factor",
        type=int,
        default=None,
        help="Fixed martingale-rounds factor: target_mr = cashout x mr_factor. "
        "Example: --mr-factor 11 sizes base_bet so the bank covers "
        "cashout x 11 consecutive losses (44 rounds for 4x, 33 for 3x, etc.). "
        "base_bet is fixed per multiplier; trigger_count is still optimised "
        "by simulation. Overrides --max-base-pct.",
    )
    parser.add_argument(
        "--min-fired",
        type=int,
        default=10,
        help="Minimum number of times a config must have fired in the historical "
        "data to be considered viable (default: 1). "
        "Raise this to exclude rarely-triggered strategies. "
        "Example: --min-fired 10  (must have fired at least 10 times).",
    )
    parser.add_argument(
        "--multipliers",
        default=None,
        help="Comma-separated cashout multipliers "
        "(default: 2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20)",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=20,
        help="Show top N alternatives per multiplier (default: 5)",
    )
    parser.add_argument(
        "--alternatives",
        action="store_true",
        help="Print full alternative-configs table for each multiplier",
    )
    parser.add_argument(
        "--streaks-only",
        action="store_true",
        help="Print streak summary table and exit",
    )
    parser.add_argument(
        "--export",
        action="store_true",
        help="Export best configs to primary_strategy_configs.json",
    )
    args = parser.parse_args()

    # Parse multiplier list
    if args.multipliers:
        try:
            cashout_list = [int(x.strip()) for x in args.multipliers.split(",")]
        except ValueError:
            print("ERROR: --multipliers must be comma-separated integers, e.g. 2,3,4,5")
            sys.exit(1)
    else:
        cashout_list = list(range(2, 21))

    db_path = args.db or get_db_path()
    if not Path(db_path).exists():
        print(f"ERROR: Database not found at {db_path}")
        sys.exit(1)

    print(f"Loading multiplier history from: {db_path}")
    mults = load_multipliers(db_path)
    if not mults:
        print("ERROR: No multiplier data found in database.")
        sys.exit(1)
    print(f"Loaded {len(mults):,} rounds.\n")

    print_streak_table(mults, cashout_list)

    if args.streaks_only:
        return

    if args.mr_factor is not None:
        mode_str = f"fixed-MR mode: target_mr = cashout x {args.mr_factor}"
    else:
        max_base = args.bank * args.max_base_pct / 100
        mode_str = (
            f"adaptive mode: max_base_bet={max_base:,.0f} "
            f"({args.max_base_pct:.2f}% of bank)"
        )

    print(
        f"Simulating strategies for bank={args.bank:,.0f}  "
        f"min_bet={args.min_bet:,.0f}  "
        f"min_fired={args.min_fired}  "
        f"{mode_str}  "
        f"multipliers={cashout_list[0]}x-{cashout_list[-1]}x\n"
    )

    results = []
    for co in cashout_list:
        sys.stdout.write(f"  Optimising {co}x ... ")
        sys.stdout.flush()
        r = optimize_one(
            mults,
            co,
            args.bank,
            args.min_bet,
            args.max_base_pct,
            args.top,
            mr_factor=args.mr_factor,
            min_fired=args.min_fired,
        )
        b = r["best"]
        if b:
            sys.stdout.write(
                f"best tc={b['trigger_count']:>3}  "
                f"base_bet={b['base_bet']:>10,.0f}  "
                f"fired={b['fired']:>5}  "
                f"profit={b['profit']:>12,.0f}\n"
            )
        else:
            sys.stdout.write("no viable config found\n")
        results.append(r)

    print_summary_table(results, bank=args.bank)

    if args.alternatives:
        print()
        print("=" * 80)
        print("  ALTERNATIVE CONFIGS PER MULTIPLIER")
        print("=" * 80)
        print()
        print_alternatives(results)

    if args.export:
        out_path = str(Path(__file__).parent / "primary_strategy_configs.json")
        export_configs(results, bank=args.bank, path=out_path)

    # Ready-to-paste JSON block
    print()
    print("=" * 90)
    print("  READY-TO-PASTE  bot_config.json  'strategies'  block")
    print("=" * 90)
    print()
    strategies_json = []
    for r in results:
        b = r["best"]
        co = r["cashout"]
        if b is None:
            continue
        strategies_json.append(
            {
                "name": f"{co}x_Strategy",
                "base_bet": b["base_bet"],
                "auto_cashout": float(co),
                "trigger_threshold": round(co + 0.01, 2),
                "trigger_count": b["trigger_count"],
                "max_consecutive_losses": b["actual_mr"],
                "bet_multiplier": r["bm"],
                "enabled": True,
            }
        )
    print(json.dumps(strategies_json, indent=2))


if __name__ == "__main__":
    main()
# 64 + 23 + 35 +
