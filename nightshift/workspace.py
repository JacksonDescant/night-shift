"""Keeping four Bots straight on one shared checkout per repository.

The crew shares one checkout of each connected repository (~/.nightshift/repos/<owner>/<name>).
It must always be exactly GitHub's default branch (sync). Fixes happen in a separate worktree
per bug, so nobody's sync can pull a branch out from under the Fixer (fix start / fix ship).
Leftovers from abandoned attempts are cleared by tidy.
"""
import os
import shutil
import time
from pathlib import Path

from nightshift import config as cfg
from nightshift import github, proofs, report
from nightshift.config import CREW_HOME, git
from nightshift.gate import _suite_passes, branch_for, default_branch

STALE_AFTER = 15 * 60  # seconds: a branch or fix folder nobody touched this long is a leftover


def clone(slug):
    """The crew's shared checkout of owner/name, cloned on first use. Returns its path."""
    path = cfg.checkout_path(slug)
    if not (path / ".git").exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        proc = git("clone", "-q", f"https://github.com/{slug}.git", str(path), cwd=path.parent)
        if proc.returncode:
            raise RuntimeError(f"couldn't clone {slug}: {proc.stderr.strip()[:200]}")
    return path


def sync(repo):
    """Make the shared checkout exactly GitHub's default branch. Returns (ok, message)."""
    before = git("rev-parse", "--short", "HEAD", cwd=repo.root).stdout.strip()
    branch = git("rev-parse", "--abbrev-ref", "HEAD", cwd=repo.root).stdout.strip()
    if git("fetch", "-q", "origin", cwd=repo.root).returncode:
        return False, "couldn't reach GitHub (git fetch failed)"
    git("remote", "set-head", "origin", "--auto", cwd=repo.root)
    main = default_branch(repo)
    if branch != main and git("checkout", "-q", "-f", main, cwd=repo.root).returncode:
        return False, f"couldn't switch from {branch} to {main}"
    ahead = git("rev-list", "--count", f"origin/{main}..{main}", cwd=repo.root).stdout.strip()
    git("reset", "-q", "--hard", f"origin/{main}", cwd=repo.root)
    head = git("log", "-1", "--format=%h %s", cwd=repo.root).stdout.strip()
    notes = []
    if branch != main:
        notes.append(f"switched from {branch}")
    if ahead not in ("", "0"):
        notes.append(f"dropped {ahead} local-only commit(s); {main} only changes through PRs")
    if before and before != head.split()[0]:
        notes.append(f"moved from {before}")
    repo.config = cfg.load(repo.root).config  # nightshift.yaml may have changed
    return True, f"{main} is GitHub's latest: {head}" + (f" ({'; '.join(notes)})" if notes else "")


def fix_path(repo, bug):
    slug = (repo.slug or repo.root.name).replace("/", "__")
    return CREW_HOME / "fixes" / slug / bug


def fix_start(repo, bug):
    """A clean worktree on the bug's fix branch from GitHub's default branch, proof test in place."""
    branch, path = branch_for(bug), fix_path(repo, bug)
    number, url, _ = github.open_pr(repo.slug, branch)
    if number:
        return False, f"a fix for {bug} is already waiting: PR #{number} {url}"
    ok, message = sync(repo)
    if not ok:
        return False, message
    if branch in github.crew_branches(repo.slug):
        why = stale_reason(github.prs_for(repo.slug, branch), 0)
        if why and github.delete_branch(repo.slug, branch):
            return False, f"an old {branch} on GitHub ({why}) couldn't be deleted; run nightshift tidy"
    if path.exists():
        git("worktree", "remove", "--force", str(path), cwd=repo.root)
        shutil.rmtree(path, ignore_errors=True)
    git("worktree", "prune", cwd=repo.root)
    git("branch", "-q", "-D", branch, cwd=repo.root)  # a stale branch from an earlier, closed attempt
    path.parent.mkdir(parents=True, exist_ok=True)
    made = git("worktree", "add", "-q", "-b", branch, str(path), f"origin/{default_branch(repo)}", cwd=repo.root)
    if made.returncode:
        return False, f"couldn't create the worktree: {made.stderr.strip()[:160]}"
    test = Path(repo.config["tests_dir"]) / f"test_{bug}.py"
    proof = repo.proofs / f"test_{bug}.py"
    if not (path / test).exists():
        if not proof.exists():
            return False, f"no proof test: neither {test} on {default_branch(repo)} nor {proof}"
        (path / test).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(proof, path / test)
    return True, str(path)


