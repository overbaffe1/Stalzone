"""Live smoke test against PRODUCTION EAPI (needs real credentials).

Reads STALCRAFT_CLIENT_ID / STALCRAFT_CLIENT_SECRET / STALCRAFT_REGION from
env (CI: Secrets; locally: `.env`). STALZONE_* names and a multi-line
STALZONE_CONFIG blob also work (see szmarket.config). Skips cleanly (exit 0)
without credentials.

Probes several items (SMOKE_ITEM_IDS, comma-separated): liquid goods
(meds/ammo) should have non-empty sales history, unlike rare weapons.

Writes fetched_info/eapi_prod.json — DATA ONLY, never credentials.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from szmarket.config import Settings
from szmarket.eapi import EapiClient, EapiError, _as_list

OUT = Path("fetched_info/eapi_prod.json")
DEFAULT_ITEMS = "y1q9,g4lk0,96430,g5np"  # Viper, mil medkit, indiv medkit, 7.62 BP


def main() -> int:
    s = Settings.load()
    if not s.has_credentials:
        print("[prod-smoke] SKIP: no STALCRAFT_CLIENT_ID/SECRET in env")
        return 0
    raw_items = os.environ.get("SMOKE_ITEM_IDS",
                               os.environ.get("SMOKE_ITEM_ID", DEFAULT_ITEMS))
    items = [x.strip() for x in raw_items.split(",") if x.strip()]
    client = EapiClient(s.base_url, s.client_id, s.client_secret,
                        region=s.region, min_gap=max(s.min_gap, 1.0))
    report: dict = {"base_host": s.base_url.split("://")[-1],
                    "region": s.region, "items": {}}
    ok = True
    for item in items:
        item_rep: dict = {}
        try:
            rows = _as_list(client.lots(item, limit=5))
            item_rep["lots_ok"] = True
            item_rep["lots_count"] = len(rows)
            item_rep["lots_keys"] = sorted(rows[0].keys()) if rows else []
            item_rep["lots_sample"] = json.dumps(rows[:2], ensure_ascii=False)[:1500]
            print(f"[prod-smoke] lots {item}@{s.region}: OK ({len(rows)} rows)")
        except EapiError as e:
            ok = False
            item_rep["lots_ok"] = False
            item_rep["lots_error"] = f"HTTP {e.status}: {(e.body or '')[:300]}"
            print(f"[prod-smoke] lots {item}@{s.region}: FAIL {e}")
        try:
            payload = client.history(item, limit=20)
            rows = _as_list(payload)
            item_rep["history_ok"] = True
            item_rep["history_count"] = len(rows)
            item_rep["history_total"] = payload.get("total") if isinstance(payload, dict) else None
            item_rep["history_keys"] = sorted(rows[0].keys()) if rows else []
            item_rep["history_sample"] = json.dumps(rows[:3], ensure_ascii=False)[:2000]
            print(f"[prod-smoke] history {item}@{s.region}: OK ({len(rows)} rows)")
        except EapiError as e:
            item_rep["history_ok"] = False
            item_rep["history_error"] = f"HTTP {e.status}: {(e.body or '')[:300]}"
            print(f"[prod-smoke] history {item}@{s.region}: FAIL {e}")
        report["items"][item] = item_rep
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[prod-smoke] report -> {OUT}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
