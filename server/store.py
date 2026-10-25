"""Connected repositories and dashboard settings, kept on this machine.

Tokens are written to their own files with owner-only permissions and are never returned by
the API. Everything lives under $NIGHTSHIFT_DATA (default ~/.nightshift-dashboard).
"""
import json
import os
import secrets
import time
from pathlib import Path

DATA = Path(os.environ.get("NIGHTSHIFT_DATA", "~/.nightshift-dashboard")).expanduser()


def _read(name, default):
    try:
        return json.loads((DATA / name).read_text())
    except (OSError, ValueError):
        return default


def _write(name, data):
    DATA.mkdir(parents=True, exist_ok=True)
    path = DATA / name
    path.write_text(json.dumps(data, indent=2))
    path.chmod(0o600)


def _secret_file(name):
    return DATA / "secrets" / name


def _put_secret(name, value):
    path = _secret_file(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    path.write_text(value)
    path.chmod(0o600)


def _get_secret(name):
    try:
        return _secret_file(name).read_text().strip()
    except OSError:
        return ""


def _key(slug):
    return slug.replace("/", "__")


# ---- repositories -----------------------------------------------------------------------

def repos():
    return _read("repos.json", {})


def get(slug):
    return repos().get(slug)


def add(slug, info, token):
    data = repos()
    data[slug] = {**info, "slug": slug, "connected_at": data.get(slug, {}).get("connected_at", time.time())}
    _write("repos.json", data)
    _put_secret(f"token-{_key(slug)}", token)
    if not _get_secret(f"hook-{_key(slug)}"):
        _put_secret(f"hook-{_key(slug)}", secrets.token_hex(20))
    return data[slug]


def remove(slug):
    data = repos()
    data.pop(slug, None)
    _write("repos.json", data)
    for name in (f"token-{_key(slug)}", f"hook-{_key(slug)}"):
        _secret_file(name).unlink(missing_ok=True)


def token(slug):
    return _get_secret(f"token-{_key(slug)}")


def hook_secret(slug):
    """The secret for this repository's GitHub push webhook (HMAC-SHA256)."""
    return _get_secret(f"hook-{_key(slug)}")


# ---- settings ---------------------------------------------------------------------------

def ingest_token():
    """The bearer token the crew uses to post progress events. Created on first use."""
    value = os.environ.get("NIGHTSHIFT_TOKEN") or _get_secret("ingest-token")
    if not value:
        value = secrets.token_urlsafe(24)
        _put_secret("ingest-token", value)
    return value


def crew():
    """How to wake the crew: the Watcher routine's webhook URL and its Authorization value."""
    return {"webhook_url": os.environ.get("NIGHTSHIFT_BOT_WEBHOOK") or _read("crew.json", {}).get("webhook_url", ""),
            "webhook_auth": os.environ.get("NIGHTSHIFT_BOT_WEBHOOK_AUTH") or _get_secret("crew-webhook-auth")}


def set_crew(webhook_url=None, webhook_auth=None):
    if webhook_url is not None:
        _write("crew.json", {"webhook_url": webhook_url.strip()})
    if webhook_auth:
        _put_secret("crew-webhook-auth", webhook_auth.strip())
