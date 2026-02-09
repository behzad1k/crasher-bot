"""Reusable GUI widgets."""

import tkinter as tk
from tkinter import ttk
from typing import Callable, Dict, Optional

from crasher_bot.ui import Theme


class MultiplierCanvas(tk.Canvas):
    """Displays recent multipliers as colored tiles."""

    MAX_DISPLAY = 50
    ITEMS_PER_ROW = 14

    def __init__(self, parent, max_display: int | None = None, **kw):
        super().__init__(parent, bg=Theme.BG_DARK, highlightthickness=0, **kw)
        if max_display is not None:
            self.MAX_DISPLAY = max_display
        self.multipliers: list[float] = []
        self.bind("<Configure>", lambda _: self.draw())

        # Enable mousewheel scrolling
        self.bind("<MouseWheel>", self._on_mousewheel)
        self.bind("<Button-4>", lambda e: self.yview_scroll(-3, "units"))
        self.bind("<Button-5>", lambda e: self.yview_scroll(3, "units"))

    def _on_mousewheel(self, event):
        self.yview_scroll(-1 * (event.delta // 120), "units")

    def add(self, mult: float):
        self.multipliers.append(mult)
        if self.MAX_DISPLAY and len(self.multipliers) > self.MAX_DISPLAY:
            self.multipliers.pop(0)
        self.draw()

    def clear(self):
        self.multipliers.clear()
        self.draw()

    def draw(self):
        self.delete("all")
        if not self.multipliers:
            self.configure(scrollregion=(0, 0, 0, 0))
            return
        pad, gap = 5, 3
        w = self.winfo_width() or 800
        iw = (w - pad * 2 - gap * (self.ITEMS_PER_ROW - 1)) / self.ITEMS_PER_ROW
        ih = 30

        for i, m in enumerate(self.multipliers):
            r, c = divmod(i, self.ITEMS_PER_ROW)
            x = pad + c * (iw + gap)
            y = pad + r * (ih + gap)

            if m <= 2.0:
                color = Theme.MULT_LOW
            elif m <= 5.0:
                color = Theme.MULT_MEDIUM
            elif m <= 10.0:
                color = Theme.MULT_HIGH
            else:
                color = Theme.MULT_MEGA

            self._rounded_rect(x, y, x + iw, y + ih, fill="#000")
            self.create_text(
                x + iw / 2, y + ih / 2,
                text=f"{m:.2f}x",
                fill=color,
                font=("Segoe UI", 10, "bold"),
            )

        # Update scrollregion to fit all rows
        total_rows = (len(self.multipliers) + self.ITEMS_PER_ROW - 1) // self.ITEMS_PER_ROW
        total_height = pad * 2 + total_rows * (ih + gap)
        self.configure(scrollregion=(0, 0, w, total_height))

    def _rounded_rect(self, x1, y1, x2, y2, r=5, **kw):
        pts = [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]
        return self.create_polygon(pts, smooth=True, **kw)


class StrategyCard(ttk.Frame):
    """Editable card for a primary strategy."""

    FIELDS_LEFT = [
        ("name", "Name:", str),
        ("base_bet", "Base Bet:", float),
        ("auto_cashout", "Cashout:", float),
        ("trigger_threshold", "Trigger <:", float),
    ]
    FIELDS_RIGHT = [
        ("trigger_count", "Trigger Count:", int),
        ("max_consecutive_losses", "Max Losses:", int),
        ("bet_multiplier", "Bet Mult:", float),
    ]

    def __init__(self, parent, data: dict, on_delete: Optional[Callable] = None):
        super().__init__(parent, style="Card.TFrame")
        self.pack(fill=tk.X, pady=5)
        self._entries: Dict[str, ttk.Entry] = {}
        self.enabled_var = tk.BooleanVar(value=data.get("enabled", True))

        # Header
        hdr = ttk.Frame(self, style="Card.TFrame")
        hdr.pack(fill=tk.X, padx=10, pady=5)
        ttk.Label(hdr, text=data.get("name", "Strategy"), font=("Segoe UI", 11, "bold"), style="Heading.TLabel").pack(side=tk.LEFT)
        if on_delete:
            ttk.Button(hdr, text="Delete", command=on_delete, style="Danger.TButton", width=10).pack(side=tk.RIGHT)

        content = ttk.Frame(self, style="Card.TFrame")
        content.pack(fill=tk.X, padx=10, pady=5)
        left = ttk.Frame(content, style="Card.TFrame")
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))
        right = ttk.Frame(content, style="Card.TFrame")
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        for key, label, _ in self.FIELDS_LEFT:
            self._add_entry(left, key, label, data)
        for key, label, _ in self.FIELDS_RIGHT:
            self._add_entry(right, key, label, data)

        ttk.Checkbutton(self, text="Enabled", variable=self.enabled_var, style="Switch.TCheckbutton").pack(anchor=tk.W, padx=10, pady=2)

    def _add_entry(self, parent, key, label, data):
        f = ttk.Frame(parent, style="Card.TFrame")
        f.pack(fill=tk.X, pady=2)
        ttk.Label(f, text=label, width=16, style="TLabel").pack(side=tk.LEFT)
        e = ttk.Entry(f, style="TEntry")
        e.insert(0, str(data.get(key, "")))
        e.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._entries[key] = e

    def get_data(self) -> dict:
        out = {}
        all_fields = self.FIELDS_LEFT + self.FIELDS_RIGHT
        for key, _, typ in all_fields:
            val = self._entries[key].get()
            try:
                out[key] = typ(val)
            except (ValueError, TypeError):
                out[key] = typ() if typ != str else val
        out["enabled"] = self.enabled_var.get()
        return out


class SimpleConfigCard(ttk.Frame):
    """Generic config card with key-value fields."""

    def __init__(self, parent, title: str, description: str, fields: list, data: dict):
        super().__init__(parent, style="Card.TFrame")
        self.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)
        self._entries: Dict[str, ttk.Entry] = {}

        ttk.Label(self, text=title, font=("Segoe UI", 12, "bold"), style="Heading.TLabel").pack(pady=(0, 10))
        ttk.Label(self, text=description, wraplength=500, style="TLabel").pack(pady=(0, 15))

        for key, label, typ in fields:
            f = ttk.Frame(self, style="Card.TFrame")
            f.pack(fill=tk.X, pady=5)
            ttk.Label(f, text=label, width=25, style="TLabel").pack(side=tk.LEFT)
            e = ttk.Entry(f, style="TEntry", width=30)
            e.insert(0, str(data.get(key, "")))
            e.pack(side=tk.LEFT, padx=10)
            self._entries[key] = (e, typ)

        self.enabled_var = tk.BooleanVar(value=data.get("enabled", True))
        ttk.Checkbutton(self, text="Enabled", variable=self.enabled_var, style="Switch.TCheckbutton").pack(pady=10)

    def get_data(self) -> dict:
        out = {}
        for key, (entry, typ) in self._entries.items():
            try:
                out[key] = typ(entry.get())
            except (ValueError, TypeError):
                out[key] = typ()
        out["enabled"] = self.enabled_var.get()
        return out
