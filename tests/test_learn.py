"""The living map: what the crew learns about a repository after its map was merged."""
from nightshift import config, learn
from server import board


def test_a_fresh_map_matches_the_code(sample):
    assert learn.drift(sample) == []


def test_new_code_new_links_and_gone_paths_are_proposed(sample):
    root = sample.root
    (root / "shop/billing").mkdir()
    (root / "shop/billing/__init__.py").write_text("from shop.db import connect\n\n\ndef charge():\n    connect()\n")
    (root / "shop/orders/__init__.py").write_text((root / "shop/orders/__init__.py").read_text() + "from shop.auth import login_required\n")
    (root / "shop/auth.py").unlink()
    found = learn.drift(config.load(root))
    kinds = {(p["kind"], p.get("id") or p.get("from") or p.get("component"), p.get("to") or p.get("path")) for p in found}
    assert ("component", "billing", None) in kinds
    assert ("link", "billing", "db") in kinds
    assert ("stale", "auth", "shop/auth.py") in kinds


def test_agent_notes_apply_to_a_new_map_and_drop_once_merged(sample):
    learn.note(sample, "link", "api", "db", "the export view queries the database directly")
    learn.note(sample, "asset", "orders", why="stores order history")
    items = learn.proposals(sample)
    assert {learn.describe(p) for p in items} == {"link api -> db", "mark orders as holding data"}
    new = learn.apply(sample.config, items)
    assert ["api", "db"] in new["links"] and new["components"]["orders"]["asset"]
    sample.config.update(components=new["components"], links=new["links"])  # as if the map PR merged
    assert learn.proposals(sample) == []


def test_board_keeps_component_evidence_and_exposure_history():
    state = board.fresh("acme/shop")
    b = board.Board(state)
    ev = lambda kind, data, ts: b.apply({"type": kind, "data": data, "ts": ts})  # noqa: E731
    ev("patrol_started", {"components": ["orders"]}, 10)
    ev("finding_triaged", {"verdict": "dismissed", "component": "orders", "tool": "bandit"}, 11)
    ev("bug_confirmed", {"bug_id": "o1", "component": "orders"}, 12)
    ev("paths", {"open_bugs": {"o1": "orders"}, "reached": ["api", "orders"], "breach_paths": []}, 13)
    ev("paths", {"open_bugs": {"o1": "orders"}, "reached": ["api", "orders"], "breach_paths": []}, 14)
    ev("auto_merged", {"bug_id": "o1"}, 20)
    ev("paths", {"open_bugs": {}, "reached": ["api"], "breach_paths": []}, 21)
    ev("map_learned", {"proposals": [{"kind": "link", "from": "api", "to": "db"}]}, 22)
    st = state["map"]["stats"]["orders"]
    assert (st["last_checked"], st["findings"], st["dismissed"], st["proven"], st["fixed"]) == (10, 1, 1, 1, 1)
    assert [(h["open"], h["reached"]) for h in state["map"]["history"]] == [(1, 2), (0, 1)]
    assert state["map"]["proposals"][0]["to"] == "db"
