"""Live market research on production EAPI: most profitable crafts RIGHT NOW.

Runs in CI (workflow_dispatch) or locally with a filled `.env`. Fetches
fresh sales history for every item used by any recipe (~1000 items,
~25 min at the official 100 tokens/min limit), evaluates all 2769
recipes, then re-checks the winners against LIVE lots.

Outputs (DATA ONLY, never credentials):
  fetched_info/research.json  - full machine-readable results
  fetched_info/research.md    - human report in Russian ("do X, get Y rub")

Knobs (env):
  RESEARCH_MIN_SALES=10   min sales per item to trust a median
  RESEARCH_MAX_ITEMS=0    fetch only first N items (0 = all; for smoke tests)
  RESEARCH_WORKERS=4      parallel fetch threads (one shared token bucket)
  RESEARCH_TOP=60         candidates re-checked against live lots
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from szmarket import catalog as catmod
from szmarket.arbitrage import AUCTION_FEE, summarize_sales
from szmarket.config import Settings
from szmarket.craft import evaluate_all, find_swaps, load_recipes
from szmarket.eapi import EapiClient, EapiError, TokenBucket
from szmarket.snipe import analyze_ending
from szmarket.store import MarketStore

OUT_JSON = Path("fetched_info/research.json")
OUT_MD = Path("fetched_info/research.md")


def _money(x: float) -> str:
    return f"{x:,.0f}".replace(",", " ") + " ₽"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def main() -> int:
    t0 = time.time()
    s = Settings.load()
    if not s.has_credentials:
        print("[research] SKIP: no EAPI credentials in env")
        return 0
    min_sales = _env_int("RESEARCH_MIN_SALES", 10)
    max_items = _env_int("RESEARCH_MAX_ITEMS", 0)
    workers = _env_int("RESEARCH_WORKERS", 4)
    top_n = _env_int("RESEARCH_TOP", 60)

    cat = catmod.load_compact_catalog("data/catalog.json")
    overrides = catmod.load_overrides("data/overrides.json")
    recipes = load_recipes("data/recipes.json")

    def name_of(iid: str) -> str:
        return catmod.display_name(iid, cat, overrides)

    need: list[str] = sorted({r.product_id for r in recipes}
                             | {i for r in recipes for i, _ in r.requirements})
    if max_items > 0:
        need = need[:max_items]
    print(f"[research] region={s.region} recipes={len(recipes)} "
          f"items={len(need)} min_sales={min_sales} workers={workers}",
          flush=True)

    import concurrent.futures as cfx

    db_path = "fetched_info/research.db"
    try:
        Path(db_path).unlink(missing_ok=True)
    except OSError:
        pass
    store = MarketStore(db_path)
    client = EapiClient(s.base_url, s.client_id, s.client_secret,
                        region=s.region, min_gap=s.min_gap,
                        bucket=TokenBucket(per_minute=s.tokens_per_min),
                        request_cost=s.auction_cost)
    errors = {"n": 0}

    def _one(iid: str) -> tuple[str, int]:
        try:
            sales = client.history_all(iid, max_pages=1, limit=200)
            store.save_sales(client.region, iid, sales)
            return iid, len(sales)
        except EapiError as e:
            errors["n"] += 1
            print(f"[research] WARN {iid}: {e}", flush=True)
            return iid, -1

    # ---- phase 1: sales history --------------------------------------
    counts: dict[str, int] = {}
    done = 0
    with cfx.ThreadPoolExecutor(max_workers=workers) as ex:
        for iid, n in ex.map(_one, need):
            counts[iid] = max(n, 0)
            done += 1
            if done % 50 == 0 or done == len(need):
                el = time.time() - t0
                print(f"[research] history {done}/{len(need)} "
                      f"({el / 60:.1f} min, errors={errors['n']})", flush=True)

    # ---- phase 2: medians + craft evaluation --------------------------
    buy: dict[str, float] = {}
    sell: dict[str, float] = {}
    medians: dict[str, dict] = {}
    for iid in need:
        sm = summarize_sales(store.recent_sales(iid, s.region, 2000))
        medians[iid] = {"count": sm["count"], "median": sm["median"],
                        "p25": sm["p25"], "p75": sm["p75"],
                        "velocity": sm.get("velocity_per_day", 0.0)}
        if sm["count"] >= 3:
            buy[iid] = sm["median"]
            sell[iid] = sm["median"]
    covered = sum(1 for iid in need if counts.get(iid, 0) >= min_sales)
    print(f"[research] medians ready: {covered}/{len(need)} items "
          f"with >={min_sales} sales", flush=True)

    ranked = evaluate_all(recipes, buy, sell,
                          sales_counts={k: medians[k]["count"] for k in need},
                          min_sales=min_sales)
    winners = [ev for ev in ranked if ev["profit"] > 0][:top_n]
    print(f"[research] profitable recipes: "
          f"{sum(1 for ev in ranked if ev['profit'] > 0)} "
          f"(total evaluated {len(ranked)})", flush=True)

    # ---- phase 3: live lots for winners + snipe -----------------------
    live: dict[str, dict] = {}
    snipe_rows: list[dict] = []
    win_ids = sorted({ev["product_id"] for ev in winners})
    for k, iid in enumerate(win_ids, 1):
        try:
            lots = client.lots(iid, limit=100)
            from szmarket.eapi import _as_list
            lots = _as_list(lots)
            store.save_lots(s.region, iid, lots)
            units = [float(x.get("unit_price", 0) or 0)
                     for x in store.latest_lots(iid, s.region)]
            units = [u for u in units if u > 0]
            live[iid] = {"lots": len(units),
                         "cheapest": min(units) if units else 0.0}
            sm = summarize_sales(store.recent_sales(iid, s.region, 2000))
            for h in analyze_ending(store.latest_lots(iid, s.region), sm,
                                    max_hours=3.0, min_sales=min_sales):
                h["item_id"] = iid
                snipe_rows.append(h)
        except EapiError as e:
            print(f"[research] WARN lots {iid}: {e}", flush=True)
            live[iid] = {"lots": 0, "cheapest": 0.0}
        if k % 20 == 0 or k == len(win_ids):
            print(f"[research] lots {k}/{len(win_ids)}", flush=True)
    snipe_rows.sort(key=lambda h: h["time_left_sec"])

    # ---- phase 4: stack-split (bulk -> singles) ------------------------
    splits: list[dict] = []
    for iid in need:
        recs = store.recent_sales(iid, s.region, 2000)
        if len(recs) < 50:
            continue
        singles = [r["unit_price"] for r in recs
                   if r.get("amount") == 1 and r.get("unit_price")]
        bulk = [r["unit_price"] for r in recs
                if (r.get("amount") or 0) >= 10 and r.get("unit_price")]
        if len(singles) < 10 or len(bulk) < 10:
            continue
        import statistics
        m1, m10 = statistics.median(singles), statistics.median(bulk)
        if m10 <= 0:
            continue
        spread = (m1 * (1 - AUCTION_FEE) - m10) / m10
        if spread > 0.10:
            splits.append({"item_id": iid, "single_med": m1, "bulk_med": m10,
                           "spread": spread, "n1": len(singles),
                           "n10": len(bulk)})
    splits.sort(key=lambda d: d["spread"], reverse=True)

    # ---- phase 5: lost backpacks (`additional` field) ------------------
    packs = [i for i, info in cat.items()
             if "рюкзак" in str(info.get("ru", "")).lower()][:12]
    pack_notes: list[dict] = []
    for iid in packs:
        try:
            from szmarket.eapi import _as_list
            lots = _as_list(client.lots(iid, limit=25))
        except EapiError as e:
            pack_notes.append({"item_id": iid, "error": str(e)[:120]})
            continue
        adds = [json.dumps(l.get("additional"), ensure_ascii=False)[:300]
                for l in lots if l.get("additional")]
        pack_notes.append({"item_id": iid, "lots": len(lots),
                           "with_additional": len(adds),
                           "sample": adds[:3]})

    # ---- phase 6: cases / lottery (honest: EV unknowable) --------------
    cases = [{"item_id": i, "median": medians.get(i, {}).get("median", 0),
              "count": medians.get(i, {}).get("count", 0)}
             for i, info in cat.items()
             if "кейс" in str(info.get("ru", "")).lower()][:20]
    cases = [c for c in cases if c["count"] >= 3]

    # ---- attach liquidity stats to winners --------------------------------
    for ev in winners:
        bd = ev.get("breakdown", [])
        ev["stats"] = {
            "product_count": medians.get(ev["product_id"], {}).get("count", 0),
            "inputs_min": min(
                [medians.get(b["item"], {}).get("count", 0) for b in bd]
                or [medians.get(ev["product_id"], {}).get("count", 0)]),
            "velocity": medians.get(ev["product_id"], {}).get("velocity", 0.0),
        }

    # ---- save JSON ------------------------------------------------------
    # ---- see-saw swaps: buy cheap side, swap 1:1, sell pricey side -------
    swap_rows = []
    for swp in find_swaps(recipes):
        a, b = swp["a"], swp["b"]
        ma, mb = medians.get(a, {}), medians.get(b, {})
        if ma.get("count", 0) < min_sales or mb.get("count", 0) < min_sales:
            continue
        if ma.get("median", 0) <= 0 or mb.get("median", 0) <= 0:
            continue
        if ma["median"] <= mb["median"]:
            buy_id, sell_id, stn = a, b, swp["a_to_b"]
        else:
            buy_id, sell_id, stn = b, a, swp["b_to_a"]
        buy_m = medians[buy_id]["median"]
        sell_m = medians[sell_id]["median"]
        profit = sell_m * (1 - AUCTION_FEE) - buy_m
        if profit > 0:
            swap_rows.append({"buy_id": buy_id, "sell_id": sell_id,
                              "buy_med": buy_m, "sell_med": sell_m,
                              "profit": profit, "stations": stn,
                              "count_buy": medians[buy_id]["count"],
                              "count_sell": medians[sell_id]["count"]})
    swap_rows.sort(key=lambda d: d["profit"], reverse=True)

    result = {
        "meta": {"region": s.region, "min_sales": min_sales,
                 "recipes": len(recipes), "items": len(need),
                 "covered": covered,
                 "elapsed_min": round((time.time() - t0) / 60, 1)},
        "top": winners[:30],
        "swaps": swap_rows[:15],
        "snipe": snipe_rows[:40],
        "splits": splits[:20],
        "backpacks": pack_notes,
        "cases": cases,
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=1),
                        encoding="utf-8")

    # ---- human report (Russian) ------------------------------------------
    L: list[str] = []
    L.append(f"# Исследование рынка ({s.region})")
    L.append("")
    L.append(f"Посчитано по свежим продажам: рецептов {len(recipes)}, "
             f"предметов {len(need)}, надёжных цен (≥{min_sales} продаж) — "
             f"{covered}. Комиссия аукциона 5% учтена в профите.")
    L.append("")
    KIND_RU = {"hideout": "🔨 крафт на своей базе",
               "barter": "🔄 обмен у NPC", "craft": "🔨 крафт"}
    L.append("## Самые прибыльные крафты и обмены")
    L.append("")
    if not winners:
        L.append("Прибыльных рецептов на надёжных данных не нашлось.")
    for n, ev in enumerate(winners[:15], 1):
        pid = ev["product_id"]
        steps = []
        for b in ev["breakdown"]:
            steps.append(f"купи {name_of(b['item'])} ×{b['count']:g} "
                         f"≈ {_money(b['total'])}")
        if ev.get("money"):
            steps.append(f"заплати NPC {_money(ev['money'])}")
        live_note = ""
        lv = live.get(pid, {})
        if lv.get("cheapest"):
            if lv["cheapest"] < ev["sell_price"]:
                live_note = (f" (⚠ сейчас висит лот по {_money(lv['cheapest'])} "
                             f"за шт — выставляй дешевле него!)")
            else:
                live_note = (f" (дешевле {_money(lv['cheapest'])}/шт "
                             f"сейчас ничего нет — рынок выше медианы)")
        skill = (f", навык {ev['skill']} {ev['skill_level']}"
                 if ev.get("skill") else "")
        stt = ev.get("stats", {})
        roi_flag = (" ⚠ ROI огромный — проверь условия NPC в игре "
                    "(репутация/квест/лимит)" if ev["roi"] > 10 else "")
        L.append(f"### {n}. {name_of(pid)} ×{ev['units']:g} — "
                 f"профит {_money(ev['profit'])} (ROI {ev['roi'] * 100:.0f}%)"
                 f"{roi_flag}")
        L.append(f"{KIND_RU.get(ev['kind'], ev['kind'])}: {ev['station']}"
                 f"{skill}, энергия ⚡{ev['energy']:g}. "
                 f"Продаж: товар {stt.get('product_count', '?')}, "
                 f"входы ≥{stt.get('inputs_min', '?')}.")
        L.append(f"Делай: {'; '.join(steps) if steps else 'просто заплати NPC'} "
                 f"→ получишь {name_of(pid)} → продай ≈ "
                 f"{_money(ev['sell_price'] * ev['units'])} грязными / "
                 f"{_money(ev['revenue'])} чистыми{live_note}.")
        L.append("")
    if len(winners) > 15:
        L.append(f"…и ещё {len(winners) - 15} прибыльных (см. research.json).")
        L.append("")
    cheap_energy = [ev for ev in winners if (ev.get("energy") or 0) <= 100][:10]
    if cheap_energy:
        L.append("## Мгновенные (дешёвые по энергии ⚡≤100)")
        L.append("")
        for ev in cheap_energy:
            L.append(f"- {name_of(ev['product_id'])}: профит "
                     f"{_money(ev['profit'])}, {ev['station']}, "
                     f"⚡{ev['energy']:g}")
        L.append("")
    if splits:
        L.append("## Сплит стака: купить опт, продать в розницу")
        L.append("")
        for d in splits[:10]:
            L.append(f"- {name_of(d['item_id'])}: опт (≥10 шт) "
                     f"{_money(d['bulk_med'])}/шт → розница "
                     f"{_money(d['single_med'])}/шт, "
                     f"навар {d['spread'] * 100:.0f}% "
                     f"(продаж 1шт: {d['n1']}, опта: {d['n10']})")
        L.append("")
    if snipe_rows:
        L.append("## Заканчиваются торги (≤3 ч, ставка или выкуп)")
        L.append("")
        for h in snipe_rows[:15]:
            if h["buyout_ok"]:
                act = (f"🔥 ВЫКУПАЙ за {_money(h['buy_total'])}, профит "
                       f"{_money(h['profit_total'])}")
            else:
                act = (f"✋ СТАВЬ до {_money(h['max_buy_total'])} "
                       f"(след. ставка {_money(h['min_next_bid'])})")
            L.append(f"- {name_of(h['item_id'])} ×{h['amount']}: "
                     f"конец через {h['time_left']} — {act}")
        L.append("")
    if swap_rows:
        L.append("## Качели: обратимые обмены 1:1")
        L.append("")
        L.append("Обмен работает в обе стороны — покупай дешёвую сторону, "
                 "меняй у NPC, продавай дорогую. Повторяемо, пока жив разрыв.")
        for d in swap_rows[:10]:
            stn = ", ".join(d["stations"][:2])
            L.append(f"- {name_of(d['buy_id'])} ({_money(d['buy_med'])}) → "
                     f"{name_of(d['sell_id'])} ({_money(d['sell_med'])}): "
                     f"профит {_money(d['profit'])} @ {stn} "
                     f"(продаж {d['count_buy']}/{d['count_sell']})")
        L.append("")
    # best hour to sell (UTC) for the most liquid winner products
    L.append("## Когда продавать (час по UTC)")
    L.append("")
    hourly_done = 0
    hourly_seen: set[str] = set()
    for ev in winners[:15]:
        pid = ev["product_id"]
        if pid in hourly_seen:
            continue
        hourly_seen.add(pid)
        buckets: dict[int, list[float]] = {}
        for r in store.recent_sales(pid, s.region, 2000):
            t = str(r.get("sold_at") or "")
            try:
                hr = int(t[11:13]) if len(t) >= 13 else -1
            except ValueError:
                hr = -1
            if 0 <= hr <= 23 and r.get("unit_price"):
                buckets.setdefault(hr, []).append(float(r["unit_price"]))
        if len(buckets) < 4:
            continue
        import statistics
        med = {h: statistics.median(v) for h, v in buckets.items()
               if len(v) >= 5}
        if len(med) < 4:
            continue
        best_h = max(med, key=med.get)
        worst_h = min(med, key=med.get)
        if med[worst_h] > 0 and med[best_h] / med[worst_h] >= 1.15:
            L.append(f"- {name_of(pid)}: лучше всего в {best_h:02d}:00 UTC "
                     f"({_money(med[best_h])}), хуже в {worst_h:02d}:00 UTC "
                     f"({_money(med[worst_h])})")
            hourly_done += 1
            if hourly_done >= 8:
                break
    if not hourly_done:
        L.append("Значимых различий по часам не нашлось (разброс < 15%) — "
                 "продавай когда удобно.")
    L.append("")
    L.append("## Потерянные рюкзаки")
    L.append("")
    if not pack_notes or all(p.get("lots", 0) == 0 for p in pack_notes):
        L.append("Активных лотов с рюкзаками не найдено.")
    for p in pack_notes:
        if not p.get("lots"):
            continue
        L.append(f"- {name_of(p['item_id'])}: лотов {p['lots']}, "
                 f"с полем additional: {p.get('with_additional', 0)}")
        for smp in p.get("sample", [])[:2]:
            L.append(f"  `additional: {smp}`")
    L.append("Напоминание: чужой рюкзак открыть нельзя, скупщику он идёт "
             "за 10% содержимого — это не способ заработка, а выкуп для "
             "владельца.")
    L.append("")
    L.append("## Кейсы («лотерея»)")
    L.append("")
    if cases:
        for c in cases[:10]:
            L.append(f"- {name_of(c['item_id'])}: медиана "
                     f"{_money(c['median'])} ({c['count']} продаж)")
        L.append("")
    L.append("Честно: окупаемость кейсов посчитать нельзя — нужна таблица "
             "дропа с шансами, а её в открытом API нет. Покупай кейс, только "
             "если медиана его содержимого (которое ты знаешь из игры) выше "
             "цены кейса.")
    L.append("")
    OUT_MD.write_text("\n".join(L), encoding="utf-8")
    store.close()
    print(f"[research] DONE in {(time.time() - t0) / 60:.1f} min -> "
          f"{OUT_JSON}, {OUT_MD}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
