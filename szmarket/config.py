"""Configuration loading: `.env` file + environment variables (stdlib only).

No third-party dependency (no python-dotenv) so CI stays minimal.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent

# Public demo credentials from the official docs (https://eapi.stalcraft.net/auth.html).
# Safe to commit: they only access the demo API with fake data.
DEMO_BASE_URL = "https://dapi.stalzone.com"
DEMO_CLIENT_ID = "1"
DEMO_CLIENT_SECRET = "E98cm6J9NNjTQopph0c2eIXNKafg4R1Cjz0TZh2D"

PROD_BASE_URL = "https://eapi.stalcraft.net"

# Auction economics (verified against community projects).
AUCTION_FEE = 0.05  # 5% commission taken from the sale price
BREAKEVEN_MULT = 1.0 / (1.0 - AUCTION_FEE)  # buy * 1.05263... = breakeven sale


def load_dotenv(path: str | Path = ".env") -> dict[str, str]:
    """Minimal `.env` parser (KEY=VALUE, ignores comments/blank lines).

    Never overrides real environment variables. Returns the values it saw.
    """
    p = Path(path)
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    values: dict[str, str] = {}
    if not p.exists():
        return values
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("'\"")
        if not key:
            continue
        if key not in os.environ:
            os.environ[key] = val
        values[key] = os.environ.get(key, val)
    return values


# Official rate limits (EXBO-Studio/stalzone-api#3, staff reply):
# 100 tokens per minute per app, auction requests cost 2 tokens
# => max ~50 auction requests/minute sustained (bursts allowed).
RATE_TOKENS_PER_MIN = 100.0
RATE_AUCTION_COST = 2.0


@dataclass
class Settings:
    base_url: str = PROD_BASE_URL
    client_id: str = ""
    client_secret: str = ""
    region: str = "RU"
    min_gap: float = 0.1
    tokens_per_min: float = RATE_TOKENS_PER_MIN
    auction_cost: float = RATE_AUCTION_COST
    db_path: str = "data/market.db"

    @staticmethod
    def _parse_blob(blob: str) -> dict[str, str]:
        """Parse a multi-line `KEY = VALUE` blob (the STALZONE_CONFIG secret)."""
        out: dict[str, str] = {}
        for line in blob.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip("'\"")
            if key:
                out[key] = val
        return out

    @classmethod
    def load(cls, env_file: str | Path = ".env") -> "Settings":
        load_dotenv(env_file)
        blob_vars = cls._parse_blob(os.environ.get("STALZONE_CONFIG", ""))

        def get(*names: str, default: str = "") -> str:
            for n in names:  # real env / .env first
                v = os.environ.get(n, "")
                if v:
                    return v
            for n in names:  # then the STALZONE_CONFIG blob
                v = blob_vars.get(n, "")
                if v:
                    return v
            return default

        return cls(
            base_url=get("EAPI_BASE_URL", default=PROD_BASE_URL).rstrip("/"),
            client_id=get("STALCRAFT_CLIENT_ID", "STALZONE_CLIENT_ID"),
            client_secret=get("STALCRAFT_CLIENT_SECRET", "STALZONE_CLIENT_SECRET"),
            region=get("STALCRAFT_REGION", "STALZONE_REGION", default="RU"),
            min_gap=float(get("EAPI_MIN_GAP", default="0.1")),
            tokens_per_min=float(get("EAPI_TOKENS_PER_MIN",
                                     default=str(RATE_TOKENS_PER_MIN))),
            auction_cost=float(get("EAPI_AUCTION_COST",
                                   default=str(RATE_AUCTION_COST))),
            db_path=get("MARKET_DB", default="data/market.db"),
        )

    @property
    def has_credentials(self) -> bool:
        return bool(self.client_id and self.client_secret)
