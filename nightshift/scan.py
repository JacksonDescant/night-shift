"""Run the scanners over a repository and normalize their findings.

Semgrep (its Python ruleset plus ours), Bandit, gitleaks and pip-audit. Every finding gets a
stable fingerprint (rule + the flagged line's text) so a dismissed false positive stays
dismissed after the code around it moves.
"""
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
SEMGREP_RULES = HERE / "rules" / "semgrep.yml"
SKIP_DIRS = [".venv", "venv", ".nightshift", "node_modules", ".git", "build", "dist", ".tox"]


def _bin(name):
    venv = Path(sys.executable).parent / name
    return str(venv) if venv.exists() else shutil.which(name)


def _run(cmd, root, timeout=300):
    try:
        proc = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, str(exc)
    return proc.stdout, proc.stderr


def _fingerprint(root, tool, rule, file, line):
    """Stable across line moves: the rule plus the flagged source line's text."""
    try:
        text = (root / file).read_text().splitlines()[line - 1].strip()
    except (OSError, IndexError, UnicodeDecodeError):
        text = str(line)
    return hashlib.sha1(f"{tool}:{rule}:{file}:{text}".encode()).hexdigest()[:12]


def _finding(root, tool, rule, file, line, message, severity):
    file = str(Path(file).resolve().relative_to(root)) if Path(file).is_absolute() else str(Path(file))
    return {
        "id": hashlib.sha1(f"{tool}:{rule}:{file}:{line}".encode()).hexdigest()[:8],
        "fingerprint": _fingerprint(root, tool, rule, file, line),
        "tool": tool, "rule": rule, "file": file, "line": line,
        "message": message.strip()[:300], "severity": severity.lower(),
    }


def semgrep(root):
    exe = _bin("semgrep")
    if not exe:
        return [], "semgrep not installed"
    excludes = [arg for d in SKIP_DIRS + ["tests"] for arg in ("--exclude", d)]
    base = [exe, "scan", "--json", "--metrics=off", "--quiet", *excludes]
    out, err = _run(base + ["--config", "p/python", "--config", str(SEMGREP_RULES), "."], root)
    if not out:
        out, err = _run(base + ["--config", str(SEMGREP_RULES), "."], root)  # offline: our rules only
    try:
        results = json.loads(out)["results"]
    except (TypeError, ValueError, KeyError):
        return [], f"semgrep failed: {(err or '')[:200]}"
    return [_finding(root, "semgrep", r["check_id"].split(".")[-1], r["path"], r["start"]["line"],
                     r["extra"]["message"], r["extra"].get("severity", "warning")) for r in results], None


def bandit(root):
    exe = _bin("bandit")
    if not exe:
        return [], "bandit not installed"
    out, err = _run([exe, "-r", ".", "-f", "json", "-q", "-x", ",".join(f"./{d}" for d in SKIP_DIRS + ["tests"])], root)
    try:
        results = json.loads(out)["results"]
    except (TypeError, ValueError, KeyError):
        return [], f"bandit failed: {(err or '')[:200]}"
    return [_finding(root, "bandit", f'{r["test_id"]}-{r["test_name"]}', r["filename"].removeprefix("./"),
                     r["line_number"], r["issue_text"], r["issue_severity"]) for r in results], None


def gitleaks(root):
    exe = _bin("gitleaks")
    if not exe:
        return [], "gitleaks not installed"
    out, err = _run([exe, "dir", ".", "--report-format", "json", "--report-path", "-",
                     "--no-banner", "--exit-code", "0", "--log-level", "error"], root)
    try:
        results = json.loads(out or "[]")
    except ValueError:
        return [], f"gitleaks failed: {(err or '')[:200]}"
    return [_finding(root, "gitleaks", r["RuleID"], r["File"], r["StartLine"],
                     f'{r["Description"]} (secret redacted)', "high")
            for r in results if not any(part in SKIP_DIRS for part in Path(r["File"]).parts)], None


def pip_audit(root):
    exe = _bin("pip-audit")
    req = root / "requirements.txt"
    if not exe:
        return [], "pip-audit not installed"
    if not req.exists():
        return [], None  # nothing pinned to audit
    out, err = _run([exe, "-r", "requirements.txt", "--no-deps", "--disable-pip", "-f", "json",
                     "--progress-spinner", "off"], root)
    try:
        deps = json.loads(out)["dependencies"]
    except (TypeError, ValueError, KeyError):
        return [], f"pip-audit failed: {(err or '')[:200]}"
    lines = req.read_text().splitlines()
    findings = []
    for dep in deps:
        line = next((n for n, text in enumerate(lines, 1) if text.lower().startswith(dep["name"].lower())), 0)
        for vuln in dep.get("vulns", []):
            fixed = ", ".join(vuln.get("fix_versions", [])) or "none"
            findings.append(_finding(root, "pip-audit", vuln["id"], "requirements.txt", line,
                                     f'{dep["name"]} {dep["version"]}: {vuln.get("description", "")[:160]} (fixed in {fixed})',
                                     "high"))
    return findings, None


SCANNERS = {"semgrep": semgrep, "bandit": bandit, "gitleaks": gitleaks, "pip-audit": pip_audit}


def scan(root):
    root = Path(root).resolve()
    findings, errors, seen = [], {}, set()
    for name, fn in SCANNERS.items():
        found, err = fn(root)
        for f in found:
            if f["id"] not in seen:
                seen.add(f["id"])
                findings.append(f)
        if err:
            errors[name] = err
    return {"findings": findings, "errors": errors}