def fix_ship(repo, bug, summary):
    """Verify the fix in the mimic, then commit, push and open the PR. Returns (ok, message)."""
    branch, path = branch_for(bug), fix_path(repo, bug)
    test = f"{repo.config['tests_dir']}/test_{bug}.py"
    if not path.is_dir():
        return False, f"no fix workspace at {path}: run fix start {bug} first"

    changed = [line[3:].strip('"') for line in git("status", "--porcelain", "-uall", cwd=path).stdout.splitlines()]
    other_tests = [p for p in changed if p != test and (p.startswith(("tests/", "test/")) or "/tests/" in p)]
    if other_tests:
        return False, f"a fix may not edit other tests: {', '.join(other_tests)}"
    if cfg.CONFIG_FILE in changed:
        return False, f"a fix may not edit {cfg.CONFIG_FILE}"
    code = [p for p in changed if p != test]
    if not code:
        return False, "no code change yet"

    if not proofs.run_pytest([test], path).passed:
        return False, f"{test} still fails on the fix: keep working"
    suite_ok, detail = _suite_passes(repo, path)
    if not suite_ok:
        return False, f"{detail} with the fix: it broke normal behaviour"
    before = {b for b, r in proofs.run_all(repo).items() if not r["passed"]}
    after = {b for b, r in proofs.run_all(repo, path).items() if not r["passed"]}
    if after - before:
        return False, f"the fix makes other proofs fail: {', '.join(sorted(after - before))}"

    title = proofs.meta(path / test)["title"]
    git("add", "-A", "--", *code, test, cwd=path)
    commit = git("commit", "-q", "-m", f"Night Shift: fix {bug}\n\n{summary}", cwd=path)
    if commit.returncode:
        return False, f"commit failed: {commit.stderr.strip()[:160]}"
    push = git("push", "-q", "-u", "origin", branch, cwd=path)
    if push.returncode and branch in github.crew_branches(repo.slug):
        # An old branch of the same name is in the way (its PR was closed): clear it and push again.
        why = stale_reason(github.prs_for(repo.slug, branch), 0)
        if why and not github.delete_branch(repo.slug, branch):
            push = git("push", "-q", "-u", "origin", branch, cwd=path)
    if push.returncode:
        return False, f"push failed: {push.stderr.strip()[:160]}"
    body = (f"**Bug:** {title} (`{bug}`)\n\n**Fix:** {summary}\n\n"
            f"**Evidence:** `{test}` fails on `{default_branch(repo)}` and passes here, in an isolated mimic. "
            f"The repository's tests pass and no other Night Shift proof newly fails.\n\n"
            f"Opened by Night Shift. The Night Manager runs the autonomy gate next.")
    number, url, error = github.create_pr(repo.slug, branch, f"Night Shift: fix {title}", body, default_branch(repo))
    if not number:
        return False, f"pushed {branch} but couldn't open the PR: {error}"
    report.post(repo, "pr_opened", {"bug_id": bug, "url": url, "summary": summary})
    git("worktree", "remove", "--force", str(path), cwd=repo.root)
    return True, f"PR #{number} {url}"


