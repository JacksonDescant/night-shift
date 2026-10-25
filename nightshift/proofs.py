"""Proof tests, and the mimic they run in.

A proof is a pytest file asserting the SAFE behaviour, so it FAILS while the bug exists. Its
module docstring names the bug and where it lives:

    \"\"\"Night Shift proof: users can read other users' orders [component: orders]\"\"\"

While being proved it sits in `.nightshift/proofs/test_<bug>.py` (untracked). The Fixer commits
it beside the fix as `<tests_dir>/test_<bug>.py`, where it stays as a regression test.

Every proof runs in the mimic: a throwaway container built from the repository's own
dependencies, with no network, the code mounted read-only and nothing from production.
NIGHTSHIFT_MIMIC=local runs pytest in-process instead (the repo's .venv if it has one).
"""
import ast
import hashlib
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from collections import namedtuple
from pathlib import Path

from nightshift.config import CREW_HOME

# rc: pytest exit code. 0 = all passed, 1 = tests ran and some failed, anything else = could not run.
Result = namedtuple("Result", "passed output xml rc")
META = re.compile(r"\[component:\s*([\w.-]+)\]")
DEP_FILES = ("requirements.txt", "requirements-dev.txt", "pyproject.toml", "setup.py", "setup.cfg", "Pipfile.lock")
DOCKERFILE = """FROM python:3.12-slim
COPY . /src
RUN pip install --no-cache-dir pytest \\
 && cd /src \\
 && if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; fi \\
 && if [ -f requirements-dev.txt ]; then pip install --no-cache-dir -r requirements-dev.txt; fi \\
 && if [ -f pyproject.toml ] || [ -f setup.py ]; then pip install --no-cache-dir . || true; fi
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/work
WORKDIR /work
"""


def bug_id(path):
    return Path(path).stem.removeprefix("test_")


def meta(path):
    """{"title", "component"} from a proof's module docstring."""
    try:
        doc = ast.get_docstring(ast.parse(Path(path).read_text())) or ""
    except (OSError, SyntaxError, UnicodeDecodeError):
        doc = ""
    m = META.search(doc)
    title = META.sub("", doc.split("\n")[0]).replace("Night Shift proof:", "").strip()
    return {"title": title or bug_id(path), "component": m.group(1) if m else None}


def proof_files(repo, src=None):
    """{bug: absolute path}: committed regression tests, plus local proofs not yet committed."""
    src = Path(src or repo.root)
    files = {}
    proofs = src / ".nightshift" / "proofs"
    for path in sorted(proofs.glob("test_*.py")) if proofs.is_dir() else []:
        files[bug_id(path)] = path
    tests = src / repo.config["tests_dir"]
    for path in sorted(tests.glob("test_*.py")) if tests.is_dir() else []:
        files[bug_id(path)] = path
    return files


# ---- the mimic -----------------------------------------------------------------------

_docker_ok = None


def docker_available():
    global _docker_ok
    if _docker_ok is None:
        _docker_ok = bool(shutil.which("docker")) and subprocess.run(
            ["docker", "info"], capture_output=True).returncode == 0
    return _docker_ok


def mode():
    wanted = os.environ.get("NIGHTSHIFT_MIMIC", "docker")
    return "docker" if wanted == "docker" and docker_available() else "local"


def image_for(src):
    """Build (once per dependency set) the mimic image for a checkout. Returns its tag."""
    tag = f"nightshift-mimic:{hashlib.sha1((DOCKERFILE + _dep_hash(src)).encode()).hexdigest()[:12]}"
    if subprocess.run(["docker", "image", "inspect", tag], capture_output=True).returncode:
        subprocess.run(["docker", "build", "-q", "-t", tag, "-f", "-", str(src)], input=DOCKERFILE,
                       capture_output=True, text=True, check=True)
    return tag


def _dep_hash(src):
    digest = hashlib.sha1()
    for name in DEP_FILES:
        f = Path(src) / name
        if f.exists():
            digest.update(name.encode() + f.read_bytes())
    return digest.hexdigest()[:12]


