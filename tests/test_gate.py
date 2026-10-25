"""The autonomy gate, end to end on the sample repo (local mimic, no GitHub)."""
from nightshift import gate


def test_a_small_local_fix_passes_all_five(sample, fix_branch):
    fix_branch()
    result = gate.check(sample, "orders_leak", base="main")
    assert [c["ok"] for c in result["checks"]] == [True] * 5
    assert result["verdict"] == "auto_merge"


def test_a_fix_touching_auth_is_escalated(sample, fix_branch):
    def touch_auth(root):
        (root / "shop/auth.py").write_text((root / "shop/auth.py").read_text() + "\n\ndef login_check():\n    return True\n")
    fix_branch(extra=touch_auth)
    result = gate.check(sample, "orders_leak", base="main")
    failed = {c["name"] for c in result["checks"] if not c["ok"]}
    assert result["verdict"] == "escalate"
    assert failed == {"small diff inside the component", "nothing sensitive changed"}


def test_auto_merge_can_be_switched_off(sample, fix_branch, monkeypatch):
    fix_branch()
    monkeypatch.setenv("NIGHTSHIFT_AUTOMERGE", "0")
    result = gate.check(sample, "orders_leak", base="main")
    assert result["verdict"] == "escalate" and result["checks"][-1]["name"] == "auto-merge enabled"
