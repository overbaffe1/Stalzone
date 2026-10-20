import json
import os

from szmarket import catalog, config


def test_load_catalog(tmp_path):
    d = tmp_path / "ru" / "items" / "weapon" / "pistol"
    d.mkdir(parents=True)
    (d / "0n9q.json").write_text(json.dumps({
        "id": "0n9q", "category": "weapon/pistol",
        "name": {"type": "translation", "key": "k",
                 "lines": {"ru": "ПМ", "en": "PM"}}}), encoding="utf-8")
    (d / "broken.json").write_text("{oops", encoding="utf-8")
    out = catalog.load_catalog(tmp_path / "ru" / "items")
    assert out == {"0n9q": {"id": "0n9q", "name_ru": "ПМ",
                            "name_en": "PM", "category": "weapon/pistol"}}


def test_dotenv_and_settings(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# comment\nSTALCRAFT_REGION=EU\nEAPI_MIN_GAP=0.5\n",
                   encoding="utf-8")
    monkeypatch.delenv("STALCRAFT_REGION", raising=False)
    monkeypatch.delenv("EAPI_MIN_GAP", raising=False)
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    s = config.Settings.load(".env")
    assert s.region == "EU"
    assert s.min_gap == 0.5
    assert os.environ["STALCRAFT_REGION"] == "EU"
    # real env wins over .env
    monkeypatch.setenv("STALCRAFT_REGION", "NA")
    assert config.Settings.load(".env").region == "NA"


def test_stalzone_env_fallback_names(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    for k in ("STALCRAFT_CLIENT_ID", "STALCRAFT_CLIENT_SECRET", "STALCRAFT_REGION",
              "STALZONE_CLIENT_ID", "STALZONE_CLIENT_SECRET", "STALZONE_REGION"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("STALZONE_CLIENT_ID", "4682")
    monkeypatch.setenv("STALZONE_CLIENT_SECRET", "s3cr3t")
    monkeypatch.setenv("STALZONE_REGION", "EU")
    s = config.Settings.load(tmp_path / ".env-missing")
    assert (s.client_id, s.client_secret, s.region) == ("4682", "s3cr3t", "EU")
    assert s.has_credentials
    # STALCRAFT_* wins when both are set
    monkeypatch.setenv("STALCRAFT_REGION", "RU")
    assert config.Settings.load(tmp_path / ".env-missing").region == "RU"