def propose_map(repo, config, note=""):
    """Open a PR adding (or updating) nightshift.yaml, so a human reviews the crew's map."""
    branch = "nightshift/map"
    path = CREW_HOME / "fixes" / (repo.slug or repo.root.name).replace("/", "__") / "map"
    if path.exists():
        git("worktree", "remove", "--force", str(path), cwd=repo.root)
        shutil.rmtree(path, ignore_errors=True)
    git("branch", "-q", "-D", branch, cwd=repo.root)
    path.parent.mkdir(parents=True, exist_ok=True)
    made = git("worktree", "add", "-q", "-b", branch, str(path), f"origin/{default_branch(repo)}", cwd=repo.root)
    if made.returncode:
        return False, f"couldn't create the worktree: {made.stderr.strip()[:160]}"
    import yaml

    (path / cfg.CONFIG_FILE).write_text(
        "# Night Shift's map of this repository. Drafted by the crew; edit freely.\n"
        "# Docs: https://github.com/JacksonDescant/night-shift#the-map-nightshiftyaml\n"
        + yaml.safe_dump(config, sort_keys=False))
    git("add", cfg.CONFIG_FILE, cwd=path)
    if git("commit", "-q", "-m", "Night Shift: map this repository", cwd=path).returncode:
        return False, "nothing changed in the map"
    if git("push", "-q", "-f", "-u", "origin", branch, cwd=path).returncode:
        return False, "couldn't push the map branch"
    number, url, _ = github.open_pr(repo.slug, branch)
    if not number:
        body = ("Night Shift drafted a map of this repository: its components, which ones face the internet, "
                "which hold valuable data, how they link, and which code always needs a human.\n\n"
                f"{note}\n\nReview it, fix anything that's wrong, and merge it to let the crew start patrolling.")
        number, url, error = github.create_pr(repo.slug, branch, "Night Shift: map this repository", body,
                                              default_branch(repo))
        if not number:
            return False, f"couldn't open the PR: {error}"
    git("worktree", "remove", "--force", str(path), cwd=repo.root)
    return True, f"PR #{number} {url}"


def stale_reason(prs, age):
    """Why a crew branch is a leftover, or None to keep it. prs: every PR for the branch."""
    if any(p["state"] == "open" for p in prs):
        return None  # a fix waiting to be gated or reviewed
    if prs:
        last = max(prs, key=lambda p: p["number"])
        return f"its PR #{last['number']} was {'merged' if last['merged'] else 'closed'}"
    if age is not None and age > STALE_AFTER:
        return "it never got a PR and nobody touched it for 15 minutes"
    return None  # probably a fix being pushed right now


def _recently_touched(path):
    newest = 0
    for folder, _, files in os.walk(path):
        if "/.git" in folder or "/.nightshift" in folder:
            continue
        for f in files:
            try:
                newest = max(newest, os.path.getmtime(os.path.join(folder, f)))
            except OSError:
                pass
    return time.time() - newest < STALE_AFTER


def _worktrees(repo):
    """{branch: path} of every worktree on a branch."""
    found, path = {}, None
    for line in git("worktree", "list", "--porcelain", cwd=repo.root).stdout.splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree "):]
        elif line.startswith("branch refs/heads/"):
            found[line[len("branch refs/heads/"):]] = path
    return found


def tidy(repo):
    """Clear leftovers that trip the crew up: fix branches on GitHub and here whose PR is gone,
    and fix folders nobody is working in. Never touches an open PR or a fix in progress."""
    done = []
    for branch, sha in sorted(github.crew_branches(repo.slug).items()):
        prs = github.prs_for(repo.slug, branch)
        why = stale_reason(prs, None if prs else github.commit_age_seconds(repo.slug, sha))
        if why:
            error = github.delete_branch(repo.slug, branch)
            done.append(f"deleted GitHub branch {branch} ({why})" if not error
                        else f"couldn't delete GitHub branch {branch}: {error}")
    git("worktree", "prune", cwd=repo.root)
    open_heads = {p["branch"] for p in github.crew_prs(repo.slug) if p["state"] == "open"}
    trees = _worktrees(repo)
    current = git("rev-parse", "--abbrev-ref", "HEAD", cwd=repo.root).stdout.strip()
    local = git("branch", "--list", f"{github.BRANCH_PREFIX}*", "--format=%(refname:short)", cwd=repo.root).stdout.split()
    for branch in local:
        if branch in open_heads or branch == current:
            continue
        path = trees.get(branch)
        if path and Path(path).resolve() != repo.root.resolve():
            if _recently_touched(path):
                continue  # the Fixer is mid-fix in there
            git("worktree", "remove", "--force", path, cwd=repo.root)
            done.append(f"removed stale fix folder {path}")
        if git("branch", "-q", "-D", branch, cwd=repo.root).returncode == 0:
            done.append(f"deleted local branch {branch}")
    return done
