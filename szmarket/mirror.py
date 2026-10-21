"""Unofficial EAPI mirror client (no credentials needed).

Source: lunar-client.ru/web-api/stalcraft/auction.php (documented in
Art3mLapa/unofficial-stalzone-api, "Зеркала официального API").
Response shapes match the official EAPI ({total, prices:[...]}), so all
analytics code works unchanged. Data may lag behind official EAPI;
treat it as a fallback when no Client-Secret is configured.

Params: action=history|lots, item=<id>, region=ru, limit=200.
"""
from __future__ import annotations

import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .eapi import EapiError, _as_list

MIRROR_BASE = "https://lunar-client.ru/web-api/stalcraft/auction.php"


class MirrorClient:
    """Drop-in reader with the EapiClient subset the app uses."""

    is_mirror = True

    def __init__(
        self,
        region: str = "RU",
        base_url: str = MIRROR_BASE,
        timeout: float = 25.0,
        min_gap: float = 1.0,
        max_retries: int = 3,
    ) -> None:
        self.region = (region or "RU").upper()
        self.base_url = base_url
        self.timeout = timeout
        self.min_gap = min_gap
        self.max_retries = max_retries
        self._last_ts = 0.0

    def _get(self, action: str, item_id: str, limit: int) -> Any:
        params = {"action": action, "item": item_id,
                  "region": self.region.lower(),
                  "limit": int(limit)}
        url = self.base_url + "?" + urllib.parse.urlencode(params)
        last: Exception | None = None
        for attempt in range(self.max_retries + 1):
            wait = self.min_gap - (time.monotonic() - self._last_ts)
            if wait > 0:
                time.sleep(wait)
            self._last_ts = time.monotonic()
            try:
                req = urllib.request.Request(
                    url, headers={"User-Agent": "szmarket/0.5",
                                  "Accept": "application/json"})
                with urllib.request.urlopen(req,
                                            timeout=self.timeout) as resp:
                    import json

                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                try:
                    body = e.read().decode("utf-8", "replace")[:500]
                except Exception:  # noqa: BLE001
                    body = ""
                last = EapiError(f"mirror HTTP {e.code} action={action} "
                                 f"item={item_id}", status=e.code, body=body)
                if e.code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                    time.sleep(2.0 * (attempt + 1))
                    continue
                raise last from e
            except (urllib.error.URLError, TimeoutError, ValueError) as e:
                last = EapiError(f"mirror network error: {e}")
                if attempt < self.max_retries:
                    time.sleep(2.0 * (attempt + 1))
                    continue
                raise last from e
        raise last or EapiError("mirror request failed")

    def lots(self, item_id: str, limit: int = 200, **_: Any) -> Any:
        return self._get("lots", item_id, limit)

    def history(self, item_id: str, limit: int = 200, **_: Any) -> Any:
        return self._get("history", item_id, limit)

    def lots_all(self, item_id: str, limit: int = 200,
                 max_pages: int = 1, **_: Any) -> list[dict[str, Any]]:
        return _as_list(self.lots(item_id, limit=limit))

    def history_all(self, item_id: str, limit: int = 200,
                    max_pages: int = 1, **_: Any) -> list[dict[str, Any]]:
        return _as_list(self.history(item_id, limit=limit))
