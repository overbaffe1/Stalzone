"""Desktop MVP (CustomTkinter). Run on your PC:

  pip install -r requirements-ui.txt
  copy .env.example -> .env   (fill in your Client Secret)
  python -m ui.app

Works next to the game: enter item ids, refresh prices from EAPI,
see top crafts and top resale deals. Network calls run in background
threads so the window never freezes.
"""
from __future__ import annotations

import threading
import traceback


def main() -> None:
    try:
        import customtkinter as ctk
    except ImportError:
        print("CustomTkinter is not installed. Run: pip install -r requirements-ui.txt")
        return

    from szmarket.arbitrage import find_deals, summarize_sales
    from szmarket.config import Settings
    from szmarket.craft import evaluate_all, load_recipes
    from szmarket.eapi import EapiClient, EapiError
    from szmarket.store import MarketStore

    s = Settings.load()
    store = MarketStore(s.db_path)

    ctk.set_appearance_mode("dark")
    app = ctk.CTk()
    app.title("SZMarket — STALCRAFT auction analytics")
    app.geometry("920x640")

    # -- top controls --------------------------------------------------
    top = ctk.CTkFrame(app)
    top.pack(fill="x", padx=10, pady=10)

    creds_txt = "EAPI: credentials OK" if s.has_credentials else "EAPI: NO credentials (.env)"
    ctk.CTkLabel(top, text=creds_txt).pack(side="left", padx=8)

    ctk.CTkLabel(top, text="Region:").pack(side="left", padx=(12, 2))
    region_var = ctk.StringVar(value=s.region)
    ctk.CTkEntry(top, textvariable=region_var, width=60).pack(side="left")

    ctk.CTkLabel(top, text="Items (ids, comma):").pack(side="left", padx=(12, 2))
    items_var = ctk.StringVar(value="y1q9, g4lk0, 96430")
    ctk.CTkEntry(top, textvariable=items_var, width=220).pack(side="left")

    out = ctk.CTkTextbox(app, wrap="word")
    out.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    def log(msg: str) -> None:
        out.insert("end", msg + "\n")
        out.see("end")

    def client() -> EapiClient:
        st = Settings.load()  # re-read .env every action
        if not st.has_credentials:
            raise EapiError("No credentials: copy .env.example -> .env first")
        return EapiClient(st.base_url, st.client_id, st.client_secret,
                          region=region_var.get().strip() or st.region,
                          min_gap=max(st.min_gap, 1.0))

    def run_bg(fn):
        threading.Thread(target=_guarded(fn), daemon=True).start()

    def _guarded(fn):
        def wrap():
            try:
                fn()
            except Exception as e:  # noqa: BLE001 - show in UI, don't crash
                log(f"ERROR: {e}\n{traceback.format_exc(limit=3)}")
        return wrap

    def item_ids() -> list[str]:
        return [x.strip() for x in items_var.get().split(",") if x.strip()]

    # -- actions --------------------------------------------------------
    def do_refresh():
        c = client()
        for iid in item_ids():
            lots = c.lots_all(iid, max_pages=2)
            n = store.save_lots(c.region, iid, lots)
            log(f"[lots] {iid}@{c.region}: fetched {len(lots)}, new {n}")
            sales = c.history_all(iid, max_pages=2)
            m = store.save_sales(c.region, iid, sales)
            log(f"[history] {iid}@{c.region}: fetched {len(sales)}, saved {m}")
        log("Refresh done.")

    def do_deals():
        region = region_var.get().strip() or s.region
        for iid in item_ids():
            summary = summarize_sales(store.recent_sales(iid, region, 1000))
            deals = find_deals(store.latest_lots(iid, region), summary,
                               min_sales=5, min_velocity=0.5)
            log(f"--- {iid}: sales={summary['count']}, "
                f"med={summary['median']:.0f}, P25={summary['p25']:.0f}, "
                f"vel={summary['velocity_per_day']:.1f}/d")
            for d in deals[:5]:
                log(f"  DEAL x{d['amount']}: buy {d['unit_buy']:.0f} -> "
                    f"net {d['profit_total']:.0f} (+{d['margin']*100:.1f}%)")

    def do_crafts():
        region = region_var.get().strip() or s.region
        recipes = load_recipes("data/recipes.json")
        log(f"Loaded {len(recipes)} recipes.")
        buy: dict[str, float] = {}
        sell: dict[str, float] = {}
        need = {r.product_id for r in recipes}
        for r in recipes:
            need.update(i for i, _ in r.requirements)
        for iid in sorted(need):
            sm = summarize_sales(store.recent_sales(iid, region, 1000))
            if sm["count"]:
                buy[iid] = sm["median"]
                sell[iid] = sm["median"]
        results = evaluate_all(recipes, buy, sell)
        if not results:
            log("No craft has full price coverage. Refresh prices first, "
                "or fill data/recipes.json with real recipes.")
            return
        for ev in results[:15]:
            log(f"  {ev['recipe_id']}: profit {ev['profit']:.0f} "
                f"(cost {ev['cost']:.0f}, ROI {ev['roi']*100:.1f}%)")

    btns = ctk.CTkFrame(app)
    btns.pack(fill="x", padx=10, pady=(0, 10))
    ctk.CTkButton(btns, text="🔄 Refresh prices (EAPI)",
                  command=lambda: run_bg(do_refresh)).pack(side="left", padx=5)
    ctk.CTkButton(btns, text="💰 Top deals",
                  command=lambda: run_bg(do_deals)).pack(side="left", padx=5)
    ctk.CTkButton(btns, text="🧪 Top crafts",
                  command=lambda: run_bg(do_crafts)).pack(side="left", padx=5)
    ctk.CTkButton(btns, text="Clear",
                  command=lambda: out.delete("0.0", "end")).pack(side="right", padx=5)

    log("SZMarket ready. Region/item list on top, actions below.\n"
        "Step 1: Refresh prices. Step 2: Top deals / Top crafts.\n")
    app.mainloop()
    store.close()


if __name__ == "__main__":
    main()
