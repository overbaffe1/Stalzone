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
    from szmarket.arbitrage import find_deals, summarize_sales
    from szmarket.config import Settings
    from szmarket.craft import evaluate_all, load_recipes
    from szmarket.eapi import EapiClient, EapiError, TokenBucket
    from szmarket.route import build_trip, load_map
    from szmarket.snipe import analyze_ending
    from szmarket.store import MarketStore
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
    region_default = str(watch_data.get("region") or s.region)

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

    ctk.set_appearance_mode("dark")
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
                 else "⛔ Нет ключей (.env)")
    ctk.CTkLabel(top, text=creds_txt).pack(side="left", padx=8)
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
    hover_var = ctk.StringVar(value="")
    ctk.CTkLabel(map_head, textvariable=hover_var,
                 font=ctk.CTkFont(size=11)).pack(side="left", padx=12)
    map_body = ctk.CTkFrame(map_frame)
    map_body.pack(fill="both", expand=True, padx=4, pady=(0, 4))
    map_view = MapView(map_body, on_select=lambda st: station_popup(st),
                       hover_var=hover_var)
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
             "enabled": {i: bool(enabled.get(i, True)) for i in watch_items}},
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
            ctk.CTkLabel(fr, text=f"{name_of(iid)}\nID: {iid}",
                         justify="left", anchor="w", font=sub_font).pack(
                             side="left", padx=4, pady=4, fill="x",
                             expand=True)
            ctk.CTkButton(fr, text="✕", width=40,
                          command=lambda i=iid: remove_item(i)).pack(
                              side="right", padx=6)

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
    def make_client(region: str) -> EapiClient:
        st = Settings.load()  # перечитываем .env при каждом действии
        if not st.has_credentials:
            raise EapiError("Нет ключей API: скопируйте .env.example в .env")
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
            deals = find_deals(store.latest_lots(iid, region), sm,
                               min_sales=ms, min_velocity=0.5)
            head = (f"Продаж: {sm['count']} · медиана {_fmt_money(sm['median'])} "
                    f"· P25 {_fmt_money(sm['p25'])}")
            if not deals:
                rows.append({"icon": iid, "title": name_of(iid),
                             "sub": head + " · дешёвых лотов сейчас нет"})
                continue
            for d in deals[:3]:
                total += d["profit_total"]
                rows.append({
                    "icon": iid,
                    "title": f"{name_of(iid)} ×{d['amount']} → "
                             f"+{_fmt_money(d['profit_total'])}",
                    "sub": (f"Купить за {_fmt_money(d['unit_buy'] * d['amount'])} "
                            f"(+{d['margin'] * 100:.1f}%) · {head}")})
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
            sm = summarize_sales(store.recent_sales(iid, region, 2000))
            for h in analyze_ending(store.latest_lots(iid, region), sm,
                                    max_hours=hours, min_sales=ms):
                if h["verdict"] == "buyout":
                    action = (f"🔥 ВЫКУПАЙ за {_fmt_money(h['buy_total'])} · "
                              f"профит {_fmt_money(h['profit_total'])} "
                              f"(+{h['margin'] * 100:.1f}%)")
                else:
                    action = (f"✋ СТАВЬ максимум до {_fmt_money(h['max_buy_total'])} "
                              f"(след. ставка {_fmt_money(h['min_next_bid'])}) · "
                              f"профит {_fmt_money(h['profit_bid'])}")
                rows.append({
                    "icon": iid, "_left": h["time_left_sec"],
                    "title": (f"{name_of(iid)} ×{h['amount']} — "
                              f"конец через {h['time_left']} ⏰"),
                    "sub": action})
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
                                 sales_counts=counts, min_sales=ms)
        prefetch_icons([e["product_id"] for e in craft_evs
                        if e["profit"] > 0][:20])

        def finish() -> None:
            render_craft_rows()
            n = sum(1 for e in craft_evs if e["profit"] > 0)
            tot = sum(e["profit"] for e in craft_evs if e["profit"] > 0)
            set_status(f"Прибыльных: {n} · Σ профит {_fmt_money(tot)}."
                       if n else "Прибыльных крафтов нет.")
        app.after(0, finish)

    def craft_row(ev: dict) -> dict:
        parts = []
        for b in ev["breakdown"][:4]:
            parts.append(f"{display(b['item'])} ×{b['count']:g} = "
                         f"{_fmt_money(b['total'])}")
        if len(ev["breakdown"]) > 4:
            parts.append(f"…+{len(ev['breakdown']) - 4}")
        if ev.get("money"):
            parts.append(f"деньги {_fmt_money(ev['money'])}")
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
                    f"ROI {ev['roi'] * 100:.0f}%{energy_txt}{skill_txt} · "
                    f"продаж: товар {n_prod}, входы ≥{n_in}\n"
                    f"Входы: {'; '.join(parts) if parts else '—'}"),
            "buttons": [("🗺 Карта", lambda e=ev: show_map_single(e)),
                        ("✓ В рейсе" if in_trip else "+ Рейс",
                         lambda r=rid: toggle_trip(r))],
        }

    def render_craft_rows() -> None:  # главный поток
        rows = [craft_row(ev) for ev in craft_evs if ev["profit"] > 0][:20]
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
        _step_label(steps_box, f"📍 {info.get('short', st)}",
                    f"Фракция: {info.get('faction', '—')}", color=ACCENT)
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
                 "5) «+ Рейс» отмечает крафты, «🧭 Рейс» строит общий маршрут")},
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
