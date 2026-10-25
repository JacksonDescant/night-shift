"""The autonomy gate: does a fix merge on its own, or wait for a human?

Auto-merge only when all five hold, checked in clean worktrees and the mimic:
  1. the proof fails on the code the fix was written against (the bug was real)
  2. the same proof passes on the fix branch
  3. nothing else broke: the repository's own tests pass and no other proof newly fails
  4. the diff is small and stays inside the bug's component
  5. nothing sensitive changed: auth code, dependencies, migrations, CI (see `sensitive`)
"""
import os
import shutil

from nightshift import components, github, memory, proofs, report
from nightshift.config import git

GATEKEEPER = "Night Manager"


def branch_for(bug):
    return f"nightshift/fix-{bug}"


def default_branch(repo):
    ref = git("symbolic-ref", "--short", "refs/remotes/origin/HEAD", cwd=repo.root).stdout.strip()
    return ref.removeprefix("origin/") or "main"


class Worktree:
    """A throwaway checkout of one revision, so the gate never touches the shared checkout."""

    def __init__(self, repo, rev, name):
        self.repo, self.rev = repo, rev
        self.path = repo.state / "worktrees" / name

    def __enter__(self):
        if self.path.exists():
            git("worktree", "remove", "--force", str(self.path), cwd=self.repo.root)
            shutil.rmtree(self.path, ignore_errors=True)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        proc = git("worktree", "add", "--detach", "-q", str(self.path), self.rev, cwd=self.repo.root)
        if proc.returncode:
            raise RuntimeError(proc.stderr)
        return self.path

    def __exit__(self, *exc):
        git("worktree", "remove", "--force", str(self.path), cwd=self.repo.root)


def _suite_passes(repo, src):
    """The repository's own tests (not Night Shift's proofs). True if they pass or there are none."""
    suite = [p for p in repo.config.get("suite", ["tests"]) if (src / p).exists()]
    if not suite:
        return True, "no test suite found"
    ignore = f"--ignore={repo.config['tests_dir']}"
    result = proofs.run_pytest([*suite, ignore], src)
    return result.rc in (0, 5), "the repository's tests pass" if result.rc in (0, 5) else "the repository's tests fail"


def check(repo, bug, branch=None, base=None):
    """Run every rule. Returns {"bug", "branch", "verdict": "auto_merge"|"escalate", "checks": [...]}."""
    base = base or f"origin/{default_branch(repo)}"
    branch = branch or branch_for(bug)
    test_path = f"{repo.config['tests_dir']}/test_{bug}.py"
    merge_base = git("merge-base", base, branch, cwd=repo.root).stdout.strip()
    checks = []

    def rule(name, ok, detail):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    test_source = git("show", f"{branch}:{test_path}", cwd=repo.root).stdout
    if not merge_base or not test_source:
        rule("proof test committed", False, f"{test_path} missing on {branch}")
        return _result(repo, bug, branch, checks)
    component = proofs.meta(repo.root / test_path)["component"] if (repo.root / test_path).exists() else None

    with Worktree(repo, merge_base, "base") as base_wt, Worktree(repo, branch, "fix") as fix_wt:
        (base_wt / test_path).parent.mkdir(parents=True, exist_ok=True)
        (base_wt / test_path).write_text(test_source)
        component = component or proofs.meta(base_wt / test_path)["component"]
        failed_before = proofs.proves_bug(test_path, base_wt)
        rule("proof fails on the vulnerable code", failed_before,
             f"{test_path} {'fails' if failed_before else 'passes'} at {merge_base[:7]}")

        passes_after = proofs.run_pytest([test_path], fix_wt).passed
        rule("proof passes after the fix", passes_after, f"{test_path} {'passes' if passes_after else 'fails'} on {branch}")

        suite_ok, suite_detail = _suite_passes(repo, fix_wt)
        before = {b for b, r in proofs.run_all(repo, base_wt).items() if not r["passed"]}
        after = {b for b, r in proofs.run_all(repo, fix_wt).items() if not r["passed"]}
        newly = sorted(after - before)
        rule("nothing else broke", suite_ok and not newly,
             suite_detail if not suite_ok else f"newly failing proofs: {', '.join(newly)}" if newly
             else f"{suite_detail}; no other proof newly fails")

        changes = components.diff_changes(repo.root, merge_base, branch)
        code = {p: c for p, c in changes.items() if p != test_path}
        other_tests = sorted(p for p in code if p.startswith(("tests/", "test/")) or "/tests/" in p)
        size = sum(len(c["added"]) + len(c["removed"]) for c in code.values())
        touched = components.components_for_changes(repo, code, branch)
        stray = sorted(touched - {component})
        limit = repo.config["autonomy"]["max_changed_lines"]
        detail = f"{size} lines in {', '.join(sorted(touched)) or 'no mapped component'}"
        if size > limit:
            detail += f"; over the {limit}-line limit"
        if stray:
            detail += f"; outside {component}: {', '.join(stray)}"
        if other_tests:
            detail += f"; edits other tests: {', '.join(other_tests)}"
        rule("small diff inside the component", size <= limit and not stray and not other_tests, detail)

        sensitive = components.sensitive_touches(repo, code, branch)
        rule("nothing sensitive changed", not sensitive, "; ".join(sensitive) or "no auth, dependency, migration or CI changes")

    return _result(repo, bug, branch, checks)


