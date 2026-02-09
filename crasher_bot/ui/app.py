"""Main GUI application."""

import logging
import queue
import threading
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, scrolledtext, ttk
from typing import List, Optional

from crasher_bot.config import BotConfig
from crasher_bot.core import Database
from crasher_bot.core.engine import BotEngine
from crasher_bot.ui import Theme
from crasher_bot.ui.widgets import MultiplierCanvas, SimpleConfigCard, StrategyCard

logger = logging.getLogger(__name__)


class _QueueLogHandler(logging.Handler):
    def __init__(self, q: queue.Queue):
        super().__init__()
        self.q = q

    def emit(self, record):
        self.q.put(self.format(record))


class Application:
    """Tkinter GUI for the crasher bot."""

    def __init__(self, config_path: str = "./bot_config.json"):
        self.config_path = config_path
        self.config = self._load_config()

        self.root = tk.Tk()
        self.root.title("Crasher Bot Control Panel")
        self.root.geometry("1000x700")
        self.root.minsize(900, 600)

        self.bot: Optional[BotEngine] = None
        self.bot_thread: Optional[threading.Thread] = None
        self.bot_running = False
        self.log_queue: queue.Queue = queue.Queue()
        self._primary_cards: List[StrategyCard] = []

        self._setup_logging()
        self._apply_theme()
        self._build_ui()

        self.root.after(100, self._poll_logs)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── Config ──────────────────────────────────────────────────────

    def _load_config(self) -> dict:
        try:
            with open(self.config_path) as f:
                import json
                return json.load(f)
        except (FileNotFoundError, Exception) as e:
            logger.warning("Config load error: %s", e)
            return {"username": "", "password": "", "game_url": "", "strategies": []}

    def _save_config(self) -> bool:
        try:
            import json
            with open(self.config_path, "w") as f:
                json.dump(self.config, f, indent=2)
            return True
        except Exception as e:
            messagebox.showerror("Error", f"Save failed: {e}")
            return False

    # ── Logging ─────────────────────────────────────────────────────

    def _setup_logging(self):
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        for h in root_logger.handlers[:]:
            root_logger.removeHandler(h)
        h = _QueueLogHandler(self.log_queue)
        h.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
        root_logger.addHandler(h)

    def _poll_logs(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self._log_widget.configure(state=tk.NORMAL)
                self._log_widget.insert(tk.END, msg + "\n")
                self._log_widget.see(tk.END)
                self._log_widget.configure(state=tk.DISABLED)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_logs)

    # ── Theme ───────────────────────────────────────────────────────

    def _apply_theme(self):
        s = ttk.Style()
        self.root.configure(bg=Theme.BG_DARK)
        s.configure("TFrame", background=Theme.BG_DARK)
        s.configure("Card.TFrame", background=Theme.BG_LIGHT, relief=tk.RAISED)
        s.configure("TLabel", background=Theme.BG_DARK, foreground=Theme.FG_PRIMARY, font=("Segoe UI", 10))
        s.configure("Heading.TLabel", background=Theme.BG_LIGHT, foreground=Theme.FG_PRIMARY, font=("Segoe UI", 11, "bold"))
        s.configure("TButton", borderwidth=0, focuscolor="none", font=("Segoe UI", 10))
        s.map("TButton", background=[("active", Theme.BG_HOVER)])
        s.configure("Success.TButton", background=Theme.ACCENT_SUCCESS)
        s.configure("Danger.TButton", background=Theme.ACCENT_DANGER)
        s.configure("Warning.TButton", background=Theme.ACCENT_WARNING)
        s.configure("TEntry", fieldbackground=Theme.BG_MEDIUM, foreground=Theme.FG_PRIMARY, insertcolor=Theme.FG_PRIMARY)
        s.configure("TNotebook", background=Theme.BG_DARK, borderwidth=0)
        s.configure("TNotebook.Tab", background=Theme.BG_MEDIUM, foreground=Theme.FG_SECONDARY, padding=[20, 10], font=("Segoe UI", 10))
        s.map("TNotebook.Tab", background=[("selected", Theme.BG_LIGHT)], foreground=[("selected", Theme.FG_PRIMARY)])
        s.configure("TCheckbutton", background=Theme.BG_DARK, foreground=Theme.FG_PRIMARY, font=("Segoe UI", 10))
        s.configure("Switch.TCheckbutton", background=Theme.BG_LIGHT, foreground=Theme.FG_PRIMARY)

    # ── UI Build ────────────────────────────────────────────────────

    def _build_ui(self):
        nb = ttk.Notebook(self.root)
        nb.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        self._build_control_tab(nb)
        self._build_strategy_tab(nb)
        self._build_logs_tab(nb)
        self._build_history_tab(nb)

    def _build_control_tab(self, nb: ttk.Notebook):
        tab = ttk.Frame(nb)
        nb.add(tab, text="Control")

        card = ttk.Frame(tab, style="Card.TFrame")
        card.pack(fill=tk.X, padx=20, pady=20)
        ttk.Label(card, text="Bot Control", font=("Segoe UI", 14, "bold"), style="Heading.TLabel").pack(pady=10)

        bf = ttk.Frame(card, style="Card.TFrame")
        bf.pack(pady=10)
        self._start_btn = ttk.Button(bf, text="Start Bot", command=self._toggle_bot, style="Success.TButton", width=20)
        self._start_btn.pack(side=tk.LEFT, padx=5)
        ttk.Button(bf, text="Stop Active Strategy", command=self._force_stop, style="Warning.TButton", width=20).pack(side=tk.LEFT, padx=5)

        self._status = ttk.Label(card, text="Status: Stopped", font=("Segoe UI", 11), style="Heading.TLabel")
        self._status.pack(pady=5)

        af = ttk.Frame(card, style="Card.TFrame")
        af.pack(pady=10)
        self._autopilot_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(af, text="Auto-Pilot (automatic betting)", variable=self._autopilot_var, command=self._toggle_autopilot, style="Switch.TCheckbutton").pack(pady=5)

        # Manual activation
        mf = ttk.Frame(tab, style="Card.TFrame")
        mf.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 20))
        ttk.Label(mf, text="Manual Strategy Activation", font=("Segoe UI", 14, "bold"), style="Heading.TLabel").pack(pady=10)
        self._manual_frame = ttk.Frame(mf, style="Card.TFrame")
        self._manual_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        self._refresh_manual_buttons()

    def _build_strategy_tab(self, nb: ttk.Notebook):
        tab = ttk.Frame(nb)
        nb.add(tab, text="Strategies")

        inner = ttk.Notebook(tab)
        inner.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Primary
        pt = ttk.Frame(inner)
        inner.add(pt, text="Primary")
        hdr = ttk.Frame(pt)
        hdr.pack(fill=tk.X, padx=20, pady=10)
        ttk.Label(hdr, text="Primary Strategies", font=("Segoe UI", 12, "bold"), style="TLabel").pack(side=tk.LEFT)
        ttk.Button(hdr, text="Add Strategy", command=self._add_primary, style="Success.TButton").pack(side=tk.RIGHT)
        self._primary_frame = ttk.Frame(pt)
        self._primary_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)
        self._refresh_primary_cards()

        # Secondary
        st = ttk.Frame(inner)
        inner.add(st, text="Secondary")
        sec_data = self.config.get("secondary_strategy", {"enabled": False, "base_bet": 1000, "auto_cashout": 2.0, "max_consecutive_losses": 10, "bet_multiplier": 2.0})
        self._secondary_card = SimpleConfigCard(
            st, "Secondary Strategy",
            "Signal-based with 21-round monitoring. Activates when 3+ of last 5 rounds are above 2x.",
            [("base_bet", "Base Bet:", float), ("auto_cashout", "Auto Cashout:", float), ("max_consecutive_losses", "Max Losses:", int), ("bet_multiplier", "Bet Multiplier:", float)],
            sec_data,
        )

        # Tertiary
        tt = ttk.Frame(inner)
        inner.add(tt, text="Tertiary")
        ter_data = self.config.get("tertiary_strategy", {"enabled": False, "base_bet": 1000, "auto_cashout": 2.0, "max_consecutive_losses": 10, "max_losses_in_window": 7, "loss_check_window": 10})
        self._tertiary_card = SimpleConfigCard(
            tt, "Tertiary Strategy",
            "Signal-based instant betting. Starts immediately if in hotstreak.",
            [("base_bet", "Base Bet:", float), ("auto_cashout", "Auto Cashout:", float), ("max_consecutive_losses", "Max Losses:", int), ("max_losses_in_window", "Max Losses in Window:", int), ("loss_check_window", "Window Size:", int)],
            ter_data,
        )

        ttk.Button(tab, text="Apply Changes", command=self._apply_changes, style="Success.TButton", width=20).pack(pady=10)

    def _build_logs_tab(self, nb: ttk.Notebook):
        tab = ttk.Frame(nb)
        nb.add(tab, text="Logs")
        self._log_widget = scrolledtext.ScrolledText(tab, bg=Theme.BG_MEDIUM, fg=Theme.FG_PRIMARY, font=("Consolas", 9), wrap=tk.WORD, state=tk.DISABLED)
        self._log_widget.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        ttk.Button(tab, text="Clear", command=lambda: (self._log_widget.configure(state=tk.NORMAL), self._log_widget.delete(1.0, tk.END), self._log_widget.configure(state=tk.DISABLED))).pack(pady=5)

    def _build_history_tab(self, nb: ttk.Notebook):
        tab = ttk.Frame(nb)
        nb.add(tab, text="History")

        # ── Header bar with session selector ───────────────────────
        header = ttk.Frame(tab, style="Card.TFrame")
        header.pack(fill=tk.X, padx=20, pady=(10, 0))

        ttk.Label(header, text="Session:", style="Heading.TLabel").pack(side=tk.LEFT, padx=(10, 5), pady=8)

        self._session_var = tk.StringVar()
        self._session_combo = ttk.Combobox(
            header,
            textvariable=self._session_var,
            state="readonly",
            width=55,
            font=("Segoe UI", 10),
        )
        self._session_combo.pack(side=tk.LEFT, padx=5, pady=8)
        self._session_combo.bind("<<ComboboxSelected>>", lambda _: self._on_session_selected())

        ttk.Button(header, text="Refresh", command=self._refresh_session_list, width=10).pack(side=tk.LEFT, padx=5, pady=8)
        ttk.Button(header, text="Clear View", command=self._clear_history_view, width=10).pack(side=tk.RIGHT, padx=10, pady=8)

        # ── Stats bar ──────────────────────────────────────────────
        stats = ttk.Frame(tab, style="Card.TFrame")
        stats.pack(fill=tk.X, padx=20, pady=(5, 0))

        self._history_stats = ttk.Label(stats, text="", style="Heading.TLabel", foreground=Theme.FG_SECONDARY)
        self._history_stats.pack(side=tk.LEFT, padx=10, pady=5)

        # ── Multiplier display with scrollbar ──────────────────────
        display_frame = ttk.Frame(tab)
        display_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)

        scrollbar = ttk.Scrollbar(display_frame, orient=tk.VERTICAL)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self._mult_display = MultiplierCanvas(display_frame, height=500, max_display=0)
        self._mult_display.MAX_DISPLAY = 0  # 0 = unlimited for history
        self._mult_display.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._mult_display.configure(yscrollcommand=scrollbar.set)
        scrollbar.configure(command=self._mult_display.yview)

        # Session ID→int mapping (populated by _refresh_session_list)
        self._session_map: dict[str, int] = {}

        # Load sessions on startup
        self.root.after(200, self._refresh_session_list)

    def _refresh_session_list(self):
        """Reload the session dropdown from the database."""
        try:
            db = self._get_history_db()
            sessions = db.list_sessions()
            db.close()
        except Exception as e:
            logger.warning("Could not load sessions: %s", e)
            return

        self._session_map.clear()
        labels = []

        for sid, start_ts, end_ts, count in sessions:
            start_str = self._format_ts(start_ts)
            end_str = self._format_ts(end_ts) if end_ts else "running"
            label = f"#{sid}  |  {start_str} → {end_str}  |  {count} rounds"
            labels.append(label)
            self._session_map[label] = sid

        self._session_combo["values"] = labels

        if labels:
            # Default to latest session (first in list, since ORDER BY DESC)
            self._session_combo.current(0)
            self._on_session_selected()
        else:
            self._session_combo.set("")
            self._mult_display.clear()
            self._history_stats.configure(text="No sessions found")

    def _on_session_selected(self):
        """Load multipliers for the selected session."""
        label = self._session_var.get()
        sid = self._session_map.get(label)
        if sid is None:
            return

        try:
            db = self._get_history_db()
            mults = db.get_all_session_multipliers(sid)
            db.close()
        except Exception as e:
            logger.warning("Could not load session #%d: %s", sid, e)
            return

        self._mult_display.clear()
        for m in mults:
            self._mult_display.multipliers.append(m)
        self._mult_display.draw()

        # Stats
        if mults:
            avg = sum(mults) / len(mults)
            above_2x = sum(1 for m in mults if m >= 2.0)
            pct = (above_2x / len(mults)) * 100
            mx = max(mults)
            self._history_stats.configure(
                text=f"{len(mults)} rounds  |  Avg: {avg:.2f}x  |  Max: {mx:.2f}x  |  ≥2x: {above_2x} ({pct:.0f}%)"
            )
        else:
            self._history_stats.configure(text="Empty session")

    def _clear_history_view(self):
        self._mult_display.clear()
        self._history_stats.configure(text="")

    def _get_history_db(self) -> Database:
        """Open a read-only DB connection for the history tab."""
        return Database()

    @staticmethod
    def _format_ts(ts_str: str) -> str:
        """Format a timestamp string for display."""
        if not ts_str:
            return "?"
        try:
            dt = datetime.fromisoformat(ts_str)
            return dt.strftime("%b %d, %H:%M")
        except (ValueError, TypeError):
            return str(ts_str)[:16]

    # ── Actions ─────────────────────────────────────────────────────

    def _toggle_bot(self):
        if self.bot_running:
            self._stop_bot()
        else:
            self._start_bot()

    def _start_bot(self):
        if self.bot_running:
            return
        cfg = BotConfig.from_dict(self.config)
        errors = cfg.validate()
        if errors:
            messagebox.showerror("Config Error", "\n".join(errors))
            return
        self.bot_running = True
        self._start_btn.configure(text="Stop Bot", style="Danger.TButton")
        self._status.configure(text="Status: Starting...")
        self.bot_thread = threading.Thread(target=self._run_bot, daemon=True)
        self.bot_thread.start()

    def _stop_bot(self):
        self.bot_running = False
        if self.bot:
            self.bot.running = False
        self._start_btn.configure(text="Start Bot", style="Success.TButton")
        self._status.configure(text="Status: Stopped")

    def _run_bot(self):
        try:
            cfg = BotConfig.from_dict(self.config)
            self.bot = BotEngine(cfg)

            def _on_mult(m):
                self._mult_display.add(m)
                # Auto-refresh session list once (so the new/continued session appears)
                if not hasattr(self, '_session_refreshed_for_run'):
                    self._session_refreshed_for_run = True
                    self._refresh_session_list()

            self.bot.on_multiplier = lambda m: self.root.after(0, _on_mult, m)
            self.root.after(0, lambda: self._status.configure(text="Status: Running"))
            self.bot.run()
        except Exception:
            logger.exception("Bot thread error")
        finally:
            self.bot_running = False
            if hasattr(self, '_session_refreshed_for_run'):
                del self._session_refreshed_for_run
            self.root.after(0, lambda: self._start_btn.configure(text="Start Bot", style="Success.TButton"))
            self.root.after(0, lambda: self._status.configure(text="Status: Stopped"))
            self.root.after(0, self._refresh_session_list)

    def _toggle_autopilot(self):
        val = self._autopilot_var.get()
        if self.bot:
            self.bot.command_queue.put({"action": "set_autopilot", "value": val})
        logger.info("Autopilot %s", "ON" if val else "OFF")

    def _force_stop(self):
        if self.bot:
            self.bot.command_queue.put({"action": "force_stop"})
            logger.info("Force-stop sent")

    def _refresh_manual_buttons(self):
        for w in self._manual_frame.winfo_children():
            w.destroy()
        for i, s in enumerate(self.config.get("strategies", [])):
            if s.get("enabled", True):
                ttk.Button(self._manual_frame, text=f"Activate: {s.get('name', f'Strategy {i+1}')}", command=lambda idx=i: self._activate_primary(idx), width=40).pack(pady=3, anchor=tk.W)
        if self.config.get("secondary_strategy", {}).get("enabled"):
            ttk.Button(self._manual_frame, text="Activate: Secondary", command=self._activate_secondary, width=40).pack(pady=3, anchor=tk.W)

    def _activate_primary(self, idx: int):
        if not self.bot:
            messagebox.showwarning("Warning", "Bot not running")
            return
        self._autopilot_var.set(False)
        self.bot.command_queue.put({"action": "set_autopilot", "value": False})
        self.bot.command_queue.put({"action": "activate_primary", "index": idx})

    def _activate_secondary(self):
        if not self.bot:
            messagebox.showwarning("Warning", "Bot not running")
            return
        self._autopilot_var.set(False)
        self.bot.command_queue.put({"action": "set_autopilot", "value": False})
        self.bot.command_queue.put({"action": "activate_secondary"})

    # ── Strategy editing ────────────────────────────────────────────

    def _refresh_primary_cards(self):
        for w in self._primary_frame.winfo_children():
            w.destroy()
        self._primary_cards = []
        for i, s in enumerate(self.config.get("strategies", [])):
            card = StrategyCard(self._primary_frame, s, on_delete=lambda idx=i: self._delete_primary(idx))
            self._primary_cards.append(card)

    def _add_primary(self):
        n = len(self.config.get("strategies", [])) + 1
        self.config.setdefault("strategies", []).append({
            "name": f"Strategy {n}", "enabled": True, "base_bet": 1000,
            "auto_cashout": 2.0, "trigger_threshold": 2.0, "trigger_count": 5,
            "max_consecutive_losses": 10, "bet_multiplier": 2.0,
        })
        self._refresh_primary_cards()

    def _delete_primary(self, idx: int):
        if messagebox.askyesno("Confirm", "Delete this strategy?"):
            del self.config["strategies"][idx]
            self._refresh_primary_cards()

    def _apply_changes(self):
        self.config["strategies"] = [c.get_data() for c in self._primary_cards]
        self.config["secondary_strategy"] = self._secondary_card.get_data()
        self.config["tertiary_strategy"] = self._tertiary_card.get_data()
        if self._save_config():
            self._refresh_manual_buttons()
            if self.bot and self.bot_running:
                self.bot.command_queue.put({"action": "reload_config", "config": self.config})
            messagebox.showinfo("Success", "Changes applied!")

    # ── Lifecycle ───────────────────────────────────────────────────

    def _on_close(self):
        if self.bot_running:
            if messagebox.askokcancel("Quit", "Bot running. Stop and quit?"):
                self._stop_bot()
                self.root.after(1000, self.root.destroy)
        else:
            self.root.destroy()

    def run(self):
        self.root.mainloop()
