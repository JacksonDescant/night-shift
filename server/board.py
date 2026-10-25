"""One repository's live picture for the dashboard: what each Bot is doing, a case per bug,
the score, and the attack paths.

Everything is derived from the progress events the crew already sends, so the Bots don't
need to know about this view.
"""
import time

CREW = ["Watcher", "Triage Guard", "Fixer", "Night Manager"]
ROLES = {
    "Watcher": "picks what to check and scans it",
    "Triage Guard": "proves what's real",
    "Fixer": "writes the smallest fix",
    "Night Manager": "checks the fix and merges it",
}
STAGES = ["spotted", "proven", "fixed", "verified", "closed"]


def fresh(slug):
    return {
        "repo": slug,
        "crew": {name: {"role": ROLES[name], "state": "idle", "step": "", "started": None, "finished": None}
                 for name in CREW},
        "cases": {},
        "case_order": [],
        "activity": [],
        "prs": {},
        "push": None,
        "cycle": {"start": None, "end": None},
        "verdict": None,  # how the latest task ended: {"clean", "message", "components", "dismissed", "bugs"}
        "metrics": {"proven": 0, "dismissed": 0, "auto_merges": 0, "escalations": 0, "human_touches": 0,
                    "push_to_proof": None, "push_to_merge": None, "fastest_fix": None, "bot_messages": 0},
        "patrol": {"components": [], "trigger": None, "reason": "", "scores": []},
        "scanning": False,
        "map": {"config": None, "summary": "", "pr": None, "status": None, "recommended_fix": None, "reason": "",
                "proposals": [],    # what the crew learned since the map was merged (dashed on the map)
                "update_pr": None,  # the PR proposing those changes, once the crew opens it
                "stats": {},        # per component: findings, dismissed, proven, fixed, last_checked
                "history": []},     # exposure over time: {ts, open, reached, breaches}
        "last_event": None,
    }


