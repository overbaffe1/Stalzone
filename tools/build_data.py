"""Build compact `data/catalog.json` + `data/recipes.json` from official dumps.

Sources (EXBO-Studio/stalzone-database, realm `ru`):
  listing.json          — all 2300+ items: id, RU/EN names, icon path
  hideout_recipes.json  — 373 hideout crafts (bench, perks, ingredients)
  barter_recipes.json   — settlement barter offers (items + money cost)

Usage:
  python tools/build_data.py --from-dir /tmp/exbo   # local *.json files
  python tools/build_data.py --download              # fetch from GitHub raw
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW_BASE = ("https://raw.githubusercontent.com/EXBO-Studio/stalzone-database"
            "/main/ru")
SOURCES = ["listing.json", "hideout_recipes.json", "barter_recipes.json"]

BENCH_RU = {
    "workbench": "Верстак",
    "laboratory_table": "Лабораторный стол",
    "kitchen_table": "Кухонный стол",
}


def _ru(text_obj: dict, default: str = "") -> str:
    if not isinstance(text_obj, dict):
        return default
    lines = text_obj.get("lines", {})
    return str(lines.get("ru", lines.get("en", default)))


def fetch_sources(out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in SOURCES:
        url = f"{RAW_BASE}/{name}"
        print(f"[build] downloading {url} ...")
        req = urllib.request.Request(url, headers={"User-Agent": "szmarket/0.1"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            (out_dir / name).write_bytes(resp.read())
    return out_dir


def build_catalog(listing: list) -> dict:
    catalog: dict[str, dict] = {}
    for entry in listing:
        data_path = str(entry.get("data", ""))
        stem = Path(data_path).stem
        if not stem or stem in catalog:
            continue
        cat = data_path.replace("/items/", "").rsplit("/", 1)[0] if "/" in data_path else ""
        catalog[stem] = {
            "ru": _ru(entry.get("name", {}), stem),
            "en": str(entry.get("name", {}).get("lines", {}).get("en", "")),
            "icon": str(entry.get("icon", "")),
            "cat": cat,
        }
    return catalog


def build_recipes(hideout: dict, barter: list) -> list[dict]:
    perk_ru = {p["id"]: _ru(p.get("name", {}), p["id"]) for p in hideout.get("perks", [])}
    out: list[dict] = []

    for i, r in enumerate(hideout.get("recipes", [])):
        result = r.get("result", [])
        if not result:
            continue
        perks = r.get("requirements", {}).get("perks", {}) or {}
        skill_id = next(iter(perks), "")
        bench = str(r.get("bench", ""))
        out.append({
            "id": f"hideout:{i:03d}:{result[0]['item']}",
            "kind": "hideout",
            "station": BENCH_RU.get(bench, bench),
            "category": _ru(r.get("category", "")),
            "skill": perk_ru.get(skill_id, skill_id),
            "skill_level": int(perks.get(skill_id, 0)) if skill_id else 0,
            "productId": str(result[0]["item"]),
            "productCount": int(result[0].get("amount", 1)),
            "money": 0,
            "energy": float(r.get("energy", 0) or 0),
            "requirements": [
                {"itemId": str(g["item"]), "count": int(g.get("amount", 1))}
                for g in r.get("ingredients", [])
            ],
        })

    n = 0
    for s in barter:
        settlement = _ru(s.get("settlementTitle", {}), "?")
        for rec in s.get("recipes", []):
            for oi, offer in enumerate(rec.get("offers", [])):
                out.append({
                    "id": f"barter:{n:03d}:{rec.get('item')}",
                    "kind": "barter",
                    "station": f"Бартер · {settlement}",
                    "category": "",
                    "skill": "",
                    "skill_level": int(rec.get("settlementRequiredLevel", 0)),
                    "productId": str(rec.get("item")),
                    "productCount": 1,
                    "money": int(offer.get("cost", 0)),
                    "energy": 0.0,
                    "requirements": [
                        {"itemId": str(g["item"]), "count": int(g.get("amount", 1))}
                        for g in offer.get("requiredItems", [])
                    ],
                })
                n += 1
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--from-dir", type=Path)
    src.add_argument("--download", action="store_true")
    args = ap.parse_args(argv)

    if args.download:
        src_dir = fetch_sources(ROOT / "data" / "raw")
    else:
        src_dir = args.from_dir
        missing = [n for n in SOURCES if not (src_dir / n).exists()]
        if missing:
            print(f"[build] missing in {src_dir}: {missing}")
            return 1

    listing = json.loads((src_dir / "listing.json").read_text(encoding="utf-8"))
    hideout = json.loads((src_dir / "hideout_recipes.json").read_text(encoding="utf-8"))
    barter = json.loads((src_dir / "barter_recipes.json").read_text(encoding="utf-8"))

    catalog = build_catalog(listing)
    recipes = build_recipes(hideout, barter)

    meta = {"built_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "items": len(catalog), "recipes": len(recipes)}
    (ROOT / "data" / "catalog.json").write_text(
        json.dumps({"_meta": meta, "items": catalog}, ensure_ascii=False,
                   separators=(",", ":")),
        encoding="utf-8")
    (ROOT / "data" / "recipes.json").write_text(
        json.dumps({"_meta": meta, "recipes": recipes}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    print(f"[build] items={len(catalog)} recipes={len(recipes)} "
          f"(hideout={sum(1 for r in recipes if r['kind'] == 'hideout')}, "
          f"barter={sum(1 for r in recipes if r['kind'] == 'barter')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
