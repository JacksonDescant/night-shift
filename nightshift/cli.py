"""nightshift <command>: the tools the crew runs, one step each, inside the repository it guards.

Every command that prints a "HAND OFF:" line is telling the Bot who works next. Only `next`
printing "DONE:" ends a task.
"""
import argparse
import json
import os
import sys
import time

import yaml

from nightshift import attack, components, github, memory, patrol, proofs, report, scan
from nightshift import config as cfg

MAX_FIXES_PER_TASK = 6


def _list(value):
    return [v.strip() for v in value.split(",") if v.strip()] if value else None


def _repo():
    return cfg.find_repo()


# ---- waking up --------------------------------------------------------------------------

def cmd_wake(args):
    """The Watcher's whole job: read what woke the crew, get the repo, sync, pick components,
    scan, save the task, and say who's next."""
    from nightshift import repomap, workspace

    raw = (args.payload or "").strip()
    try:
        payload = json.loads(raw) if raw.startswith("{") else {"trigger": raw or "full"}
    except ValueError:
        payload = {"trigger": "full"}
    cfg.remember_server(payload.get("server"))
    slug = payload.get("repo") or cfg.current_task().get("repo")
    repo = cfg.load(workspace.clone(slug) if slug else cfg.find_repo().root)
    ok, message = workspace.sync(repo)
    print(message)
    if not ok:
        print("HAND OFF: none. Couldn't sync with GitHub; try again in a minute.")
        sys.exit(1)
    for line in workspace.tidy(repo):
        print(f"TIDY: {line}")
    trigger = {"push": "event"}.get(payload.get("trigger", "full"), payload.get("trigger", "full"))
    mem = memory.load(repo)

    if trigger == "map" or not repo.has_map:
        draft = repomap.draft(repo.root)
        (repo.state / "map-draft.yaml").write_text(yaml.safe_dump(draft, sort_keys=False))
        _start_task(repo, mem, "map", None, "drafting a map of the repository")
        report.post(repo, "map_drafted", {"config": draft, "summary": repomap.summary(draft)})
        print(f"Drafted a map: {repomap.summary(draft)}")
        print(f"HAND OFF: @Triage Guard review the draft map at {repo.state / 'map-draft.yaml'}, "
              f"fix what's wrong, then run: nightshift map propose")
        return

    if trigger == "map_update":
        cmd_map(argparse.Namespace(action="propose", learned=True, args=[], note=None, why=None))
        return

    if trigger == "resume" and cfg.current_task().get("repo") == repo.slug:
        task = cfg.current_task()
        print(f"Resuming: {task.get('reason', '')} ({payload.get('detail', 'a human acted on a PR')}).")
        _paths(repo, task.get("components"))
        _next(repo)
        return
    if trigger == "bug":
        bug = payload.get("bug", "")
        component = proofs.run_all(repo).get(bug, {}).get("component")
        _start_task(repo, mem, "bug", [component] if component else None, f"the dashboard asked to fix {bug}", bug=bug)
        print(f"Task: fix {bug}.")
        _next(repo)
        return
    if trigger not in ("event", "patrol", "full"):
        trigger = "full"
    focus, ranked, why = patrol.choose(repo, trigger, mem)
    if focus == []:
        _start_task(repo, mem, trigger, [], why)
        report.post(repo, "patrol_done", {"components": []})
        print("HAND OFF: none. That push touched no mapped components, so there's nothing to check.")
        return
    names = _start_task(repo, mem, trigger, focus, why, ranked)
    result = _scan(repo, focus)
    print(f"Task: {why}: {', '.join(names)}.")
    print(f"HAND OFF: @Triage Guard your turn: components {','.join(names)}, "
          f"{len(result['findings'])} findings in .nightshift/raw.json.")


def _start_task(repo, mem, trigger, focus, why, ranked=(), bug=None):
    names = list(repo.config["components"]) if focus is None else focus
    report.post(repo, "patrol_started", {"components": names, "trigger": trigger, "reason": why,
                                         "scores": [{"component": e["component"], "score": e["score"],
                                                     "why": patrol.describe(e)} for e in list(ranked)[:4]]})
    cfg.save_task({"repo": repo.slug, "path": str(repo.root), "trigger": trigger, "components": focus,
                   "bug": bug, "reason": why, "started": time.time(), "handoffs": 0})
    memory.record_patrol(mem, names)
    patrol.remember_seen(repo, mem)
    memory.save(repo, mem)
    return names


def cmd_where(_args):
    print(cfg.current_task().get("path") or "no current task")


# ---- scanning and proving ---------------------------------------------------------------

