"""Where things live: the repository the crew guards, its Night Shift config, and local state.

The crew runs inside a checkout of the connected repository. That repository carries a
committed `nightshift.yaml` (its component map and autonomy policy); everything else the crew
writes stays in an untracked `.nightshift/` folder next to it.
"""
import copy
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

CONFIG_FILE = "nightshift.yaml"
STATE_DIR = ".nightshift"
CREW_HOME = Path(os.environ.get("NIGHTSHIFT_HOME", "~/.nightshift")).expanduser()  # shared by the crew
CHECKOUTS = CREW_HOME / "repos"

# The Bots run commands in non-interactive shells that never read ~/.bashrc, so tools installed
# next to this interpreter or in ~/.local/bin (gh, gitleaks, scanners) must be findable without it.
_TOOL_DIRS = [os.path.dirname(sys.executable), os.path.expanduser("~/.local/bin"), "/opt/homebrew/bin", "/usr/local/bin"]
os.environ["PATH"] = os.pathsep.join(
    [d for d in _TOOL_DIRS if os.path.isdir(d) and d not in os.environ.get("PATH", "").split(os.pathsep)]
    + [os.environ.get("PATH", "")]
)

DEFAULTS = {
    "version": 1,
    # name -> {name, paths: [file | dir/ | file:function], entry: bool, asset: bool}
    "components": {},
    # [from, to]: an attacker who controls `from` can reach `to`
    "links": [],
    # changes here always need a human, whatever the other rules say
    "sensitive": {
        "paths": ["requirements*.txt", "*/requirements*.txt", "pyproject.toml", "setup.py", "setup.cfg",
                  "Pipfile*", "poetry.lock", "*migrations/*", ".github/*", "Dockerfile*", "docker-compose*",
                  CONFIG_FILE],
        "symbols": [],
        "decorators": [],
    },
    "tests_dir": "tests/nightshift",
    "autonomy": {"enabled": True, "max_changed_lines": 12},
    "patrol": {"components_per_run": 2,
               "weights": {"proximity": 3, "recent_changes": 2, "bug_history": 2, "staleness": 1}},
}


def _merge(base, override):
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        out[key] = _merge(out[key], value) if isinstance(value, dict) and isinstance(out.get(key), dict) else value
    return out


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


@dataclass
class Repo:
    """A checkout of the repository the crew is guarding."""

    root: Path
    config: dict = field(default_factory=dict)

    @property
    def state(self):
        path = self.root / STATE_DIR
        path.mkdir(exist_ok=True)
        return path

    @property
    def proofs(self):
        path = self.state / "proofs"
        path.mkdir(exist_ok=True)
        return path

    @property
    def tests_dir(self):
        return self.root / self.config["tests_dir"]

    @property
    def slug(self):
        """owner/name from the origin remote, or None."""
        url = git("remote", "get-url", "origin", cwd=self.root).stdout.strip()
        m = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$", url)
        return m.group(1) if m else None

    @property
    def has_map(self):
        return bool(self.config.get("components"))

    def read_state(self, name, default=None):
        path = self.state / name
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text())
        except ValueError:
            return default

    def write_state(self, name, data):
        path = self.state / name
        path.write_text(json.dumps(data, indent=2))
        return path



def load(root):
    root = Path(root).resolve()
    path = root / CONFIG_FILE
    raw = yaml.safe_load(path.read_text()) if path.exists() else {}
    merged = _merge(DEFAULTS, raw)
    # A repo's own sensitive list adds to the built-in one (dependencies, CI, migrations); it never removes from it.
    for key, base in DEFAULTS["sensitive"].items():
        merged["sensitive"][key] = list(dict.fromkeys(base + list((raw or {}).get("sensitive", {}).get(key, []))))
    repo = Repo(root, merged)
    _ignore_state(root)
    return repo


def _ignore_state(root):
    """Keep .nightshift/ out of the guarded repo's commits without editing its .gitignore."""
    exclude = root / ".git" / "info" / "exclude"
    if exclude.parent.is_dir():
        text = exclude.read_text() if exclude.exists() else ""
        if f"/{STATE_DIR}/" not in text.splitlines():
            exclude.write_text(text + ("" if text.endswith("\n") or not text else "\n") + f"/{STATE_DIR}/\n")


def checkout_path(slug):
    owner, name = slug.split("/", 1)
    return CHECKOUTS / owner / name


def current_task():
    path = CREW_HOME / "task.json"
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def save_task(task):
    CREW_HOME.mkdir(parents=True, exist_ok=True)
    (CREW_HOME / "task.json").write_text(json.dumps(task, indent=2))


def find_repo():
    """The repo to work on: $NIGHTSHIFT_REPO, else the git checkout we're in, else the current task's."""
    if os.environ.get("NIGHTSHIFT_REPO"):
        return load(os.environ["NIGHTSHIFT_REPO"])
    top = git("rev-parse", "--show-toplevel", cwd=os.getcwd())
    if top.returncode == 0:
        return load(top.stdout.strip())
    task = current_task()
    if task.get("path") and Path(task["path"]).is_dir():
        return load(task["path"])
    raise SystemExit("Not inside a repository and no current task. Run `nightshift wake` or cd into the repo.")


def load_env():
    try:
        from dotenv import load_dotenv

        load_dotenv(CREW_HOME / "env")
        load_dotenv()
    except ImportError:
        pass
