"""Live smoke test against the PUBLIC demo API (no secrets needed).

Uses demo credentials published in the official docs, so CI can run it:
  GET {demo}/EU/clans                     (documented, must work)
  GET {demo}/{region}/auction/{item}/lots (best effort: demo may lack auction)
  GET {demo}/{region}/auction/{item}/history

Writes fetched_info/eapi_smoke.json. Exit 0 iff the clans call works.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from szmarket.config import DEMO_BASE_URL, DEMO_CLIENT_ID, DEMO_CLIENT_SECRET
from szmarket.eapi import EapiClient, EapiError, _as_list

OUT = Path("fetched_info/eapi_smoke.json")
ITEM = "y1q9"  # "Viper" SMG, known item id
REGION = "EU"


def main() -> int:
    client = EapiClient(DEMO_BASE_URL, DEMO_CLIENT_ID, DEMO_CLIENT_SECRET,
                        region=REGION, min_gap=0.5)
    report: dict = {"base": DEMO_BASE_URL, "region": REGION, "item": ITEM}
    ok = True

    try:
        clans = client.clans()
        report["clans_ok"] = True
        report["clans_type"] = type(clans).__name__
        report["clans_sample"] = str(clans)[:500]
        print(f"[smoke] clans EU: OK ({type(clans).__name__})")
    except EapiError as e:
        ok = False
        report["clans_ok"] = False
        report["clans_error"] = f"HTTP {e.status}: {(e.body or '')[:300]}"
        print(f"[smoke] clans EU: FAIL {e}")

    for name, fn in (("lots", lambda: client.lots(ITEM, limit=5)),
                     ("history", lambda: client.history(ITEM, limit=5))):
        try:
            payload = fn()
            rows = _as_list(payload)
            report[f"{name}_ok"] = True
            report[f"{name}_count"] = len(rows)
            report[f"{name}_keys"] = sorted(rows[0].keys()) if rows else []
            report[f"{name}_sample"] = json.dumps(rows[:2], ensure_ascii=False)[:1500]
            print(f"[smoke] {name} {ITEM}: OK ({len(rows)} rows, "
                  f"keys={report[f'{name}_keys']})")
        except EapiError as e:
            report[f"{name}_ok"] = False
            report[f"{name}_error"] = f"HTTP {e.status}: {(e.body or '')[:300]}"
            print(f"[smoke] {name} {ITEM}: N/A ({e})")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[smoke] report -> {OUT}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