def cmd_scan(args):
    _scan(_repo(), _list(args.components))


def _scan(repo, focus):
    report.post(repo, "scan_started", {"components": focus})
    result = scan.scan(repo.root)
    mem = memory.load(repo)
    result["findings"] = [f for f in result["findings"]
                          if components.finding_in(repo, f, focus) and not memory.is_dismissed(mem, f)]
    for f in result["findings"]:
        f["component"] = components.component_of(repo, f["file"], f["line"])
    path = repo.write_state("raw.json", result)
    report.post(repo, "scan_done", {"count": len(result["findings"]), "errors": result["errors"]})
    print(f"{len(result['findings'])} findings -> {path}")
    for name, err in result["errors"].items():
        print(f"  warning: {name}: {err}", file=sys.stderr)
    for f in result["findings"]:
        print(f"  [{f['id']}] {f['tool']} {f['rule']} {f['file']}:{f['line']} ({f['component'] or 'unmapped'})")
    return result


def cmd_bug(args):
    repo = _repo()
    mem = memory.load(repo)
    if args.action == "dismiss":
        raw = repo.read_state("raw.json", {}) or {}
        finding = next((f for f in raw.get("findings", []) if f["id"] == args.target), None)
        if not finding:
            sys.exit(f"no finding {args.target} in .nightshift/raw.json")
        memory.dismiss(mem, finding, args.reason or "")
        memory.save(repo, mem)
        report.post(repo, "finding_triaged", {"id": args.target, "verdict": "dismissed", "reason": args.reason or ""})
        print(f"dismissed {args.target}; it won't be raised again")
        return

    bug = args.target
    proof = repo.proofs / f"test_{bug}.py"
    if not proof.exists():
        sys.exit(f"write the proof first: {proof}")
    info = proofs.meta(proof)
    if info["component"] not in repo.config["components"]:
        sys.exit(f"the proof's docstring must name a mapped component, e.g. [component: "
                 f"{next(iter(repo.config['components']), 'api')}]; got {info['component']!r}")
    rel = str(proof.relative_to(repo.root))
    result = proofs.run_pytest([rel], repo.root)
    if result.rc == 0:
        print(f"NOT PROVEN: {rel} passes, so there's no bug here. Delete it and dismiss the finding.")
        sys.exit(2)
    if result.rc != 1:
        print(f"NOT PROVEN: the test couldn't run (pytest exit {result.rc}); fix the test.\n{result.output}")
        sys.exit(3)
    proofs.run_all(repo)  # refresh failures for the dashboard
    memory.record_bug(mem, info["component"])
    memory.save(repo, mem)
    report.post(repo, "finding_triaged", {"id": args.finding or "review", "verdict": "confirmed", "bug_id": bug,
                                          "component": info["component"], "reason": info["title"]})
    report.post(repo, "bug_confirmed", {"bug_id": bug, "test_path": rel})
    print(f"PROVEN: {bug} ({info['title']}) fails in the mimic.")


def cmd_paths(args):
    state = _paths(_repo(), _list(args.components))
    print(json.dumps({k: state[k] for k in ("open_bugs", "breach_paths", "recommended_fix", "reason")}, indent=2))


def _paths(repo, focus=None):
    """Run every proof, work out the attack paths, and tell the dashboard what changed."""
    from nightshift import gate

    results = proofs.run_all(repo)
    open_now = proofs.open_bugs(results)
    previous = repo.read_state("paths.json", {}) or {}
    waiting = {p["bug"] for p in github.crew_prs(repo.slug) if p["state"] == "open"} if repo.slug else set()
    fix, reason = attack.recommend(repo.config, open_now, waiting, focus)
    state = {**attack.status(repo.config, open_now), "open_bugs": open_now, "recommended_fix": fix,
             "reason": reason, "titles": {b: r["title"] for b, r in results.items()}}
    repo.write_state("paths.json", state)
    for bug in sorted(set(previous.get("open_bugs", {})) - set(open_now)):
        report.post(repo, "bug_fixed", {"bug_id": bug})
    for bug in sorted(set(open_now) - set(previous.get("open_bugs", {}))):
        report.post(repo, "bug_confirmed", {"bug_id": bug, "test_path": results[bug]["path"]})
    report.post(repo, "paths", {**state, "default_branch": gate.default_branch(repo),
                                "map": {k: repo.config[k] for k in ("components", "links")}})
    _post_learned(repo)
    return state


def _post_learned(repo):
    from nightshift import learn

    items = learn.proposals(repo)
    report.post(repo, "map_learned", {"proposals": items})
    return items


