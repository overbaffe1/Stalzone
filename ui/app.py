"""Десктопный интерфейс SZMarket (CustomTkinter) — на русском.

Запуск на своём ПК:
  pip install -r requirements-ui.txt
  copy .env.example .env   (вписать Client Secret)
  python -m ui.app

Окно работает рядом с игрой: список предметов с иконками и русскими
названиями, обновление цен из EAPI, выгодные лоты, крафты в плюс
и лоты с истекающим сроком («снайпер», включая ставки на торгах).

Потоки: сеть и база — в фоновых потоках; все виджеты создаются
и меняются ТОЛЬКО в главном потоке через app.after().

v3: чекбоксы в списке, фильтр станций/поселений для крафтов,
фоновая докачка ВСЕХ иконок с индикатором, кнопка «Копировать»,
настройка минимального числа продаж, ставки (+5%), разбивка затрат.
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


def _load_json(path: str, default: Any) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


KIND_ICON = {"craft": "🔨", "hideout": "🔨", "barter": "🔄", "shop": "🏪"}


def main() -> None:
    try:
        import customtkinter as ctk
    except ImportError:
        print("Нет CustomTkinter. Выполните: pip install -r requirements-ui.txt")
        return

    from szmarket import catalog as catmod
    from szmarket import icons as iconsmod
    from szmarket.arbitrage import find_deals, summarize_sales
    from szmarket.config import Settings
    from szmarket.craft import evaluate_all, load_recipes
    from szmarket.eapi import EapiClient, EapiError, TokenBucket
    from szmarket.snipe import analyze_ending
    from szmarket.store import MarketStore

    s = Settings.load()
    store = MarketStore(s.db_path)
    cat = catmod.load_compact_catalog("data/catalog.json")
    overrides = catmod.load_overrides("data/overrides.json")
    recipes = load_recipes("data/recipes.json")
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
    app = ctk.CTk()
    app.title("SZMarket — аукцион STALCRAFT: крафты и сделки")
    app.geometry("1120x760")

    img_cache: dict[str, Any] = {}
    last_rows: list[dict] = []  # для кнопки «Копировать»

    def tk_icon(item_id: str, size: int = 44) -> Any | None:
        """CTkImage из кэша (вызывать ТОЛЬКО в главном потоке)."""
        if not item_id:
            return None
        key = f"{item_id}:{size}"
        if key in img_cache:
            return img_cache[key]
        pil = iconsmod.load_icon_image(item_id, cat, size=size)
        if pil is None:
            return None
        try:
            img = ctk.CTkImage(light_image=pil, dark_image=pil, size=(size, size))
        except Exception:  # noqa: BLE001 - без иконки тоже можно жить
            return None
        img_cache[key] = img
        return img

    def prefetch_icons(ids: list[str]) -> None:
        """Прогреть дисковый кэш иконок (вызывать из фонового потока)."""
        for iid in dict.fromkeys(i for i in ids if i):
            iconsmod.icon_file(iid, cat)

    # ---- верхняя панель ------------------------------------------------
    top = ctk.CTkFrame(app)
    top.pack(fill="x", padx=10, pady=(10, 5))
    creds_txt = ("✅ EAPI подключён" if s.has_credentials
                 else "⛔ Нет ключей: скопируйте .env.example в .env")
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

    # ---- поиск ----------------------------------------------------------
    search_fr = ctk.CTkFrame(app)
    search_fr.pack(fill="x", padx=10, pady=5)
    ctk.CTkLabel(search_fr, text="🔎 Найти:").pack(side="left", padx=8)
    search_var = ctk.StringVar()
    search_entry = ctk.CTkEntry(search_fr, textvariable=search_var, width=220,
                                placeholder_text="аптечка, патрон 7.62…")
    search_entry.pack(side="left", padx=4)
    ctk.CTkButton(search_fr, text="Найти", width=80,
                  command=lambda: do_search()).pack(side="left", padx=4)
    ctk.CTkLabel(search_fr, text="ID вручную:").pack(side="left", padx=(16, 2))
    manual_var = ctk.StringVar()
    ctk.CTkEntry(search_fr, textvariable=manual_var, width=100,
                 placeholder_text="g4lk0").pack(side="left", padx=4)
    ctk.CTkButton(search_fr, text="＋ В список", width=100,
                  command=lambda: add_manual()).pack(side="left", padx=4)
    # фильтр станций для крафтов
    ctk.CTkLabel(search_fr, text="Где крафтить:").pack(side="left", padx=(16, 2))
    station_var = ctk.StringVar(value=ZARYA_OPT)
    ctk.CTkOptionMenu(search_fr, variable=station_var,
                      values=station_options, width=220).pack(side="left", padx=4)

    # ---- середина: список + результаты ----------------------------------
    mid = ctk.CTkFrame(app)
    mid.pack(fill="both", expand=True, padx=10, pady=5)
    watch_box = ctk.CTkScrollableFrame(mid, label_text="Мой список (✔ = участвует)",
                                       width=350)
    watch_box.pack(side="left", fill="both", padx=(0, 5))
    res_box = ctk.CTkScrollableFrame(mid, label_text="Результаты")
    res_box.pack(side="left", fill="both", expand=True)

    # ---- строки (только главный поток!) ---------------------------------
    def _row(parent: Any, icon_id: str, title: str, sub: str,
             btn_text: str | None = None, btn_cmd: Any = None) -> None:
        fr = ctk.CTkFrame(parent)
        fr.pack(fill="x", padx=4, pady=3)
        if icon_id:
            img = tk_icon(icon_id)
            if img is not None:
                ctk.CTkLabel(fr, text="", image=img).pack(side="left", padx=6, pady=4)
        ctk.CTkLabel(fr, text=f"{title}\n{sub}" if sub else title,
                     justify="left", anchor="w").pack(
                         side="left", padx=4, pady=4, fill="x", expand=True)
        if btn_text:
            ctk.CTkButton(fr, text=btn_text, width=100,
                          command=btn_cmd).pack(side="right", padx=6)

    def render_rows(rows: list[dict], clear_first: bool = True) -> None:
        nonlocal last_rows
        if clear_first:
            for w in res_box.winfo_children():
                w.destroy()
            last_rows = list(rows)
        else:
            last_rows = last_rows + list(rows)
        for r in rows:
            _row(res_box, r.get("icon", ""), r.get("title", ""),
                 r.get("sub", ""), r.get("btn"), r.get("cmd"))

    def show_rows(rows: list[dict]) -> None:  # из любого потока
        app.after(0, lambda: render_rows(rows, True))

    def log(msg: str) -> None:  # из любого потока
        app.after(0, lambda: render_rows([{"title": msg}], False))

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
        if not watch_items:
            ctk.CTkLabel(watch_box, justify="left",
                         text="Список пуст.\nНайдите предмет выше\nи нажмите «＋ В список».").pack(padx=8, pady=8)
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
                ctk.CTkLabel(fr, text="", image=img).pack(side="left", padx=4, pady=4)
            ctk.CTkLabel(fr, text=f"{name_of(iid)}\nID: {iid}",
                         justify="left", anchor="w").pack(
                             side="left", padx=4, pady=4, fill="x", expand=True)
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
                 "btn": "＋ В список",
                 "cmd": (lambda i=i: lambda: add_item(i))()}
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
        run_bg(lambda: fn(params))

    def do_refresh(p: dict) -> None:
        items = p["items"]
        if not items:
            log("Список пуст (или все галочки сняты) — добавьте предметы поиском выше.")
            return
        c = make_client(p["region"])
        with EAPI_LOCK:
            for k, iid in enumerate(items, 1):
                lots = c.lots_all(iid, max_pages=2)
                n = store.save_lots(c.region, iid, lots)
                sales = c.history_all(iid, max_pages=2)
                m = store.save_sales(c.region, iid, sales)
                log(f"[{k}/{len(items)}] {display(iid)}: "
                    f"лотов {len(lots)} (новых {n}), продаж {len(sales)}")
        log("✅ Готово. Теперь «Выгодные лоты» или «Скоро конец».")

    def do_deals(p: dict) -> None:
        region = p["region"] or s.region
        ms = p["min_sales"]
        rows: list[dict] = []
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
                rows.append({
                    "icon": iid,
                    "title": f"{name_of(iid)} ×{d['amount']}",
                    "sub": (f"Купить за {_fmt_money(d['unit_buy'] * d['amount'])} → "
                            f"профит {_fmt_money(d['profit_total'])} "
                            f"(+{d['margin'] * 100:.1f}%) · {head}")})
        prefetch_icons([r.get("icon", "") for r in rows])
        show_rows(rows or [{"title": "Список пуст.",
                            "sub": "Добавьте предметы поиском выше."}])

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

    def _is_fresh(iid: str, region: str, max_age_h: float = 6.0) -> bool:
        import time as _t

        rows = store.recent_sales(iid, region, limit=5)
        if not rows:
            return False
        newest = max(r.get("fetched_at", 0) or 0 for r in rows)
        return (_t.time() - newest) < max_age_h * 3600

    def do_crafts(p: dict) -> None:
        """Крафты/обмен/покупки в плюс с фильтром станций и разбивкой затрат."""
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
        sales_counts: dict[str, int] = {}
        for iid in need:
            sm = summarize_sales(store.recent_sales(iid, region, 2000))
            sales_counts[iid] = sm["count"]
            if sm["count"] >= 3:
                buy[iid] = sm["median"]
                sell[iid] = sm["median"]
        rows: list[dict] = []
        for ev in evaluate_all(picked, buy, sell,
                               sales_counts=sales_counts, min_sales=ms):
            if ev["profit"] <= 0:
                continue
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
            rows.append({
                "icon": ev["product_id"],
                "title": (f"{kind} {name_of(ev['product_id'])} ×{ev['units']:g} — "
                          f"профит {_fmt_money(ev['profit'])}"),
                "sub": (f"{ev['station']} · затраты {_fmt_money(ev['cost'])} · "
                        f"ROI {ev['roi'] * 100:.0f}%{energy_txt}{skill_txt} · "
                        f"продаж: товар {n_prod}, входы ≥{n_in}\n"
                        f"Входы: {'; '.join(parts) if parts else '—'}")})
            if len(rows) >= 20:
                break
        if not rows:
            rows = [{"title": "Прибыльных крафтов не нашлось.",
                     "sub": ("Цены свежие — рынок сейчас ровный, "
                             "или мало продаж для оценки. Загляните позже.")}]
        else:
            prefetch_icons([r.get("icon", "") for r in rows])
        show_rows(rows)

    # ---- нижние кнопки -----------------------------------------------------
    btns = ctk.CTkFrame(app)
    btns.pack(fill="x", padx=10, pady=(0, 10))
    ctk.CTkButton(btns, text="🔄 Обновить цены",
                  command=lambda: clicked(do_refresh)).pack(side="left", padx=5)
    ctk.CTkButton(btns, text="💰 Выгодные лоты",
                  command=lambda: clicked(do_deals)).pack(side="left", padx=5)
    ctk.CTkButton(btns, text="🧪 Крафты в плюс",
                  command=lambda: clicked(do_crafts)).pack(side="left", padx=5)
    ctk.CTkButton(btns, text="⏰ Скоро конец",
                  command=lambda: clicked(do_snipe)).pack(side="left", padx=5)
    ctk.CTkButton(btns, text="📋 Копировать",
                  command=copy_results).pack(side="left", padx=5)
    ctk.CTkButton(btns, text="Очистить",
                  command=lambda: render_rows([])).pack(side="right", padx=5)

    # ---- фоновая докачка ВСЕХ иконок ---------------------------------------
    def icons_bulk() -> None:
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
                 "4) «📋 Копировать» — скопировать результаты текстом")},
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
