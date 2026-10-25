"""GitHub calls the crew makes, through gh's plain REST API.

Fine-grained tokens can't read check runs, and gh's higher-level commands (gh pr view /
merge) ask GitHub for check status behind the scenes, which fails with such a token. These
REST endpoints only need Contents + Pull requests access on the connected repository.
"""
import datetime
import json
import subprocess
import time

BACKOFF = (0, 5, 10, 20)  # GitHub computes mergeability for a few seconds after a PR opens
BRANCH_PREFIX = "nightshift/fix-"


def _api(*args):
    """Run `gh api ...`. Returns (ok, parsed JSON or text, error text). Never raises."""
    try:
        proc = subprocess.run(["gh", "api", *args], capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        return False, None, "gh is not installed or not on PATH"
    except subprocess.TimeoutExpired:
        return False, None, "gh timed out"
    out = proc.stdout.strip()
    try:
        data = json.loads(out) if out else None
    except ValueError:
        data = out
    if proc.returncode:
        message = data.get("message") if isinstance(data, dict) else None
        return False, data, (message or proc.stderr.strip() or out)[:200]
    return True, data, ""


def _epoch(iso):
    try:
        return datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return None


def _summary(p):
    merged = p.get("merged_at") is not None
    return {"number": p["number"], "state": "merged" if merged else p["state"], "merged": merged,
            "branch": p["head"]["ref"], "bug": p["head"]["ref"].removeprefix(BRANCH_PREFIX),
            "url": p["html_url"], "title": p["title"],
            "created": _epoch(p.get("created_at")), "closed": _epoch(p.get("closed_at"))}


def open_pr(slug, branch):
    """(number, url, error) of the open PR whose head is branch."""
    ok, data, error = _api(f"repos/{slug}/pulls?state=open&head={slug.split('/')[0]}:{branch}")
    if not ok:
        return None, None, error
    if not data:
        return None, None, f"no open PR for {branch} on GitHub"
    return str(data[0]["number"]), data[0]["html_url"], ""


def find_pr(slug, branch, backoff=BACKOFF):
    """open_pr with retries. Returns (number, url, error)."""
    error = ""
    for wait in backoff:
        time.sleep(wait)
        number, url, error = open_pr(slug, branch)
        if number:
            return number, url, ""
    return None, None, error


def crew_prs(slug, limit=30):
    """The crew's most recent PRs, newest first."""
    ok, data, _ = _api(f"repos/{slug}/pulls?state=all&sort=updated&direction=desc&per_page={limit}")
    if not ok or not isinstance(data, list):
        return []
    return [_summary(p) for p in data if p["head"]["ref"].startswith(BRANCH_PREFIX)]


def prs_for(slug, branch):
    ok, data, _ = _api(f"repos/{slug}/pulls?state=all&head={slug.split('/')[0]}:{branch}")
    return [_summary(p) for p in data] if ok and isinstance(data, list) else []


def create_pr(slug, branch, title, body, base):
    """Open a PR. Returns (number, url, error)."""
    ok, data, error = _api(f"repos/{slug}/pulls", "-X", "POST", "-f", f"title={title}",
                           "-f", f"head={branch}", "-f", f"base={base}", "-f", f"body={body}")
    return (str(data["number"]), data["html_url"], "") if ok else (None, None, error)


def merge_pr(slug, number, branch, backoff=BACKOFF):
    """Merge a PR (a merge commit, so history shows it) and delete its branch. Returns error or ''."""
    error = ""
    for wait in backoff:
        time.sleep(wait)
        ok, _, error = _api(f"repos/{slug}/pulls/{number}/merge", "-X", "PUT", "-f", "merge_method=merge")
        if ok or _merged(slug, number):
            _api(f"repos/{slug}/git/refs/heads/{branch}", "-X", "DELETE")
            return ""
    return error or "merge failed"


def _merged(slug, number):
    ok, data, _ = _api(f"repos/{slug}/pulls/{number}")
    return ok and isinstance(data, dict) and data.get("merged") is True


def crew_branches(slug):
    """{branch: head sha} for every crew fix branch on GitHub."""
    ok, data, _ = _api(f"repos/{slug}/git/matching-refs/heads/{BRANCH_PREFIX}")
    if not ok or not isinstance(data, list):
        return {}
    return {r["ref"].removeprefix("refs/heads/"): r["object"]["sha"] for r in data}


def commit_age_seconds(slug, sha):
    ok, data, _ = _api(f"repos/{slug}/commits/{sha}")
    try:
        when = datetime.datetime.fromisoformat(data["commit"]["committer"]["date"].replace("Z", "+00:00"))
    except (TypeError, KeyError, ValueError):
        return None
    return (datetime.datetime.now(datetime.timezone.utc) - when).total_seconds() if ok else None


def delete_branch(slug, branch):
    ok, _, error = _api(f"repos/{slug}/git/refs/heads/{branch}", "-X", "DELETE")
    return "" if ok else error
