"""Map drafting, component attribution and sensitive-code detection on the sample repo."""
from nightshift import components, proofs, repomap


def test_draft_finds_components_entry_and_asset(sample):
    comps = sample.config["components"]
    assert set(comps) == {"api", "auth", "db", "orders"}
    assert comps["api"].get("entry") and comps["db"].get("asset")
    assert ["orders", "db"] in sample.config["links"]
    assert "@login_required" in sample.config["sensitive"]["decorators"]
    assert "Entry points: Api" in repomap.summary(sample.config)


def test_changes_map_to_components_and_sensitive_code(sample):
    change = {"shop/orders/__init__.py": {"lines": {7}, "added": [], "removed": []}}
    assert components.components_for_changes(sample, change) == {"orders"}
    assert components.sensitive_touches(sample, change) == []
    auth = {"shop/auth.py": {"lines": {2}, "added": ["    @login_required"], "removed": []}}
    assert components.sensitive_touches(sample, auth)
    deps = {"requirements.txt": {"lines": {1}, "added": ["flask"], "removed": []}}
    assert any("requirements" in r for r in components.sensitive_touches(sample, deps))


def test_proof_metadata_and_open_bugs(sample, proof):
    assert proofs.meta(proof) == {"title": "list_orders returns other users' orders", "component": "orders"}
    results = proofs.run_all(sample)
    assert proofs.open_bugs(results) == {"orders_leak": "orders"}
    assert "assert" in results["orders_leak"]["failure"]
    assert proofs.proves_bug(str(proof.relative_to(sample.root)), sample.root)
