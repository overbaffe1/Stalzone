"""Десктопный интерфейс SZMarket (CustomTkinter) — на русском.

Запуск на своём ПК:
  pip install -r requirements-ui.txt
  copy .env.example .env   (вписать Client Secret)
  python -m ui.app

Окно работает рядом с игрой: список предметов, выгодные лоты, крафты,
снайпер и КАРТА Зоны с маршрутом рейса (куда прийти, что купить,
где обменять, где продать).

Потоки: сеть и база — в фоновых потоках; все виджеты создаются
и меняются ТОЛЬКО в главном потоке через app.after().
"""
from __future__ import annotations

import concurrent.futures as cf
import datetime as dt
import json
import threading
import traceback
from pathlib import Path
from typing import Any

EAPI_LOCK = threading.Lock()  # сериализуем сетевые запросы между кнопками


def _fmt_money(x: float) -> str:
    return f"{x:,.0f}".replace(",", " ") + " ₽"


def _fmt_num(x: float) -> str:
    return f"{x:,.0f}".replace(",", " ")


def _load_json(path: str, default: Any) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


KIND_ICON = {"craft": "🔨", "hideout": "🔨", "barter": "🔄", "shop": "🏪"}
ACCENT = "#FFC857"      # золото для прибыли и маршрута
GREEN = "#7CE38B"
RED = "#FF7A7A"
TEXT = "#E8EDE9"