# ---- what's next ------------------------------------------------------------------------

def cmd_next(_args):
    _next(_repo())


def _next(repo):
    """Who works next on the current task, or DONE. Triage Guard and the Night Manager run this."""
    task = cfg.current_task()
    open_now = proofs.open_bugs(proofs.run_all(repo))
    prs = github.crew_prs(repo.slug) if repo.slug else []
    waiting = {p["bug"] for p in prs if p["state"] == "open"}
    declined = _declined_since(prs, task.get("started") or 0)
    if task.get("handoffs", 0) >= MAX_FIXES_PER_TASK:
        bug, reason = None, f"stopping after {MAX_FIXES_PER_TASK} fixes in one task; run another sweep to continue."
    else:
        bug, reason = attack.next_fix(repo.config, open_now, waiting, declined, task.get("components"), task.get("bug"))
    if bug is None:
        report.post(repo, "patrol_done", {"components": task.get("components")})
        print(f"DONE: {reason}")
        print("HAND OFF: @Night Manager write the log (nothing left to fix in this task).")
        sys.exit(10)
    task["handoffs"] = task.get("handoffs", 0) + 1
    cfg.save_task(task)
    print(f"NEXT: {bug}. {reason}")
    print(f"HAND OFF: @Fixer fix {bug}. Reason: {reason}")


def _declined_since(prs, started):
    """Bugs whose latest crew PR a human closed without merging during this task: don't redo them."""
    latest = {}
    for pr in prs:
        if pr["bug"] not in latest or pr["number"] > latest[pr["bug"]]["number"]:
            latest[pr["bug"]] = pr
    return {b for b, pr in latest.items() if pr["state"] == "closed" and (pr["closed"] or 0) >= started}


# ---- fixing and gating ------------------------------------------------------------------

def cmd_fix(args):
    from nightshift import workspace

    repo = _repo()
    if args.action == "start":
        ok, message = workspace.fix_start(repo, args.bug)
        if ok:
            print(f"Fix workspace: {message}")
            print(f"Edit code only inside {message} (that folder is yours alone; the shared checkout stays on "
                  f"the default branch). Don't edit tests or {cfg.CONFIG_FILE}.")
            print(f"When it's ready: nightshift fix ship {args.bug} --summary \"<one sentence>\"")
        else:
            print(f"RESULT: STOP ({message})")
        sys.exit(0 if ok else 1)
    if not args.summary:
        sys.exit("fix ship needs --summary \"<the fix in one plain sentence>\"")
    ok, message = workspace.fix_ship(repo, args.bug, args.summary)
    print(f"RESULT: PR OPENED {message}" if ok else f"RESULT: NOT SHIPPED ({message})")
    sys.exit(0 if ok else 1)


def _summary(lines):
    """Append to the GitHub Actions job summary when running in CI."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a") as fh:
            fh.write("\n".join(lines) + "\n")


def cmd_gate(args):
    from nightshift import gate

    repo = _repo()
    result = gate.check(repo, args.bug, args.branch, args.base)
    for c in result["checks"]:
        print(f"  [{'ok' if c['ok'] else 'NO'}] {c['name']}: {c['detail']}")
    print(f"verdict: {result['verdict']}")
    if args.ci:
        verdict = "Can merge on its own: all five checks pass" if result["verdict"] == "auto_merge" \
            else "Needs a human: review this PR"
        _summary([f"## Night Shift gate: {result['bug']}", f"**{verdict}**", "",
                  "| check | result | detail |", "|---|---|---|",
                  *[f"| {c['name']} | {'pass' if c['ok'] else 'FAIL'} | {c['detail']} |" for c in result["checks"]]])
        # The check fails only when the fix itself is unproven (rules 1-3); escalation is not a failure.
        sys.exit(0 if len(result["checks"]) >= 3 and all(c["ok"] for c in result["checks"][:3]) else 1)
    if args.apply:
        outcome = gate.apply(repo, result)
        print({"merged": "RESULT: MERGED (the crew closed it)",
               "escalated": "RESULT: ESCALATED (a human must review the PR)",
               "retry": "RESULT: RETRY (all checks passed; GitHub didn't find or merge the PR yet. "
                        "Run the same command again in 60 seconds)"}[outcome])
        sys.exit({"merged": 0, "escalated": 2, "retry": 75}[outcome])


def cmd_prove_branch(args):
    """CI: a proof branch's test must fail on otherwise-unchanged code, in the mimic."""
    repo = _repo()
    test = f"{repo.config['tests_dir']}/test_{args.bug}.py"
    changed = sorted(components.diff_changes(repo.root, args.base, "HEAD"))
    code = [p for p in changed if not p.startswith(repo.config["tests_dir"])]
    if not (repo.root / test).exists():
        verdict, ok = f"no proof test at {test}", False
    elif code:
        verdict, ok = f"a proof branch may only add tests, but it changes {', '.join(code)}", False
    else:
        result = proofs.run_pytest([test], repo.root)
        ok = result.rc == 1
        verdict = (f"PROVEN: {test} fails on the current code ({proofs.mode()} mimic)" if ok else
                   f"NOT PROVEN: {test} passes, so there is no bug to fix" if result.rc == 0 else
                   f"NOT PROVEN: the test could not run (pytest exit {result.rc})")
        print(result.output)
    print(verdict)
    _summary([f"## Night Shift proof: {args.bug}", f"**{verdict}**"])
    sys.exit(0 if ok else 1)


