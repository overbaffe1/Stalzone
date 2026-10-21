"""Схематичная карта Зоны на tkinter.Canvas: поселения, маршрут, прибыль.

Никаких внешних картинок: всё рисуется кодом в тёмном сталкерском стиле.
Координаты поселений — data/map_settlements.json (0..1, y=0 это север).
"""
from __future__ import annotations

import tkinter as tk
from typing import Any, Callable

BG = "#0B0F0C"
BAND_N = "#130C11"   # север — красноватый
BAND_M = "#0C1210"
BAND_S = "#0A100B"   # юг — зеленее
GRID = "#1B241F"
ROAD = "#2E3833"
GOLD = "#FFC857"
TEXT = "#E8EDE9"
DIM = "#8A948E"

# дороги между поселениями (короткие имена из map_settlements.json)
ROADS = [
    ("Завет", "Север"), ("Север", "Рубеж"), ("Рубеж", "Бар"),
    ("Бар", "Гейгер"), ("Бар", "Наёмники"), ("Бар", "Зари"),
    ("Зари", "Пожарка"), ("Зари", "Школа"), ("Школа", "Лодочная"),
    ("Школа", "Атаман"), ("Пожарка", "Атаман"),
]


def fmt_short(x: float) -> str:
    """+1.9 млн / +65 тыс / +850 — компактно для бейджей на карте."""
    sign = "+" if x >= 0 else "−"
    a = abs(x)
    if a >= 1_000_000:
        return f"{sign}{a / 1_000_000:.1f} млн".replace(".", ",")
    if a >= 10_000:
        return f"{sign}{a / 1_000:.0f} тыс"
    if a >= 1_000:
        return f"{sign}{a / 1_000:.1f} тыс".replace(".", ",")
    return f"{sign}{a:,.0f}".replace(",", " ")


