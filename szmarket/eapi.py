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
import threading
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


class TokenBucket:
    """Client-side rate limiter mirroring EXBO's token bucket.

    Official numbers (EXBO-Studio/stalzone-api#3, staff reply):
    100 tokens per minute per app, auction requests cost 2 tokens
    => max ~50 auction requests/minute sustained. Bursts up to the full
    bucket capacity are allowed. Thread-safe: share one client/bucket
    across fetcher threads.
    """

    def __init__(self, per_minute: float = 100.0, capacity: float | None = None):
        self.rate = per_minute / 60.0
        self.capacity = capacity if capacity is not None else per_minute
        self.tokens = float(self.capacity)
        self.updated = time.monotonic()
        self._lock = threading.Lock()

    def take(self, cost: float = 1.0, max_wait: float = 180.0) -> float:
        """Block until `cost` tokens are available; returns seconds waited."""
        waited = 0.0
        while True:
            with self._lock:
                now = time.monotonic()
                self.tokens = min(self.capacity,
                                  self.tokens + (now - self.updated) * self.rate)
                self.updated = now
                if self.tokens >= cost:
                    self.tokens -= cost
                    return waited
                need = (cost - self.tokens) / self.rate
            step = min(max(need, 0.01), 1.0)
            if waited + step > max_wait:
                raise EapiError("rate budget exhausted (bucket wait too long)")
            time.sleep(step)
            waited += step


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
        min_gap: float = 0.1,
        timeout: float = 20.0,
        max_retries: int = 3,
        bucket: TokenBucket | None = None,
        request_cost: float = 2.0,
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
        self.bucket = bucket or TokenBucket()
        self.request_cost = request_cost
        self._last_ts = 0.0
        self._ts_lock = threading.Lock()

    def _headers(self) -> dict[str, str]:
        return {
            "Client-Id": self.client_id,
            "Client-Secret": self.client_secret,
            "Accept": "application/json",
            "User-Agent": "stalzone-market-app/0.1",
        }

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        # Rate-limit politeness: token bucket first, tiny gap as a floor.
        self.bucket.take(self.request_cost)
        with self._ts_lock:
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
    def clans(self, region: str | None = None, limit: int = 100,
                offset: int = 0) -> Any:
        return self._get(f"/{region or self.region}/clans",
                         {"limit": limit, "offset": offset})

    def operations(self, username: str | None = None,
                   region: str | None = None, limit: int = 20) -> Any:
        """Operation sessions, optionally filtered by character name."""
        return self._get(
            f"/{region or self.region}/operations/sessions",
            {"limit": limit, "order": "descending",
             "username": username} if username else
            {"limit": limit, "order": "descending"})

    def emission(self, region: str | None = None) -> Any:
        """Current/previous emission state (needs app token)."""
        return self._get(f"/{region or self.region}/emission")
    def profile(self, character: str,
                region: str | None = None) -> Any:
        """Public character profile: alliance, stats, clan."""
        import urllib.parse

        name = urllib.parse.quote(str(character).strip(), safe="")
        return self._get(
            f"/{region or self.region}/character/by-name/{name}/profile")

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


CLAN_SCORE_KEYS = ("rating", "score", "points", "clanPoints", "warPoints",
                   "pvpScore", "totalScore", "experience", "exp")


def clan_score(clan: Any) -> tuple[str, Any]:
    """Очки клана для топа: (поле, значение). Поле автоопределяется."""
    if not isinstance(clan, dict):
        return ("—", None)
    for key in CLAN_SCORE_KEYS:
        val = clan.get(key)
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            return (key, val)
    return ("—", None)


def stalker_stats(data: Any) -> dict[str, float]:
    """Сводные боевые статы персонажа из EAPI-профиля (плоский dict)."""
    out = {"kills": 0.0, "deaths": 0.0, "kd": 0.0, "shots": 0.0,
           "hits": 0.0, "acc": 0.0, "mutants": 0.0, "npc": 0.0, "ach": 0.0}
    if not isinstance(data, dict):
        return out
    raw = {s.get("id"): s.get("value") for s in data.get("stats", [])
           if isinstance(s, dict)}

    def num(key: str) -> float:
        try:
            return float(raw.get(key) or 0)
        except (TypeError, ValueError):
            return 0.0

    kil, dea = num("kil"), num("dea")
    fir, hit = num("sho-fir"), num("sho-hit")
    out.update(kills=kil, deaths=dea, shots=fir, hits=hit,
               mutants=num("mut-kil"), npc=num("npc-kil"),
               ach=num("ach-points"),
               kd=(kil / dea if dea else kil),
               acc=(hit / fir * 100 if fir else 0.0))
    return out
