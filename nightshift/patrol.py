"""Patrol strategy: spend limited attention on the components most likely to let an attack
reach an asset.

risk(component) = w.proximity      * how close it sits to an asset along the links
                + w.recent_changes * how often recent commits touched it
                + w.bug_history    * how many bugs it has had before (memory)
                + w.staleness      * how long since it was last checked
"""
import time

from nightshift import attack, components
from nightshift.config import git

RECENT_COMMITS = 10


def recent_changes(repo):
    shas = git("rev-list", f"--max-count={RECENT_COMMITS}", "--no-merges", "HEAD", cwd=repo.root).stdout.split()
    counts = {}
    for sha in shas:
        if git("rev-parse", "--verify", "--quiet", f"{sha}^", cwd=repo.root).returncode:
            continue  # root commit
        for name in components.components_for_diff(repo, f"{sha}^", sha):
            counts[name] = counts.get(name, 0) + 1
    return counts


def scores(repo, memory, now=None):
    now = now or time.time()
    w = repo.config["patrol"]["weights"]
    changes = recent_changes(repo)
    result = []
    for name in repo.config["components"]:
        info = memory["components"].get(name, {})
        last = info.get("last_patrol")
        parts = {
            "proximity": attack.proximity(repo.config, name),
            "recent_changes": min(changes.get(name, 0), 3) / 3,
            "bug_history": min(info.get("bugs", 0), 3) / 3,
            "staleness": 1.0 if last is None else min((now - last) / 3600, 1.0),
        }
        total = sum(w.get(k, 1) * v for k, v in parts.items())
        result.append({"component": name, "score": round(total, 2), "parts": {k: round(v, 2) for k, v in parts.items()}})
    return sorted(result, key=lambda r: -r["score"])


def describe(entry):
    p = entry["parts"]
    bits = []
    if p["proximity"] >= 1:
        bits.append("holds valuable data")
    elif p["proximity"] >= 0.5:
        bits.append("one step from valuable data")
    if p["recent_changes"]:
        bits.append("changed recently")
    if p["bug_history"]:
        bits.append("had bugs before")
    if p["staleness"] >= 1:
        bits.append("not checked in a while")
    return ", ".join(bits) or "routine check"


def choose(repo, trigger, memory):
    """Which components to check: (names or None for all, ranked scores, reason)."""
    if trigger == "full" or not repo.has_map:
        return None, [], "full sweep of every component"
    if trigger == "event":
        since = _since(repo, memory)
        touched = sorted(components.components_for_diff(repo, since, "HEAD"))
        what = "the latest push" if since == "HEAD~1" else f"every commit since the last patrol ({since[:7]})"
        return touched, [], f"components touched by {what}"
    ranked = scores(repo, memory)
    k = repo.config["patrol"]["components_per_run"]
    return [e["component"] for e in ranked[:k]], ranked, "scheduled patrol: highest-risk components"


def _head(repo):
    return git("rev-parse", "HEAD", cwd=repo.root).stdout.strip()


def _since(repo, memory):
    """Diff a push patrol from the last commit any patrol saw, so a multi-commit push or a
    merge isn't missed. Falls back to the previous commit."""
    seen = memory.get("last_seen")
    if seen and seen != _head(repo) and git("merge-base", "--is-ancestor", seen, "HEAD", cwd=repo.root).returncode == 0:
        return seen
    return "HEAD~1"


def remember_seen(repo, memory):
    memory["last_seen"] = _head(repo)
