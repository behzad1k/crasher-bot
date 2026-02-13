"""Main GUI application."""

import logging
import platform
import queue
import threading
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, scrolledtext, ttk
from typing import Dict, List, Optional

from crasher_bot.config import BotConfig, get_default_config_path
from crasher_bot.core import Database
from crasher_bot.core.engine import BotEngine
from crasher_bot.ui import Theme
from crasher_bot.ui.widgets import (
    MultiplierCanvas,
    SimpleConfigCard,
    StrategyCard,
    bind_mousewheel,
)

logger = logging.getLogger(__name__)


class _QueueLogHandler(logging.Handler):
    def __init__(self, q: queue.Queue):
        super().__init__()
        self.q = q

    def emit(self, record):
        self.q.put(self.format(record))


class Application:
    """Tkinter GUI for the crasher bot."""

    def __init__(self, config_path: str = ""):
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

    def _apply_theme(self):
        s = ttk.Style()
        if self.root.tk.call("tk", "windowingsystem") != "aqua":
            s.theme_use("clam")

        self.root.configure(bg=Theme.BG_DARK)
        s.configure("TFrame", background=Theme.BG_DARK)
        s.configure("Card.TFrame", background=Theme.BG_LIGHT, relief=tk.RAISED)
        s.configure(
            "TLabel",
            background=Theme.BG_DARK,
            foreground=Theme.FG_PRIMARY,
            font=("Segoe UI", 10),
        )
        s.configure(
            "Heading.TLabel",
            background=Theme.BG_LIGHT,
            foreground=Theme.FG_PRIMARY,
            font=("Segoe UI", 11, "bold"),
        )
        s.configure(
            "Desc.TLabel",
            background=Theme.BG_LIGHT,
            foreground=Theme.FG_SECONDARY,
            font=("Segoe UI", 9),
        )

        s.configure(
            "TButton",
            borderwidth=0,
            focuscolor="none",
            font=("Segoe UI", 10),
            background=Theme.BG_MEDIUM,
            foreground=Theme.FG_PRIMARY,
        )
        s.map(
            "TButton",
            background=[("active", Theme.BG_HOVER), ("pressed", Theme.BG_HOVER)],
            foreground=[("active", Theme.FG_PRIMARY)],
        )
        s.configure(
            "Success.TButton", background=Theme.ACCENT_SUCCESS, foreground="#000000"
        )
        s.map(
            "Success.TButton",
            background=[("active", "#00b85c")],
            foreground=[("active", "#000000")],
        )
        s.configure(
            "Danger.TButton", background=Theme.ACCENT_DANGER, foreground="#000000"
        )
        s.map(
            "Danger.TButton",
            background=[("active", "#e55a5a")],
            foreground=[("active", "#000000")],
        )
        s.configure(
            "Warning.TButton", background=Theme.ACCENT_WARNING, foreground="#000000"
        )
        s.map(
            "Warning.TButton",
            background=[("active", "#e69540")],
            foreground=[("active", "#000000")],
        )

        s.configure(
            "TEntry",
            fieldbackground=Theme.BG_MEDIUM,
            foreground=Theme.FG_PRIMARY,
            insertcolor=Theme.FG_PRIMARY,
            bordercolor=Theme.BORDER,
            lightcolor=Theme.BORDER,
            darkcolor=Theme.BORDER,
        )

        s.configure(
            "TNotebook",
            background=Theme.BG_DARK,
            borderwidth=0,
            tabmargins=[0, 0, 0, 0],
        )
        s.configure(
            "TNotebook.Tab",
            background=Theme.BG_MEDIUM,
            foreground=Theme.FG_SECONDARY,
            padding=[20, 10],
            font=("Segoe UI", 10),
            borderwidth=0,
        )
        s.map(
            "TNotebook.Tab",
            background=[("selected", Theme.BG_LIGHT)],
            foreground=[("selected", Theme.FG_PRIMARY)],
        )

        s.configure(
            "TCheckbutton",
            background=Theme.BG_DARK,
            foreground=Theme.FG_PRIMARY,
            font=("Segoe UI", 10),
            indicatorcolor=Theme.BG_MEDIUM,
            indicatorbackground=Theme.BG_MEDIUM,
        )
        s.map(
            "TCheckbutton",
            indicatorcolor=[("selected", Theme.ACCENT_PRIMARY)],
            background=[("active", Theme.BG_DARK)],
        )
        s.configure(
            "Switch.TCheckbutton",
            background=Theme.BG_LIGHT,
            foreground=Theme.FG_PRIMARY,
            indicatorcolor=Theme.BG_MEDIUM,
            indicatorbackground=Theme.BG_MEDIUM,
        )
        s.map(
            "Switch.TCheckbutton",
            indicatorcolor=[("selected", Theme.ACCENT_PRIMARY)],
            background=[("active", Theme.BG_LIGHT)],
        )

        s.configure(
            "TCombobox",
            fieldbackground=Theme.BG_MEDIUM,
            background=Theme.BG_MEDIUM,
            foreground=Theme.FG_PRIMARY,
            arrowcolor=Theme.FG_PRIMARY,
            bordercolor=Theme.BORDER,
            lightcolor=Theme.BORDER,
            darkcolor=Theme.BORDER,
            selectbackground=Theme.BG_MEDIUM,
            selectforeground=Theme.FG_PRIMARY,
        )
        s.map(
            "TCombobox",
            fieldbackground=[("readonly", Theme.BG_MEDIUM)],
            foreground=[("readonly", Theme.FG_PRIMARY)],
            selectbackground=[("readonly", Theme.BG_MEDIUM)],
            selectforeground=[("readonly", Theme.FG_PRIMARY)],
        )
        self.root.option_add("*TCombobox*Listbox.background", Theme.BG_MEDIUM)
        self.root.option_add("*TCombobox*Listbox.foreground", Theme.FG_PRIMARY)
        self.root.option_add(
            "*TCombobox*Listbox.selectBackground", Theme.ACCENT_PRIMARY
        )
        self.root.option_add("*TCombobox*Listbox.selectForeground", Theme.FG_PRIMARY)

        s.configure(
            "Vertical.TScrollbar",
            background=Theme.BG_MEDIUM,
            troughcolor=Theme.BG_DARK,
            arrowcolor=Theme.FG_SECONDARY,
            bordercolor=Theme.BG_DARK,
        )
        s.map("Vertical.TScrollbar", background=[("active", Theme.BG_HOVER)])

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

        # Credentials card
        cred_card = ttk.Frame(tab, style="Card.TFrame")
        cred_card.pack(fill=tk.X, padx=20, pady=(20, 10))
        ttk.Label(
            cred_card,
            text="Credentials",
            font=("Segoe UI", 14, "bold"),
            style="Heading.TLabel",
        ).pack(pady=(10, 5))

        uf = ttk.Frame(cred_card, style="Card.TFrame")
        uf.pack(fill=tk.X, padx=20, pady=3)
        ttk.Label(uf, text="Username:", width=12, style="Heading.TLabel").pack(
            side=tk.LEFT
        )
        self._username_entry = ttk.Entry(uf, style="TEntry", width=35)
        self._username_entry.insert(0, self.config.get("username", ""))
        self._username_entry.pack(side=tk.LEFT, padx=5)

        pf = ttk.Frame(cred_card, style="Card.TFrame")
        pf.pack(fill=tk.X, padx=20, pady=(3, 10))
        ttk.Label(pf, text="Password:", width=12, style="Heading.TLabel").pack(
            side=tk.LEFT
        )
        self._password_entry = ttk.Entry(pf, style="TEntry", width=35, show="•")
        self._password_entry.insert(0, self.config.get("password", ""))
        self._password_entry.pack(side=tk.LEFT, padx=5)

        self._show_pw_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            pf,
            text="Show",
            variable=self._show_pw_var,
            command=self._toggle_password_visibility,
            style="Switch.TCheckbutton",
        ).pack(side=tk.LEFT, padx=5)

        # Bot control card
        card = ttk.Frame(tab, style="Card.TFrame")
        card.pack(fill=tk.X, padx=20, pady=(0, 10))
        ttk.Label(
            card,
            text="Bot Control",
            font=("Segoe UI", 14, "bold"),
            style="Heading.TLabel",
        ).pack(pady=10)

        bf = ttk.Frame(card, style="Card.TFrame")
        bf.pack(pady=10)
        self._start_btn = ttk.Button(
            bf,
            text="Start Bot",
            command=self._toggle_bot,
            style="Success.TButton",
            width=20,
        )
        self._start_btn.pack(side=tk.LEFT, padx=5)
        ttk.Button(
            bf,
            text="Stop Active Strategy",
            command=self._force_stop,
            style="Warning.TButton",
            width=20,
        ).pack(side=tk.LEFT, padx=5)

        self._status = ttk.Label(
            card, text="Status: Stopped", font=("Segoe UI", 11), style="Heading.TLabel"
        )
        self._status.pack(pady=5)

        af = ttk.Frame(card, style="Card.TFrame")
        af.pack(pady=10)
        self._autopilot_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            af,
            text="Auto-Pilot (automatic betting)",
            variable=self._autopilot_var,
            command=self._toggle_autopilot,
            style="Switch.TCheckbutton",
        ).pack(pady=5)

        # Manual activation (scrollable)
        mf = ttk.Frame(tab, style="Card.TFrame")
        mf.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 20))
        ttk.Label(
            mf,
            text="Manual Strategy Activation",
            font=("Segoe UI", 14, "bold"),
            style="Heading.TLabel",
        ).pack(pady=10)

        manual_canvas = tk.Canvas(mf, bg=Theme.BG_LIGHT, highlightthickness=0)
        manual_scrollbar = ttk.Scrollbar(
            mf, orient=tk.VERTICAL, command=manual_canvas.yview
        )
        self._manual_frame = ttk.Frame(manual_canvas, style="Card.TFrame")

        self._manual_frame.bind(
            "<Configure>",
            lambda e: manual_canvas.configure(scrollregion=manual_canvas.bbox("all")),
        )
        manual_canvas.create_window(
            (0, 0), window=self._manual_frame, anchor=tk.NW, tags="inner"
        )
        manual_canvas.bind(
            "<Configure>", lambda e: manual_canvas.itemconfigure("inner", width=e.width)
        )
        manual_canvas.configure(yscrollcommand=manual_scrollbar.set)

        manual_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        manual_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10, pady=10)

        self._manual_canvas = manual_canvas
        bind_mousewheel(self._manual_frame, manual_canvas)
        self._refresh_manual_buttons()

    def _build_strategy_tab(self, nb: ttk.Notebook):
        tab = ttk.Frame(nb)
        nb.add(tab, text="Strategies")

        bottom = ttk.Frame(tab)
        bottom.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=10)
        ttk.Button(
            bottom,
            text="Apply Changes",
            command=self._apply_changes,
            style="Success.TButton",
            width=20,
        ).pack()

        inner = ttk.Notebook(tab)
        inner.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Primary (scrollable)
        pt = ttk.Frame(inner)
        inner.add(pt, text="Primary")
        hdr = ttk.Frame(pt)
        hdr.pack(fill=tk.X, padx=20, pady=10)
        ttk.Label(
            hdr,
            text="Primary Strategies",
            font=("Segoe UI", 12, "bold"),
            style="TLabel",
        ).pack(side=tk.LEFT)
        ttk.Button(
            hdr, text="Add Strategy", command=self._add_primary, style="Success.TButton"
        ).pack(side=tk.RIGHT)

        primary_canvas = tk.Canvas(pt, bg=Theme.BG_DARK, highlightthickness=0)
        primary_scrollbar = ttk.Scrollbar(
            pt, orient=tk.VERTICAL, command=primary_canvas.yview
        )
        self._primary_frame = ttk.Frame(primary_canvas)

        self._primary_frame.bind(
            "<Configure>",
            lambda e: primary_canvas.configure(scrollregion=primary_canvas.bbox("all")),
        )
        primary_canvas.create_window(
            (0, 0), window=self._primary_frame, anchor=tk.NW, tags="inner"
        )
        primary_canvas.bind(
            "<Configure>",
            lambda e: primary_canvas.itemconfigure("inner", width=e.width),
        )
        primary_canvas.configure(yscrollcommand=primary_scrollbar.set)

        primary_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        primary_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=20, pady=10)

        self._primary_canvas = primary_canvas
        bind_mousewheel(self._primary_frame, primary_canvas)
        self._refresh_primary_cards()

        # Custom
        ct = ttk.Frame(inner)
        inner.add(ct, text="Custom")
        cst_data = self.config.get(
            "custom_strategy",
            {
                "enabled": False,
                "base_bet": 1000,
                "auto_cashout": 2.0,
                "max_consecutive_losses": 10,
                "max_losses_in_window": 7,
                "loss_check_window": 10,
                "bet_multiplier": 2.0,
                "stop_profit_count": 0,
                "cooldown_after_win": 0,
                "cooldown_after_loss": 0,
                "activate_on_strong_hotstreak": True,
                "activate_on_weak_hotstreak": False,
                "activate_on_rule_of_17": True,
                "activate_on_pre_streak_pattern": True,
                "activate_on_high_deviation_10": False,
                "activate_on_high_deviation_15": False,
                "signal_confirm_threshold": 2.0,
                "signal_confirm_count": 3,
                "signal_confirm_window": 5,
                "signal_monitor_rounds": 20,
            },
        )
        self._custom_card = CustomConfigCard(ct, cst_data)

    def _build_logs_tab(self, nb: ttk.Notebook):
        tab = ttk.Frame(nb)
        nb.add(tab, text="Logs")
        self._log_widget = scrolledtext.ScrolledText(
            tab,
            bg=Theme.BG_MEDIUM,
            fg=Theme.FG_PRIMARY,
            font=("Consolas", 9),
            wrap=tk.WORD,
            state=tk.DISABLED,
            insertbackground=Theme.FG_PRIMARY,
            selectbackground=Theme.ACCENT_PRIMARY,
            selectforeground=Theme.FG_PRIMARY,
        )
        self._log_widget.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        ttk.Button(
            tab,
            text="Clear",
            command=lambda: (
                self._log_widget.configure(state=tk.NORMAL),
                self._log_widget.delete(1.0, tk.END),
                self._log_widget.configure(state=tk.DISABLED),
            ),
        ).pack(pady=5)

    def _build_history_tab(self, nb: ttk.Notebook):
        tab = ttk.Frame(nb)
        nb.add(tab, text="History")

        header = ttk.Frame(tab, style="Card.TFrame")
        header.pack(fill=tk.X, padx=20, pady=(10, 0))
        ttk.Label(header, text="Session:", style="Heading.TLabel").pack(
            side=tk.LEFT, padx=(10, 5), pady=8
        )

        self._session_var = tk.StringVar()
        self._session_combo = ttk.Combobox(
            header,
            textvariable=self._session_var,
            state="readonly",
            width=55,
            font=("Segoe UI", 10),
        )
        self._session_combo.pack(side=tk.LEFT, padx=5, pady=8)
        self._session_combo.bind(
            "<<ComboboxSelected>>", lambda _: self._on_session_selected()
        )

        ttk.Button(
            header, text="Refresh", command=self._refresh_session_list, width=10
        ).pack(side=tk.LEFT, padx=5, pady=8)
        ttk.Button(
            header, text="Clear View", command=self._clear_history_view, width=10
        ).pack(side=tk.RIGHT, padx=10, pady=8)

        stats = ttk.Frame(tab, style="Card.TFrame")
        stats.pack(fill=tk.X, padx=20, pady=(5, 0))
        self._history_stats = ttk.Label(
            stats, text="", style="Heading.TLabel", foreground=Theme.FG_SECONDARY
        )
        self._history_stats.pack(side=tk.LEFT, padx=10, pady=5)

        display_frame = ttk.Frame(tab)
        display_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)

        scrollbar = ttk.Scrollbar(display_frame, orient=tk.VERTICAL)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self._mult_display = MultiplierCanvas(display_frame, height=500, max_display=0)
        self._mult_display.MAX_DISPLAY = 0
        self._mult_display.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._mult_display.configure(yscrollcommand=scrollbar.set)
        scrollbar.configure(command=self._mult_display.yview)

        self._session_map: dict[str, int] = {}
        self.root.after(200, self._refresh_session_list)

    def _refresh_session_list(self):
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
            label = f"#{sid}  |  {start_str} -> {end_str}  |  {count} rounds"
            labels.append(label)
            self._session_map[label] = sid

        self._session_combo["values"] = labels
        if labels:
            self._session_combo.current(0)
            self._on_session_selected()
        else:
            self._session_combo.set("")
            self._mult_display.clear()
            self._history_stats.configure(text="No sessions found")

    def _on_session_selected(self):
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

        if mults:
            avg = sum(mults) / len(mults)
            above_2x = sum(1 for m in mults if m >= 2.0)
            pct = (above_2x / len(mults)) * 100
            mx = max(mults)
            self._history_stats.configure(
                text=f"{len(mults)} rounds  |  Avg: {avg:.2f}x  |  Max: {mx:.2f}x  |  >=2x: {above_2x} ({pct:.0f}%)"
            )
        else:
            self._history_stats.configure(text="Empty session")

    def _clear_history_view(self):
        self._mult_display.clear()
        self._history_stats.configure(text="")

    def _get_history_db(self) -> Database:
        return Database()

    @staticmethod
    def _format_ts(ts_str: str) -> str:
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
        self.config["username"] = self._username_entry.get().strip()
        self.config["password"] = self._password_entry.get().strip()
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
                if not hasattr(self, "_session_refreshed_for_run"):
                    self._session_refreshed_for_run = True
                    self._refresh_session_list()

            self.bot.on_multiplier = lambda m: self.root.after(0, _on_mult, m)
            self.root.after(0, lambda: self._status.configure(text="Status: Running"))
            self.bot.run()
        except Exception:
            logger.exception("Bot thread error")
        finally:
            self.bot_running = False
            if hasattr(self, "_session_refreshed_for_run"):
                del self._session_refreshed_for_run
            self.root.after(
                0,
                lambda: self._start_btn.configure(
                    text="Start Bot", style="Success.TButton"
                ),
            )
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

    def _toggle_password_visibility(self):
        if self._show_pw_var.get():
            self._password_entry.configure(show="")
        else:
            self._password_entry.configure(show="•")

    def _refresh_manual_buttons(self):
        for w in self._manual_frame.winfo_children():
            w.destroy()
        for i, s in enumerate(self.config.get("strategies", [])):
            if s.get("enabled", True):
                ttk.Button(
                    self._manual_frame,
                    text=f"Activate: {s.get('name', f'Strategy {i + 1}')}",
                    command=lambda idx=i: self._activate_primary(idx),
                    width=40,
                ).pack(pady=3, anchor=tk.W)
        if self.config.get("custom_strategy", {}).get("enabled"):
            ttk.Button(
                self._manual_frame,
                text="Activate: Custom",
                command=self._activate_custom,
                width=40,
            ).pack(pady=3, anchor=tk.W)
        # Re-bind mousewheel after rebuilding buttons
        bind_mousewheel(self._manual_frame, self._manual_canvas)

    def _activate_primary(self, idx: int):
        if not self.bot:
            messagebox.showwarning("Warning", "Bot not running")
            return
        self._autopilot_var.set(False)
        self.bot.command_queue.put({"action": "set_autopilot", "value": False})
        self.bot.command_queue.put({"action": "activate_primary", "index": idx})

    def _activate_custom(self):
        if not self.bot:
            messagebox.showwarning("Warning", "Bot not running")
            return
        self._autopilot_var.set(False)
        self.bot.command_queue.put({"action": "set_autopilot", "value": False})
        self.bot.command_queue.put({"action": "activate_custom"})

    # ── Strategy editing ────────────────────────────────────────────

    def _refresh_primary_cards(self):
        for w in self._primary_frame.winfo_children():
            w.destroy()
        self._primary_cards = []
        for i, s in enumerate(self.config.get("strategies", [])):
            card = StrategyCard(
                self._primary_frame,
                s,
                on_delete=lambda idx=i: self._delete_primary(idx),
            )
            self._primary_cards.append(card)
        bind_mousewheel(self._primary_frame, self._primary_canvas)

    def _add_primary(self):
        n = len(self.config.get("strategies", [])) + 1
        self.config.setdefault("strategies", []).append(
            {
                "name": f"Strategy {n}",
                "enabled": True,
                "base_bet": 1000,
                "auto_cashout": 2.0,
                "trigger_threshold": 2.0,
                "trigger_count": 5,
                "max_consecutive_losses": 10,
                "bet_multiplier": 2.0,
            }
        )
        self._refresh_primary_cards()

    def _delete_primary(self, idx: int):
        if messagebox.askyesno("Confirm", "Delete this strategy?"):
            del self.config["strategies"][idx]
            self._refresh_primary_cards()

    def _apply_changes(self):
        self.config["username"] = self._username_entry.get().strip()
        self.config["password"] = self._password_entry.get().strip()
        self.config["strategies"] = [c.get_data() for c in self._primary_cards]
        self.config["custom_strategy"] = self._custom_card.get_data()
        if self._save_config():
            self._refresh_manual_buttons()
            if self.bot and self.bot_running:
                self.bot.command_queue.put(
                    {"action": "reload_config", "config": self.config}
                )
            messagebox.showinfo("Success", "Changes applied!")

    def _on_close(self):
        if self.bot_running:
            if messagebox.askokcancel("Quit", "Bot running. Stop and quit?"):
                self._stop_bot()
                self.root.after(1000, self.root.destroy)
        else:
            self.root.destroy()

    def run(self):
        self.root.mainloop()


