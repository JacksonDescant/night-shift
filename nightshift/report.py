"""Send the crew's progress to the dashboard. Never fails the pipeline.

Events go to the dashboard's per-repository endpoint and are also appended to
.nightshift/events.jsonl, so nothing is lost if the dashboard is unreachable.
"""
import json
import os
import subprocess
import time

import httpx

from nightshift import components
from nightshift.config import load_env

EVENT_TYPES = {
    "patrol_started", "patrol_done", "scan_started", "scan_done", "finding_triaged", "bug_confirmed",
    "paths", "pr_opened", "bug_fixed", "gate_result", "escalated", "auto_merged", "chat", "push",
    "review_merged", "review_declined", "map_drafted",
}


def _read(path, limit):
    try:
        return path.read_text()[:limit]
    except (OSError, UnicodeDecodeError):
        return None


def enrich(repo, event_type, data):
    """Attach the evidence the dashboard's case view shows, so Bots only send ids and paths."""
    data = dict(data)
    if event_type == "finding_triaged" and data.get("id") and "tool" not in data:
        raw = repo.read_state("raw.json", {}) or {}
        findings = raw.get("findings", [])
        finding = next((f for f in findings if f["id"] == data["id"]), None)
        if finding is None and data.get("component"):
            # Found by reading the code: still credit a scanner that flagged the same component.
            finding = next((f for f in findings if components.finding_in(repo, f, [data["component"]])), None)
        if finding:
            data.update({k: finding[k] for k in ("tool", "rule", "file", "line", "message")})
    elif event_type == "bug_confirmed" and data.get("test_path"):
        data.setdefault("test_code", _read(repo.root / data["test_path"], 2500))
        proof = (repo.read_state("proofs.json", {}) or {}).get(data.get("bug_id", ""), {})
        data.setdefault("failure", proof.get("failure"))
        data.setdefault("title", proof.get("title"))
        data.setdefault("component", proof.get("component"))
    elif event_type == "pr_opened" and data.get("bug_id") and "diff" not in data:
        from nightshift.gate import branch_for, default_branch

        diff = subprocess.run(["git", "diff", f"origin/{default_branch(repo)}...{branch_for(data['bug_id'])}",
                               "--", ".", f":!{repo.config['tests_dir']}"],
                              cwd=repo.root, capture_output=True, text=True).stdout
        if diff:
            data["diff"] = diff[:4000]
            data["lines"] = sum(1 for line in diff.splitlines()
                                if line[:1] in "+-" and not line.startswith(("+++", "---")))
    return data


def post(repo, event_type, data=None):
    load_env()
    event = {"type": event_type, "data": enrich(repo, event_type, data or {}), "ts": time.time()}
    with open(repo.state / "events.jsonl", "a") as fh:
        fh.write(json.dumps(event) + "\n")
    server = os.environ.get("NIGHTSHIFT_SERVER", "http://127.0.0.1:8000").rstrip("/")
    token = os.environ.get("NIGHTSHIFT_TOKEN", "")
    if repo.slug:
        try:
            httpx.post(f"{server}/api/repos/{repo.slug}/events", json=event,
                       headers={"Authorization": f"Bearer {token}"}, timeout=3)
        except httpx.HTTPError:
            pass  # the events log above still has it
    return event


def chat(repo, bot, text):
    return post(repo, "chat", {"bot": bot, "text": text})
