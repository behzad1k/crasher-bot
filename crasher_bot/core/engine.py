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
from crasher_bot.strategies import CustomState, StrategyState

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
        self.custom: Optional[CustomState] = None
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
        cs = self.config.custom_strategy
        if cs and cs.enabled:
            self.custom = CustomState(
                base_bet=cs.base_bet,
                auto_cashout=cs.auto_cashout,
                max_consecutive_losses=cs.max_consecutive_losses,
                max_losses_in_window=cs.max_losses_in_window,
                loss_check_window=cs.loss_check_window,
                bet_multiplier=cs.bet_multiplier,
                stop_profit_count=cs.stop_profit_count,
                activate_on_strong_hotstreak=cs.activate_on_strong_hotstreak,
                activate_on_weak_hotstreak=cs.activate_on_weak_hotstreak,
                activate_on_rule_of_17=cs.activate_on_rule_of_17,
                activate_on_pre_streak_pattern=cs.activate_on_pre_streak_pattern,
                activate_on_high_deviation_10=cs.activate_on_high_deviation_10,
                activate_on_high_deviation_15=cs.activate_on_high_deviation_15,
                signal_confirm_threshold=cs.signal_confirm_threshold,
                signal_confirm_count=cs.signal_confirm_count,
                signal_confirm_window=cs.signal_confirm_window,
                signal_monitor_rounds=cs.signal_monitor_rounds,
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

                # Custom strategy – handle active bets
                if self.custom and self.custom.waiting_for_result:
                    self._custom_result(mult)

                # Custom strategy – signal monitoring
                if self.custom and self.custom.monitoring and not self.custom.is_active:
                    self._custom_monitor_round(mult)

                # Custom strategy – hotstreak activation
                if self.custom:
                    self._check_custom_hotstreak()

                # Signal analysis (only when idle)
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
                    if not self.custom or not self.custom.is_active:
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

    # ── Custom strategy ─────────────────────────────────────────────

    def _check_custom_hotstreak(self):
        """Activate custom on hotstreak detection – bets instantly."""
        cst = self.custom
        if (
            cst.is_active
            or cst.waiting_for_result
            or self.strategy_active
            or not self.autopilot
        ):
            return

        hs = self.tracker.current_hotstreak
        if hs is None:
            return

        hs_type = hs.get("type", "")
        if cst.should_activate_on_hotstreak(hs_type):
            logger.info(
                "[Custom] %s hotstreak detected – betting instantly",
                hs_type.capitalize(),
            )
            self._activate_custom_betting(reason=f"{hs_type}_hotstreak")

    def _custom_signal_triggered(self, reason: str):
        """Handle a signal trigger – check confirmation or start monitoring."""
        cst = self.custom
        if (
            cst.is_active
            or cst.waiting_for_result
            or self.strategy_active
            or not self.autopilot
        ):
            return

        # Check if last N rounds already confirm the signal
        recent = self.tracker.get_last_n(cst.signal_confirm_window)
        if cst.check_confirmation(recent):
            logger.info(
                "[Custom] Signal '%s' confirmed immediately (%d+/%d above %sx) – betting",
                reason,
                cst.signal_confirm_count,
                cst.signal_confirm_window,
                cst.signal_confirm_threshold,
            )
            self._activate_custom_betting(reason=f"{reason} (confirmed)")
        else:
            # Start monitoring to wait for confirmation
            if cst.monitoring:
                logger.info("[Custom] New signal '%s' – restarting monitor", reason)
            else:
                logger.info(
                    "[Custom] Signal '%s' – monitoring next %d rounds for %d+/%d above %sx",
                    reason,
                    cst.signal_monitor_rounds,
                    cst.signal_confirm_count,
                    cst.signal_confirm_window,
                    cst.signal_confirm_threshold,
                )
            cst.start_monitoring(reason, initial=list(recent))

    def _custom_monitor_round(self, mult: float):
        """Process a round during signal monitoring."""
        cst = self.custom
        cst.rounds_monitored += 1
        cst.monitoring_history.append(mult)
        logger.info(
            "[Custom] Monitor %d/%d: %sx (signal: %s)",
            cst.rounds_monitored,
            cst.signal_monitor_rounds,
            mult,
            cst.pending_signal_reason,
        )

        # Check confirmation on the monitoring window
        if len(cst.monitoring_history) >= cst.signal_confirm_window:
            last_n = cst.monitoring_history[-cst.signal_confirm_window :]
            above = sum(1 for m in last_n if m >= cst.signal_confirm_threshold)
            if above >= cst.signal_confirm_count:
                if not self.strategy_active and self.autopilot:
                    reason = cst.pending_signal_reason or "signal"
                    logger.info(
                        "[Custom] Signal confirmed during monitoring (%d/%d above %sx) – betting",
                        above,
                        cst.signal_confirm_window,
                        cst.signal_confirm_threshold,
                    )
                    cst.stop_monitoring()
                    self._activate_custom_betting(reason=f"{reason} (confirmed)")
                    return

        # Max monitoring rounds reached
        if cst.rounds_monitored >= cst.signal_monitor_rounds:
            logger.info(
                "[Custom] Monitor expired after %d rounds – signal not confirmed",
                cst.signal_monitor_rounds,
            )
            cst.stop_monitoring()

    def _activate_custom_betting(self, reason: str = "manual"):
        """Activate the custom strategy and place the first bet."""
        cst = self.custom
        cst.stop_monitoring()
        cst.is_active = True
        self.strategy_active = True
        if not self.driver.setup_auto_cashout(cst.auto_cashout):
            cst.reset()
            self.strategy_active = False
            return
        time.sleep(2)
        bet = cst.next_bet()
        if self.driver.place_bet(bet):
            cst.current_bet = bet
            cst.waiting_for_result = True
            logger.info("[Custom] BET %d (reason: %s)", bet, reason)
        else:
            cst.reset()
            self.strategy_active = False

    def _custom_result(self, mult: float):
        cst = self.custom
        if mult >= cst.auto_cashout:
            profit = cst.current_bet * (cst.auto_cashout - 1)
            cst.total_profit += profit
            self.total_profit += profit
            cst.total_wins += 1
            cst.record_outcome("win")
            self.db.add_bet(cst.name, cst.current_bet, "win", mult, profit)
            logger.info(
                "[Custom] WIN +%.0f (wins: %d, total: %.0f)",
                profit,
                cst.total_wins,
                cst.total_profit,
            )
            cst.consecutive_losses = 0
            cst.current_bet = cst.base_bet
            cst.waiting_for_result = False

            # Check stop-profit condition
            if cst.should_stop_for_profit():
                logger.info(
                    "[Custom] Stop-profit reached (%d wins) – stopping",
                    cst.total_wins,
                )
                cst.full_reset()
                self.strategy_active = False
                return

            # Continue betting if still in hotstreak or recently ended one
            if self.autopilot and (
                self.tracker.in_hotstreak() or self.tracker.just_ended_hotstreak()
            ):
                time.sleep(1)
                bet = cst.next_bet()
                if self.driver.place_bet(bet):
                    cst.current_bet = bet
                    cst.waiting_for_result = True
                    logger.info("[Custom] Continue BET %d", bet)
                else:
                    cst.reset()
                    self.strategy_active = False
            else:
                logger.info("[Custom] No active hotstreak/signal – pausing")
                cst.reset()
                self.strategy_active = False
        else:
            loss = cst.current_bet
            cst.total_profit -= loss
            self.total_profit -= loss
            cst.consecutive_losses += 1
            cst.record_outcome("loss")
            self.db.add_bet(cst.name, cst.current_bet, "loss", mult, -loss)
            logger.info(
                "[Custom] LOSS -%.0f (streak: %d, window losses: %d/%d)",
                loss,
                cst.consecutive_losses,
                cst.losses_in_window(),
                cst.loss_check_window,
            )

            # Check max consecutive losses
            if cst.consecutive_losses >= cst.max_consecutive_losses:
                logger.info("[Custom] Max consecutive losses – stopping")
                cst.full_reset()
                self.strategy_active = False
                return

            # Check window loss limit
            if cst.should_stop_for_window_losses():
                logger.info(
                    "[Custom] Window loss limit (%d/%d in last %d) – stopping",
                    cst.losses_in_window(),
                    cst.max_losses_in_window,
                    cst.loss_check_window,
                )
                cst.full_reset()
                self.strategy_active = False
                return

            # Continue betting with martingale
            cst.current_bet = cst.next_bet()
            cst.waiting_for_result = False
            time.sleep(1)
            if self.driver.place_bet(cst.current_bet):
                cst.waiting_for_result = True
                logger.info("[Custom] Follow-up BET %d", cst.current_bet)
            else:
                logger.error("[Custom] Failed to place follow-up bet")
                cst.reset()
                self.strategy_active = False

    # ── Signal analysis ─────────────────────────────────────────────

    def _analyze_signals(self):
        if self.strategy_active or self.tracker.in_hotstreak():
            return

        triggered_signals: List[str] = []

        for win_size, min_len in [(10, 10), (15, 15)]:
            window = self.tracker.get_last_n(win_size)
            if len(window) < min_len:
                continue
            signals = analyze_window(window, win_size)
            for sig in signals:
                logger.info("SIGNAL: %s (window=%d)", sig, win_size)
                if (
                    self.custom
                    and not self.custom.is_active
                    and not self.strategy_active
                ):
                    if sig == "high_stddev":
                        if self.custom.should_activate_on_high_stddev(win_size):
                            triggered_signals.append(f"{sig}_w{win_size}")
                    elif self.custom.should_activate_on_signal(sig):
                        triggered_signals.append(sig)

        if self.tracker.just_ended_hotstreak():
            for sig in check_chain_patterns(self.tracker):
                logger.info("SIGNAL: %s", sig)
                if (
                    self.custom
                    and not self.custom.is_active
                    and not self.strategy_active
                ):
                    if self.custom.should_activate_on_signal(sig):
                        triggered_signals.append(sig)

        # Route signals through confirmation/monitoring
        if triggered_signals and self.custom and self.autopilot:
            if not self.custom.is_active and not self.strategy_active:
                reason = ", ".join(triggered_signals)
                self._custom_signal_triggered(reason)

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
                elif action == "activate_custom":
                    self._manual_activate_custom()
                elif action == "reload_config":
                    self._hot_reload(cmd["config"])
            except queue.Empty:
                break

    def _force_stop_all(self):
        for s in self.primaries.values():
            s.reset()
        if self.custom:
            self.custom.full_reset()
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

    def _manual_activate_custom(self):
        if self.custom and not self.custom.is_active and not self.strategy_active:
            logger.info("[Custom] Manually activated")
            self._activate_custom_betting(reason="manual")
        elif self.custom and self.strategy_active:
            logger.warning("[Custom] Cannot activate – another strategy is active")

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
            self.custom
            and self.custom.consecutive_losses >= self.custom.max_consecutive_losses
        ):
            logger.warning("[Custom] Max consecutive losses")
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
        if self.custom:
            logger.info(
                "  [Custom] P/L: %.0f  Wins: %d  Losses: %d",
                self.custom.total_profit,
                self.custom.total_wins,
                self.custom.consecutive_losses,
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
        if self.custom:
            triggers = []
            if self.custom.activate_on_strong_hotstreak:
                triggers.append("strong_hs")
            if self.custom.activate_on_weak_hotstreak:
                triggers.append("weak_hs")
            if self.custom.activate_on_rule_of_17:
                triggers.append("rule17")
            if self.custom.activate_on_pre_streak_pattern:
                triggers.append("pre_streak")
            if self.custom.activate_on_high_deviation_10:
                triggers.append("stddev10")
            if self.custom.activate_on_high_deviation_15:
                triggers.append("stddev15")
            logger.info(
                "  [Custom] cashout=%sx, window=%d/%d, stop_profit=%d, confirm=%d+/%d>%sx, monitor=%d, triggers=[%s]",
                self.custom.auto_cashout,
                self.custom.max_losses_in_window,
                self.custom.loss_check_window,
                self.custom.stop_profit_count,
                self.custom.signal_confirm_count,
                self.custom.signal_confirm_window,
                self.custom.signal_confirm_threshold,
                self.custom.signal_monitor_rounds,
                ", ".join(triggers) if triggers else "none",
            )
        logger.info("=" * 60)
