"""The dashboard's per-repository picture, driven by crew events."""
from server import board


def new_board():
    state = board.fresh("acme/shop")
    b = board.Board(state)
    return state, lambda kind, data, ts: b.apply({"type": kind, "data": data, "ts": ts})


def test_autonomous_fix_walks_the_crew_and_the_case():
    state, ev = new_board()
    ev("push", {"message": "speed up orders"}, 100)
    ev("patrol_started", {"components": ["orders"], "trigger": "event"}, 101)
    assert state["crew"]["Watcher"]["state"] == "working"
    ev("scan_done", {"count": 1}, 105)
    ev("finding_triaged", {"id": "f1", "verdict": "confirmed", "bug_id": "o1", "tool": "semgrep", "rule": "r",
                           "file": "a.py", "line": 3, "component": "orders"}, 110)
    ev("bug_confirmed", {"bug_id": "o1", "title": "Orders leak", "test_path": ".nightshift/proofs/test_o1.py"}, 120)
    ev("paths", {"recommended_fix": "o1", "open_bugs": {"o1": "orders"}}, 121)
    assert state["crew"]["Fixer"]["state"] == "working"
    ev("pr_opened", {"bug_id": "o1", "url": "u", "lines": 2}, 150)
    ev("gate_result", {"bug": "o1", "verdict": "auto_merge", "checks": [{"name": "x", "ok": True}] * 5}, 170)
    ev("auto_merged", {"bug_id": "o1"}, 175)
    c = state["cases"]["o1"]
    assert c["stage"] == "closed" and c["closed"]["how"] == "auto" and c["title"] == "Orders leak"
    m = state["metrics"]
    assert (m["proven"], m["auto_merges"], m["push_to_proof"], m["push_to_merge"]) == (1, 1, 20.0, 75.0)


def test_escalation_then_human_merge():
    state, ev = new_board()
    ev("push", {"message": "admin tweak"}, 0)
    ev("bug_confirmed", {"bug_id": "a1", "test_path": ".nightshift/proofs/test_a1.py"}, 5)
    ev("pr_opened", {"bug_id": "a1", "url": "u"}, 20)
    ev("escalated", {"bug_id": "a1", "reasons": ["edits sensitive function require_admin()"]}, 30)
    assert state["crew"]["Night Manager"]["state"] == "waiting"
    ev("review_merged", {"bug_id": "a1", "number": 6, "url": "u"}, 90)
    assert state["cases"]["a1"]["closed"]["how"] == "human" and state["metrics"]["human_touches"] == 1


def test_github_hiccup_is_not_an_escalation():
    state, ev = new_board()
    ev("push", {"message": "x"}, 0)
    ev("bug_confirmed", {"bug_id": "o1", "test_path": "t"}, 5)
    ev("escalated", {"bug_id": "o1", "reasons": ["couldn't find the PR"], "retryable": True}, 31)
    assert state["cases"]["o1"]["retrying"] and state["metrics"]["escalations"] == 0
    ev("auto_merged", {"bug_id": "o1"}, 60)
    assert state["cases"]["o1"]["closed"]["how"] == "auto"


def test_a_harmless_push_ends_all_clear():
    state, ev = new_board()
    ev("push", {"message": "add currency to the menu API"}, 0)
    ev("patrol_started", {"components": ["menu"], "trigger": "event"}, 2)
    ev("finding_triaged", {"id": "f1", "verdict": "dismissed", "reason": "harmless"}, 10)
    ev("patrol_done", {}, 30)
    v = state["verdict"]
    assert v["clean"] and v["message"] == "add currency to the menu API" and v["dismissed"] == 1


def test_a_returning_bug_is_caught_by_its_regression_test():
    state, ev = new_board()
    ev("push", {"message": "refactor"}, 0)
    ev("bug_confirmed", {"bug_id": "o1", "test_path": "tests/nightshift/test_o1.py"}, 5)
    assert "regression test" in state["cases"]["o1"]["spotted"]["source"]