def _local_python(src):
    """Python with the repository's dependencies: $NIGHTSHIFT_LOCAL_PYTHON, the repo's own .venv,
    or a cached virtualenv Night Shift builds once per dependency set."""
    if os.environ.get("NIGHTSHIFT_LOCAL_PYTHON"):
        return os.environ["NIGHTSHIFT_LOCAL_PYTHON"]
    src = Path(src)
    own = src / ".venv" / "bin" / "python"
    if own.exists():
        return str(own)
    venv = CREW_HOME / "venvs" / _dep_hash(src)
    python = venv / "bin" / "python"
    if not python.exists():
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True, capture_output=True)
        pip = [str(python), "-m", "pip", "install", "-q", "pytest"]
        subprocess.run(pip, check=True, capture_output=True)
        for req in ("requirements.txt", "requirements-dev.txt"):
            if (src / req).exists():
                subprocess.run([*pip[:-1], "-r", str(src / req)], capture_output=True)
        if (src / "pyproject.toml").exists() or (src / "setup.py").exists():
            subprocess.run([*pip[:-1], str(src)], capture_output=True)
    return str(python)


def run_pytest(paths, src, junit=False, attempts=3):
    """Run pytest on paths (relative to src) in the mimic. Returns Result(passed, output, xml, rc).

    Errors other than test failures (e.g. a just-written file not yet visible in the
    container's bind mount) are retried, so they are never mistaken for a proof.
    """
    src = Path(src).resolve()
    out = src / ".nightshift" / "mimic"
    out.mkdir(parents=True, exist_ok=True)
    xml_name = f"{uuid.uuid4().hex}.xml" if junit else None
    args = ["-m", "pytest", "-q", "-p", "no:cacheprovider", *[str(p) for p in paths]]
    if mode() == "docker":
        if xml_name:
            args.append(f"--junitxml=/out/{xml_name}")
        cmd = ["docker", "run", "--rm", "--network", "none", "--read-only", "--tmpfs", "/tmp",
               "-v", f"{src}:/work:ro", "-v", f"{out}:/out", image_for(src), "python", *args]
    else:
        if xml_name:
            args.append(f"--junitxml={out / xml_name}")
        cmd = [_local_python(src), *args]
    for attempt in range(attempts):
        proc = subprocess.run(cmd, cwd=src, capture_output=True, text=True)
        if proc.returncode in (0, 1):
            break
        time.sleep(0.7 * (attempt + 1))
    return Result(proc.returncode == 0, (proc.stdout + proc.stderr)[-1800:],
                  out / xml_name if xml_name else None, proc.returncode)


def proves_bug(path, src):
    """True only if the test ran and failed: the bug is real on this code."""
    return run_pytest([path], src).rc == 1


def run_all(repo, src=None):
    """Run every proof in the mimic. Returns {bug: {"passed", "path", "title", "component", "failure"}}."""
    src = Path(src or repo.root).resolve()
    files = proof_files(repo, src)
    if not files:
        return {}
    rel = {bug: str(path.relative_to(src)) for bug, path in files.items()}
    by_module = {Path(p).stem: bug for bug, p in rel.items()}
    result = run_pytest(sorted(rel.values()), src, junit=True)
    passed, failures = {}, {}
    if result.xml and result.xml.exists():
        for case in ET.parse(result.xml).iter("testcase"):
            bug = by_module.get(case.get("classname", "").split(".")[-1])
            if bug is None:
                continue
            problem = case.find("failure") if case.find("failure") is not None else case.find("error")
            passed[bug] = passed.get(bug, True) and problem is None
            if problem is not None and bug not in failures:
                failures[bug] = f"{case.get('name')}: {problem.get('message', '')}".strip()[:600]
        result.xml.unlink()
    out = {}
    for bug, path in rel.items():
        info = meta(src / path)
        out[bug] = {"passed": passed.get(bug, False),  # a test that can't even run counts as failing
                    "path": path, "failure": failures.get(bug), **info}
    if src == repo.root.resolve():
        repo.write_state("proofs.json", out)
    return out


def open_bugs(results):
    """{bug: component} for every proof that fails right now."""
    return {bug: r["component"] for bug, r in results.items() if not r["passed"] and r["component"]}