class MapView(tk.Canvas):
    """Канвас карты. Маркеры ставит приложение через set_markers()."""

    def __init__(self, master: Any,
                 on_select: Callable[[str], None] | None = None,
                 hover_var: Any = None, **kw: Any):
        super().__init__(master, bg=BG, highlightthickness=0, **kw)
        self._on_select = on_select
        self._hover_var = hover_var
        self._markers: list[dict[str, Any]] = []
        self._pos: dict[str, tuple[float, float]] = {}
        self._home_txt = ""
        self._route: list[str] = []
        self._after_id: str | None = None
        self.bind("<Configure>", self._on_resize)
        self.bind("<Button-1>", self._on_click)
        self.bind("<Motion>", self._on_hover)
        self.bind("<Leave>", lambda _e: self._set_hover(""))

    # -- данные ------------------------------------------------------
    def set_markers(self, markers: list[dict[str, Any]],
                    route: list[str] | None = None,
                    home_txt: str = "") -> None:
        """markers: [{station, x, y, color, short, num|None, badge|None}]."""
        self._markers = markers
        self._route = route or []
        self._home_txt = home_txt
        self.draw()

    # -- события -----------------------------------------------------
    def _on_resize(self, _e: Any = None) -> None:
        if self._after_id:
            self.after_cancel(self._after_id)
        self._after_id = self.after(150, self.draw)

    def _hit(self, x: float, y: float) -> dict[str, Any] | None:
        best = None
        for m in self._markers:
            px, py = self._pos.get(m["station"], (None, None))
            if px is None:
                continue
            if abs(px - x) < 16 and abs(py - y) < 16:
                best = m
        return best

    def _on_click(self, e: Any) -> None:
        m = self._hit(e.x, e.y)
        if m and self._on_select:
            self._on_select(m["station"])

    def _set_hover(self, txt: str) -> None:
        if self._hover_var is not None:
            try:
                self._hover_var.set(txt)
            except Exception:  # noqa: BLE001 - виджет уже мёртв
                pass

    def _on_hover(self, e: Any) -> None:
        m = self._hit(e.x, e.y)
        if m:
            extra = f" · {m['badge']}" if m.get("badge") else ""
            self._set_hover(f"📍 {m['short']}{extra} — клик: что тут делать")
        else:
            self._set_hover("")

    # -- рисование ---------------------------------------------------
    def _xy(self, x: float, y: float, w: float, h: float,
            pad: int = 46) -> tuple[float, float]:
        return pad + x * (w - 2 * pad), pad + y * (h - 2 * pad - 8)

    def draw(self) -> None:
        self.delete("all")
        w = max(self.winfo_width(), 200)
        h = max(self.winfo_height(), 200)
        pad = 46
        self._pos = {}
        # полосы севера/центра/юга
        self.create_rectangle(0, 0, w, h * 0.33, fill=BAND_N, outline="")
        self.create_rectangle(0, h * 0.33, w, h * 0.66, fill=BAND_M,
                              outline="")
        self.create_rectangle(0, h * 0.66, w, h, fill=BAND_S, outline="")
        # сетка
        for i in range(1, 10):
            gx = pad + i / 10 * (w - 2 * pad)
            gy = pad + i / 10 * (h - 2 * pad - 8)
            self.create_line(gx, 0, gx, h, fill=GRID)
            self.create_line(0, gy, w, gy, fill=GRID)
        # подписи зон
        self.create_text(w - 10, 14, text="СЕВЕР · Припять", fill=DIM,
                         anchor="ne", font=("Arial", 9, "bold"))
        self.create_text(w - 10, h - 10, text="ЮГ · новички", fill=DIM,
                         anchor="se", font=("Arial", 9, "bold"))
        self.create_text(12, 14, text="⬆ С", fill=TEXT, anchor="nw",
                         font=("Arial", 11, "bold"))
        self.create_text(w / 2, 14,
                         text="⚖ Аукцион общий на регион — купи/продай с любой базы",
                         fill=DIM, font=("Arial", 9))
        by_short = {m["short"]: m for m in self._markers}
        for a, b in ROADS:
            if a in by_short and b in by_short:
                x1, y1 = self._xy(by_short[a]["x"], by_short[a]["y"], w, h)
                x2, y2 = self._xy(by_short[b]["x"], by_short[b]["y"], w, h)
                self.create_line(x1, y1, x2, y2, fill=ROAD, width=2)
        # маршрут
        pts = []
        for st in self._route:
            m = next((k for k in self._markers if k["station"] == st), None)
            if m:
                pts.append(self._xy(m["x"], m["y"], w, h))
        if len(pts) >= 2:
            self.create_line(*[c for p in pts for c in p], fill=GOLD,
                             width=3, dash=(8, 5))
        # маркеры
        for m in self._markers:
            px, py = self._xy(m["x"], m["y"], w, h)
            self._pos[m["station"]] = (px, py)
            active = m.get("num") is not None
            self.create_oval(px - 10, py - 10, px + 10, py + 10,
                             fill=m["color"], outline=GOLD if active else BG,
                             width=3 if active else 2)
            if active:
                self.create_oval(px - 9, py - 30, px + 9, py - 12,
                                 fill=GOLD, outline="")
                self.create_text(px, py - 21, text=str(m["num"]),
                                 fill="#111111", font=("Arial", 10, "bold"))
            self.create_text(px, py + 20, text=m["short"], fill=TEXT,
                             font=("Arial", 10, "bold"))
            if m.get("badge"):
                self.create_text(px, py + 34, text=m["badge"], fill="#7CE38B",
                                 font=("Arial", 9, "bold"))
        # своя база
        if self._home_txt:
            self.create_text(w - 12, h - 30, text="🏠 " + self._home_txt,
                             fill=TEXT, anchor="se", font=("Arial", 10, "bold"))
        self.create_text(12, h - 10, text="схема, не масштаб",
                         fill=DIM, anchor="sw", font=("Arial", 8))