def main() -> None:
    try:
        import customtkinter as ctk
    except ImportError:
        print("Нет CustomTkinter. Выполните: pip install -r requirements-ui.txt")
        return

    from szmarket import catalog as catmod
    from szmarket.arbitrage import (
        QLT_NAMES, find_deals, summarize_by_quality, summarize_sales)
    from szmarket.config import Settings
    from szmarket.craft import evaluate_all, find_swaps, load_recipes
    from szmarket.eapi import EapiClient, EapiError, TokenBucket
    from szmarket.mirror import MirrorClient
    from szmarket.route import build_trip, load_map
    from szmarket.snipe import analyze_ending
    from szmarket.store import MarketStore
    from szmarket.zonemap import (apply_calibration, ensure_background,
                                  load_calibration, save_calibration)
    from szmarket import gamemap as gamemod
    from szmarket import waypoints as wpmod
    from ui.dossier import open_dossier
    from ui.mapview import MapView, fmt_short

    s = Settings.load()
    store = MarketStore(s.db_path)
    cat = catmod.load_compact_catalog("data/catalog.json")
    overrides = catmod.load_overrides("data/overrides.json")
    recipes = load_recipes("data/recipes.json")
    settlements = load_map("data/map_settlements.json")
    watch_data = _load_json("data/watchlist.json", {}) or {}
    watch_items: list[str] = list(
        watch_data.get("items") or ["g4lk0", "96430", "g5np", "y1q9"])
    enabled: dict[str, bool] = dict(watch_data.get("enabled") or {})
    targets: dict[str, float] = {k: float(v) for k, v in
                                 (watch_data.get("targets") or {}).items()}
    pins: set[str] = set(watch_data.get("pins") or [])
    notes: dict[str, str] = {k: str(v) for k, v in
                             (watch_data.get("notes") or {}).items()}
    theme_mode = str(watch_data.get("theme") or "dark")
    region_default = str(watch_data.get("region") or s.region)
    theme_state = {"mode": theme_mode}

    def name_of(iid: str) -> str:
        return catmod.display_name(iid, cat, overrides)

    def display(iid: str) -> str:
        nm = name_of(iid)
        return nm if nm != iid else iid

    # ---- станции/поселения для фильтра крафтов ---------------------------
    stations = sorted({r.station for r in recipes if r.station})
    ZARYA_OPT = "⭐ Своя база + База «Зари»"
    station_options = [ZARYA_OPT, "Всё"] + stations

    def station_match(rec: Any, opt: str) -> bool:
        """Фильтр мест крафта/обмена. Своя база = убежище (Верстак, столы)."""
        if opt == "Всё":
            return True
        if opt == ZARYA_OPT:
            if rec.kind == "hideout":
                return True
            low = (rec.station or "").lower()
            return ("зари" in low) or ("заря" in low)
        return rec.station == opt

    # обратимые обмены 1:1 («качели»): купи дешёвую сторону, обменяй, продай
    swap_pairs = {(d["a"], d["b"]) for d in find_swaps(recipes)}

    def station_short(st: str) -> str:
        info = settlements.get(st)
        if info and info.get("short"):
            return str(info["short"])
        return st.replace("Бартер · ", "")

    def is_swap(product_id: str, inputs: list[str]) -> bool:
        return (len(inputs) == 1
                and (min(inputs[0], product_id), max(inputs[0], product_id))
                in swap_pairs)

    ctk.set_appearance_mode(theme_mode)
    ctk.set_default_color_theme("dark-blue")
    app = ctk.CTk()
    app.title("SZMarket — аукцион STALCRAFT: крафты, сделки, карта")
    app.geometry("1220x800")
    title_font = ctk.CTkFont(size=13, weight="bold")
    sub_font = ctk.CTkFont(size=12)

    img_cache: dict[str, Any] = {}
    last_rows: list[dict] = []  # для кнопки «Копировать»
    craft_evs: list[dict] = []  # все оценённые крафты (для карты)
    trip: dict[str, dict] = {}  # recipe_id -> ev, рейс пользователя
    sales_counts: dict[str, int] = {}

    def tk_icon(item_id: str, size: int = 44) -> Any | None:
        """CTkImage из кэша (вызывать ТОЛЬКО в главном потоке)."""
        if not item_id:
            return None
        key = f"{item_id}:{size}"
        if key in img_cache:
            return img_cache[key]
        from szmarket import icons as iconsmod

        pil = iconsmod.load_icon_image(item_id, cat, size=size)
        if pil is None:
            return None
        try:
            img = ctk.CTkImage(light_image=pil, dark_image=pil,
                               size=(size, size))
        except Exception:  # noqa: BLE001 - без иконки тоже можно жить
            return None
        img_cache[key] = img
        return img

    def prefetch_icons(ids: list[str]) -> None:
        """Прогреть дисковый кэш иконок (вызывать из фонового потока)."""
        from szmarket import icons as iconsmod

        for iid in dict.fromkeys(i for i in ids if i):
            iconsmod.icon_file(iid, cat)

    # ---- шапка ---------------------------------------------------------
    top = ctk.CTkFrame(app)
    top.pack(fill="x", padx=10, pady=(10, 5))
    ctk.CTkLabel(top, text="SZMarket", font=ctk.CTkFont(size=17, weight="bold"),
                 text_color=ACCENT).pack(side="left", padx=(8, 2))
    creds_txt = ("✅ EAPI" if s.has_credentials
                 else "🪞 Зеркало (без ключа)")
    ctk.CTkLabel(top, text=creds_txt).pack(side="left", padx=8)
    emis_var = ctk.StringVar(value="☢ …")
    ctk.CTkLabel(top, textvariable=emis_var).pack(side="left", padx=4)
    ctk.CTkButton(top, text="🔄", width=36,
                  command=lambda: refresh_emission()).pack(side="left")
    ctk.CTkLabel(top, text="Регион:").pack(side="left", padx=(12, 2))
    region_var = ctk.StringVar(value=region_default)
    ctk.CTkEntry(top, textvariable=region_var, width=60).pack(side="left")
    ctk.CTkLabel(top, text="Часов до конца:").pack(side="left", padx=(12, 2))
    hours_var = ctk.StringVar(value="3")
    ctk.CTkEntry(top, textvariable=hours_var, width=50).pack(side="left")
    ctk.CTkLabel(top, text="Мин. продаж:").pack(side="left", padx=(12, 2))
    min_sales_var = ctk.StringVar(value="5")
    ctk.CTkEntry(top, textvariable=min_sales_var, width=50).pack(side="left")
    icons_var = ctk.StringVar(value="🖼 Иконки: …")
    ctk.CTkLabel(top, textvariable=icons_var).pack(side="right", padx=8)
    theme_btn = ctk.CTkButton(top, text="🌙", width=40,
                              command=lambda: toggle_theme())
    theme_btn.pack(side="right", padx=2)
    ctk.CTkButton(top, text="🪪 Сталкер", width=100,
                  command=lambda: open_stalker()).pack(side="right", padx=2)
    ctk.CTkButton(top, text="🏰 Кланы", width=90,
                  command=lambda: open_clans()).pack(side="right", padx=2)

    # ---- поиск + фильтры --------------------------------------------------
    search_fr = ctk.CTkFrame(app)
    search_fr.pack(fill="x", padx=10, pady=5)
    ctk.CTkLabel(search_fr, text="🔎").pack(side="left", padx=(8, 2))
    search_var = ctk.StringVar()
    search_entry = ctk.CTkEntry(search_fr, textvariable=search_var, width=200,
                                placeholder_text="аптечка, патрон 7.62…")
    search_entry.pack(side="left", padx=4)
    ctk.CTkButton(search_fr, text="Найти", width=80,
                  command=lambda: do_search()).pack(side="left", padx=4)
    ctk.CTkLabel(search_fr, text="ID:").pack(side="left", padx=(12, 2))
    manual_var = ctk.StringVar()
    ctk.CTkEntry(search_fr, textvariable=manual_var, width=90,
                 placeholder_text="g4lk0").pack(side="left", padx=4)
    ctk.CTkButton(search_fr, text="＋", width=40,
                  command=lambda: add_manual()).pack(side="left", padx=4)
    ctk.CTkLabel(search_fr, text="Где крафтить:").pack(side="left",
                                                      padx=(12, 2))
    station_var = ctk.StringVar(value=ZARYA_OPT)
    ctk.CTkOptionMenu(search_fr, variable=station_var,
                      values=station_options, width=215).pack(side="left",
                                                              padx=4)
    sort_var = ctk.StringVar(value="Профит")
    ctk.CTkOptionMenu(search_fr, variable=sort_var,
                      values=["Профит", "₽ за ⚡", "ROI"], width=95,
                      command=lambda _v: render_craft_rows()).pack(
                          side="left", padx=4)
    energy_var = ctk.StringVar(value="⚡∞")
    ctk.CTkOptionMenu(search_fr, variable=energy_var,
                      values=["⚡∞", "⚡≤1000", "⚡≤500", "⚡≤300"], width=95,
                      command=lambda _v: render_craft_rows()).pack(
                          side="left", padx=4)
    pins_only = {"on": False}

    def toggle_pins_only() -> None:  # главный поток
        pins_only["on"] = not pins_only["on"]
        pins_btn.configure(text="📌✓" if pins_only["on"] else "📌")
        render_craft_rows()

    pins_btn = ctk.CTkButton(search_fr, text="📌", width=40,
                             command=lambda: toggle_pins_only())
    pins_btn.pack(side="left", padx=4)

    # ---- середина: список + результаты/карта -------------------------------
    mid = ctk.CTkFrame(app)
    mid.pack(fill="both", expand=True, padx=10, pady=5)
    watch_box = ctk.CTkScrollableFrame(mid, label_text="Мой список",
                                       width=300)
    watch_box.pack(side="left", fill="both", padx=(0, 5))
    right = ctk.CTkFrame(mid)
    right.pack(side="left", fill="both", expand=True)
    res_box = ctk.CTkScrollableFrame(right, label_text="Результаты")
    res_box.pack(fill="both", expand=True)

    # ---- фрейм карты (скрыт, показывается вместо списка) -------------------
    map_frame = ctk.CTkFrame(right)
    map_head = ctk.CTkFrame(map_frame)
    map_head.pack(fill="x", padx=4, pady=4)
    ctk.CTkButton(map_head, text="← К списку", width=100,
                  command=lambda: show_view("list")).pack(side="left",
                                                           padx=4)
    ctk.CTkLabel(map_head, text="Старт рейса:").pack(side="left",
                                                    padx=(12, 2))
    map_starts = [st for st, info in settlements.items()
                  if not info.get("offmap")]
    start_var = ctk.StringVar(value="Бартер · База «Зари»"
                              if "Бартер · База «Зари»" in map_starts
                              else (map_starts[0] if map_starts else ""))
    ctk.CTkOptionMenu(map_head, variable=start_var, values=map_starts,
                      width=220, command=lambda _v: refresh_map()
                      ).pack(side="left", padx=4)
    mapbg_btn = ctk.CTkButton(map_head, text="🛰 Реальная карта", width=140,
                              command=lambda: toggle_map_bg())
    mapbg_btn.pack(side="left", padx=4)
    calib_btn = ctk.CTkButton(map_head, text="🎯 Калибровка", width=130,
                              command=lambda: toggle_calibrate())
    calib_btn.pack(side="left", padx=4)
    wp_btn = ctk.CTkButton(map_head, text="📍 В игру", width=100,
                           command=lambda: export_waypoints())
    wp_btn.pack(side="left", padx=4)
    wpclr_btn = ctk.CTkButton(map_head, text="🧹", width=40,
                              command=lambda: clear_waypoints())
    wpclr_btn.pack(side="left", padx=2)
    gamebg_btn = ctk.CTkButton(map_head, text="🎮 Из игры", width=100,
                               command=lambda: open_gamemap())
    gamebg_btn.pack(side="left", padx=4)
    hover_var = ctk.StringVar(value="")
    ctk.CTkLabel(map_head, textvariable=hover_var,
                 font=ctk.CTkFont(size=11)).pack(side="left", padx=12)
    map_state = {"bg": False, "bg_path": None, "calib": load_calibration(),
                 "calibrating": False}
    map_body = ctk.CTkFrame(map_frame)
    map_body.pack(fill="both", expand=True, padx=4, pady=(0, 4))
    def on_marker_move(st: str, x: float, y: float) -> None:  # главный поток
        map_state["calib"][st] = [round(x, 4), round(y, 4)]
        save_calibration(map_state["calib"])
        set_status(f"🎯 {station_short(st)}: точка сохранена.")

    def toggle_map_bg() -> None:  # главный поток
        if map_state["bg"]:
            map_state["bg"] = False
            map_view.set_background(None)
            mapbg_btn.configure(text="🛰 Реальная карта")
            return
        mapbg_btn.configure(text="⏳ Загрузка…")

        def load() -> None:
            path = map_state["bg_path"] or ensure_background()
            app.after(0, lambda: _bg_ready(path))

        def _bg_ready(path: Any) -> None:
            if path is None:
                mapbg_btn.configure(text="🛰 Реальная карта")
                log("⚠ Не скачалась карта Wiki (нет сети/PIL?). Остаюсь на схеме.")
                return
            map_state["bg"] = True
            map_state["bg_path"] = path
            map_view.set_background(path)
            mapbg_btn.configure(text="🗺 Схема")
            set_status("Реальная карта загружена. Включи 🎯 Калибровку "
                       "и перетащи маркеры на истинные места (один раз).")

        run_bg(load)

    def toggle_calibrate() -> None:  # главный поток
        on = not map_state["calibrating"]
        map_state["calibrating"] = on
        map_view.set_calibrate(on)
        calib_btn.configure(text="✅ Готово" if on else "🎯 Калибровка")
        if on:
            set_status("Калибровка: перетаскивай маркеры. "
                       "Подсказки где искать — кликни по поселению.")

    map_view = MapView(map_body, on_select=lambda st: station_popup(st),
                       hover_var=hover_var, on_move=on_marker_move)
    map_view.pack(side="left", fill="both", expand=True, padx=(0, 4))
    steps_box = ctk.CTkScrollableFrame(map_body, label_text="Маршрут",
                                       width=380)
    steps_box.pack(side="left", fill="both")
    current_trip: dict[str, Any] = {}

    def show_view(which: str) -> None:  # главный поток
        if which == "map":
            res_box.pack_forget()
            map_frame.pack(fill="both", expand=True)
        else:
            map_frame.pack_forget()
            res_box.pack(fill="both", expand=True)

    # ---- строки результатов (только главный поток!) -------------------------
    def _row(parent: Any, icon_id: str, title: str, sub: str,
             buttons: list[tuple[str, Any]] | None = None) -> None:
        fr = ctk.CTkFrame(parent)
        fr.pack(fill="x", padx=4, pady=3)
        if icon_id:
            img = tk_icon(icon_id)
            if img is not None:
                ctk.CTkLabel(fr, text="", image=img).pack(side="left",
                                                          padx=6, pady=4)
        ctk.CTkLabel(fr, text=f"{title}\n{sub}" if sub else title,
                     justify="left", anchor="w", font=sub_font).pack(
                         side="left", padx=4, pady=4, fill="x", expand=True)
        for text, cmd in buttons or []:
            ctk.CTkButton(fr, text=text, width=88 if len(text) < 8 else 110,
                          command=cmd).pack(side="right", padx=4)

    def render_rows(rows: list[dict], clear_first: bool = True) -> None:
        nonlocal last_rows
        if clear_first:
            for w in res_box.winfo_children():
                w.destroy()
            last_rows = list(rows)
        else:
            last_rows = last_rows + list(rows)
        for r in rows:
            btns = r.get("buttons")
            if btns is None and r.get("btn"):
                btns = [(r["btn"], r.get("cmd"))]
            _row(res_box, r.get("icon", ""), r.get("title", ""),
                 r.get("sub", ""), btns)

    def show_rows(rows: list[dict]) -> None:  # из любого потока
        app.after(0, lambda: (show_view("list"), render_rows(rows, True)))

    def log(msg: str) -> None:  # из любого потока
        app.after(0, lambda: render_rows([{"title": msg}], False))

    def set_status(msg: str) -> None:  # из любого потока
        app.after(0, lambda: status_var.set(msg))

    def copy_results() -> None:  # главный поток
        if not last_rows:
            return
        lines = []
        for r in last_rows:
            t = r.get("title", "")
            sb = (r.get("sub", "") or "").replace("\n", " | ")
            lines.append(f"{t} — {sb}" if sb else t)
        app.clipboard_clear()
        app.clipboard_append("\n".join(lines))
        log("📋 Скопировано в буфер обмена.")

    # ---- список отслеживания (главный поток) -----------------------------
    def save_watch() -> None:
        Path("data").mkdir(exist_ok=True)
        Path("data/watchlist.json").write_text(json.dumps(
            {"region": region_var.get().strip() or s.region,
             "items": watch_items,
             "enabled": {i: bool(enabled.get(i, True)) for i in watch_items},
             "targets": {i: targets[i] for i in watch_items if i in targets},
             "pins": sorted(pins),
             "notes": {i: notes[i] for i in watch_items if i in notes},
             "theme": theme_state["mode"]},
            ensure_ascii=False, indent=1), encoding="utf-8")

    def toggle_item(iid: str, var: Any) -> None:
        enabled[iid] = bool(var.get())
        save_watch()

    def render_watch() -> None:
        for w in watch_box.winfo_children():
            w.destroy()
        on = sum(1 for i in watch_items if enabled.get(i, True))
        watch_box.configure(label_text=f"Мой список ({on}/{len(watch_items)} ✔)")
        if not watch_items:
            ctk.CTkLabel(watch_box, justify="left",
                         text="Список пуст.\nНайдите предмет выше\nи нажмите «＋».").pack(padx=8, pady=8)
            return
        for iid in list(watch_items):
            fr = ctk.CTkFrame(watch_box)
            fr.pack(fill="x", padx=4, pady=3)
            var = ctk.BooleanVar(value=bool(enabled.get(iid, True)))
            ctk.CTkCheckBox(fr, text="", variable=var, width=26,
                            command=lambda i=iid, v=var: toggle_item(i, v)
                            ).pack(side="left", padx=(6, 0))
            img = tk_icon(iid)
            if img is not None:
                ctk.CTkLabel(fr, text="", image=img).pack(side="left",
                                                          padx=4, pady=4)
            tgt_txt = (f" · 🎯{_fmt_money(targets[iid])}"
                         if iid in targets else "")
            ctk.CTkLabel(fr, text=f"{name_of(iid)}\nID: {iid}{tgt_txt}",
                         justify="left", anchor="w", font=sub_font).pack(
                             side="left", padx=4, pady=4, fill="x",
                             expand=True)
            ctk.CTkButton(fr, text="📋", width=40,
                          command=lambda i=iid: open_dossier_win(i)).pack(
                              side="right", padx=2)
            ctk.CTkButton(fr, text="✕", width=40,
                          command=lambda i=iid: remove_item(i)).pack(
                              side="right", padx=2)

    def add_item(iid: str) -> None:
        iid = (iid or "").strip()
        if not iid or iid in watch_items:
            return
        watch_items.append(iid)
        enabled[iid] = True
        save_watch()
        render_watch()

    def remove_item(iid: str) -> None:
        if iid in watch_items:
            watch_items.remove(iid)
            enabled.pop(iid, None)
            save_watch()
            render_watch()

    def add_manual() -> None:
        raw = manual_var.get().strip()
        if not raw:
            return
        for part in raw.split(","):
            add_item(part)
        manual_var.set("")

    def do_search() -> None:
        q = search_var.get()
        hits = catmod.search_items(q, cat, overrides, limit=12)
        if not hits:
            show_rows([{"title": f"«{q}» — ничего не найдено.",
                        "sub": "Попробуйте другое слово или введите ID вручную."}])
            return
        rows = [{"icon": i,
                 "title": ru or i,
                 "sub": f"{en} · ID: {i}" if en else f"ID: {i}",
                 "buttons": [("＋ В список",
                              (lambda i=i: lambda: add_item(i))())]}
                for i, ru, en in hits]
        show_rows(rows)

    # ---- фоновые действия --------------------------------------------------
    def make_client(region: str, quiet: bool = False) -> Any:
        st = Settings.load()  # перечитываем .env при каждом действии
        if not st.has_credentials:
            if not quiet:
                log("🪞 Ключа нет — беру данные из зеркала "
                    "(может отставать от игры).")
            return MirrorClient(region=region or st.region)
        return EapiClient(st.base_url, st.client_id, st.client_secret,
                          region=region or st.region,
                          min_gap=st.min_gap, max_retries=4,
                          bucket=TokenBucket(per_minute=st.tokens_per_min),
                          request_cost=st.auction_cost)

    def run_bg(fn: Any) -> None:
        threading.Thread(target=_guarded(fn), daemon=True).start()

    def _guarded(fn: Any) -> Any:
        def wrap() -> None:
            try:
                fn()
            except Exception as e:  # noqa: BLE001 - показать в UI, не падать
                log(f"❌ Ошибка: {e}\n{traceback.format_exc(limit=2)}")
        return wrap

    def clicked(fn: Any) -> None:
        """Собрать параметры в главном потоке и запустить fn в фоне."""
        params = {
            "region": region_var.get().strip(),
            "items": [i for i in list(watch_items) if enabled.get(i, True)],
            "station": station_var.get(),
        }
        try:
            params["hours"] = float((hours_var.get() or "3").replace(",", "."))
        except ValueError:
            params["hours"] = 3.0
        try:
            params["min_sales"] = max(1, int(min_sales_var.get() or "5"))
        except ValueError:
            params["min_sales"] = 5
        set_status("Работаю…")
        run_bg(lambda: fn(params))

    def do_refresh(p: dict) -> None:
        items = p["items"]
        if not items:
            log("Список пуст (или все галочки сняты) — добавьте предметы поиском выше.")
            set_status("Нечего обновлять.")
            return
        c = make_client(p["region"])
        with EAPI_LOCK:
            for k, iid in enumerate(items, 1):
                lots = c.lots_all(iid, max_pages=2)
                n = store.save_lots(c.region, iid, lots)
                sales = c.history_all(iid, max_pages=2)
                store.save_sales(c.region, iid, sales)
                log(f"[{k}/{len(items)}] {display(iid)}: "
                    f"лотов {len(lots)} (новых {n}), продаж {len(sales)}")
        log("✅ Готово. Теперь «Выгодные лоты» или «Скоро конец».")
        set_status(f"Обновлено предметов: {len(items)}.")

    def do_deals(p: dict) -> None:
        region = p["region"] or s.region
        ms = p["min_sales"]
        rows: list[dict] = []
        total = 0.0
        for iid in p["items"]:
            sm = summarize_sales(store.recent_sales(iid, region, 2000))
            if sm["count"] < ms:
                rows.append({"icon": iid, "title": name_of(iid),
                             "sub": f"Мало данных ({sm['count']} продаж, надо {ms}). "
                                    f"Нажмите «Обновить цены»."})
                continue
            qb = summarize_by_quality(
                store.recent_sales(iid, region, 2000))
            deals = find_deals(store.latest_lots(iid, region), sm,
                               min_sales=ms, min_velocity=0.5,
                               qlt_summaries=qb)
            head = (f"Продаж: {sm['count']} · медиана {_fmt_money(sm['median'])} "
                    f"· P25 {_fmt_money(sm['p25'])}")
            if not deals:
                rows.append({"icon": iid, "title": name_of(iid),
                             "sub": head + " · дешёвых лотов сейчас нет"})
                continue
            for d in deals[:3]:
                total += d["profit_total"]
                q = d.get("qlt")
                qtier = (f" · тир {q} ({QLT_NAMES.get(q, '?')})"
                         if q is not None else "")
                rows.append({
                    "icon": iid,
                    "title": f"{name_of(iid)} ×{d['amount']} → "
                             f"+{_fmt_money(d['profit_total'])}",
                    "sub": (f"Купить за {_fmt_money(d['unit_buy'] * d['amount'])} "
                            f"(+{d['margin'] * 100:.1f}%){qtier} · {head}")})
        prefetch_icons([r.get("icon", "") for r in rows])
        show_rows(rows or [{"title": "Список пуст.",
                            "sub": "Добавьте предметы поиском выше."}])
        n = sum(1 for r in rows if "→" in r.get("title", ""))
        set_status(f"Сделок: {n} · Σ профит {_fmt_money(total)}."
                   if n else "Дешёвых лотов нет.")

    def do_snipe(p: dict) -> None:
        """Лоты с истекающим сроком: выкуп И ставки (+5% к текущей)."""
        region = p["region"] or s.region
        hours = p["hours"]
        ms = p["min_sales"]
        rows: list[dict] = []
        for iid in p["items"]:
            recs = store.recent_sales(iid, region, 2000)
            sm = summarize_sales(recs)
            qb = summarize_by_quality(recs)
            for h in analyze_ending(store.latest_lots(iid, region), sm,
                                    max_hours=hours, min_sales=ms,
                                    qlt_summaries=qb):
                if h["buyout_ok"]:
                    action = (f"🔥 ВЫКУПАЙ за {_fmt_money(h['buy_total'])} · "
                              f"профит {_fmt_money(h['profit_total'])} "
                              f"(+{h['margin'] * 100:.1f}%)")
                else:
                    action = (f"✋ СТАВЬ максимум до {_fmt_money(h['max_buy_total'])} "
                              f"(след. ставка {_fmt_money(h['min_next_bid'])}) · "
                              f"профит {_fmt_money(h['profit_bid'])}")
                q = h.get("qlt")
                qtier = (f" · тир {q} ({QLT_NAMES.get(q, '?')})"
                         if q is not None else "")
                rows.append({
                    "icon": iid, "_left": h["time_left_sec"],
                    "title": (f"{name_of(iid)} ×{h['amount']} — "
                              f"конец через {h['time_left']} ⏰"),
                    "sub": action + qtier})
        rows.sort(key=lambda r: r.pop("_left"))
        if not rows:
            rows = [{"title": f"Лотов с концом < {hours:g} ч и профитом нет.",
                     "sub": ("Обновите цены и загляните позже — "
                             "такие лоты разбирают за минуты.")}]
        else:
            prefetch_icons([r.get("icon", "") for r in rows])
        show_rows(rows)
        set_status(f"Горящих лотов: {len(rows)}."
                   if "концом" not in rows[0]["title"] else "Пусто.")

    def _is_fresh(iid: str, region: str, max_age_h: float = 6.0) -> bool:
        import time as _t

        rows = store.recent_sales(iid, region, limit=5)
        if not rows:
            return False
        newest = max(r.get("fetched_at", 0) or 0 for r in rows)
        return (_t.time() - newest) < max_age_h * 3600

    def do_crafts(p: dict) -> None:
        """Крафты/обмен/покупки в плюс с фильтром станций и разбивкой затрат."""
        nonlocal craft_evs, sales_counts
        region = p["region"] or s.region
        ms = p["min_sales"]
        station_opt = p["station"]
        picked = [r for r in recipes if station_match(r, station_opt)]
        need: set[str] = set()
        for r in picked:
            need.add(r.product_id)
            need.update(i for i, _ in r.requirements)
        missing = sorted(i for i in need if not _is_fresh(i, region))
        if missing:
            log(f"📥 Докачиваю цены: {len(missing)} предметов (4 потока)…")
            c = make_client(p["region"])
            done = {"n": 0}
            with EAPI_LOCK:
                def _one(iid: str) -> None:
                    try:
                        sales = c.history_all(iid, max_pages=1, limit=200)
                        store.save_sales(c.region, iid, sales)
                    except EapiError as e:
                        log(f"  ⚠ {display(iid)}: {e}")
                    done["n"] += 1
                    if done["n"] % 25 == 0 or done["n"] == len(missing):
                        log(f"  … {done['n']}/{len(missing)}")
                with cf.ThreadPoolExecutor(max_workers=4) as ex:
                    list(ex.map(_one, missing))
        buy: dict[str, float] = {}
        sell: dict[str, float] = {}
        counts: dict[str, int] = {}
        for iid in need:
            sm = summarize_sales(store.recent_sales(iid, region, 2000))
            counts[iid] = sm["count"]
            if sm["count"] >= 3:
                buy[iid] = sm["median"]
                sell[iid] = sm["median"]
        sales_counts = counts
        craft_evs = evaluate_all(picked, buy, sell,
                                 sales_counts=counts, min_sales=ms,
                                 chain_depth=2)
        prefetch_icons([e["product_id"] for e in craft_evs
                        if e["profit"] > 0][:20])

        def finish() -> None:
            render_craft_rows()
            n = sum(1 for e in craft_evs if e["profit"] > 0)
            tot = sum(e["profit"] for e in craft_evs if e["profit"] > 0)
            set_status(f"Прибыльных: {n} · Σ профит {_fmt_money(tot)}."
                       if n else "Прибыльных крафтов нет.")
        app.after(0, finish)

    def toggle_pin(rid: str) -> None:  # главный поток
        if rid in pins:
            pins.discard(rid)
        else:
            pins.add(rid)
        save_watch()
        render_craft_rows()

    def craft_row(ev: dict) -> dict:
        parts = []
        for b in ev["breakdown"][:4]:
            via_txt = (f" ⛓{station_short(b['via'])}" if b.get("via")
                       else "")
            parts.append(f"{display(b['item'])} ×{b['count']:g} = "
                         f"{_fmt_money(b['total'])}{via_txt}")
        if len(ev["breakdown"]) > 4:
            parts.append(f"…+{len(ev['breakdown']) - 4}")
        if ev.get("money"):
            parts.append(f"деньги {_fmt_money(ev['money'])}")
        swap_txt = (" · 🔁 качели" if is_swap(
            ev["product_id"], [b["item"] for b in ev["breakdown"]]) else "")
        kind_key = ("shop" if ev["kind"] == "barter" and not ev["breakdown"]
                    else ev["kind"])
        kind = KIND_ICON.get(kind_key, "•")
        energy_txt = (f" · ⚡{ev['energy']:g}" if ev.get("energy") else "")
        skill_txt = (f" · 🎓{ev['skill']} {ev['skill_level']}"
                     if ev.get("skill") else "")
        n_prod = sales_counts.get(ev["product_id"], 0)
        n_in = min([sales_counts.get(b["item"], 0)
                    for b in ev["breakdown"]] or [n_prod])
        rid = ev["recipe_id"]
        in_trip = rid in trip
        return {
            "icon": ev["product_id"],
            "title": (f"{kind} {name_of(ev['product_id'])} ×{ev['units']:g} — "
                      f"профит {_fmt_money(ev['profit'])}"),
            "sub": (f"{ev['station']} · затраты {_fmt_money(ev['cost'])} · "
                    f"ROI {ev['roi'] * 100:.0f}%{energy_txt}{skill_txt}"
                    f"{swap_txt} · "
                    f"продаж: товар {n_prod}, входы ≥{n_in}\n"
                    f"Входы: {'; '.join(parts) if parts else '—'}"),
            "buttons": [("🗺 Карта", lambda e=ev: show_map_single(e)),
                        ("✓ В рейсе" if in_trip else "+ Рейс",
                         lambda r=rid: toggle_trip(r)),
                        ("✓ 📌" if rid in pins else "+ 📌",
                         lambda r=rid: toggle_pin(r))],
        }

    def render_craft_rows() -> None:  # главный поток
        if not craft_evs:
            return
        lim = {"⚡∞": None, "⚡≤1000": 1000.0, "⚡≤500": 500.0,
               "⚡≤300": 300.0}[energy_var.get()]
        evs = [e for e in craft_evs if e["profit"] > 0
               and (lim is None or (e.get("energy") or 0) <= lim)
               and (not pins_only["on"] or e["recipe_id"] in pins)]
        mode = sort_var.get()
        if mode == "₽ за ⚡":
            evs.sort(key=lambda e: e["profit"] / max(e.get("energy") or 0, 1),
                     reverse=True)
        elif mode == "ROI":
            evs.sort(key=lambda e: e["roi"], reverse=True)
        rows = [craft_row(ev) for ev in evs[:20]]
        if not rows:
            rows = [{"title": "Прибыльных крафтов не нашлось.",
                     "sub": ("Цены свежие — рынок сейчас ровный, "
                             "или мало продаж для оценки. Загляните позже.")}]
        show_view("list")
        render_rows(rows, True)

    def toggle_trip(rid: str) -> None:  # главный поток
        if rid in trip:
            trip.pop(rid)
        else:
            ev = next((e for e in craft_evs if e["recipe_id"] == rid), None)
            if ev:
                trip[rid] = ev
        trip_btn.configure(text=f"🧭 Рейс ({len(trip)})")
        render_craft_rows()

    # ---- карта: маршруты ----------------------------------------------------
    def _step_label(parent: Any, head: str, body: str = "",
                    color: str = TEXT) -> None:
        ctk.CTkLabel(parent, text=head, font=title_font, anchor="w",
                     justify="left", text_color=color).pack(fill="x",
                                                            padx=6, pady=(6, 0))
        if body:
            ctk.CTkLabel(parent, text=body, font=sub_font, anchor="w",
                         justify="left", wraplength=340).pack(fill="x",
                                                              padx=6, pady=(0, 2))

    def show_map_single(ev: dict) -> None:  # главный поток
        show_map_trip(build_trip([ev], start_var.get(), settlements))

    def show_map_trip(tr: dict) -> None:  # главный поток
        nonlocal current_trip
        current_trip = tr
        refresh_map()

    def refresh_map() -> None:  # главный поток
        tr = current_trip
        if not tr:
            return
        start = start_var.get() or tr.get("start", "")
        # пересобрать порядок от нового старта (крафты те же)
        evals = (list(tr.get("home", []))
                 + [e for t in list(tr.get("stops", [])) + list(tr.get("offmap", []))
                    for e in t["crafts"]])
        tr = build_trip(evals, start, settlements)
        by_stop = {t["station"]: t for t in tr["stops"]}
        markers = []
        route = []
        if start in settlements and not settlements[start].get("offmap"):
            route.append(start)
        for n, t in enumerate(tr["stops"], 1):
            info = settlements.get(t["station"], {})
            if not info or info.get("offmap"):
                continue
            if t["station"] not in route:
                route.append(t["station"])
            markers.append({"station": t["station"], "x": info["x"],
                            "y": info["y"], "color": info.get("color", "#888"),
                            "short": info.get("short", "?"), "num": n,
                            "badge": fmt_short(t["profit"])})
        for st, info in settlements.items():
            if info.get("offmap") or st in by_stop or st == start:
                continue
            markers.append({"station": st, "x": info["x"], "y": info["y"],
                            "color": info.get("color", "#888"),
                            "short": info.get("short", "?")})
        if start in settlements and start not in by_stop:
            info = settlements[start]
            markers.append({"station": start, "x": info["x"], "y": info["y"],
                            "color": info.get("color", "#888"),
                            "short": info.get("short", "?"), "num": "S"})
        home_profit = sum(float(e.get("profit", 0) or 0)
                          for e in tr.get("home", []))
        home_txt = (f"Своя база: {len(tr['home'])} кр. {fmt_short(home_profit)}"
                    if tr.get("home") else "")
        apply_calibration(markers, map_state["calib"])
        map_view.set_markers(markers, route, home_txt)
        render_steps(tr)
        show_view("map")

    def render_steps(tr: dict) -> None:  # главный поток
        for w in steps_box.winfo_children():
            w.destroy()
        total = tr.get("total_profit", 0.0)
        _step_label(steps_box, f"💰 Рейс · профит {_fmt_money(total)}",
                    color=ACCENT)
        # 0. дом
        if tr.get("home"):
            lines = [f"• {name_of(e['product_id'])} ×{e['units']:g} → "
                     f"+{_fmt_money(e['profit'])}" for e in tr["home"]]
            _step_label(steps_box, "🏠 0. Дома (убежище)", "\n".join(lines))
        # 1. закупка на аукционе
        buy_all: dict[str, float] = {}
        buy_cost: dict[str, float] = {}
        npc_money = 0.0
        all_evs = (list(tr.get("home", []))
                   + [e for t in list(tr.get("stops", [])) + list(tr.get("offmap", []))
                      for e in t["crafts"]])
        for e in all_evs:
            npc_money += float(e.get("money", 0) or 0)
            for b in e.get("breakdown", []):
                buy_all[b["item"]] = buy_all.get(b["item"], 0.0) + float(b["count"])
                buy_cost[b["item"]] = buy_cost.get(b["item"], 0.0) + float(b["total"])
        if buy_all:
            lines = [f"• {display(i)} ×{q:g} ≈ {_fmt_money(buy_cost[i])}"
                     for i, q in sorted(buy_all.items(),
                                        key=lambda kv: -buy_cost[kv[0]])]
            if npc_money:
                lines.append(f"• + деньги NPC {_fmt_money(npc_money)}")
            _step_label(steps_box, "⚖ 1. Купи на аукционе (с любой базы)",
                        "\n".join(lines))
        elif npc_money:
            _step_label(steps_box, "⚖ 1. Аукцион не нужен",
                        f"Только деньги NPC {_fmt_money(npc_money)}.")
        # 2+. остановки
        for n, t in enumerate(tr.get("stops", []), 1):
            short = settlements.get(t["station"], {}).get("short",
                                                           t["station"])
            lines = []
            for e in t["crafts"]:
                need = ", ".join(f"{display(b['item'])} ×{b['count']:g}"
                                 for b in e.get("breakdown", []))
                if e.get("money"):
                    need += (f"{', ' if need else ''}"
                             f"деньги {_fmt_money(e['money'])}")
                lines.append(f"• {name_of(e['product_id'])} ×{e['units']:g} "
                             f"→ +{_fmt_money(e['profit'])}"
                             + (f" ({need})" if need else ""))
            _step_label(steps_box, f"📍 {n + 1}. {short} "
                                   f"(+{_fmt_money(t['profit'])})",
                        "\n".join(lines))
        if tr.get("offmap"):
            lines = [f"• {name_of(e['product_id'])} → +{_fmt_money(e['profit'])}"
                     for t in tr["offmap"] for e in t["crafts"]]
            _step_label(steps_box, "🎯 Арена (вне карты)", "\n".join(lines))
        _step_label(steps_box, f"🏁 Финал: продай всё на аукционе",
                    f"Ожидаемый профит рейса {_fmt_money(total)} "
                    f"(комиссия 5% уже вычтена).", color=GREEN)

    def station_popup(st: str) -> None:  # главный поток, клик по карте
        for w in steps_box.winfo_children():
            w.destroy()
        info = settlements.get(st, {})
        where = (f"Ищи: {info['location']}" if info.get("location")
                 else f"Фракция: {info.get('faction', '—')}")
        _step_label(steps_box, f"📍 {info.get('short', st)}", where,
                    color=ACCENT)
        here = [e for e in craft_evs if e.get("station") == st
                and e["profit"] > 0][:12]
        if not here:
            _step_label(steps_box, "Тут сейчас делать нечего",
                        "Прибыльных обменов нет. Сначала «🧪 Крафты в плюс», "
                        "потом кликай по карте.")
            return
        for e in here:
            need = ", ".join(f"{display(b['item'])} ×{b['count']:g}"
                             for b in e.get("breakdown", []))
            if e.get("money"):
                need += (f"{', ' if need else ''}"
                         f"деньги {_fmt_money(e['money'])}")
            _step_label(steps_box,
                        f"{name_of(e['product_id'])} → +{_fmt_money(e['profit'])}",
                        need or "—")

    def get_target(iid: str) -> float | None:  # главный поток
        return targets.get(iid)

    def set_target(iid: str, price: float | None) -> None:  # главный поток
        if price is None:
            targets.pop(iid, None)
        else:
            targets[iid] = float(price)
        save_watch()
        render_watch()

    def get_note(iid: str) -> str:
        return notes.get(iid, "")

    def set_note(iid: str, text: str) -> None:
        text = (text or "").strip()
        if text:
            notes[iid] = text
        else:
            notes.pop(iid, None)
        save_watch()

    def open_dossier_win(iid: str) -> None:  # главный поток
        ctx = {"store": store,
               "region": region_var.get().strip() or s.region,
               "recipes": recipes, "fmt_money": _fmt_money,
               "name_of": name_of, "tk_icon": tk_icon,
               "station_short": station_short, "get_target": get_target,
               "set_target": set_target, "get_note": get_note,
               "set_note": set_note}
        open_dossier(app, ctx, iid)

    watchdog = {"on": False, "stop": None, "cool": {}, "region": ""}

    def watchdog_popup(title: str, msg: str) -> None:  # главный поток
        win = ctk.CTkToplevel(app)
        win.title(title)
        win.geometry("420x180")
        win.attributes("-topmost", True)
        ctk.CTkLabel(win, text=msg, font=title_font, wraplength=380,
                     justify="left").pack(padx=12, pady=12, fill="both",
                                           expand=True)
        ctk.CTkButton(win, text="Понял", command=win.destroy).pack(pady=8)
        try:
            import winsound

            winsound.Beep(880, 300)
        except Exception:  # noqa: BLE001 - не Windows
            pass

    def toggle_watchdog() -> None:  # главный поток
        if watchdog["on"]:
            watchdog["on"] = False
            ev = watchdog["stop"]
            if ev is not None:
                ev.set()
            wd_btn.configure(text="🔔 Сторож")
            set_status("Сторож выключен.")
            return
        if not any(i in targets for i in watch_items if enabled.get(i, True)):
            log("Сторожу нечего караулить: откройте 📋 досье предмета "
                "и задайте цель 🎯.")
            return
        watchdog["on"] = True
        watchdog["stop"] = threading.Event()
        watchdog["region"] = region_var.get().strip()
        wd_btn.configure(text="🔕 Стоп")
        set_status("Сторож включён: проверка каждые 5 мин.")
        threading.Thread(target=_guarded(watchdog_loop), daemon=True).start()

    def watchdog_loop() -> None:  # фон
        import time as _t

        stop = watchdog["stop"]
        while watchdog["on"]:
            try:
                c = make_client(watchdog["region"], quiet=True)
                with EAPI_LOCK:
                    items = [i for i in list(watch_items)
                             if enabled.get(i, True) and i in targets]
                    for iid in items:
                        if stop is not None and stop.is_set():
                            return
                        lots = c.lots_all(iid, limit=50, max_pages=1)
                        store.save_lots(c.region, iid, lots)
                        units = [float(x.get("unit_price", 0) or 0)
                                 for x in store.latest_lots(iid, c.region)]
                        units = [u for u in units if u > 0]
                        if units and min(units) <= targets[iid]:
                            last = watchdog["cool"].get(iid, 0)
                            if _t.time() - last > 1800:
                                watchdog["cool"][iid] = _t.time()
                                msg = (f"{name_of(iid)}: лот по "
                                       f"{_fmt_money(min(units))} ≤ цели "
                                       f"{_fmt_money(targets[iid])}!")
                                app.after(0, lambda m=msg: watchdog_popup(
                                    "🔔 Сторож: цена достигнута", m))
                                app.after(0, lambda m=msg: set_status(m))
            except Exception as e:  # noqa: BLE001 - сторож не падает
                app.after(0, lambda: log(f"⚠ Сторож: {e}"))
            for _ in range(60):
                if stop is not None and stop.wait(5):
                    return

    def calib_xy(st: str) -> tuple[float, float]:
        info = settlements.get(st, {})
        c = map_state["calib"].get(st)
        if c:
            return float(c[0]), float(c[1])
        return float(info.get("x", 0.5)), float(info.get("y", 0.5))

    def _load_affine() -> dict[str, float] | None:
        try:
            d = json.loads(Path("data/wp_affine.json").read_text(
                encoding="utf-8"))
            return d if isinstance(d, dict) and "ax" in d else None
        except (OSError, ValueError):
            return None

    def _save_affine(aff: dict[str, float]) -> None:
        Path("data").mkdir(exist_ok=True)
        Path("data/wp_affine.json").write_text(
            json.dumps(aff, ensure_ascii=False, indent=1), encoding="utf-8")

    def export_waypoints() -> None:  # главный поток
        tr = current_trip
        if not tr or not tr.get("stops"):
            set_status("Нет маршрута для экспорта — постройте рейс.")
            return
        aff = _load_affine()
        if aff is None:
            open_anchor_dialog()
            return
        start = start_var.get()
        order = [(f"S {station_short(start)}", *calib_xy(start))]
        for n, t in enumerate(tr.get("stops", []), 1):
            if t["station"] == start:
                continue
            order.append((f"{n} {station_short(t['station'])} "
                          f"{fmt_short(t['profit'])}",
                          *calib_xy(t["station"])))
        marks = wpmod.build_route_marks(order, aff)
        cfg = wpmod.find_waypoints_cfg()
        if cfg is None:
            out = Path("data/route_waypoints.cfg")
            wpmod.save_waypoints(out, marks, backup=False)
            set_status(f"waypoints.cfg не найден — маршрут в {out}, "
                       f"скопируй в игру вручную.")
            return
        bak = wpmod.save_waypoints(
            cfg, wpmod.merge_marks(wpmod.load_waypoints(cfg), marks))
        set_status(f"В игре: {len(marks)} меток SZ_*."
                   + (f" Бэкап: {bak.name}." if bak else ""))

    def clear_waypoints() -> None:  # главный поток
        cfg = wpmod.find_waypoints_cfg()
        if cfg is None:
            set_status("waypoints.cfg не найден.")
            return
        wpmod.save_waypoints(
            cfg, wpmod.strip_marks(wpmod.load_waypoints(cfg)))
        set_status("Метки SZ_* убраны из игры (якоря SZ_A/SZ_B оставлены).")

    def open_anchor_dialog() -> None:  # главный поток
        win = ctk.CTkToplevel(app)
        win.title("Привязка к игре")
        win.geometry("480x400")
        win.transient(app)
        ctk.CTkLabel(win, text="Якоря: 2 метки в игре", font=title_font
                     ).pack(padx=12, pady=(10, 2))
        ctk.CTkLabel(
            win, justify="left",
            text="1) Поставь в игре метку SZ_A на одном поселении,\n"
                 "     а SZ_B — на другом (имена — ровно так!).\n"
                 "2) Выбери ниже, где какая.\n"
                 "3) Жми «Прочитать из игры» (один раз).").pack(padx=12,
                                                                pady=2)
        a_var = ctk.StringVar(value="Бартер · База «Зари»")
        b_var = ctk.StringVar(value="Бартер · Бар")
        ctk.CTkLabel(win, text="SZ_A стоит на:").pack(padx=12, pady=(6, 0))
        ctk.CTkOptionMenu(win, variable=a_var, values=map_starts, width=260
                          ).pack(padx=12)
        ctk.CTkLabel(win, text="SZ_B стоит на:").pack(padx=12, pady=(6, 0))
        ctk.CTkOptionMenu(win, variable=b_var, values=map_starts, width=260
                          ).pack(padx=12)
        status_l = ctk.CTkLabel(win, text="", wraplength=440)
        status_l.pack(padx=12, pady=6)

        def do_read() -> None:
            cfg = wpmod.find_waypoints_cfg()
            if cfg is None:
                status_l.configure(
                    text="waypoints.cfg не найден. Запусти игру хоть раз "
                         "и поставь любую метку.")
                return
            marks = wpmod.load_waypoints(cfg)
            pa = wpmod.find_anchor(marks, wpmod.ANCHOR_A)
            pb = wpmod.find_anchor(marks, wpmod.ANCHOR_B)
            if pa is None or pb is None:
                status_l.configure(text="Не вижу SZ_A/SZ_B в файле. "
                                        "Проверь имена меток в игре.")
                return
            aff = wpmod.fit_affine(calib_xy(a_var.get()), pa,
                                   calib_xy(b_var.get()), pb)
            if aff is None:
                status_l.configure(text="Якоря совпали — поставь их "
                                        "в РАЗНЫХ поселениях.")
                return
            _save_affine(aff)
            status_l.configure(
                text=f"Готово! A=({pa['x']:.0f}, {pa['z']:.0f}) "
                     f"B=({pb['x']:.0f}, {pb['z']:.0f}). Жми «📍 В игру».")

        ctk.CTkButton(win, text="Прочитать из игры", command=do_read
                      ).pack(pady=8)

    emis_state: dict[str, Any] = {"last_alert": ""}

    def _emis_text(data: Any) -> tuple[str, bool]:
        """(текст пилюли, идёт ли выброс сейчас)."""
        if not isinstance(data, dict):
            return "☢ ?", False
        now = dt.datetime.now(dt.timezone.utc)

        def _parse(val: Any) -> dt.datetime | None:
            try:
                return dt.datetime.fromisoformat(
                    str(val).replace("Z", "+00:00"))
            except ValueError:
                return None

        cur = _parse(data.get("currentStart"))
        if cur is not None:
            mins = max(0, int((now - cur).total_seconds() // 60))
            return f"☢ ИДЁТ ({mins} мин)", True
        prev = _parse(data.get("previousEnd") or data.get("previousStart"))
        if prev is None:
            return "☢ ?", False
        mins = int((now - prev).total_seconds() // 60)
        if mins < 0:
            return "☢ скоро?", False
        if mins < 60:
            return f"☢ {mins} мин назад", False
        return f"☢ {mins // 60} ч {mins % 60} мин назад", False

    def emission_alert() -> None:  # главный поток
        try:
            import winsound

            winsound.Beep(660, 300)
            winsound.Beep(880, 400)
        except Exception:  # noqa: BLE001
            pass
        win = ctk.CTkToplevel(app)
        win.title("☢ Выброс!")
        win.geometry("340x140")
        win.attributes("-topmost", True)
        ctk.CTkLabel(win, text="☢ В Зоне начался выброс!\nПрячься, сталкер.",
                     font=title_font).pack(padx=12, pady=12)
        ctk.CTkButton(win, text="Понял", width=100,
                      command=win.destroy).pack(pady=4)

    def refresh_emission(silent: bool = False) -> None:  # главный поток
        region = region_var.get().strip()

        def work() -> None:
            try:
                with EAPI_LOCK:
                    c = make_client(region, quiet=True)
                    data = (c.emission(region or None)
                            if hasattr(c, "emission") else None)
                if data is None:
                    app.after(0, lambda: emis_var.set("☢ нужен ключ"))
                    return
                txt, active = _emis_text(data)
                app.after(0, lambda: emis_var.set(txt))
                if active:
                    key = str(data.get("currentStart"))
                    if emis_state["last_alert"] != key:
                        emis_state["last_alert"] = key
                        app.after(0, lambda: emission_alert())
            except Exception as e:  # noqa: BLE001
                app.after(0, lambda: emis_var.set("☢ ?"))
                if not silent:
                    app.after(0, lambda: set_status(f"Выброс: {e}"))

        threading.Thread(target=work, daemon=True).start()

    def _emis_tick() -> None:  # главный поток, раз в 5 минут
        refresh_emission(silent=True)
        app.after(300000, _emis_tick)

    ALLIANCE_RU = {"bandits": "Бандиты", "stalkers": "Сталкеры",
                   "dolg": "Долг", "svoboda": "Свобода",
                   "mercenaries": "Наёмники", "scientists": "Учёные",
                   "military": "Военные", "monolith": "Монолит",
                   "loners": "Одиночки"}

    def _fmt_playtime(ns: Any) -> str:
        try:
            hours = float(ns) / 3.6e12
        except (TypeError, ValueError):
            return "?"
        if hours < 1:
            return f"~{hours * 60:.0f} мин"
        if hours < 48:
            return f"~{hours:.0f} ч"
        return f"~{hours / 24:.0f} дн"

    def open_stalker() -> None:  # главный поток
        win = ctk.CTkToplevel(app)
        win.title("Досье сталкера")
        win.geometry("440x480")
        win.transient(app)
        ctk.CTkLabel(win, text="Ник персонажа:", font=title_font
                     ).pack(padx=12, pady=(10, 0))
        name_var = ctk.StringVar()
        ent = ctk.CTkEntry(win, textvariable=name_var, width=260,
                           placeholder_text="VasyaPupkin")
        ent.pack(padx=12, pady=4)
        res_l = ctk.CTkLabel(win, text="", justify="left", wraplength=400)
        res_l.pack(padx=12, pady=6)

        def show(data: Any) -> None:
            if not isinstance(data, dict) or not data.get("username"):
                res_l.configure(text="Не найден. Проверь ник и регион.")
                return
            st = {s.get("id"): s.get("value")
                  for s in data.get("stats", [])
                  if isinstance(s, dict)}
            kil = float(st.get("kil") or 0)
            dea = float(st.get("dea") or 0)
            fir = float(st.get("sho-fir") or 0)
            hit = float(st.get("sho-hit") or 0)
            kd = kil / dea if dea else kil
            acc = hit / fir * 100 if fir else 0.0
            ally = ALLIANCE_RU.get(str(data.get("alliance")),
                                   data.get("alliance") or "?")
            last = str(data.get("lastLogin") or "")[:10]
            res_l.configure(text="\n".join([
                f"🪪 {data.get('username')} · {ally}",
                f"Был в игре: {last or '?'}",
                f"Фраги: {kil:.0f} · Смерти: {dea:.0f} · K/D: {kd:.2f}",
                f"Точность: {acc:.1f}% (выстрелов: {fir:.0f})",
                f"Мутанты: {st.get('mut-kil', 0)} · "
                f"NPC: {st.get('npc-kil', 0)}",
                f"В игре: {_fmt_playtime(st.get('pla-tim'))}",
                f"Очки достижений: {st.get('ach-points', 0)}",
            ]))

        def fetch() -> None:
            name = name_var.get().strip()
            if not name:
                return
            region = region_var.get().strip()
            res_l.configure(text="Ищу…")

            def work() -> None:
                try:
                    with EAPI_LOCK:
                        c = make_client(region, quiet=True)
                        if not hasattr(c, "profile"):
                            raise RuntimeError(
                                "нужен ключ API (зеркало не умеет профили)")
                        data = c.profile(name, region or None)
                    app.after(0, lambda: show(data))
                except Exception as e:  # noqa: BLE001
                    app.after(0, lambda: res_l.configure(text=f"Ошибка: {e}"))

            threading.Thread(target=work, daemon=True).start()

        def fetch_ops() -> None:
            name = name_var.get().strip()
            if not name:
                return
            region = region_var.get().strip()
            res_l.configure(text="Ищу операции…")

            def work() -> None:
                try:
                    with EAPI_LOCK:
                        c = make_client(region, quiet=True)
                        if not hasattr(c, "operations"):
                            raise RuntimeError("нужен ключ API")
                        payload = c.operations(name, region or None,
                                               limit=10)
                    data = (payload.get("data", [])
                            if isinstance(payload, dict) else payload)
                    if not data:
                        raise RuntimeError("операций не нашлось")
                    lines = []
                    for s in data[:10]:
                        end = str(s.get("endTime") or "")[:10]
                        dur = float(s.get("sessionDurationSeconds") or 0)
                        lines.append(
                            f"• {s.get('map', '?')} · {end} · "
                            f"сложность {s.get('difficulty', '?')} · "
                            f"{dur / 60:.0f} мин · "
                            f"награда {s.get('difficultyReward', 0)}")
                    app.after(0, lambda: res_l.configure(
                        text="\n".join(lines)))
                except Exception as e:  # noqa: BLE001
                    app.after(0, lambda: res_l.configure(text=f"Ошибка: {e}"))

            threading.Thread(target=work, daemon=True).start()

        ctk.CTkButton(win, text="Найти", command=fetch).pack(pady=4)
        ctk.CTkButton(win, text="📜 Операции",
                      command=fetch_ops).pack(pady=4)
        ent.bind("<Return>", lambda _e: fetch())

    def toggle_theme() -> None:  # главный поток
        new = "Light" if theme_state["mode"] == "Dark" else "Dark"
        ctk.set_appearance_mode(new)
        theme_state["mode"] = new
        theme_btn.configure(text="☀️" if new == "Dark" else "🌙")
        save_watch()

    theme_btn.configure(text="☀️" if theme_state["mode"] == "Dark"
                        else "🌙")
    refresh_emission(silent=True)
    app.after(300000, _emis_tick)

    def open_shopping() -> None:  # главный поток
        tr = current_trip
        if not tr or (not tr.get("stops") and not tr.get("home")):
            set_status("Нет рейса — нажмите «🧭 Рейс» сначала.")
            return

        def need_buy(crafts: list[dict]) -> dict[str, float]:
            buy: dict[str, float] = {}
            for e in crafts:
                for b in e.get("breakdown", []):
                    if b.get("via"):  # полуфабрикат — скрафтим сами
                        continue
                    iid = b["item"]
                    buy[iid] = buy.get(iid, 0.0) + float(b["count"])
            return buy

        win = ctk.CTkToplevel(app)
        win.title("Список покупок")
        win.geometry("520x600")
        win.transient(app)
        box = ctk.CTkScrollableFrame(win, label_text="Что докупить")
        box.pack(fill="both", expand=True, padx=10, pady=10)
        lines: list[str] = []

        def section(title: str, buy: dict[str, float],
                    money: float) -> None:
            ctk.CTkLabel(box, text=title, font=title_font, anchor="w"
                         ).pack(fill="x", padx=8, pady=(6, 2))
            lines.append(title)
            for iid, cnt in sorted(buy.items(),
                                   key=lambda kv: display(kv[0])):
                ctk.CTkLabel(box, text=f"  • {display(iid)} ×{cnt:g}",
                             anchor="w").pack(fill="x", padx=8)
                lines.append(f"{display(iid)} x{cnt:g}")
            if money:
                ctk.CTkLabel(box, text=f"  💵 NPC: {_fmt_money(money)}",
                             anchor="w").pack(fill="x", padx=8)
                lines.append(f"NPC деньги: {money:g}")

        for n, t in enumerate(tr.get("stops", []), 1):
            section(f"{n}. {station_short(t['station'])}",
                    need_buy(t.get("crafts", [])),
                    float(t.get("money_npc", 0) or 0))
        if tr.get("home"):
            money = sum(float(e.get("money", 0) or 0)
                        for e in tr["home"])
            section("🏠 Своя база", need_buy(tr["home"]), money)

        def copy_list() -> None:
            app.clipboard_clear()
            app.clipboard_append("\n".join(lines))
            set_status("Список покупок скопирован.")

        def copy_discord() -> None:
            total = _fmt_money(float(tr.get("total_profit", 0) or 0))
            head = (f"🧭 **Рейс SZMarket** · профит **{total}** · "
                    f"старт: {start_var.get()}")
            body = "\n".join(lines)
            app.clipboard_clear()
            app.clipboard_append(f"{head}\n```\n{body}\n```")
            set_status("Рейс скопирован для Discord.")

        brow = ctk.CTkFrame(win)
        brow.pack(pady=8)
        ctk.CTkButton(brow, text="📋 Копировать список",
                      command=copy_list).pack(side="left", padx=4)
        ctk.CTkButton(brow, text="📤 В Discord",
                      command=copy_discord).pack(side="left", padx=4)

    def open_gamemap() -> None:  # главный поток
        from tkinter import filedialog

        win = ctk.CTkToplevel(app)
        win.title("Карта из файлов игры")
        win.geometry("520x430")
        win.transient(app)
        ctk.CTkLabel(
            win, justify="left",
            text="Соберу фон карты из тайлов твоей игры\n"
                 "(modassets/assets/pda). Нужен пакет sc-file:\n"
                 "pip install sc-file").pack(padx=12, pady=(10, 4))
        hints = gamemod.default_hints()
        root_var = ctk.StringVar(value=str(hints[0]) if hints else "")
        row = ctk.CTkFrame(win)
        row.pack(fill="x", padx=12, pady=4)
        ctk.CTkEntry(row, textvariable=root_var, width=360).pack(
            side="left", padx=4)
        ctk.CTkButton(row, text="Выбрать…", width=100,
                      command=lambda: browse()).pack(side="left", padx=4)
        maps_var = ctk.StringVar(value="")
        map_menu = ctk.CTkOptionMenu(win, variable=maps_var, values=[],
                                     width=260)
        map_menu.pack(padx=12, pady=4)
        status_l = ctk.CTkLabel(win, text="", wraplength=480,
                                justify="left")
        status_l.pack(padx=12, pady=6)

        def browse() -> None:
            init = root_var.get().strip() or (
                str(hints[0]) if hints else "")
            d = filedialog.askdirectory(title="Папка игры (STALZONE)",
                                        initialdir=init or None)
            if d:
                root_var.set(d)
                refresh_maps()

        def refresh_maps() -> None:
            root = gamemod.find_game_root(root_var.get().strip() or None)
            if root is None:
                status_l.configure(
                    text="Не вижу modassets/assets — выбери папку игры.")
                map_menu.configure(values=[])
                return
            root_var.set(str(root))
            names = gamemod.list_maps(root, "ru")
            if not names:
                status_l.configure(text="PDA-карты не найдены в pda/.")
                map_menu.configure(values=[])
                return
            map_menu.configure(values=names)
            maps_var.set(names[0])
            tiles = gamemod.collect_map_tiles(root, names[0], "ru")
            b = gamemod.tile_bounds(tiles)
            status_l.configure(
                text=f"Карт: {len(names)}. «{names[0]}»: "
                     f"{b['count'] if b else 0} тайлов." if b else
                f"Карт: {len(names)}. В «{names[0]}» тайлов нет.")

        def build() -> None:
            root = root_var.get().strip()
            name = maps_var.get().strip()
            if not root or not name:
                return
            gamebg_btn.configure(text="⏳ Сборка…")
            status_l.configure(text="Сканирую тайлы…")

            def work() -> None:
                try:
                    tiles = gamemod.collect_map_tiles(root, name, "ru")
                    if not tiles:
                        raise RuntimeError("тайлы не найдены")
                    safe = "".join(c if c.isalnum() or c in "-_"
                                   else "_" for c in name)
                    out = Path(f"data/maps/game_{safe}.jpg")
                    app.after(0, lambda: status_l.configure(
                        text=f"Собираю {len(tiles)} тайлов… "
                             f"(нужен sc-file)"))
                    meta = gamemod.assemble(tiles, out)
                    meta.update({"map": name, "root": root})
                    Path(f"data/maps/game_{safe}.json").write_text(
                        json.dumps(meta, ensure_ascii=False, indent=1),
                        encoding="utf-8")
                    app.after(0, lambda: use(meta))
                except Exception as e:  # noqa: BLE001
                    app.after(0, lambda: status_l.configure(
                        text=f"Не собралось: {e}"))
                    app.after(0, lambda: gamebg_btn.configure(
                        text="🎮 Из игры"))

            threading.Thread(target=work, daemon=True).start()

        def use(meta: dict) -> None:
            map_state["bg"] = True
            map_state["bg_path"] = meta["image"]
            map_view.set_background(meta["image"])
            mapbg_btn.configure(text="🗺 Схема")
            gamebg_btn.configure(text="🎮 Из игры")
            b = meta
            set_status(f"Фон из игры: {b.get('tiles', '?')} тайлов "
                       f"(x {b.get('min_x')}…{b.get('max_x')}, "
                       f"z {b.get('min_z')}…{b.get('max_z')}). "
                       f"Откалибруй маркеры через 🎯.")
            win.destroy()

        ctk.CTkButton(win, text="Собрать и показать",
                      command=build).pack(pady=8)
        refresh_maps()

    def open_clans() -> None:  # главный поток
        win = ctk.CTkToplevel(app)
        win.title("Кланы Зоны")
        win.geometry("560x620")
        win.transient(app)
        ctk.CTkLabel(win, text="Поиск по названию/тегу:").pack(padx=12,
                                                               pady=(8, 0))
        q_var = ctk.StringVar()
        ctk.CTkEntry(win, textvariable=q_var, width=300,
                     placeholder_text="Долг, Свобода, [DD]…").pack(padx=12,
                                                                  pady=4)
        box = ctk.CTkScrollableFrame(win, label_text="Топ кланов")
        box.pack(fill="both", expand=True, padx=10, pady=6)
        clans: list[dict] = []

        def render() -> None:
            for w in box.winfo_children():
                w.destroy()
            q = q_var.get().strip().lower()
            shown = [c for c in clans
                     if not q or q in str(c.get("name", "")).lower()
                     or q in str(c.get("tag", "")).lower()][:60]
            if not shown:
                ctk.CTkLabel(box, text="Ничего не нашлось.").pack(padx=8,
                                                                  pady=8)
                return
            for c in shown:
                ally = ALLIANCE_RU.get(str(c.get("alliance")),
                                       c.get("alliance") or "?")
                txt = (f"[{c.get('tag', '?')}] {c.get('name', '?')} · "
                       f"ур. {c.get('level', '?')} · "
                       f"бойцов: {c.get('memberCount', '?')} · {ally}\n"
                       f"Лидер: {c.get('leader', '?')}")
                fr = ctk.CTkFrame(box)
                fr.pack(fill="x", padx=6, pady=3)
                ctk.CTkLabel(fr, text=txt, anchor="w", justify="left"
                             ).pack(side="left", padx=8, pady=4)
                desc = str(c.get("description") or "")

                def show_d(d=desc, n=c.get("name", "?")) -> None:
                    set_status(f"{n}: {d[:180] or 'без описания'}")
                ctk.CTkButton(fr, text="📜", width=40,
                              command=show_d).pack(side="right", padx=6)

        q_var.trace_add("write", lambda *_a: render())

        def fetch() -> None:
            region = region_var.get().strip()
            ctk.CTkLabel(box, text="Загружаю…").pack(padx=8, pady=8)

            def work() -> None:
                try:
                    with EAPI_LOCK:
                        c = make_client(region, quiet=True)
                        if not hasattr(c, "clans"):
                            raise RuntimeError("нужен ключ API")
                        allc: list[dict] = []
                        seen_total = 0
                        for page in range(3):
                            payload = c.clans(region or None,
                                              limit=100, offset=page * 100)
                            data = (payload.get("data", [])
                                    if isinstance(payload, dict)
                                    else payload)
                            if not data:
                                break
                            allc.extend(data)
                            seen_total = (payload.get("totalClans", 0)
                                          if isinstance(payload, dict)
                                          else len(allc))
                            if len(allc) >= (seen_total or 0) > 0:
                                break
                    clans.clear()
                    clans.extend(allc)
                    app.after(0, render)
                    app.after(0, lambda: set_status(
                        f"Кланов загружено: {len(allc)}"))
                except Exception as e:  # noqa: BLE001
                    app.after(0, lambda: set_status(f"Кланы: {e}"))

            threading.Thread(target=work, daemon=True).start()

        fetch()

    farm_state: dict[str, Any] = {"t0": 0.0, "run": False, "acc": 0.0}

    def _farm_log() -> list[dict]:
        try:
            d = json.loads(Path("data/farm.json").read_text(
                encoding="utf-8"))
            return d if isinstance(d, list) else []
        except (OSError, ValueError):
            return []

    def _farm_save(entry: dict) -> None:
        Path("data").mkdir(exist_ok=True)
        log = _farm_log() + [entry]
        Path("data/farm.json").write_text(
            json.dumps(log[-200:], ensure_ascii=False, indent=1),
            encoding="utf-8")

    def open_farm() -> None:  # главный поток
        import time as _t

        win = ctk.CTkToplevel(app)
        win.title("Таймер фарма")
        win.geometry("440x520")
        win.transient(app)
        time_var = ctk.StringVar(value="00:00:00")
        ctk.CTkLabel(win, textvariable=time_var,
                     font=ctk.CTkFont(size=34, weight="bold")).pack(pady=10)
        rate_var = ctk.StringVar(value="₽/час: —")
        ctk.CTkLabel(win, textvariable=rate_var, font=title_font
                     ).pack(pady=2)
        ctk.CTkLabel(win, text="Нафармил (₽):").pack(pady=(8, 0))
        loot_var = ctk.StringVar(value="")
        ctk.CTkEntry(win, textvariable=loot_var, width=200,
                     placeholder_text="например 150000").pack(pady=4)
        ctk.CTkLabel(win, text="Где / что:").pack(pady=(4, 0))
        spot_var = ctk.StringVar(value="")
        ctk.CTkEntry(win, textvariable=spot_var, width=200,
                     placeholder_text="Болота, арты…").pack(pady=4)

        def elapsed() -> float:
            e = farm_state["acc"]
            if farm_state["run"]:
                e += _t.monotonic() - farm_state["t0"]
            return e

        def tick() -> None:
            if not win.winfo_exists():
                return
            e = elapsed()
            h, m, s = int(e // 3600), int(e % 3600 // 60), int(e % 60)
            time_var.set(f"{h:02d}:{m:02d}:{s:02d}")
            try:
                loot = float(loot_var.get().strip().replace(" ", "")
                             or 0)
            except ValueError:
                loot = 0.0
            rate_var.set("₽/час: " + (_fmt_money(loot / (e / 3600))
                                     if e >= 60 and loot > 0 else "—"))
            win.after(1000, tick)

        def start_stop() -> None:
            if farm_state["run"]:
                farm_state["acc"] += _t.monotonic() - farm_state["t0"]
                farm_state["run"] = False
                ss_btn.configure(text="▶ Старт")
            else:
                farm_state["t0"] = _t.monotonic()
                farm_state["run"] = True
                ss_btn.configure(text="⏸ Стоп")

        def reset() -> None:
            farm_state.update(t0=0.0, run=False, acc=0.0)
            ss_btn.configure(text="▶ Старт")

        def finish() -> None:
            e = elapsed()
            if e < 60:
                set_status("Фарм короче минуты — не записываю.")
                return
            try:
                loot = float(loot_var.get().strip().replace(" ", "")
                             or 0)
            except ValueError:
                loot = 0.0
            _farm_save({"date": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
                        "minutes": round(e / 60, 1), "loot": loot,
                        "per_hour": round(loot / (e / 3600), 0),
                        "spot": spot_var.get().strip()})
            set_status(f"Фарм записан: {e / 60:.0f} мин, "
                       f"{_fmt_money(loot)}.")
            reset()
            hist()

        def hist() -> None:
            for w in hist_box.winfo_children():
                w.destroy()
            for en in reversed(_farm_log()[-8:]):
                ctk.CTkLabel(
                    hist_box,
                    text=f"{en.get('date', '?')} · "
                         f"{en.get('minutes', '?')} мин · "
                         f"{_fmt_money(float(en.get('loot', 0)))} · "
                         f"{_fmt_money(float(en.get('per_hour', 0)))}/ч · "
                         f"{en.get('spot', '')}",
                    anchor="w").pack(fill="x", padx=6, pady=1)

        btns_row = ctk.CTkFrame(win)
        btns_row.pack(pady=8)
        ss_btn = ctk.CTkButton(btns_row, text="▶ Старт", width=100,
                               command=start_stop)
        ss_btn.pack(side="left", padx=4)
        ctk.CTkButton(btns_row, text="Сброс", width=80,
                      command=reset).pack(side="left", padx=4)
        ctk.CTkButton(btns_row, text="✅ Готово, записать", width=150,
                      command=finish).pack(side="left", padx=4)
        hist_box = ctk.CTkScrollableFrame(win, label_text="Последние выходы",
                                          height=140)
        hist_box.pack(fill="both", expand=True, padx=10, pady=6)
        hist()
        tick()

    def open_trip() -> None:  # главный поток
        if not trip:
            log("Рейс пуст — нажмите «+ Рейс» на строках крафтов.")
            return
        show_map_trip(build_trip(list(trip.values()), start_var.get(),
                                 settlements))
        n_stops = len(current_trip.get("stops", []))
        set_status(f"Рейс: остановок {n_stops} · профит "
                   f"{_fmt_money(current_trip.get('total_profit', 0))}.")

    # ---- нижние кнопки + статус-бар -------------------------------------------
    btns = ctk.CTkFrame(app)
    btns.pack(fill="x", padx=10, pady=(0, 5))
    ctk.CTkButton(btns, text="🔄 Обновить цены",
                  command=lambda: clicked(do_refresh)).pack(side="left", padx=5)
    ctk.CTkButton(btns, text="💰 Выгодные лоты",
                  command=lambda: clicked(do_deals)).pack(side="left", padx=5)
    ctk.CTkButton(btns, text="🧪 Крафты в плюс",
                  command=lambda: clicked(do_crafts)).pack(side="left", padx=5)
    ctk.CTkButton(btns, text="⏰ Скоро конец",
                  command=lambda: clicked(do_snipe)).pack(side="left", padx=5)
    trip_btn = ctk.CTkButton(btns, text="🧭 Рейс (0)", command=open_trip)
    trip_btn.pack(side="left", padx=5)
    ctk.CTkButton(btns, text="🛒 Список",
                  command=open_shopping).pack(side="left", padx=5)
    wd_btn = ctk.CTkButton(btns, text="🔔 Сторож", command=toggle_watchdog)
    wd_btn.pack(side="left", padx=5)
    ctk.CTkButton(btns, text="⏱ Фарм",
                  command=open_farm).pack(side="left", padx=5)
    ctk.CTkButton(btns, text="📋 Копировать",
                  command=copy_results).pack(side="left", padx=5)
    ctk.CTkButton(btns, text="Очистить",
                  command=lambda: render_rows([])).pack(side="right", padx=5)
    status_var = ctk.StringVar(value="Готов.")
    ctk.CTkLabel(app, textvariable=status_var, anchor="w",
                 font=ctk.CTkFont(size=11)).pack(fill="x", padx=14, pady=(0, 6))

    # ---- фоновая докачка ВСЕХ иконок ---------------------------------------
    def icons_bulk() -> None:
        from szmarket import icons as iconsmod

        ids = [i for i, info in cat.items() if info.get("icon")]
        total = len(ids)
        missing = [i for i in ids
                   if not (Path("data/icons") / f"{i}.png").exists()]
        state = {"done": total - len(missing)}

        def tick() -> None:
            d = state["done"]
            mark = " 🟢" if d >= total and total else " ⏳"
            icons_var.set(f"🖼 Иконки: {d}/{total}{mark}")

        app.after(0, tick)
        if not missing:
            return
        with cf.ThreadPoolExecutor(max_workers=6) as ex:
            for _ in ex.map(lambda i: iconsmod.icon_file(i, cat), missing):
                state["done"] += 1
                if state["done"] % 50 == 0:
                    app.after(0, tick)
        app.after(0, tick)

    # ---- старт ---------------------------------------------------------------
    render_watch()
    render_rows([
        {"title": "👋 Привет! Как пользоваться:",
         "sub": ("1) Найдите предметы поиском и добавьте в список слева "
                 "(галочка ✔ = участвует в проверках)\n"
                 "2) «Обновить цены» — скачать лоты и продажи из игры\n"
                 "3) «Выгодные лоты» / «Скоро конец» / «Крафты в плюс»\n"
                 "4) «🗺 Карта» на строке крафта — маршрут: где купить, "
                 "куда прийти, где продать\n"
                 "5) «+ Рейс» отмечает крафты, «🧭 Рейс» строит общий маршрут, "
                 "«📍 В игру» переносит его в игру метками\n"
                 "6) «📋» в списке — досье предмета, 🎯 — цель для «🔔 Сторожа»\n"
                 "7) В шапке: ☢ выбросы Зоны · «🪪 Сталкер» — досье игрока · "
                 "«🛒 Список» — что докупить для рейса\n"
                 "8) «🎮 Из игры» — фон карты из файлов твоей игры · "
                 "«🏰 Кланы» — топ кланов · «⏱ Фарм» — ₽/час")},
        {"title": f"📦 Каталог: {len(cat)} предметов · рецептов: {len(recipes)}",
         "sub": ("Названия и иконки — из официальной базы EXBO. "
                 "Иконки докачиваются в фоне (статус справа вверху).")},
    ])
    search_entry.bind("<Return>", lambda _e: do_search())
    threading.Thread(target=icons_bulk, daemon=True).start()
    app.mainloop()
    store.close()


if __name__ == "__main__":
    main()
