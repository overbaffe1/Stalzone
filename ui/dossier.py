"""Досье предмета: окно-энциклопедия (цены, тиры, график, рецепты, цель).

Всё из локальной базы (SQLite) — новых запросов к API не делает.
Вызывается из главного потока.
"""
from __future__ import annotations

import statistics
import tkinter as tk
from typing import Any

GOLD = "#FFC857"
GREEN = "#7CE38B"
DIM = "#8A948E"


def _sparkline(parent: Any, values: list[float], width: int = 500,
               height: int = 90) -> None:
    cv = tk.Canvas(parent, width=width, height=height, bg="#0B0F0C",
                   highlightthickness=0)
    cv.pack(fill="x", padx=8, pady=4)
    if len(values) < 2:
        cv.create_text(width / 2, height / 2, text="мало продаж для графика",
                       fill=DIM, font=("Arial", 10))
        return
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    pad = 8
    n = len(values)
    pts = []
    for k, v in enumerate(values):
        x = pad + k / (n - 1) * (width - 2 * pad)
        y = height - pad - (v - lo) / span * (height - 2 * pad)
        pts.extend([x, y])
    cv.create_line(*pts, fill=GOLD, width=2)
    cv.create_oval(pts[-2] - 4, pts[-1] - 4, pts[-2] + 4, pts[-1] + 4,
                   fill=GREEN, outline="")
    cv.create_text(pad + 2, pad, text=f"{hi:,.0f}".replace(",", " "),
                   fill=DIM, anchor="nw", font=("Arial", 8))
    cv.create_text(pad + 2, height - pad, text=f"{lo:,.0f}".replace(",", " "),
                   fill=DIM, anchor="sw", font=("Arial", 8))


def open_dossier(app: Any, ctx: dict[str, Any], item_id: str) -> None:
    """Открыть окно досье. ctx: helpers + store + recipes (см. ui/app.py)."""
    import customtkinter as ctk

    from szmarket.arbitrage import summarize_by_quality, summarize_sales

    store = ctx["store"]
    region = ctx["region"]
    fmt_money = ctx["fmt_money"]
    name_of = ctx["name_of"]
    tk_icon = ctx["tk_icon"]
    recipes = ctx["recipes"]
    station_short = ctx["station_short"]
    get_target = ctx["get_target"]
    set_target = ctx["set_target"]

    win = ctk.CTkToplevel(app)
    win.title(f"Досье: {name_of(item_id)}")
    win.geometry("560x700")
    win.transient(app)

    head = ctk.CTkFrame(win)
    head.pack(fill="x", padx=10, pady=(10, 5))
    img = tk_icon(item_id, 56)
    if img is not None:
        ctk.CTkLabel(head, text="", image=img).pack(side="left", padx=8,
                                                    pady=6)
    ctk.CTkLabel(head, text=f"{name_of(item_id)}\nID: {item_id}",
                 font=ctk.CTkFont(size=14, weight="bold"), justify="left",
                 anchor="w").pack(side="left", padx=4)

    # цель сторожевого пса
    tgt = ctk.CTkFrame(win)
    tgt.pack(fill="x", padx=10, pady=5)
    ctk.CTkLabel(tgt, text="🎯 Сторож: брать если лот дешевле").pack(
        side="left", padx=8)
    cur = get_target(item_id)
    tgt_var = ctk.StringVar(value=f"{cur:g}" if cur else "")
    ctk.CTkEntry(tgt, textvariable=tgt_var, width=120,
                 placeholder_text="рублей").pack(side="left", padx=4)

    def save_tgt() -> None:
        raw = tgt_var.get().strip().replace(" ", "")
        try:
            val = float(raw) if raw else 0.0
        except ValueError:
            val = 0.0
        set_target(item_id, val if val > 0 else None)
        win.destroy()

    ctk.CTkButton(tgt, text="OK", width=60, command=save_tgt).pack(
        side="left", padx=4)

    box = ctk.CTkScrollableFrame(win, label_text="Рынок")
    box.pack(fill="both", expand=True, padx=10, pady=5)

    def line(txt: str, bold: bool = False) -> None:
        ctk.CTkLabel(box, text=txt, anchor="w", justify="left",
                     font=ctk.CTkFont(size=12, weight="bold" if bold else "normal")
                     ).pack(fill="x", padx=8, pady=1)

    recs = store.recent_sales(item_id, region, 2000)
    sm = summarize_sales(recs)
    if sm["count"] == 0:
        line("Продаж в базе нет — нажмите «🔄 Обновить цены».", True)
        return
    line(f"Продаж: {sm['count']} · скорость {sm['velocity_per_day']:.1f}/день",
         True)
    line(f"Медиана {fmt_money(sm['median'])} · P25 {fmt_money(sm['p25'])} · "
         f"P75 {fmt_money(sm['p75'])}")
    line(f"Мин {fmt_money(sm['min'])} · макс {fmt_money(sm['max'])} · "
         f"оборот {fmt_money(sm['turnover'])}")
    qb = summarize_by_quality(recs)
    tiers = sorted(qb.get("qlt", {}).items())
    if tiers:
        from szmarket.arbitrage import QLT_NAMES

        line("Тиры качества:", True)
        for q, s in tiers:
            line(f"  тир {q} ({QLT_NAMES.get(q, '?')}): "
                 f"{fmt_money(s['median'])} × {s['count']} шт")
    line("Динамика цены (продажи, слева старые):", True)
    _sparkline(box, [float(r["unit_price"]) for r in reversed(recs)][-120:])

    # лучший час
    buckets: dict[int, list[float]] = {}
    for r in recs:
        t = str(r.get("sold_at") or "")
        try:
            hr = int(t[11:13]) if len(t) >= 13 else -1
        except ValueError:
            hr = -1
        if 0 <= hr <= 23 and r.get("unit_price"):
            buckets.setdefault(hr, []).append(float(r["unit_price"]))
    med = {h: statistics.median(v) for h, v in buckets.items() if len(v) >= 5}
    if len(med) >= 4:
        bh, wh = max(med, key=med.get), min(med, key=med.get)
        line(f"Лучший час продажи: {bh:02d}:00 UTC ({fmt_money(med[bh])}), "
             f"худший {wh:02d}:00 UTC ({fmt_money(med[wh])}).")

    # живые лоты
    lots = [l for l in store.latest_lots(item_id, region)
            if (l.get("unit_price") or 0) > 0]
    lots.sort(key=lambda l: l["unit_price"])
    if lots:
        line(f"Живых лотов: {len(lots)}, дешевле всего:", True)
        for l in lots[:3]:
            line(f"  ×{l.get('amount', 1)} по {fmt_money(l['unit_price'])} "
                 f"(до {str(l.get('end_time', '?'))[:16].replace('T', ' ')})")

    # рецепты
    made = [r for r in recipes if r.product_id == item_id][:8]
    used = [r for r in recipes if
            any(i == item_id for i, _ in r.requirements)][:8]
    if made:
        line(f"Где добыть (рецептов: {len(made)}+):", True)
        for r in made[:6]:
            if r.kind == "hideout":
                line(f"  🔨 {r.station}"
                     + (f" ({r.skill} {r.skill_level})" if r.skill else ""))
            else:
                need = ", ".join(f"×{n:g}" for _, n in r.requirements)
                line(f"  🔄 {station_short(r.station)}: {need or ''}"
                     f"{' + деньги' if r.money else ''}")
    if used:
        line(f"Где нужен (рецептов: {len(used)}+):", True)
        for r in used[:6]:
            line(f"  → {name_of(r.product_id)} @ {station_short(r.station)}")