# ---- housekeeping -----------------------------------------------------------------------

def cmd_sync(_args):
    from nightshift import workspace

    ok, message = workspace.sync(_repo())
    print(message)
    sys.exit(0 if ok else 1)


def cmd_tidy(_args):
    from nightshift import workspace

    done = workspace.tidy(_repo())
    print("\n".join(f"TIDY: {line}" for line in done) or "TIDY: nothing to clean up")


def cmd_map(args):
    from nightshift import learn, repomap, workspace

    repo = _repo()
    if args.action == "note":
        kinds = {"link": 2, "entry": 1, "asset": 1, "component": 2}
        if not args.args or args.args[0] not in kinds or len(args.args) != kinds[args.args[0]] + 1:
            sys.exit("usage: nightshift map note link <from> <to> | entry <component> | asset <component> "
                     "| component <id> <path> [--why '...']")
        kind, *rest = args.args
        names = rest if kind == "link" else rest[:1]
        unknown = [n for n in names if kind != "component" and n not in repo.config["components"]
                   and not any(p.get("id") == n for p in learn.proposals(repo))]
        if unknown:
            sys.exit(f"unknown component {', '.join(unknown)}; see nightshift map show")
        item = learn.note(repo, kind, rest[0], rest[1] if len(rest) > 1 else None, args.why or "")
        _post_learned(repo)
        print(f"RESULT: NOTED {learn.describe(item)}. It shows on the dashboard as a proposed map change.")
        return
    if args.action == "learned":
        items = learn.proposals(repo)
        for p in items:
            print(f"  {learn.describe(p)}  ({p['source']}: {p['why']})")
        print(f"{len(items)} proposed change{'s' if len(items) != 1 else ''} to the map.")
        return
    if args.action == "propose" and args.learned:
        items = learn.proposals(repo)
        if not items:
            print("RESULT: NOT PROPOSED (the map matches the code; nothing learned)")
            return
        new = learn.apply(repo.config, items)
        note = "The crew learned this since the map was merged:\n" + "\n".join(
            f"- {learn.describe(p)}: {p['why']}" for p in items)
        ok, message = workspace.propose_map(repo, new, note)
        if ok:
            report.post(repo, "map_drafted", {"summary": f"{len(items)} learned change(s) proposed", "pr": message})
            print(f"RESULT: MAP UPDATE PROPOSED {message}")
            print("HAND OFF: @Night Manager the map update is waiting for a human to review and merge.")
        else:
            print(f"RESULT: NOT PROPOSED ({message})")
        return
    if args.action == "show":
        print(yaml.safe_dump({k: repo.config[k] for k in ("components", "links", "sensitive")}, sort_keys=False))
        return
    if args.action == "draft":
        draft = repomap.draft(repo.root)
        path = repo.state / "map-draft.yaml"
        path.write_text(yaml.safe_dump(draft, sort_keys=False))
        report.post(repo, "map_drafted", {"config": draft, "summary": repomap.summary(draft)})
        print(f"{repomap.summary(draft)}\nDraft written to {path}. Review it, then: nightshift map propose")
        return
    path = repo.state / "map-draft.yaml"
    if not path.exists():
        sys.exit("no draft yet: run nightshift map draft")
    draft = yaml.safe_load(path.read_text())
    ok, message = workspace.propose_map(repo, draft, args.note or "")
    if ok:
        report.post(repo, "map_drafted", {"config": draft, "summary": repomap.summary(draft), "pr": message})
        print(f"RESULT: MAP PROPOSED {message}")
        print("HAND OFF: @Night Manager the map is waiting for a human to review and merge.")
    else:
        print(f"RESULT: NOT PROPOSED ({message})")


