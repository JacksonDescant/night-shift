"""First-run setup: the crew setup page's values, and the crew following the dashboard's tunnel."""
from fastapi.testclient import TestClient

from nightshift import config as cfg
from server import main, store


def test_setup_page_has_every_bot_and_the_public_url(monkeypatch):
    monkeypatch.setenv("NIGHTSHIFT_PUBLIC_URL", "https://abc.trycloudflare.com/")
    s = TestClient(main.app, client=("127.0.0.1", 5000)).get("/api/setup").json()
    assert s["public_url"] == "https://abc.trycloudflare.com"
    assert s["ingest_token"] == store.ingest_token()
    assert [b["name"] for b in s["bots"]] == ["Watcher", "Triage Guard", "Fixer", "Night Manager"]
    assert all(b["prompt"] for b in s["bots"])


def test_setup_page_is_local_only():
    assert TestClient(main.app, client=("203.0.113.5", 5000)).get("/api/setup").status_code == 403


def test_crew_remembers_the_dashboards_new_address(monkeypatch):
    monkeypatch.delenv("NIGHTSHIFT_SERVER", raising=False)
    cfg.remember_server("https://new.trycloudflare.com/")
    cfg.remember_server("http://evil.example")  # never plain http or anything that isn't a bare origin
    env = (cfg.CREW_HOME / "env").read_text()
    assert env.count("NIGHTSHIFT_SERVER=") == 1 and "NIGHTSHIFT_SERVER=https://new.trycloudflare.com\n" in env
    assert cfg.os.environ["NIGHTSHIFT_SERVER"] == "https://new.trycloudflare.com"
