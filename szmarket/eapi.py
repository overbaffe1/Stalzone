"""Minimal STALCRAFT EAPI client (stdlib only, secret-based auth).

Auth: https://eapi.stalcraft.net/auth.html — we use the simple
"Secret Based Authentication": every request carries
`Client-Id` + `Client-Secret` headers, no token juggling needed.

Endpoints used (all regions: RU / EU / NA / SEA):
  GET /{region}/auction/{item}/lots    — active lots (paginated, ~200 max per call)
  GET /{region}/auction/{item}/history — sales history (price + amount)
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class EapiError(Exception):
    def __init__(self, message: str, status: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status = status
        self.body = body


def _as_list(payload: Any) -> list[dict[str, Any]]:
    """EAPI responses may be a bare list or a paginated listing object.

    Known listing keys (per official reference + onejeuu/stalcraft-api):
      lots -> {"lots": [...], "total": N}
      history -> {"prices": [...], "total": N}
    """
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("lots", "prices", "items", "history", "results", "data"):
            if isinstance(payload.get(key), list):
                return payload[key]
    return []


class EapiClient:
    def __init__(
        self,
        base_url: str,
        client_id: str,
        client_secret: str,
        region: str = "RU",
        min_gap: float = 1.0,
        timeout: float = 20.0,
        max_retries: int = 3,
    ) -> None:
        if not client_id or not client_secret:
            raise ValueError("EapiClient needs client_id + client_secret")
        self.base_url = base_url.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.region = region
        self.min_gap = min_gap
        self.timeout = timeout
        self.max_retries = max_retries
        self._last_ts = 0.0

    def _headers(self) -> dict[str, str]:
        return {
            "Client-Id": self.client_id,
            "Client-Secret": self.client_secret,
            "Accept": "application/json",
            "User-Agent": "stalzone-market-app/0.1",
        }

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        # Rate-limit politeness: minimal gap between requests.
        wait = self.min_gap - (time.monotonic() - self._last_ts)
        if wait > 0:
            time.sleep(wait)
        url = self.base_url + path
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            if clean:
                url += "?" + urllib.parse.urlencode(clean)

        last_err: Exception | None = None
        for attempt in range(self.max_retries + 1):
            req = urllib.request.Request(url, headers=self._headers(), method="GET")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    self._last_ts = time.monotonic()
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                try:
                    body = e.read().decode("utf-8", "replace")[:2000]
                except Exception:
                    body = ""
                if e.code == 429 and attempt < self.max_retries:
                    retry_after = None
                    try:
                        retry_after = e.headers.get("Retry-After") if e.headers else None
                    except Exception:
                        retry_after = None
                    time.sleep(float(retry_after) if retry_after else 2.0 * (attempt + 1))
                    continue
                raise EapiError(f"GET {path} -> HTTP {e.code}: {body}", status=e.code, body=body)
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as e:
                last_err = e
                if attempt < self.max_retries:
                    time.sleep(1.0 * (attempt + 1))
                    continue
                raise EapiError(f"GET {path} failed: {e!r}")
        raise EapiError(f"GET {path} failed after retries: {last_err!r}")

    # -- public API ------------------------------------------------------
    def clans(self, region: str | None = None) -> Any:
        return self._get(f"/{region or self.region}/clans")

    def lots(
        self,
        item_id: str,
        region: str | None = None,
        limit: int = 100,
        offset: int = 0,
        sort: str | None = None,
        order: str | None = None,
    ) -> Any:
        return self._get(
            f"/{region or self.region}/auction/{item_id}/lots",
            {"limit": limit, "offset": offset, "sort": sort, "order": order},
        )

    def lots_all(
        self, item_id: str, region: str | None = None, limit: int = 200, max_pages: int = 5
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for page in range(max_pages):
            batch = _as_list(self.lots(item_id, region, limit=limit, offset=page * limit))
            if not batch:
                break
            out.extend(batch)
            if len(batch) < limit:
                break
        return out

    def history(
        self, item_id: str, region: str | None = None, limit: int = 100, offset: int = 0
    ) -> Any:
        return self._get(
            f"/{region or self.region}/auction/{item_id}/history",
            {"limit": limit, "offset": offset},
        )

    def history_all(
        self, item_id: str, region: str | None = None, limit: int = 200, max_pages: int = 5
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for page in range(max_pages):
            batch = _as_list(self.history(item_id, region, limit=limit, offset=page * limit))
            if not batch:
                break
            out.extend(batch)
            if len(batch) < limit:
                break
        return out
