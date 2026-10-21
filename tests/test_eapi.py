import io
import json
import urllib.error

from szmarket.eapi import EapiClient, EapiError


class FakeResp:
    def __init__(self, payload):
        self._blob = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._blob

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _client(**kw):
    kw.setdefault("min_gap", 0)
    return EapiClient("https://eapi.test", "ID123", "SECRET", region="EU", **kw)


def test_auth_headers_and_url(monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["headers"] = dict(req.header_items())
        return FakeResp([{"ok": True}])

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    out = _client().lots("y1q9", limit=5, offset=10)
    assert out == [{"ok": True}]
    assert seen["url"] == "https://eapi.test/EU/auction/y1q9/lots?limit=5&offset=10"
    lowered = {k.lower(): v for k, v in seen["headers"].items()}
    assert lowered["client-id"] == "ID123"
    assert lowered["client-secret"] == "SECRET"


def test_lots_all_paginates_and_stops(monkeypatch):
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(req.full_url)
        page = len(calls)
        if page <= 2:
            return FakeResp([{"n": page}] * 2)
        return FakeResp([])

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    out = _client().lots_all("abc", limit=2, max_pages=5)
    assert len(out) == 4
    assert len(calls) == 3  # third page empty -> stop
    assert "offset=4" in calls[2]


def test_retry_on_429_then_success(monkeypatch):
    state = {"n": 0}

    def fake_urlopen(req, timeout=None):
        state["n"] += 1
        if state["n"] == 1:
            raise urllib.error.HTTPError(
                req.full_url, 429, "Too Many", {"Retry-After": "0"},
                io.BytesIO(b"slow down"),
            )
        return FakeResp({"lots": []})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("time.sleep", lambda s: None)
    assert _client().history("abc") == {"lots": []}
    assert state["n"] == 2


def test_http_error_wrapped(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, 404, "Not Found", {}, io.BytesIO(b"nope"))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    try:
        _client().lots("zzz")
    except EapiError as e:
        assert e.status == 404
    else:
        raise AssertionError("expected EapiError")


def test_client_requires_credentials():
    try:
        EapiClient("https://eapi.test", "", "")
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_history_prices_listing_unwrap(monkeypatch):
    from szmarket.eapi import _as_list

    # history -> {"prices": [...], "total": N} (NOT a bare list!)
    payload = {"prices": [{"amount": 5, "price": 1000,
                           "time": "2026-10-20T10:00:00Z"}],
               "total": 1}
    rows = _as_list(payload)
    assert len(rows) == 1
    assert rows[0]["price"] == 1000

    def fake_urlopen(req, timeout=None):
        return FakeResp(payload)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    out = _client().history_all("g4lk0", limit=200, max_pages=1)
    assert out == payload["prices"]


def test_clan_score_autodetect():
    from szmarket.eapi import clan_score

    assert clan_score({"name": "x", "rating": 1500}) == ("rating", 1500)
    assert clan_score({"score": 42.5}) == ("score", 42.5)
    assert clan_score({"level": 10}) == ("—", None)
    assert clan_score(None) == ("—", None)


def test_stalker_stats_math():
    from szmarket.eapi import stalker_stats

    ss = stalker_stats({"stats": [
        {"id": "kil", "value": 100}, {"id": "dea", "value": 25},
        {"id": "sho-fir", "value": 1000}, {"id": "sho-hit", "value": 250},
        {"id": "mut-kil", "value": 7}]})
    assert ss["kd"] == 4.0
    assert ss["acc"] == 25.0
    assert ss["mutants"] == 7.0
    assert stalker_stats({})["kd"] == 0.0
