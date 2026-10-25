from nightshift import attack

MAP = {"components": {
    "api": {"name": "Public API", "entry": True},
    "accounts": {"name": "Accounts"},
    "orders": {"name": "Orders"},
    "admin": {"name": "Admin"},
    "db": {"name": "Database", "asset": True},
    "logs": {"name": "Logs"},
}, "links": [["api", "accounts"], ["api", "orders"], ["orders", "db"], ["accounts", "admin"], ["admin", "db"], ["api", "logs"]]}


def test_no_bugs_means_only_the_public_surface_is_reachable():
    assert attack.reach(MAP, set()) == {"api"}
    assert attack.breach_paths(MAP, set()) == []


def test_bugs_must_line_up_to_reach_an_asset():
    assert attack.breach_paths(MAP, {"accounts", "db"}) == []  # admin is closed
    assert attack.breach_paths(MAP, {"accounts", "admin", "db"}) == [["api", "accounts", "admin", "db"]]


def test_breach_recommends_the_single_cut():
    open_bugs = {"a1": "accounts", "a2": "admin", "d1": "db", "o1": "orders"}
    bug, reason = attack.recommend(MAP, open_bugs)
    assert bug == "d1" and "alone cuts every path" in reason  # both paths end in the database


def test_breach_cut_skips_a_fix_already_waiting():
    open_bugs = {"a1": "accounts", "a2": "admin", "d1": "db"}
    bug, _ = attack.recommend(MAP, open_bugs, skip={"d1"})
    assert bug in {"a1", "a2"}


def test_without_breach_prefers_the_bug_that_shrinks_reach_most():
    bug, reason = attack.recommend(MAP, {"o1": "orders", "l1": "logs"})
    assert bug == "o1"  # orders leads toward the database, logs doesn't
    assert "No path reaches an asset" in reason


def test_scope_limits_the_choice_unless_breached():
    assert attack.recommend(MAP, {"o1": "orders", "l1": "logs"}, scope=["logs"])[0] == "l1"
    breach = {"o1": "orders", "d1": "db", "l1": "logs"}
    assert attack.recommend(MAP, breach, scope=["logs"])[0] == "d1"


def test_next_fix_lifecycle():
    assert attack.next_fix(MAP, {}, scope=["orders"])[0] is None
    assert "safe" in attack.next_fix(MAP, {}, scope=["orders"])[1]
    assert attack.next_fix(MAP, {"o1": "orders"})[0] == "o1"
    bug, reason = attack.next_fix(MAP, {"o1": "orders"}, waiting={"o1"})
    assert bug is None and "waiting for review: o1" in reason
    assert attack.next_fix(MAP, {"o1": "orders"}, bug="o1")[0] == "o1"
    assert attack.next_fix(MAP, {"o1": "orders"}, declined={"o1"}, bug="o1")[0] is None
    assert attack.next_fix(MAP, {}, bug="o1")[0] is None


def test_proximity_falls_off_with_distance():
    assert attack.proximity(MAP, "db") == 1.0
    assert attack.proximity(MAP, "orders") > attack.proximity(MAP, "accounts")
    assert attack.proximity(MAP, "logs") == 0.0
