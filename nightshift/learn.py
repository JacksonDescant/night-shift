"""The living map: what the crew has learned about a repository since its map was merged.

The committed nightshift.yaml is the map the crew acts on. Code moves on, though, and the
crew learns things while it reads it. Each check compares the map with the code (new code
no component owns, imports that link components the map doesn't, paths that no longer
exist) and adds what the agents noted themselves (`nightshift map note`). The dashboard
shows these as proposed changes; `nightshift map propose --learned` turns them into a PR,
so a human still reviews every change to the map.
"""
import copy
import time

from nightshift import components, memory, repomap


def _owners(repo, files):
    return {c for f in files if (c := components.component_of(repo, f))}


def _files_of(root, paths):
    out = set()
    for path in paths:
        target = root / path.rstrip("/")
        if target.is_dir():
            out |= {p.relative_to(root).as_posix() for p in target.rglob("*.py")}
        elif target.is_file():
            out.add(path)
    return out


def drift(repo):
    """Differences between the committed map and the code, as proposals."""
    config = repo.config
    if not config["components"]:
        return []
    draft = repomap.draft(repo.root)
    found = []
    files_by_draft = {key: _files_of(repo.root, c["paths"]) for key, c in draft["components"].items()}

    for key, comp in draft["components"].items():
        unowned = sorted(f for f in files_by_draft[key] if not components.component_of(repo, f))
        if unowned:
            found.append({"kind": "component", "id": key if key not in config["components"] else f"{key}-new", "name": comp["name"],
                          "paths": comp["paths"], "entry": bool(comp.get("entry")), "asset": bool(comp.get("asset")),
                          "why": f"{len(unowned)} file{'s' if len(unowned) > 1 else ''} no component owns, e.g. {unowned[0]}",
                          "source": "code"})

    known = {tuple(link) for link in config["links"]}
    new_ids = {p["id"] for p in found}
    for a, b in draft["links"]:
        froms = _owners(repo, files_by_draft.get(a, ())) or ({a} if a in new_ids else set())
        tos = _owners(repo, files_by_draft.get(b, ())) or ({b} if b in new_ids else set())
        for x in sorted(froms):
            for y in sorted(tos):
                if x != y and (x, y) not in known:
                    known.add((x, y))
                    found.append({"kind": "link", "from": x, "to": y,
                                  "why": f"code in {x} imports {y}", "source": "code"})

    for name, comp in config["components"].items():
        for entry in comp.get("paths", []):
            path = entry.partition(":")[0]
            if not any(ch in path for ch in "*?[") and not (repo.root / path.rstrip("/")).exists():
                found.append({"kind": "stale", "component": name, "path": entry,
                              "why": f"{entry} no longer exists", "source": "code"})
    return found


def note(repo, kind, target, other=None, why=""):
    """Record something an agent learned. kind: link | entry | asset | component."""
    mem = memory.load(repo)
    notes = mem.setdefault("map_notes", [])
    item = {"kind": kind, "why": why, "source": "agent", "ts": time.time()}
    if kind == "link":
        item.update({"from": target, "to": other})
    elif kind == "component":
        item.update({"id": target, "name": target.replace("-", " ").title(), "paths": [other] if other else []})
    else:
        item.update({"component": target})
    notes[:] = [n for n in notes if _key(n) != _key(item)] + [item]
    memory.save(repo, mem)
    return item


def _key(p):
    return (p["kind"], p.get("from"), p.get("to"), p.get("id"), p.get("component"), p.get("path"))


def _already_in(config, p):
    comps = config["components"]
    if p["kind"] == "link":
        return [p["from"], p["to"]] in [list(link) for link in config["links"]]
    if p["kind"] == "component":
        return p["id"] in comps
    if p["kind"] in ("entry", "asset"):
        return bool(comps.get(p["component"], {}).get(p["kind"]))
    if p["kind"] == "stale":
        return p["path"] not in comps.get(p["component"], {}).get("paths", [])
    return False


def proposals(repo):
    """Everything the crew would change in the map now. Agent notes the map already has are dropped."""
    mem = memory.load(repo)
    notes = [n for n in mem.get("map_notes", []) if not _already_in(repo.config, n)]
    if len(notes) != len(mem.get("map_notes", [])):
        mem["map_notes"] = notes
        memory.save(repo, mem)
    seen, out = set(), []
    for p in notes + drift(repo):
        if _key(p) not in seen:
            seen.add(_key(p))
            out.append(p)
    return out


def apply(config, items):
    """The map with the proposals applied (a new dict)."""
    new = copy.deepcopy({k: config[k] for k in ("version", "components", "links", "sensitive", "tests_dir", "autonomy")
                         if k in config})
    comps = new["components"]
    for p in items:
        if p["kind"] == "component" and p["id"] not in comps:
            comps[p["id"]] = {"name": p["name"], "paths": p["paths"], **({"entry": True} if p.get("entry") else {}),
                              **({"asset": True} if p.get("asset") else {})}
    for p in items:
        if p["kind"] == "link" and p["from"] in comps and p["to"] in comps and [p["from"], p["to"]] not in new["links"]:
            new["links"].append([p["from"], p["to"]])
        elif p["kind"] in ("entry", "asset") and p["component"] in comps:
            comps[p["component"]][p["kind"]] = True
        elif p["kind"] == "stale" and p["component"] in comps:
            comps[p["component"]]["paths"] = [x for x in comps[p["component"]]["paths"] if x != p["path"]]
    return new


def describe(p):
    if p["kind"] == "link":
        return f"link {p['from']} -> {p['to']}"
    if p["kind"] == "component":
        return f"new component {p['id']} ({', '.join(p['paths'])})"
    if p["kind"] == "stale":
        return f"remove {p['path']} from {p['component']}"
    return f"mark {p['component']} as {'an entry point' if p['kind'] == 'entry' else 'holding data'}"
