from nightshift.cli import _declined_since
from nightshift.workspace import STALE_AFTER, stale_reason


def test_open_pr_is_never_a_leftover():
    assert stale_reason([{"number": 4, "state": "open", "merged": False}], None) is None


def test_closed_or_merged_pr_branch_is_a_leftover():
    assert "PR #2 was closed" in stale_reason([{"number": 2, "state": "closed", "merged": False}], None)
    assert "PR #3 was merged" in stale_reason([{"number": 3, "state": "merged", "merged": True}], None)


def test_branch_without_pr_is_kept_while_fresh():
    assert stale_reason([], 60) is None
    assert "never got a PR" in stale_reason([], STALE_AFTER + 1)


def test_declined_only_counts_closes_during_this_task():
    prs = [{"bug": "a", "number": 1, "state": "closed", "closed": 50},
           {"bug": "b", "number": 2, "state": "closed", "closed": 150},
           {"bug": "c", "number": 3, "state": "merged", "closed": 150}]
    assert _declined_since(prs, 100) == {"b"}
