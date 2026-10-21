"""Mirror client: URL shape, unwrap, error wrapping (mocked network)."""
import io
import json
import urllib.error

import pytest

from szmarket.mirror import MIRROR_BASE, MirrorClient


class FakeResp:
    def __init__(self, payload):
        self._blob = json.dumps(payload).encode()

    def read(self):
        return self._blob

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_history_unwraps_prices_listing(monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        return FakeResp({"total": 2, "prices": [{"amount": 1, "price": 500,
                                                "time": "2026-10-21T10:00:00Z"}]})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    rows = MirrorClient(region="RU", min_gap=0).history_all("knmdv")
    assert rows and rows[0]["price"] == 500
    assert "action=history" in seen["url"]
    assert "item=knmdv" in seen["url"]
    assert "region=ru" in seen["url"]
    assert seen["url"].startswith(MIRROR_BASE)


def test_lots_passthrough(monkeypatch):
    def fake_urlopen(req, timeout=None):
        assert "action=lots" in req.full_url
        return FakeResp({"total": 1, "lots": [{"amount": 3}]})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    assert MirrorClient(min_gap=0).lots_all("abc") == [{"amount": 3}]


def test_http_error_wrapped(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 503, "Down", {},
                                     io.BytesIO(b"oops"))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("time.sleep", lambda s: None)
    from szmarket.eapi import EapiError

    with pytest.raises(EapiError):
        MirrorClient(min_gap=0, max_retries=0).history("abc")
