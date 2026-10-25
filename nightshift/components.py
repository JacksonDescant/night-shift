"""Which components a change (or a finding) belongs to, and whether it touches sensitive code.

A component's `paths` entries are a file (`app/db.py`), a directory (`app/api/`), a glob
(`app/routes/*.py`) or one function (`app/orders.py:get_order`, decorators included).
"""
import ast
import fnmatch
import re
import subprocess

HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def diff_changes(root, base, head=None):
    """Parse `git diff -U0`. Returns {path: {"lines": {new-side line numbers}, "added": [...], "removed": [...]}}.

    Pure deletions are anchored at the new-side line where they happened.
    """
    cmd = ["git", "diff", "-U0", base] + ([head] if head else []) + ["--"]
    out = subprocess.run(cmd, cwd=root, capture_output=True, text=True).stdout
    changes, old_path, path = {}, None, None
    for line in out.splitlines():
        if line.startswith("--- "):
            old_path = None if line[4:] == "/dev/null" else line[6:]
        elif line.startswith("+++ "):
            path = old_path if line[4:] == "/dev/null" else line[6:]
            changes.setdefault(path, {"lines": set(), "added": [], "removed": []})
        elif (m := HUNK.match(line)) and path:
            start, count = int(m[3]), int(m[4] if m[4] is not None else 1)
            changes[path]["lines"].update(range(start, start + count) if count else {max(start, 1)})
        elif path and line.startswith("+"):
            changes[path]["added"].append(line[1:])
        elif path and line.startswith("-"):
            changes[path]["removed"].append(line[1:])
    return changes


def read_source(root, path, rev=None):
    if rev:
        proc = subprocess.run(["git", "show", f"{rev}:{path}"], cwd=root, capture_output=True, text=True)
        return proc.stdout if proc.returncode == 0 else ""
    try:
        return (root / path).read_text()
    except (OSError, UnicodeDecodeError):
        return ""


def function_ranges(source):
    """{name: (first line incl. decorators, last line)} for every function, nested ones included."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}
    ranges = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start = min([node.lineno] + [d.lineno for d in node.decorator_list])
            ranges[node.name] = (start, node.end_lineno)
    return ranges


def matches(pattern, path):
    if pattern.endswith("/"):
        return path.startswith(pattern)
    return path == pattern or fnmatch.fnmatch(path, pattern)


def _entries(config):
    for name, info in config["components"].items():
        for entry in info.get("paths", []):
            path, _, func = entry.partition(":")
            yield name, path, func or None


def components_for_changes(repo, changes, rev=None):
    """Names of the components whose files or functions the changes touch."""
    touched = set()
    for path, change in changes.items():
        entries = [(name, p, func) for name, p, func in _entries(repo.config) if matches(p, path)]
        if not entries:
            continue
        whole = {name for name, _, func in entries if func is None}
        funcs = [(name, func) for name, _, func in entries if func]
        ranges = function_ranges(read_source(repo.root, path, rev)) if funcs else {}
        for line in change["lines"]:
            owners = {name for name, func in funcs if func in ranges and ranges[func][0] <= line <= ranges[func][1]}
            # Lines outside any owned function (shared helpers) touch every component in that file.
            touched |= owners or whole or {name for name, _ in funcs}
    return touched


def components_for_diff(repo, base, head=None):
    return components_for_changes(repo, diff_changes(repo.root, base, head), head)


def component_of(repo, path, line=None):
    """The component a file (and line) belongs to, or None."""
    found = components_for_changes(repo, {path: {"lines": {line or 1}, "added": [], "removed": []}})
    return sorted(found)[0] if found else None


def finding_in(repo, finding, components):
    """True if a scanner finding sits in one of the components (None means everywhere)."""
    if components is None:
        return True
    change = {finding["file"]: {"lines": {finding["line"] or 1}, "added": [], "removed": []}}
    return bool(components_for_changes(repo, change) & set(components))


def sensitive_touches(repo, changes, rev=None):
    """Reasons a change touches code that always needs a human (empty if none)."""
    rules = repo.config["sensitive"]
    reasons = []
    for path, change in changes.items():
        for pattern in rules.get("paths", []):
            if matches(pattern, path):
                reasons.append(f"changes {path} (sensitive: {pattern})")
        for text in change["added"] + change["removed"]:
            for deco in rules.get("decorators", []):
                if text.strip().startswith(deco):
                    reasons.append(f"adds or removes {deco} in {path}")
        symbols = [s.partition(":")[2] for s in rules.get("symbols", []) if s.partition(":")[0] == path]
        if symbols:
            ranges = function_ranges(read_source(repo.root, path, rev))
            for sym in symbols:
                if sym in ranges and any(ranges[sym][0] <= n <= ranges[sym][1] for n in change["lines"]):
                    reasons.append(f"edits sensitive function {sym}()")
    return sorted(set(reasons))