# ── Custom Strategy Config Card ─────────────────────────────────────


class CustomConfigCard(ttk.Frame):
    """Config card for the custom strategy with descriptions, activation triggers, and signal confirmation."""

    FIELDS = [
        (
            "base_bet",
            "Base Bet:",
            float,
            "Starting bet amount for each betting sequence.",
        ),
        (
            "auto_cashout",
            "Auto Cashout:",
            float,
            "Multiplier at which to automatically cash out (e.g. 2.0 = double).",
        ),
        (
            "max_consecutive_losses",
            "Max Losses:",
            int,
            "Stop betting after this many consecutive losses.",
        ),
        (
            "max_losses_in_window",
            "Max Losses in Window:",
            int,
            "Stop if this many losses occur within the rolling window.",
        ),
        (
            "loss_check_window",
            "Window Size:",
            int,
            "Number of recent outcomes to track for the rolling loss check.",
        ),
        (
            "bet_multiplier",
            "Bet Multiplier:",
            float,
            "Multiply bet by this after each loss (martingale factor).",
        ),
        (
            "stop_profit_count",
            "Stop After Wins (0=off):",
            int,
            "Automatically stop after reaching this many wins. 0 to disable.",
        ),
        (
            "cooldown_after_win",
            "Cooldown After Win (0=off):",
            int,
            "Number of rounds to skip betting after each win. 0 to disable.",
        ),
        (
            "cooldown_after_loss",
            "Cooldown After Loss (0=off):",
            int,
            "Number of rounds to skip betting after each loss. 0 to disable.",
        ),
    ]

    CONFIRM_FIELDS = [
        (
            "signal_confirm_threshold",
            "Confirm Threshold:",
            float,
            "Multiplier threshold for signal confirmation (e.g. 2.0).",
        ),
        (
            "signal_confirm_count",
            "Confirm Count:",
            int,
            "How many rounds must exceed the threshold to confirm a signal.",
        ),
        (
            "signal_confirm_window",
            "Confirm Window:",
            int,
            "Number of recent rounds to check for confirmation.",
        ),
        (
            "signal_monitor_rounds",
            "Monitor Rounds:",
            int,
            "Max rounds to watch for confirmation after a signal before giving up.",
        ),
    ]

    TRIGGER_FLAGS = [
        (
            "activate_on_strong_hotstreak",
            "Activate on Strong Hotstreak",
            "Start betting immediately when 75%+ of recent rounds are above 2x.",
        ),
        (
            "activate_on_weak_hotstreak",
            "Activate on Weak Hotstreak",
            "Start betting immediately when 65%+ of recent rounds are above 2x.",
        ),
        (
            "activate_on_rule_of_17",
            "Activate on Rule of 17",
            "Trigger after a hotstreak if no cold streak occurs within 15 rounds.",
        ),
        (
            "activate_on_pre_streak_pattern",
            "Activate on Pre-Streak Pattern",
            "Trigger when avg > 3.75x with high volatility in the last 10 rounds.",
        ),
        (
            "activate_on_high_deviation_10",
            "Activate on High Std Dev (10-round)",
            "Trigger when standard deviation exceeds 25 in a 10-round window.",
        ),
        (
            "activate_on_high_deviation_15",
            "Activate on High Std Dev (15-round)",
            "Trigger when standard deviation exceeds 25 in a 15-round window.",
        ),
    ]

    def __init__(self, parent, data: dict):
        super().__init__(parent, style="Card.TFrame")
        self.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)
        self._entries: Dict[str, tuple] = {}
        self._trigger_vars: Dict[str, tk.BooleanVar] = {}

        # Scrollable content
        canvas = tk.Canvas(self, bg=Theme.BG_LIGHT, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self, orient=tk.VERTICAL, command=canvas.yview)
        inner = ttk.Frame(canvas, style="Card.TFrame")

        inner.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=inner, anchor=tk.NW, tags="inner")
        canvas.bind(
            "<Configure>", lambda e: canvas.itemconfigure("inner", width=e.width)
        )
        canvas.configure(yscrollcommand=scrollbar.set)

        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Bind mousewheel scrolling to the inner frame and all children
        bind_mousewheel(inner, canvas)

        # ── Title ──────────────────────────────────────────────────
        ttk.Label(
            inner,
            text="Custom Strategy",
            font=("Segoe UI", 12, "bold"),
            style="Heading.TLabel",
        ).pack(pady=(0, 5))
        ttk.Label(
            inner,
            text=(
                "Signal & hotstreak-based betting. Hotstreaks trigger instant betting. "
                "Signals start a monitoring period to confirm a promising pattern before betting begins."
            ),
            wraplength=500,
            style="Desc.TLabel",
        ).pack(pady=(0, 15))

        # ── Betting parameters ─────────────────────────────────────
        self._section_label(inner, "Betting Parameters")
        for key, label, typ, desc in self.FIELDS:
            self._add_field(inner, key, label, typ, desc, data)

        # ── Signal confirmation ────────────────────────────────────
        self._section_label(inner, "Signal Confirmation")
        ttk.Label(
            inner,
            text=(
                "When a signal fires, check if the last N rounds already show a promising pattern. "
                "If not, monitor upcoming rounds (up to the monitor limit) before committing."
            ),
            wraplength=500,
            style="Desc.TLabel",
        ).pack(anchor=tk.W, padx=10, pady=(0, 8))
        for key, label, typ, desc in self.CONFIRM_FIELDS:
            self._add_field(inner, key, label, typ, desc, data)

        # ── Activation triggers ────────────────────────────────────
        self._section_label(inner, "Activation Triggers")
        ttk.Label(
            inner,
            text=(
                "Hotstreak triggers bet immediately. Signal triggers go through confirmation first."
            ),
            wraplength=500,
            style="Desc.TLabel",
        ).pack(anchor=tk.W, padx=10, pady=(0, 8))
        for key, label, desc in self.TRIGGER_FLAGS:
            var = tk.BooleanVar(value=data.get(key, False))
            cb = ttk.Checkbutton(
                inner, text=label, variable=var, style="Switch.TCheckbutton"
            )
            cb.pack(anchor=tk.W, padx=10, pady=1)
            ttk.Label(inner, text=desc, style="Desc.TLabel", wraplength=480).pack(
                anchor=tk.W, padx=30, pady=(0, 4)
            )
            self._trigger_vars[key] = var

        # ── Enabled ────────────────────────────────────────────────
        self.enabled_var = tk.BooleanVar(value=data.get("enabled", False))
        ttk.Checkbutton(
            inner,
            text="Enabled",
            variable=self.enabled_var,
            style="Switch.TCheckbutton",
        ).pack(pady=10)

        # Re-bind after all widgets are created
        bind_mousewheel(inner, canvas)

    def _section_label(self, parent, text: str):
        f = ttk.Frame(parent, style="Card.TFrame")
        f.pack(fill=tk.X, pady=(12, 4))
        ttk.Label(
            f, text=text, font=("Segoe UI", 11, "bold"), style="Heading.TLabel"
        ).pack(anchor=tk.W, padx=10)

    def _add_field(
        self, parent, key: str, label: str, typ: type, desc: str, data: dict
    ):
        f = ttk.Frame(parent, style="Card.TFrame")
        f.pack(fill=tk.X, pady=2, padx=10)
        ttk.Label(f, text=label, width=25, style="TLabel").pack(side=tk.LEFT)
        e = ttk.Entry(f, style="TEntry", width=20)
        e.insert(0, str(data.get(key, "")))
        e.pack(side=tk.LEFT, padx=5)
        ttk.Label(f, text=desc, style="Desc.TLabel").pack(side=tk.LEFT, padx=(5, 0))
        self._entries[key] = (e, typ)

    def get_data(self) -> dict:
        out = {}
        for key, (entry, typ) in self._entries.items():
            try:
                out[key] = typ(entry.get())
            except (ValueError, TypeError):
                out[key] = typ()
        for key, var in self._trigger_vars.items():
            out[key] = var.get()
        out["enabled"] = self.enabled_var.get()
        return out
