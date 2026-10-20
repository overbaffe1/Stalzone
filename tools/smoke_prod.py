"""Live smoke test against PRODUCTION EAPI (needs real credentials).

Reads STALCRAFT_CLIENT_ID / STALCRAFT_CLIENT_SECRET / STALCRAFT_REGION from
env (CI: GitHub Secrets; locally: `.env`). Skips cleanly (exit 0) without them.

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


def main() -> int:
    s = Settings.load()
    if not s.has_credentials:
        print("[prod-smoke] SKIP: no STALCRAFT_CLIENT_ID/SECRET in env")
        return 0
    item = os.environ.get("SMOKE_ITEM_ID", "y1q9")
    client = EapiClient(s.base_url, s.client_id, s.client_secret,
                        region=s.region, min_gap=max(s.min_gap, 1.0))
    report: dict = {"base_host": s.base_url.split("://")[-1],
                    "region": s.region, "item": item}
    ok = True
    for name, fn in (("lots", lambda: client.lots(item, limit=5)),
                     ("history", lambda: client.history(item, limit=5))):
        try:
            rows = _as_list(fn())
            report[f"{name}_ok"] = True
            report[f"{name}_count"] = len(rows)
            report[f"{name}_keys"] = sorted(rows[0].keys()) if rows else []
            report[f"{name}_sample"] = json.dumps(rows[:2], ensure_ascii=False)[:1500]
            print(f"[prod-smoke] {name} {item}@{s.region}: OK ({len(rows)} rows)")
        except EapiError as e:
            ok = False
            report[f"{name}_ok"] = False
            report[f"{name}_error"] = f"HTTP {e.status}: {(e.body or '')[:300]}"
            print(f"[prod-smoke] {name} {item}@{s.region}: FAIL {e}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[prod-smoke] report -> {OUT}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
