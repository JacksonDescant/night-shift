"""Shared fixtures: a small Python repository to point Night Shift at, in a temp folder."""
import os
import subprocess
import sys
import tempfile

# Set before nightshift is imported: keep the crew's home and the mimic inside the test run.
_HOME = tempfile.mkdtemp(prefix="nightshift-home-")
os.environ.update(NIGHTSHIFT_HOME=_HOME, NIGHTSHIFT_MIMIC="local", NIGHTSHIFT_LOCAL_PYTHON=sys.executable,
                  NIGHTSHIFT_SERVER="http://127.0.0.1:9", NIGHTSHIFT_DATA=os.path.join(_HOME, "dashboard"))

import pytest  # noqa: E402
import yaml  # noqa: E402

FILES = {
    "shop/__init__.py": "",
    "shop/db.py": "import sqlite3\n\n\ndef connect(path=':memory:'):\n    return sqlite3.connect(path)\n",
    "shop/auth.py": ("from functools import wraps\n\n\ndef login_required(view):\n    @wraps(view)\n"
                     "    def wrapped(*args, **kwargs):\n        return view(*args, **kwargs)\n    return wrapped\n"),
    "shop/orders/__init__.py": ("from shop.db import connect\n\nORDERS = [{'id': 1, 'user': 'alice'}, {'id': 2, 'user': 'bob'}]\n\n\n"
                                "def list_orders(user):\n    connect()\n    return list(ORDERS)\n"),
    "shop/api/__init__.py": ("from flask import Flask\n\nfrom shop.auth import login_required\nfrom shop.orders import list_orders\n\n"
                             "app = Flask(__name__)\n\n\n@app.get('/orders')\n@login_required\ndef orders():\n"
                             "    return {'orders': list_orders('alice')}\n"),
    "tests/test_basic.py": "from shop.orders import list_orders\n\n\ndef test_lists_orders():\n    assert list_orders('alice')\n",
}
PROOF = ('"""Night Shift proof: list_orders returns other users\' orders [component: orders]"""\n'
         "from shop.orders import list_orders\n\n\ndef test_only_own_orders():\n"
         "    assert all(o['user'] == 'alice' for o in list_orders('alice'))\n")
FIX = ("    return [o for o in ORDERS if o['user'] == user]\n", "    return list(ORDERS)\n")


def git(root, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=root,
                          capture_output=True, text=True, check=True)


@pytest.fixture
def sample(tmp_path):
    """A git repo with a mapped Python app and one real (non-security) logic bug in `orders`."""
    from nightshift import config, repomap

    root = tmp_path / "shop"
    for rel, text in FILES.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    git(root, "init", "-q", "-b", "main")
    (root / "nightshift.yaml").write_text(yaml.safe_dump(repomap.draft(root), sort_keys=False))
    git(root, "add", "-A")
    git(root, "commit", "-qm", "sample app")
    return config.load(root)


@pytest.fixture
def proof(sample):
    path = sample.proofs / "test_orders_leak.py"
    path.write_text(PROOF)
    return path


@pytest.fixture
def fix_branch(sample):
    """nightshift/fix-orders_leak: the fix plus its committed proof."""
    def make(bug="orders_leak", extra=None):
        root = sample.root
        git(root, "switch", "-q", "-c", f"nightshift/fix-{bug}")
        orders = root / "shop/orders/__init__.py"
        orders.write_text(orders.read_text().replace(FIX[1], FIX[0]))
        tests = root / sample.config["tests_dir"]
        tests.mkdir(parents=True, exist_ok=True)
        (tests / f"test_{bug}.py").write_text(PROOF)
        if extra:
            extra(root)
        git(root, "add", "-A", "shop", "tests")
        git(root, "commit", "-qm", "fix")
        git(root, "switch", "-q", "main")
        return f"nightshift/fix-{bug}"
    return make