def cmd_patrol(_args):
    repo = _repo()
    focus, ranked, _ = patrol.choose(repo, "patrol", memory.load(repo))
    for e in ranked:
        mark = "*" if e["component"] in (focus or []) else " "
        print(f"{mark} {attack.name_of(repo.config, e['component']):<24} {e['score']:>5}  {patrol.describe(e)}")


def cmd_report(args):
    data = json.loads(args.json) if args.json else {}
    if args.event not in report.EVENT_TYPES:
        sys.exit(f"unknown event type {args.event}; one of {sorted(report.EVENT_TYPES)}")
    report.post(_repo(), args.event, data)
    print(f"sent {args.event}")


def cmd_mimic(args):
    repo = _repo()
    if args.action == "status":
        print(f"mimic mode: {proofs.mode()}")
        return
    result = proofs.run_pytest(args.paths or ["."], repo.root)
    print(result.output)
    sys.exit(result.rc)


def main(argv=None):
    cfg.load_env()
    parser = argparse.ArgumentParser(prog="nightshift", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("wake", help="Watcher: handle what woke the crew (the webhook's JSON body, or push|patrol|full|map)")
    p.add_argument("payload", nargs="?", default="full")
    p.set_defaults(fn=cmd_wake)
    sub.add_parser("where", help="print the current task's checkout").set_defaults(fn=cmd_where)
    p = sub.add_parser("scan", help="run the scanners, write .nightshift/raw.json")
    p.add_argument("--components", help="comma-separated: keep only findings there, minus known false positives")
    p.set_defaults(fn=cmd_scan)
    p = sub.add_parser("bug", help="Triage: confirm BUG (its proof must fail in the mimic) or dismiss FINDING_ID")
    p.add_argument("action", choices=["confirm", "dismiss"])
    p.add_argument("target")
    p.add_argument("--finding", help="confirm: the scanner finding id it came from, if any")
    p.add_argument("--reason", help="dismiss: one-line reason")
    p.set_defaults(fn=cmd_bug)
    p = sub.add_parser("paths", help="run every proof, update attack paths and the recommended fix")
    p.add_argument("--components", help="comma-separated: prefer fixes there unless an asset is breached")
    p.set_defaults(fn=cmd_paths)
    sub.add_parser("next", help="who works next on the current task, or DONE").set_defaults(fn=cmd_next)
    p = sub.add_parser("fix", help="Fixer: start a fix in its own worktree, or ship it as a PR")
    p.add_argument("action", choices=["start", "ship"])
    p.add_argument("bug")
    p.add_argument("--summary", help="ship: the fix in one plain sentence")
    p.set_defaults(fn=cmd_fix)
    p = sub.add_parser("gate", help="check the five auto-merge rules for a fix branch")
    p.add_argument("bug")
    p.add_argument("--branch")
    p.add_argument("--base")
    p.add_argument("--apply", action="store_true", help="merge if all checks pass, otherwise escalate")
    p.add_argument("--ci", action="store_true", help="write a job summary; fail only if the fix is unproven")
    p.set_defaults(fn=cmd_gate)
    p = sub.add_parser("prove-branch", help="CI: a proof branch's test must fail on unchanged code")
    p.add_argument("bug")
    p.add_argument("--base", default="origin/HEAD")
    p.set_defaults(fn=cmd_prove_branch)
    sub.add_parser("sync", help="make the shared checkout exactly GitHub's default branch").set_defaults(fn=cmd_sync)
    sub.add_parser("tidy", help="clear leftover fix branches and folders whose PR is gone").set_defaults(fn=cmd_tidy)
    p = sub.add_parser("map", help="draft | propose | show | note | learned: the repository's component map")
    p.add_argument("action", choices=["draft", "propose", "show", "note", "learned"])
    p.add_argument("args", nargs="*", help="note: link <from> <to> | entry <c> | asset <c> | component <id> <path>")
    p.add_argument("--note", help="propose: a note for the PR description")
    p.add_argument("--learned", action="store_true", help="propose: open a PR with what the crew learned since the map was merged")
    p.add_argument("--why", help="note: the evidence, in one line")
    p.set_defaults(fn=cmd_map)
    sub.add_parser("patrol", help="show patrol risk scores").set_defaults(fn=cmd_patrol)
    p = sub.add_parser("report", help="send an event to the dashboard")
    p.add_argument("event")
    p.add_argument("--json")
    p.set_defaults(fn=cmd_report)
    p = sub.add_parser("mimic", help="status | test [paths]")
    p.add_argument("action", choices=["status", "test"])
    p.add_argument("paths", nargs="*")
    p.set_defaults(fn=cmd_mimic)
    args = parser.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
