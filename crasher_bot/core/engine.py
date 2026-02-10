"""Main bot engine – orchestrates strategies, detection, and betting."""

import logging
import queue
import time
from typing import Dict, List, Optional

from crasher_bot.config import BotConfig
from crasher_bot.core import Database
from crasher_bot.core.driver import GameDriver
from crasher_bot.core.hotstreak import (
    HotstreakTracker,
    analyze_window,
    check_chain_patterns,
)
from crasher_bot.core.session import recover_or_create
from crasher_bot.strategies import SecondaryState, StrategyState

logger = logging.getLogger(__name__)


class BotEngine:
    """Core bot loop – stateless w.r.t. GUI."""

    def __init__(self, config: BotConfig):
        self.config = config
        self.driver = GameDriver()
        self.db = Database()
        self.tracker = HotstreakTracker()

        # Strategies
        self.primaries: Dict[str, StrategyState] = {}
        self.secondary: Optional[SecondaryState] = None
        self._load_strategies()

        # Runtime state
        self.running = False
        self.autopilot = True
        self.strategy_active = False
        self.total_profit = 0.0
        self.last_seen: Optional[float] = None
        self.last_round_time = 0.0
        self.keepalive_counter = 0
        self.command_queue: queue.Queue = queue.Queue()

        # Callbacks (set by GUI or CLI)
        self.on_multiplier = None  # (float) -> None

    # ── Strategy loading ────────────────────────────────────────────

    def _load_strategies(self):
        for sc in self.config.strategies:
            if not sc.enabled:
                continue
            self.primaries[sc.name] = StrategyState(
                name=sc.name,
                base_bet=sc.base_bet,
                auto_cashout=sc.auto_cashout,
                trigger_threshold=sc.trigger_threshold,
                trigger_count=sc.trigger_count,
                max_consecutive_losses=sc.max_consecutive_losses,
                bet_multiplier=sc.bet_multiplier,
            )
        sc2 = self.config.secondary_strategy
        if sc2 and sc2.enabled:
            self.secondary = SecondaryState(
                base_bet=sc2.base_bet,
                auto_cashout=sc2.auto_cashout,
                max_consecutive_losses=sc2.max_consecutive_losses,
                bet_multiplier=sc2.bet_multiplier,
            )

    # ── Main loop ───────────────────────────────────────────────────

    def run(self):
        try:
            logger.info("=" * 60)
            logger.info("CRASHER BOT v2 STARTING")
            logger.info("=" * 60)

            if not self.driver.start():
                return
            if not self.driver.login(self.config.username, self.config.password):
                return
            if not self.driver.navigate_to_game(self.config.game_url):
                return

            time.sleep(2)
            balance = self.driver.get_balance()
            page_mults = self.driver.read_page_multipliers()

            sid = recover_or_create(
                self.db,
                page_mults,
                balance,
                self.config.import_recent_on_new_session,
            )
            self.db.current_session_id = sid

            # Seed tracker
            for m in page_mults:
                self.tracker.add_multiplier(m)
            if page_mults:
                self.last_seen = page_mults[-1]

            # Initial auto-cashout
            if self.primaries:
                first = next(iter(self.primaries.values()))
                self.driver.setup_auto_cashout(first.auto_cashout)

            self._log_strategy_summary()

            self.running = True
            active_name: Optional[str] = None
            dup_guard: Dict[str, float] = {}

            while self.running:
                self._process_commands()

                if not self._check_limits():
                    break

                mult = self.driver.detect_round_end()
                if not mult or mult == self.last_seen:
                    time.sleep(0.1)
                    continue

                now = time.time()
                if self.last_round_time and now - self.last_round_time < 3:
                    time.sleep(0.1)
                    continue
                key = f"{mult:.2f}"
                if key in dup_guard and now - dup_guard[key] < 5:
                    time.sleep(0.1)
                    continue

                # ── New round confirmed ────────────────────────────
                dup_guard[key] = now
                self.last_seen = mult
                self.last_round_time = now
                self.keepalive_counter += 1
                if len(dup_guard) > 10:
                    oldest = min(dup_guard, key=dup_guard.get)
                    del dup_guard[oldest]

                if self.keepalive_counter >= 20:
                    self.driver.click_multiplier_display()
                    self.keepalive_counter = 0

                bettors = self.driver.get_bettor_count()
                bal = self.driver.get_balance()

                parts = [f"Round: {mult}x"]
                if bettors:
                    parts.append(f"Bettors: {bettors}")
                if bal is not None:
                    parts.append(f"Bank: {bal:,.0f}")
                logger.info(" | ".join(parts))

                self.db.add_multiplier(mult, bettors)
                self.tracker.add_multiplier(mult)
                if self.on_multiplier:
                    self.on_multiplier(mult)

                # Secondary strategy
                if self.secondary:
                    self._handle_secondary(mult)

                # Hotstreak analysis (only when idle)
                if not self.strategy_active and not active_name:
                    self._analyze_signals()

                # Active primary result
                if active_name:
                    strat = self.primaries[active_name]
                    if strat.waiting_for_result:
                        active_name = self._handle_primary_result(
                            strat, mult, active_name
                        )

                # Activate new primary (if idle)
                if not active_name and not self.strategy_active and self.autopilot:
                    if not self.secondary or not self.secondary.is_active:
                        active_name = self._try_activate_primary()

                time.sleep(0.1)

        except KeyboardInterrupt:
            logger.info("Stopped by user")
        except Exception as e:
            logger.exception("Fatal error: %s", e)
        finally:
            self._shutdown()

    # ── Primary strategies ──────────────────────────────────────────

    def _try_activate_primary(self) -> Optional[str]:
        for name, s in self.primaries.items():
            if s.is_active:
                continue
            recent = self.db.get_recent_multipliers(s.trigger_count)
            if len(recent) != s.trigger_count:
                continue
            if all(m < s.trigger_threshold for m in recent):
                logger.info(
                    "[%s] TRIGGER – last %d under %sx",
                    name,
                    s.trigger_count,
                    s.trigger_threshold,
                )
                s.is_active = True
                self.strategy_active = True
                if not self.driver.setup_auto_cashout(s.auto_cashout):
                    s.is_active = False
                    self.strategy_active = False
                    continue
                time.sleep(2)
                bet = s.next_bet()
                if self.driver.place_bet(bet):
                    s.current_bet = bet
                    s.waiting_for_result = True
                    logger.info("[%s] BET %d", name, bet)
                    return name
                else:
                    s.is_active = False
                    self.strategy_active = False
        return None

    def _handle_primary_result(
        self, s: StrategyState, mult: float, active_name: str
    ) -> Optional[str]:
        if mult >= s.auto_cashout:
            profit = s.current_bet * (s.auto_cashout - 1)
            s.total_profit += profit
            self.total_profit += profit
            self.db.add_bet(s.name, s.current_bet, "win", mult, profit)
            logger.info(
                "[%s] WIN +%.0f (total: %.0f)", s.name, profit, self.total_profit
            )
            s.reset()
            self.strategy_active = False
            return None
        else:
            loss = s.current_bet
            s.total_profit -= loss
            self.total_profit -= loss
            self.db.add_bet(s.name, s.current_bet, "loss", mult, -loss)
            s.consecutive_losses += 1
            s.current_bet = s.next_bet()
            logger.info(
                "[%s] LOSS -%.0f (streak: %d, next: %.0f)",
                s.name,
                loss,
                s.consecutive_losses,
                s.current_bet,
            )
            s.waiting_for_result = False
            time.sleep(1)
            if self.driver.place_bet(s.current_bet):
                s.waiting_for_result = True
                return active_name
            else:
                logger.error("[%s] Failed to place follow-up bet", s.name)
                s.reset()
                self.strategy_active = False
                return None

    # ── Secondary strategy ──────────────────────────────────────────

    def _handle_secondary(self, mult: float):
        sec = self.secondary
        if sec.waiting_for_result:
            self._secondary_result(mult)
            return

        if sec.monitoring and not sec.is_active:
            sec.rounds_monitored += 1
            sec.monitoring_history.append(mult)
            logger.info("[Secondary] Monitor %d/21: %sx", sec.rounds_monitored, mult)

            if len(sec.monitoring_history) >= 5:
                last5 = sec.monitoring_history[-5:]
                if all(m < 2.01 for m in last5):
                    logger.info("[Secondary] Cold streak – dropping signal")
                    sec.stop_monitoring()
                    return
                above = sum(1 for m in last5 if m >= 2.0)
                if (
                    above >= 3
                    and not sec.is_active
                    and not self.strategy_active
                    and self.autopilot
                ):
                    logger.info("[Secondary] Activating (3+/5 above 2x)")
                    self._activate_secondary_betting()

            if sec.rounds_monitored >= 21:
                logger.info("[Secondary] 21 rounds – stopping monitor")
                sec.stop_monitoring()

    def _activate_secondary_betting(self):
        """Activate the secondary strategy and place the first bet."""
        sec = self.secondary
        sec.is_active = True
        self.strategy_active = True
        if not self.driver.setup_auto_cashout(sec.auto_cashout):
            sec.reset()
            sec.stop_monitoring()
            self.strategy_active = False
            return
        time.sleep(2)
        bet = sec.next_bet()
        if self.driver.place_bet(bet):
            sec.current_bet = bet
            sec.waiting_for_result = True
        else:
            sec.reset()
            sec.stop_monitoring()
            self.strategy_active = False

    def _secondary_result(self, mult: float):
        sec = self.secondary
        if sec.monitoring:
            sec.monitoring_history.append(mult)

        if mult >= sec.auto_cashout:
            profit = sec.current_bet * (sec.auto_cashout - 1)
            sec.total_profit += profit
            self.total_profit += profit
            self.db.add_bet(sec.name, sec.current_bet, "win", mult, profit)
            logger.info("[Secondary] WIN +%.0f", profit)
            sec.reset()
            sec.stop_monitoring()
            self.strategy_active = False
        else:
            loss = sec.current_bet
            sec.total_profit -= loss
            self.total_profit -= loss
            self.db.add_bet(sec.name, sec.current_bet, "loss", mult, -loss)
            sec.consecutive_losses += 1
            sec.current_bet = sec.next_bet()
            logger.info(
                "[Secondary] LOSS -%.0f (streak: %d)", loss, sec.consecutive_losses
            )

            # Cold streak while betting
            if sec.monitoring and len(sec.monitoring_history) >= 5:
                if all(m < 2.01 for m in sec.monitoring_history[-5:]):
                    logger.info("[Secondary] Cold streak while betting – stopping")
                    sec.reset()
                    sec.stop_monitoring()
                    self.strategy_active = False
                    return

            if sec.consecutive_losses >= sec.max_consecutive_losses:
                logger.info("[Secondary] Max losses – stopping")
                sec.reset()
                sec.stop_monitoring()
                self.strategy_active = False
            else:
                time.sleep(1)
                if self.driver.place_bet(sec.current_bet):
                    sec.waiting_for_result = True
                else:
                    sec.reset()
                    sec.stop_monitoring()
                    self.strategy_active = False

    # ── Signal analysis ─────────────────────────────────────────────

    def _analyze_signals(self):
        if self.strategy_active or self.tracker.in_hotstreak():
            return

        found = False
        for win_size, min_len in [(10, 10), (15, 15)]:
            window = self.tracker.get_last_n(win_size)
            if len(window) < min_len:
                continue
            signals = analyze_window(window, win_size)
            for sig in signals:
                logger.info("SIGNAL: %s (window=%d)", sig, win_size)
                found = True

        if self.tracker.just_ended_hotstreak():
            for sig in check_chain_patterns(self.tracker):
                logger.info("SIGNAL: %s", sig)
                found = True

        if found and self.secondary:
            last5 = self.tracker.get_last_n(5)
            if self.secondary.monitoring:
                logger.info("[Secondary] New signal – restarting monitor")
            else:
                logger.info("[Secondary] Signal – starting 21-round monitor")
            self.secondary.start_monitoring(last5)
            self.tracker.mark_signal()

            # Immediately check if the last 5 rounds already qualify for activation
            if (
                not self.secondary.is_active
                and not self.strategy_active
                and self.autopilot
                and len(last5) >= 5
            ):
                above = sum(1 for m in last5 if m >= 2.0)
                if above >= 3:
                    logger.info(
                        "[Secondary] Instant activation (3+/5 above 2x from signal)"
                    )
                    self._activate_secondary_betting()

    # ── Commands from GUI ───────────────────────────────────────────

    def _process_commands(self):
        while not self.command_queue.empty():
            try:
                cmd = self.command_queue.get_nowait()
                action = cmd.get("action")
                if action == "set_autopilot":
                    self.autopilot = cmd["value"]
                    logger.info("Autopilot %s", "ON" if self.autopilot else "OFF")
                elif action == "force_stop":
                    self._force_stop_all()
                elif action == "activate_primary":
                    self._manual_activate_primary(cmd["index"])
                elif action == "activate_secondary":
                    self._manual_activate_secondary()
                elif action == "reload_config":
                    self._hot_reload(cmd["config"])
            except queue.Empty:
                break

    def _force_stop_all(self):
        for s in self.primaries.values():
            s.reset()
        if self.secondary:
            self.secondary.reset()
            self.secondary.stop_monitoring()
        self.strategy_active = False
        logger.info("All strategies force-stopped")

    def _manual_activate_primary(self, index: int):
        names = list(self.primaries.keys())
        if index < len(names):
            name = names[index]
            s = self.primaries[name]
            s.is_active = True
            self.strategy_active = True
            self.driver.setup_auto_cashout(s.auto_cashout)
            time.sleep(2)
            bet = s.next_bet()
            if self.driver.place_bet(bet):
                s.current_bet = bet
                s.waiting_for_result = True
                logger.info("[%s] Manually activated", name)

    def _manual_activate_secondary(self):
        if self.secondary:
            last5 = self.tracker.get_last_n(5)
            self.secondary.start_monitoring(last5)
            logger.info("[Secondary] Manually started monitoring")

    def _hot_reload(self, raw_config: dict):
        new_cfg = BotConfig.from_dict(raw_config)
        self.config = new_cfg
        self._load_strategies()
        logger.info("Config hot-reloaded")

    # ── Limits ──────────────────────────────────────────────────────

    def _check_limits(self) -> bool:
        if self.total_profit <= -self.config.max_loss:
            logger.warning("Max loss reached: %.0f", abs(self.total_profit))
            return False
        for s in self.primaries.values():
            if s.consecutive_losses >= s.max_consecutive_losses:
                logger.warning("[%s] Max consecutive losses", s.name)
                return False
        if (
            self.secondary
            and self.secondary.consecutive_losses
            >= self.secondary.max_consecutive_losses
        ):
            logger.warning("[Secondary] Max consecutive losses")
            return False
        return True

    # ── Shutdown ────────────────────────────────────────────────────

    def _shutdown(self):
        self.running = False
        if self.db.current_session_id:
            self.db.end_session(self.driver.get_balance())

        logger.info("=" * 60)
        logger.info("SESSION SUMMARY")
        logger.info("  Total P/L: %.0f", self.total_profit)
        for name, s in self.primaries.items():
            logger.info(
                "  [%s] P/L: %.0f  Losses: %d",
                name,
                s.total_profit,
                s.consecutive_losses,
            )
        if self.secondary:
            logger.info(
                "  [Secondary] P/L: %.0f  Losses: %d",
                self.secondary.total_profit,
                self.secondary.consecutive_losses,
            )
        bal = self.driver.get_balance()
        if bal is not None:
            logger.info("  Final balance: %,.0f", bal)
        logger.info("=" * 60)

        self.driver.quit()
        self.db.close()

    def _log_strategy_summary(self):
        logger.info("=" * 60)
        logger.info("STRATEGIES:")
        for name, s in self.primaries.items():
            logger.info(
                "  [%s] trigger=%d×<%sx  cashout=%sx",
                name,
                s.trigger_count,
                s.trigger_threshold,
                s.auto_cashout,
            )
        if self.secondary:
            logger.info(
                "  [Secondary] signal-based, cashout=%sx", self.secondary.auto_cashout
            )
        logger.info("=" * 60)


# 925,000
