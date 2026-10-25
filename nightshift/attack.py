"""Attack paths over a repository's component map, and which fix matters most.

Model: an attacker starts at every `entry` component (the public surface). From a component
they control, they can move into a linked component only if that component has an open,
proven bug. Reaching an `asset` component (where the valuable data lives) through such a
chain is a breach. Individually minor bugs matter when they line up into one.
"""
from collections import deque
from itertools import combinations

MAX_PATHS = 200
MAX_CUT = 3


def _graph(config):
    comps = config["components"]
    links = {name: set() for name in comps}
    for a, b in config.get("links", []):
        if a in links and b in comps:
            links[a].add(b)
    entries = {n for n, c in comps.items() if c.get("entry")}
    assets = {n for n, c in comps.items() if c.get("asset")}
    return comps, links, entries, assets


def name_of(config, component):
    return config["components"].get(component, {}).get("name", component)


def reach(config, open_components):
    """Components an attacker can get into, given which components have open bugs."""
    _, links, entries, _ = _graph(config)
    reached, queue = set(entries), deque(entries)
    while queue:
        here = queue.popleft()
        for nxt in links[here]:
            if nxt not in reached and nxt in open_components:
                reached.add(nxt)
                queue.append(nxt)
    return reached


def breach_paths(config, open_components):
    """Every simple path entry -> ... -> asset an attacker can walk right now."""
    _, links, entries, assets = _graph(config)
    paths = []

    def walk(path):
        if len(paths) >= MAX_PATHS:
            return
        here = path[-1]
        if here in assets and here in open_components:
            paths.append(list(path))
        for nxt in sorted(links[here]):
            if nxt not in path and nxt in open_components:
                walk(path + [nxt])

    for entry in sorted(entries):
        walk([entry])
    return paths


def distance_to_assets(config):
    """Links from each component to the nearest asset (None if it can't reach one)."""
    comps, links, _, assets = _graph(config)
    dist = {}
    for start in comps:
        seen, queue = {start: 0}, deque([start])
        while queue:
            here = queue.popleft()
            if here in assets:
                dist[start] = seen[here]
                break
            for nxt in links[here]:
                if nxt not in seen:
                    seen[nxt] = seen[here] + 1
                    queue.append(nxt)
        else:
            dist[start] = None
    return dist


def proximity(config, component):
    """1.0 for an asset, falling off with distance; 0 if it never leads to one."""
    d = distance_to_assets(config).get(component)
    return 0.0 if d is None else 1.0 / (1 + d)


def status(config, open_bugs):
    """The picture for the dashboard. open_bugs: {bug_id: component}."""
    open_components = set(open_bugs.values())
    paths = breach_paths(config, open_components)
    return {
        "open_components": sorted(open_components),
        "reached": sorted(reach(config, open_components)),
        "breach_paths": paths,
        "breached_assets": sorted({p[-1] for p in paths}),
    }


def _pick_bug(open_bugs, component, skip):
    bugs = sorted(b for b, c in open_bugs.items() if c == component and b not in skip)
    return bugs[0] if bugs else None


def recommend(config, open_bugs, skip=(), scope=None):
    """The single most valuable fix. Returns (bug_id or None, reason).

    skip: bugs that already have a fix waiting or were declined. scope: components the
    current task covers (None = all); a breach overrides the scope.
    """
    skip = set(skip)
    fixable = {b: c for b, c in open_bugs.items() if b not in skip}
    open_components = set(open_bugs.values())
    paths = breach_paths(config, open_components)
    _, _, entries, _ = _graph(config)
    dist = distance_to_assets(config)

    if paths:
        # Bugs in the entry component itself aren't needed to start a walk, so they can't cut one.
        candidates = sorted({c for p in paths for c in p if c not in entries or c == p[-1]}
                            & set(fixable.values()))
        for size in range(1, min(len(candidates), MAX_CUT) + 1):
            cuts = [set(combo) for combo in combinations(candidates, size)
                    if all(set(p) & set(combo) for p in paths)]
            if cuts:
                best = min(cuts, key=lambda cut: sum((dist.get(c) or 0) for c in cut))
                component = min(best, key=lambda c: (dist.get(c) or 0, c))
                bug = _pick_bug(fixable, component, skip)
                assets = " and ".join(sorted({name_of(config, p[-1]) for p in paths}))
                others = sum(1 for c in open_bugs.values() if c == component) - 1
                if len(best) == 1 and not others:
                    return bug, f"Fixing {bug} in {name_of(config, component)} alone cuts every path to {assets}."
                if len(best) == 1:
                    return bug, (f"Closing {name_of(config, component)} cuts every path to {assets}; it has "
                                 f"{others + 1} open bugs, start with {bug}.")
                rest = ", ".join(name_of(config, c) for c in sorted(best))
                return bug, f"Cutting every path to {assets} needs fixes in {rest}; start with {bug}."

    reached = reach(config, open_components)
    in_scope = {b: c for b, c in fixable.items() if scope is None or c in scope}
    best, best_gain = None, (0, 0.0)
    for bug, component in sorted(in_scope.items()):
        after = reach(config, open_components - {component} if list(open_bugs.values()).count(component) == 1
                      else open_components)
        gain = (len(reached - after), proximity(config, component))
        if gain > best_gain:
            best, best_gain = bug, gain
    if best and best_gain[0]:
        return best, (f"No path reaches an asset yet. Fixing {best} pushes an attacker out of "
                      f"{best_gain[0]} component(s).")
    if in_scope:
        bug = max(in_scope, key=lambda b: (proximity(config, in_scope[b]), b))
        return bug, f"{bug} is open in {name_of(config, in_scope[bug])}. It doesn't lead to an asset, but it's real."
    return None, "No open bugs to fix."


def next_fix(config, open_bugs, waiting=(), declined=(), scope=None, bug=None):
    """What the crew fixes next for the current task. Returns (bug_id or None, reason).

    The task is a set of components (None = all) or one requested bug. It is done when
    every open bug in scope has a fix merged, waiting in a PR, or declined by a human.
    """
    waiting, declined = set(waiting) & set(open_bugs), set(declined) & set(open_bugs)
    if bug:
        if bug not in open_bugs:
            return None, f"{bug} is already fixed on the default branch."
        if bug in waiting:
            return None, f"{bug} already has a fix waiting in a PR."
        if bug in declined:
            return None, f"A human declined the fix for {bug}."
        return bug, f"Requested from the dashboard: fix {bug}."
    in_scope = {b for b, c in open_bugs.items() if scope is None or c in scope}
    if not in_scope and not breach_paths(config, set(open_bugs.values())):
        where = "the repository" if scope is None else "the components this change touched"
        return None, f"No open bugs in {where}: the change is safe, nothing to fix."
    choice, reason = recommend(config, open_bugs, waiting | declined, scope)
    if choice:
        return choice, reason
    held = sorted(in_scope & waiting)
    no = sorted(in_scope & declined)
    return None, ("Every open bug in scope has a fix merged or waiting"
                  + (f" (waiting for review: {', '.join(held)})" if held else "")
                  + (f" (declined: {', '.join(no)})" if no else "") + ".")