class Board:
    def __init__(self, state):
        self.s = state

    # ---- helpers ---------------------------------------------------------------------

    def bot(self, name, state, step=None, ts=None):
        b = self.s["crew"][name]
        ts = ts or time.time()
        if state == "working" and b["state"] != "working":
            b["started"], b["finished"] = ts, None
        if state in ("done", "waiting") and b["state"] == "working":
            b["finished"] = ts
        b["state"] = state
        if step is not None:
            b["step"] = step

    def names(self, components):
        comps = (self.s["map"]["config"] or {}).get("components", {})
        if components is None:
            return "every component"
        return ", ".join(comps.get(c, {}).get("name", c) for c in components) or "nothing"

    def case(self, bug, ts):
        if bug not in self.s["cases"]:
            self.s["cases"][bug] = {
                "bug": bug, "title": bug, "component": None,
                "earlier": self.s["cycle"]["start"] is None,
                "stage": None, "started": ts, "updated": ts,
                "escalated": None, "retrying": None, "declined": None,
                **{stage: None for stage in STAGES},
            }
        c = self.s["cases"][bug]
        if c["earlier"] and self.s["cycle"]["start"] is not None and self.s["cycle"]["end"] is None:
            c["earlier"] = False  # the crew is working on it in this task
        c["updated"] = ts
        if bug in self.s["case_order"]:
            self.s["case_order"].remove(bug)
        self.s["case_order"].insert(0, bug)
        return c

    def advance(self, c, stage, info, ts):
        c[stage] = {**info, "ts": ts}
        if c["stage"] is None or STAGES.index(stage) >= STAGES.index(c["stage"]):
            c["stage"] = stage

    def stat(self, component):
        """Running evidence for one component (boards saved before this existed get it lazily)."""
        stats = self.s["map"].setdefault("stats", {})
        return stats.setdefault(component, {"findings": 0, "dismissed": 0, "proven": 0, "fixed": 0,
                                            "last_checked": None, "bugs": [], "fixed_bugs": []})

    def count_fix(self, bug):
        component = self.s["cases"].get(bug, {}).get("component")
        if component:
            st = self.stat(component)
            if bug not in st["fixed_bugs"]:
                st["fixed_bugs"].append(bug)
                st["fixed"] += 1

    def since_start(self, ts):
        start = self.s["cycle"]["start"]
        return round(ts - start, 1) if start is not None else None

    def start_cycle(self, ts):
        cycle = self.s["cycle"]
        if cycle["start"] is None or cycle["end"] is not None:
            self.s["cycle"] = {"start": ts, "end": None, "dismissed0": self.s["metrics"]["dismissed"]}
            self.s["verdict"] = None
            self.s["metrics"].update(push_to_proof=None, push_to_merge=None)  # timings are per task
            for name in CREW:
                self.s["crew"][name].update(state="idle", step="", started=None, finished=None)

    def record_fix_time(self, ts, m):
        elapsed = self.since_start(ts)
        if elapsed is None:
            return
        if m["push_to_merge"] is None:
            m["push_to_merge"] = elapsed
        m["fastest_fix"] = elapsed if m["fastest_fix"] is None else min(m["fastest_fix"], elapsed)

    # ---- events ----------------------------------------------------------------------

    def apply(self, event):
        kind, d = event["type"], event.get("data") or {}
        ts = event.get("ts")
        ts = time.time() if ts is None else ts
        self.s["last_event"] = ts
        handler = getattr(self, f"on_{kind}", None)
        if handler:
            handler(d, ts, self.s["metrics"])

    def on_push(self, d, ts, m):
        self.start_cycle(ts)
        self.s["push"] = {**d, "ts": ts}
        who = f" by {d['author']}" if d.get("author") else ""
        self.s["activity"].append({"bot": "GitHub", "text": f"New push{who}: \"{d.get('message', '')}\"", "ts": ts})

    def on_map_drafted(self, d, ts, m):
        if self.s["map"]["status"] and not d.get("config"):
            # An update to a map that's already in use: proposed from what the crew learned.
            self.s["map"]["update_pr"] = d.get("pr")
            return
        self.s["map"].update(config=d.get("config") or self.s["map"]["config"], summary=d.get("summary", ""),
                             pr=d.get("pr") or self.s["map"]["pr"])

    def on_map_learned(self, d, ts, m):
        self.s["map"]["proposals"] = d.get("proposals") or []

    def on_patrol_started(self, d, ts, m):
        self.start_cycle(ts)
        self.s["patrol"] = {k: d.get(k) for k in ("components", "trigger", "reason", "scores")}
        self.s["scanning"] = True
        for component in d.get("components") or []:
            self.stat(component)["last_checked"] = ts
        self.bot("Watcher", "working", f"Checking {self.names(d.get('components'))}", ts)

    def on_scan_started(self, d, ts, m):
        self.start_cycle(ts)
        self.bot("Watcher", "working", "Running the scanners", ts)

    def on_scan_done(self, d, ts, m):
        n = d.get("count", 0)
        self.bot("Watcher", "done", f"{n} finding{'s' if n != 1 else ''} handed to Triage Guard", ts)
        self.bot("Triage Guard", "working", "Reading the findings and the change", ts)

    def on_finding_triaged(self, d, ts, m):
        bug = d.get("bug_id")
        if d.get("verdict") == "confirmed" and bug:
            c = self.case(bug, ts)
            if d.get("component"):
                c["component"] = d["component"]
            if d.get("tool"):
                source = f"{d['tool']} flagged {d.get('rule', '')} at {d.get('file', '')}:{d.get('line', '')}"
            else:
                source = "Found by reading the code. No scanner caught this one."
            self.advance(c, "spotted", {"source": source, "reason": d.get("reason", "")}, ts)
            if d.get("component") and d.get("tool"):
                self.stat(d["component"])["findings"] += 1
            self.bot("Triage Guard", "working", f"Proving {bug} in the mimic", ts)
        elif d.get("verdict") == "dismissed":
            m["dismissed"] += 1
            if d.get("component"):
                st = self.stat(d["component"])
                st["findings"] += 1
                st["dismissed"] += 1
            what = f"{d.get('tool', '')} {d.get('rule', '')}".strip() or d.get("id", "a finding")
            self.bot("Triage Guard", "working", f"Dismissed {what}: {d.get('reason', '')}"[:120], ts)

    def on_bug_confirmed(self, d, ts, m):
        bug = d["bug_id"]
        c = self.case(bug, ts)
        c["title"] = d.get("title") or c["title"]
        c["component"] = d.get("component") or c["component"]
        if c["spotted"] is None:
            if c["earlier"]:
                source = "Already known before this task"
            elif ".nightshift" not in (d.get("test_path") or ""):
                source = "Caught by its regression test from an earlier fix. The bug came back."
            else:
                source = "Found by reading the code."
            self.advance(c, "spotted", {"source": source}, ts)
        self.advance(c, "proven", {"test_path": d.get("test_path"), "test_code": d.get("test_code"),
                                   "failure": d.get("failure")}, ts)
        if c["component"] and bug not in self.stat(c["component"])["bugs"]:
            self.stat(c["component"])["bugs"].append(bug)
            self.stat(c["component"])["proven"] += 1
        if not c["earlier"]:
            m["proven"] += 1
            if m["push_to_proof"] is None:
                m["push_to_proof"] = self.since_start(ts)
            self.bot("Triage Guard", "working", f"Proved {bug}: its test fails on the current code", ts)

    def on_paths(self, d, ts, m):
        self.s["map"].update(status={k: d.get(k) for k in ("open_components", "reached", "breach_paths",
                                                             "breached_assets", "open_bugs")},
                             recommended_fix=d.get("recommended_fix"), reason=d.get("reason", ""))
        if d.get("map"):
            if d["map"] != self.s["map"]["config"]:
                self.s["map"]["update_pr"] = None  # a new map was merged
            self.s["map"]["config"] = d["map"]
        snap = {"ts": ts, "open": len(d.get("open_bugs") or {}), "reached": len(d.get("reached") or []),
                "breaches": len(d.get("breach_paths") or [])}
        history = self.s["map"].setdefault("history", [])
        if not history or any(history[-1][k] != snap[k] for k in ("open", "reached", "breaches")):
            history.append(snap)
            del history[:-300]
        for bug, component in (d.get("open_bugs") or {}).items():
            if bug in self.s["cases"] and not self.s["cases"][bug]["component"]:
                self.s["cases"][bug]["component"] = component
        for bug, title in (d.get("titles") or {}).items():
            if bug in self.s["cases"]:
                self.s["cases"][bug]["title"] = title
        if self.s["crew"]["Triage Guard"]["state"] == "working":
            fix = d.get("recommended_fix")
            self.bot("Triage Guard", "done", f"Recommends fixing {fix} first" if fix else "Nothing needs fixing", ts)
            if fix:
                self.bot("Fixer", "working", f"Writing the smallest fix for {fix}", ts)

    def on_pr_opened(self, d, ts, m):
        bug = d["bug_id"]
        c = self.case(bug, ts)
        self.advance(c, "fixed", {k: d.get(k) for k in ("url", "diff", "lines", "summary")}, ts)
        self.s["prs"][bug] = d.get("url")
        size = f"a {d['lines']}-line fix" if d.get("lines") else "a fix"
        self.bot("Fixer", "done", f"Opened a PR with {size} for {bug}", ts)
        self.bot("Night Manager", "working", f"Running the autonomy gate on {bug}", ts)

    def on_gate_result(self, d, ts, m):
        c = self.case(d["bug"], ts)
        self.advance(c, "verified", {"checks": d.get("checks", []), "verdict": d.get("verdict")}, ts)
        passed = sum(1 for x in d.get("checks", []) if x.get("ok"))
        self.bot("Night Manager", "working", f"Gate on {d['bug']}: {passed}/{len(d.get('checks', []))} checks pass", ts)

    def on_auto_merged(self, d, ts, m):
        bug = d["bug_id"]
        c = self.case(bug, ts)
        c["escalated"], c["retrying"] = None, None
        self.advance(c, "closed", {"how": "auto", "url": d.get("url")}, ts)
        self.s["prs"].pop(bug, None)
        self.count_fix(bug)
        m["auto_merges"] += 1
        self.record_fix_time(ts, m)
        self.bot("Night Manager", "done", f"Merged the fix for {bug}: all five checks pass", ts)

    def on_escalated(self, d, ts, m):
        bug = d["bug_id"]
        c = self.case(bug, ts)
        if d.get("retryable"):
            # Every rule passed; GitHub didn't cooperate yet. The crew retries, no human needed.
            c["retrying"] = d.get("reasons", [])
            self.bot("Night Manager", "working", f"Retrying the merge of {bug}", ts)
            return
        c["retrying"] = None
        c["escalated"] = d.get("reasons", [])
        m["escalations"] += 1
        self.bot("Night Manager", "waiting", f"Escalated {bug}: a human must review it", ts)

    def on_review_merged(self, d, ts, m):
        """A human merged a fix the crew had escalated."""
        bug = d["bug_id"]
        c = self.case(bug, ts)
        self.s["prs"].pop(bug, None)
        if c["closed"] is not None:
            return
        m["human_touches"] += 1
        self.advance(c, "closed", {"how": "human", "url": d.get("url")}, ts)
        self.count_fix(bug)
        self.record_fix_time(ts, m)
        if self.s["crew"]["Night Manager"]["state"] == "waiting":
            self.bot("Night Manager", "done", f"A human merged {bug}", ts)

    def on_review_declined(self, d, ts, m):
        """A human closed an escalated fix without merging: the bug stays open and isn't retried."""
        bug = d["bug_id"]
        c = self.case(bug, ts)
        self.s["prs"].pop(bug, None)
        c["declined"] = {"url": d.get("url"), "ts": ts}
        m["human_touches"] += 1
        if self.s["crew"]["Night Manager"]["state"] == "waiting":
            self.bot("Night Manager", "done", f"A human declined the fix for {bug}", ts)

    def on_bug_fixed(self, d, ts, m):
        """A proof that used to fail now passes on the default branch."""
        bug = d["bug_id"]
        self.s["prs"].pop(bug, None)
        if bug in self.s["cases"]:
            c = self.case(bug, ts)
            if c["closed"] is None:
                how = "human" if c["escalated"] is not None else "auto"
                self.advance(c, "closed", {"how": how}, ts)
            self.count_fix(bug)

    def on_patrol_done(self, d, ts, m):
        self.s["scanning"] = False
        components = self.s["patrol"].get("components")
        self.s["patrol"]["components"] = []
        cycle = self.s["cycle"]
        if cycle["start"] is not None and cycle["end"] is None:
            cycle["end"] = ts
            # Clean if no bug was spotted, proven or worked on since this task started.
            touched = [c for c in self.s["cases"].values() if c["updated"] >= cycle["start"] and not c["earlier"]]
            push = self.s["push"] if self.s["push"] and self.s["push"]["ts"] >= cycle["start"] else {}
            self.s["verdict"] = {
                "clean": not touched, "message": push.get("message"), "ts": ts,
                "components": components or [],
                "dismissed": m["dismissed"] - cycle.get("dismissed0", m["dismissed"]),
                "bugs": sorted(c["bug"] for c in touched),
            }
        for name in CREW:
            if self.s["crew"][name]["state"] == "working":
                self.bot(name, "done", None, ts)

    def on_chat(self, d, ts, m):
        self.s["activity"] = (self.s["activity"] + [{**d, "ts": ts}])[-100:]
        name = d.get("bot")
        if name in self.s["crew"]:
            m["bot_messages"] += 1
            b = self.s["crew"][name]
            # A message wakes an idle Bot; from a finished one it's a wrap-up (hand-off, log).
            if b["state"] == "idle":
                self.bot(name, "working", None, ts)
            b["step"] = d.get("text", "")[:140]
