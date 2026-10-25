"""Draft a component map for a Python repository from its code.

This is the crew's starting point, not the final word: Triage Guard reviews the draft,
fixes what the heuristics got wrong, and proposes `nightshift.yaml` to a human as a PR.

  components  packages / modules, grouped two levels deep (`app/api`, `app/orders`)
  entry       code that serves requests: route decorators, Django URL configs
  asset       code that holds the valuable data: database drivers, ORMs, models
  links       imports between components (who can reach whom)
  sensitive   auth functions and decorators, settings and secrets files
"""
import ast
import re
from collections import defaultdict
from pathlib import Path

SKIP = {".venv", "venv", ".nightshift", "node_modules", ".git", "build", "dist", ".tox", "tests", "test",
        "migrations", "docs", "__pycache__"}
ROUTE_ATTRS = {"route", "get", "post", "put", "patch", "delete", "api_route", "websocket", "view"}
ROUTE_NAMES = {"api_view", "route", "action"}
DATA_MODULES = {"sqlite3", "sqlalchemy", "psycopg", "psycopg2", "pymongo", "redis", "peewee", "tortoise",
                "django.db", "asyncpg", "aiosqlite", "boto3", "motor", "sqlmodel"}
AUTH_WORDS = re.compile(r"(login|logout|auth|permission|admin_required|_required$|^require_|verify_|password|token|session)",
                        re.I)
SENSITIVE_FILES = re.compile(r"(auth|security|settings|config|secrets?|permissions?)\w*\.(py|ini|cfg|toml|ya?ml)$", re.I)
MAX_COMPONENTS = 20


def _python_files(root):
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        if any(part in SKIP or part.startswith(".") for part in rel.parts[:-1]) or rel.name.startswith("test_"):
            continue
        if rel.name == "__init__.py" and not path.read_text(errors="ignore").strip():
            continue  # an empty package marker isn't a component
        yield rel


def _module_name(rel):
    parts = list(rel.with_suffix("").parts)
    if parts[0] == "src":
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _component_key(rel, depth):
    parts = list(rel.parts)
    if parts[0] == "src":
        parts = parts[1:]
    if len(parts) <= depth:
        return "/".join(parts)[:-3] if parts[-1].endswith(".py") else "/".join(parts)
    return "/".join(parts[:depth])


def _analyze(source):
    """(serves requests, touches data, imported modules, auth-looking functions, auth decorators)"""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False, False, set(), set(), set()
    routes = data = False
    imports, auth_funcs, auth_decos = set(), set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
            imports |= {f"{node.module}.{a.name}" for a in node.names}
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if AUTH_WORDS.search(node.name):
                auth_funcs.add(node.name)
            for deco in node.decorator_list:
                target = deco.func if isinstance(deco, ast.Call) else deco
                name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
                if name in ROUTE_ATTRS and isinstance(target, ast.Attribute) or name in ROUTE_NAMES:
                    routes = True
                if name and AUTH_WORDS.search(name) and name not in ROUTE_ATTRS:
                    auth_decos.add(f"@{name}")
        elif isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "urlpatterns" for t in node.targets):
            routes = True
    data = any(m == d or m.startswith(d + ".") for m in imports for d in DATA_MODULES)
    return routes, data, imports, auth_funcs, auth_decos


def _title(key):
    words = re.split(r"[/_\-.]+", key.split("/")[-1])
    return " ".join(w.capitalize() for w in words if w) or key


def draft(root):
    """A first nightshift.yaml for the repository at root (a dict)."""
    root = Path(root)
    files = list(_python_files(root))
    depth = 2
    while depth > 1 and len({_component_key(f, depth) for f in files}) > MAX_COMPONENTS:
        depth -= 1

    comp_of, info = {}, defaultdict(lambda: {"files": set(), "entry": False, "asset": False, "imports": set()})
    module_to_comp = {}
    sensitive_symbols, decorators, sensitive_paths = set(), set(), set()
    for rel in files:
        key = _component_key(rel, depth)
        comp_of[rel] = key
        module_to_comp[_module_name(rel)] = key
        routes, data, imports, auth_funcs, auth_decos = _analyze((root / rel).read_text(errors="ignore"))
        c = info[key]
        c["files"].add(rel)
        c["entry"] |= routes
        c["asset"] |= data or rel.stem in {"models", "db", "database", "repository", "repositories", "storage"}
        c["imports"] |= imports
        sensitive_symbols |= {f"{rel.as_posix()}:{f}" for f in auth_funcs}
        decorators |= auth_decos
        if SENSITIVE_FILES.search(rel.name):
            sensitive_paths.add(rel.as_posix())
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if path.is_file() and not any(p in SKIP or p.startswith(".") for p in rel.parts[:-1]) \
                and path.suffix in {".ini", ".cfg", ".toml", ".yml", ".yaml", ".env"} and SENSITIVE_FILES.search(rel.name):
            sensitive_paths.add(rel.as_posix())

    names = {}
    components = {}
    shared = {k.split("/")[0] for k in info}
    strip = f"{shared.pop()}/" if len(shared) == 1 and len(info) > 1 and all("/" in k for k in info) else ""
    for key, c in sorted(info.items()):
        slug = re.sub(r"[^a-z0-9]+", "-", key.removeprefix(strip).lower()).strip("-") or "root"
        names[key] = slug
        entry = {"name": _title(key), "paths": sorted({(f"{key}/" if (root / key).is_dir() else f.as_posix())
                                                      for f in c["files"]})}
        if c["entry"]:
            entry["entry"] = True
        if c["asset"]:
            entry["asset"] = True
        components[slug] = entry

    links = set()
    for key, c in info.items():
        for module in c["imports"]:
            parts = module.split(".")
            for n in range(len(parts), 0, -1):
                target = module_to_comp.get(".".join(parts[:n]))
                if target:
                    if target != key:
                        links.add((names[key], names[target]))
                    break
    return {
        "version": 1,
        "components": components,
        "links": [list(link) for link in sorted(links)],
        "sensitive": {"paths": sorted(sensitive_paths), "symbols": sorted(sensitive_symbols),
                      "decorators": sorted(decorators)},
    }


def summary(config):
    comps = config["components"]
    entries = [c["name"] for c in comps.values() if c.get("entry")]
    assets = [c["name"] for c in comps.values() if c.get("asset")]
    return (f"{len(comps)} components, {len(config['links'])} links. "
            f"Entry points: {', '.join(entries) or 'none found'}. Assets: {', '.join(assets) or 'none found'}.")
