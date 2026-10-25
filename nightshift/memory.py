"""The crew's long-term memory per repository: dismissed false positives, past fixes,
and per-component history for patrol scoring.

Kept under ~/.nightshift/memory/ so it survives re-cloning the repository. The Night
Manager keeps the same facts, in prose, in Grok Bot memory.
"""
import json
import time

from nightshift.config import CREW_HOME


def _path(repo):
    slug = (repo.slug or repo.root.name).replace("/", "__")
    return CREW_HOME / "memory" / f"{slug}.json"


def load(repo):
    try:
        data = json.loads(_path(repo).read_text())
    except (OSError, ValueError):
        data = {}
    data.setdefault("dismissed", {})
    data.setdefault("fixes", [])
    data.setdefault("components", {})
    return data


def save(repo, data):
    path = _path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def component(data, name):
    return data["components"].setdefault(name, {"bugs": 0, "last_patrol": None})


def is_dismissed(data, finding):
    return finding.get("fingerprint") in data["dismissed"]


def dismiss(data, finding, reason):
    data["dismissed"][finding["fingerprint"]] = {
        "tool": finding["tool"], "rule": finding["rule"], "file": finding["file"],
        "reason": reason, "ts": time.time(),
    }


def record_patrol(data, names):
    for name in names:
        component(data, name)["last_patrol"] = time.time()


def record_bug(data, name):
    if name:
        component(data, name)["bugs"] += 1


def record_fix(data, bug, how, url=None):
    data["fixes"].append({"bug": bug, "how": how, "url": url, "ts": time.time()})
