"""Десктопный интерфейс SZMarket (CustomTkinter) — на русском.

Запуск на своём ПК:
  pip install -r requirements-ui.txt
  copy .env.example .env   (вписать Client Secret)
  python -m ui.app

Окно работает рядом с игрой: список предметов с иконками и русскими
названиями, обновление цен из EAPI, выгодные лоты, крафты в плюс
и лоты с истекающим сроком («снайпер»).

Потоки: сеть и база — в фоновых потоках; все виджеты создаются
и меняются ТОЛЬКО в главном потоке через app.after().
"""
from __future__ import annotations

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
    from szmarket.eapi import EapiClient, EapiError
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
    region_default = str(watch_data.get("region") or s.region)

    def name_of(iid: str) -> str:
        return catmod.display_name(iid, cat, overrides)

    ctk.set_appearance_mode("dark")
    app = ctk.CTk()
    app.title("SZMarket — аукцион STALCRAFT: крафты и сделки")
    app.geometry("1040x720")

    img_cache: dict[str, Any] = {}

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

    # ---- поиск ----------------------------------------------------------
    search_fr = ctk.CTkFrame(app)
    search_fr.pack(fill="x", padx=10, pady=5)
    ctk.CTkLabel(search_fr, text="🔎 Найти:").pack(side="left", padx=8)
    search_var = ctk.StringVar()
    search_entry = ctk.CTkEntry(search_fr, textvariable=search_var, width=240,
                                placeholder_text="аптечка, патрон 7.62…")
    search_entry.pack(side="left", padx=4)
    ctk.CTkButton(search_fr, text="Найти", width=80,
                  command=lambda: do_search()).pack(side="left", padx=4)
    ctk.CTkLabel(search_fr, text="ID вручную:").pack(side="left", padx=(16, 2))
    manual_var = ctk.StringVar()
    ctk.CTkEntry(search_fr, textvariable=manual_var, width=110,
                 placeholder_text="g4lk0").pack(side="left", padx=4)
    ctk.CTkButton(search_fr, text="＋ В список", width=100,
                  command=lambda: add_manual()).pack(side="left", padx=4)

    # ---- середина: список + результаты ----------------------------------
    mid = ctk.CTkFrame(app)
    mid.pack(fill="both", expand=True, padx=10, pady=5)
    watch_box = ctk.CTkScrollableFrame(mid, label_text="Мой список", width=330)
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
        if clear_first:
            for w in res_box.winfo_children():
                w.destroy()
        for r in rows:
            _row(res_box, r.get("icon", ""), r.get("title", ""),
                 r.get("sub", ""), r.get("btn"), r.get("cmd"))

    def show_rows(rows: list[dict]) -> None:  # из любого потока
        app.after(0, lambda: render_rows(rows, True))

    def log(msg: str) -> None:  # из любого потока
        app.after(0, lambda: render_rows([{"title": msg}], False))

    # ---- список отслеживания (главный поток) -----------------------------
    def save_watch() -> None:
        Path("data").mkdir(exist_ok=True)
        Path("data/watchlist.json").write_text(json.dumps(
            {"region": region_var.get().strip() or s.region,
             "items": watch_items}, ensure_ascii=False, indent=1), encoding="utf-8")

    def render_watch() -> None:
        for w in watch_box.winfo_children():
            w.destroy()
        if not watch_items:
            ctk.CTkLabel(watch_box, justify="left",
                         text="Список пуст.\nНайдите предмет выше\nи нажмите «＋ В список».").pack(padx=8, pady=8)
            return
        for iid in list(watch_items):
            _row(watch_box, iid, name_of(iid), f"ID: {iid}",
                 "✕", lambda i=iid: remove_item(i))

    def add_item(iid: str) -> None:
        iid = (iid or "").strip()
        if not iid or iid in watch_items:
            return
        watch_items.append(iid)
        save_watch()
        render_watch()

    def remove_item(iid: str) -> None:
        if iid in watch_items:
            watch_items.remove(iid)
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
                          min_gap=max(st.min_gap, 1.0))

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
        region = region_var.get().strip()
        items = list(watch_items)
        try:
            hours = float((hours_var.get() or "3").replace(",", "."))
        except ValueError:
            hours = 3.0
        run_bg(lambda: fn(region, items, hours))

    def do_refresh(region: str, items: list[str], hours: float) -> None:
        if not items:
            log("Список пуст — добавьте предметы поиском выше.")
            return
        c = make_client(region)
        with EAPI_LOCK:
            for k, iid in enumerate(items, 1):
                lots = c.lots_all(iid, max_pages=2)
                n = store.save_lots(c.region, iid, lots)
                sales = c.history_all(iid, max_pages=2)
                m = store.save_sales(c.region, iid, sales)
                log(f"[{k}/{len(items)}] {name_of(iid)}: "
                    f"лотов {len(lots)} (новых {n}), продаж {len(sales)}")
        log("✅ Готово. Теперь «Выгодные лоты» или «Скоро конец».")

    def do_deals(region: str, items: list[str], hours: float) -> None:
        region = region or s.region
        rows: list[dict] = []
        for iid in items:
            sm = summarize_sales(store.recent_sales(iid, region, 2000))
            if sm["count"] < 5:
                rows.append({"icon": iid, "title": name_of(iid),
                             "sub": f"Мало данных ({sm['count']} продаж). "
                                    f"Нажмите «Обновить цены»."})
                continue
            deals = find_deals(store.latest_lots(iid, region), sm,
                               min_sales=5, min_velocity=0.5)
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

    def do_snipe(region: str, items: list[str], hours: float) -> None:
        region = region or s.region
        rows: list[dict] = []
        for iid in items:
            sm = summarize_sales(store.recent_sales(iid, region, 2000))
            for h in analyze_ending(store.latest_lots(iid, region), sm,
                                    max_hours=hours):
                rows.append({
                    "icon": iid, "_left": h["time_left_sec"],
                    "title": (f"{name_of(iid)} ×{h['amount']} — "
                              f"конец через {h['time_left']} ⏰"),
                    "sub": (f"Выкуп {_fmt_money(h['buy_total'])} · "
                            f"потолок для профита {_fmt_money(h['max_buy_total'])} · "
                            f"профит {_fmt_money(h['profit_total'])} "
                            f"(+{h['margin'] * 100:.1f}%)")})
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

    def do_crafts(region: str, items: list[str], hours: float) -> None:
        region = region or s.region
        need: set[str] = set()
        for r in recipes:
            need.add(r.product_id)
            need.update(i for i, _ in r.requirements)
        missing = sorted(i for i in need if not _is_fresh(i, region))
        if missing:
            log(f"📥 Докачиваю цены: {len(missing)} предметов (~{len(missing)} сек)…")
            c = make_client(region)
            with EAPI_LOCK:
                for k, iid in enumerate(missing, 1):
                    try:
                        sales = c.history_all(iid, max_pages=1, limit=200)
                        store.save_sales(c.region, iid, sales)
                    except EapiError as e:
                        log(f"  ⚠ {display(iid)}: {e}")
                    if k % 25 == 0 or k == len(missing):
                        log(f"  … {k}/{len(missing)}")
        buy: dict[str, float] = {}
        sell: dict[str, float] = {}
        for iid in need:
            sm = summarize_sales(store.recent_sales(iid, region, 2000))
            if sm["count"] >= 3:
                buy[iid] = sm["median"]
                sell[iid] = sm["median"]
        rows: list[dict] = []
        for ev in evaluate_all(recipes, buy, sell):
            if ev["profit"] <= 0:
                continue
            money_txt = (f" + деньги {_fmt_money(ev['money'])}"
                         if ev.get("money") else "")
            rows.append({
                "icon": ev["product_id"],
                "title": (f"{name_of(ev['product_id'])} ×{ev['units']:g} — "
                          f"профит {_fmt_money(ev['profit'])}"),
                "sub": (f"{ev['station']} · затраты {_fmt_money(ev['cost'])}"
                        f"{money_txt} · ROI {ev['roi'] * 100:.0f}% "
                        f"(цены = медиана продаж)")})
            if len(rows) >= 20:
                break
        if not rows:
            rows = [{"title": "Прибыльных крафтов не нашлось.",
                     "sub": "Цены свежие — значит, рынок сейчас ровный. Загляните позже."}]
        else:
            prefetch_icons([r.get("icon", "") for r in rows])
        show_rows(rows)

    def display(iid: str) -> str:
        nm = name_of(iid)
        return nm if nm != iid else iid

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
    ctk.CTkButton(btns, text="Очистить",
                  command=lambda: render_rows([])).pack(side="right", padx=5)

    # ---- старт ---------------------------------------------------------------
    render_watch()
    render_rows([
        {"title": "👋 Привет! Как пользоваться:",
         "sub": ("1) Найдите предметы поиском и добавьте в список слева\n"
                 "2) «Обновить цены» — скачать лоты и продажи из игры\n"
                 "3) «Выгодные лоты» / «Скоро конец» / «Крафты в плюс»")},
        {"title": f"📦 Каталог: {len(cat)} предметов · рецептов: {len(recipes)}",
         "sub": "Названия и иконки — из официальной базы EXBO."},
    ])
    search_entry.bind("<Return>", lambda _e: do_search())
    app.mainloop()
    store.close()


if __name__ == "__main__":
    main()