def _result(repo, bug, branch, checks):
    enabled = repo.config["autonomy"].get("enabled", True) and os.environ.get("NIGHTSHIFT_AUTOMERGE", "1") != "0"
    airtight = all(c["ok"] for c in checks)
    if airtight and not enabled:
        checks.append({"name": "auto-merge enabled", "ok": False, "detail": "turned off in nightshift.yaml"})
    return {"bug": bug, "branch": branch, "verdict": "auto_merge" if airtight and enabled else "escalate",
            "checks": checks}


def apply(repo, result):
    """Act on a gate result. Returns "merged", "escalated" (needs a human) or "retry" (GitHub hiccup)."""
    bug, branch = result["bug"], result["branch"]
    report.post(repo, "gate_result", result)
    failed = [c for c in result["checks"] if not c["ok"]]
    if result["verdict"] != "auto_merge":
        reasons = "; ".join(c["detail"] for c in failed)
        report.post(repo, "escalated", {"bug_id": bug, "reasons": [c["detail"] for c in failed]})
        report.chat(repo, GATEKEEPER, f"Escalating {bug} to a human: {reasons[:160]}.")
        return "escalated"

    # The only way into the default branch is a PR. Never merge locally: that commit would exist
    # only on this computer and split it from GitHub.
    number, url, error = github.find_pr(repo.slug, branch)
    if number is None:
        return _retry(repo, bug, f"couldn't find the PR: {error}")
    error = github.merge_pr(repo.slug, number, branch)
    if error:
        return _retry(repo, bug, f"PR #{number} didn't merge: {error}")
    main = default_branch(repo)
    git("fetch", "-q", "origin", cwd=repo.root)
    git("checkout", "-q", main, cwd=repo.root)
    git("reset", "-q", "--hard", f"origin/{main}", cwd=repo.root)
    git("branch", "-q", "-D", branch, cwd=repo.root)

    mem = memory.load(repo)
    memory.record_fix(mem, bug, "auto", url)
    memory.save(repo, mem)
    report.post(repo, "auto_merged", {"bug_id": bug, "url": url})
    report.chat(repo, GATEKEEPER, f"All five checks passed for {bug}. Merged {url}.")
    return "merged"


def _retry(repo, bug, reason):
    """The fix passed every rule; GitHub just didn't cooperate. Not a human's job: run the gate again."""
    report.post(repo, "escalated", {"bug_id": bug, "reasons": [reason], "retryable": True})
    report.chat(repo, GATEKEEPER, f"{bug} passed all five checks but {reason}. Retrying shortly.")
    return "retry"
