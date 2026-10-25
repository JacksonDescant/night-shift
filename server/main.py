"""Night Shift dashboard: connect repositories, watch the crew work, review what needs a human.

The crew (Grok Bots) posts progress to /api/repos/{owner}/{name}/events. The dashboard wakes
the crew through the Watcher routine's webhook, follows the crew's PRs on GitHub so human
reviews resume the work, and can take GitHub push webhooks so new commits wake the crew.
"""
import asyncio
import hashlib
import hmac
import json
import os
import re
import time
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from server import board, store

STATIC = Path(__file__).parent / "static"
BOTS = Path(__file__).parent.parent / "bots"
ROLES = {"watcher": "Watcher", "triage": "Triage Guard", "fixer": "Fixer", "manager": "Night Manager"}
GITHUB = "https://api.github.com"
SLUG = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
FIX_BRANCH = "nightshift/fix-"
REVIEW_EVERY = 20  # seconds between checks of the crew's PRs

app = FastAPI(title="Night Shift")
app.mount("/static", StaticFiles(directory=STATIC), name="static")
BOARDS: dict[str, dict] = {}
subscribers: set[asyncio.Queue] = set()


# ---- state ------------------------------------------------------------------------------

def _board_file(slug):
    return store.DATA / "boards" / f"{slug.replace('/', '__')}.json"


def board_for(slug):
    if slug not in BOARDS:
        try:
            BOARDS[slug] = json.loads(_board_file(slug).read_text())
        except (OSError, ValueError):
            BOARDS[slug] = board.fresh(slug)
    return BOARDS[slug]


def _save(slug):
    path = _board_file(slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(BOARDS[slug]))


async def emit(slug, kind, data, ts=None):
    event = {"type": kind, "data": data, "ts": ts or time.time()}
    board.Board(board_for(slug)).apply(event)
    _save(slug)
    for queue in list(subscribers):
        queue.put_nowait({"repo": slug, **event})


def _slug(owner, name):
    slug = f"{owner}/{name}"
    if not store.get(slug):
        raise HTTPException(404, f"{slug} isn't connected")
    return slug


# ---- access -----------------------------------------------------------------------------

def require_local(request: Request):
    """Controls and settings only work from this machine, never through a tunnel or proxy."""
    proxied = any(request.headers.get(h) for h in ("cf-connecting-ip", "x-forwarded-for", "forwarded"))
    if proxied or request.client.host not in ("127.0.0.1", "::1"):
        raise HTTPException(403, "local only")


def require_ingest(request: Request):
    if request.headers.get("authorization") != f"Bearer {store.ingest_token()}":
        raise HTTPException(401, "bad token")


# ---- GitHub -----------------------------------------------------------------------------

async def gh(slug, path, token=None):
    """GET from the GitHub REST API with the repository's stored token."""
    token = token or store.token(slug)
    async with httpx.AsyncClient(timeout=15) as client:
        return await client.get(f"{GITHUB}{path}", headers={
            "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"})


def _pr_summary(p):
    merged = p.get("merged_at") is not None
    return {"number": p["number"], "state": "merged" if merged else p["state"],
            "bug": p["head"]["ref"].removeprefix(FIX_BRANCH), "url": p["html_url"]}


# ---- waking the crew --------------------------------------------------------------------

async def wake(slug, trigger, detail, **extra):
    """Call the Watcher routine's webhook. Returns True if the crew was woken."""
    crew = store.crew()
    if not crew["webhook_url"]:
        await emit(slug, "chat", {"bot": "Night Shift", "text": "No crew webhook set (Settings), so the crew wasn't woken."})
        return False
    payload = {"repo": slug, "trigger": trigger, "detail": detail, **extra}
    if store.public_url():
        payload["server"] = store.public_url()  # the tunnel changes per run; the crew follows it
    headers = {}
    if crew["webhook_auth"]:
        auth = crew["webhook_auth"]
        headers["Authorization"] = auth if " " in auth else f"Bearer {auth}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(crew["webhook_url"], json=payload, headers=headers)
        ok, why = resp.status_code < 400, f"HTTP {resp.status_code}"
    except httpx.HTTPError as exc:
        ok, why = False, type(exc).__name__
    await emit(slug, "chat", {"bot": "Night Shift", "text": f"Woke the crew ({trigger})." if ok
                              else f"Couldn't reach the crew's webhook ({why})."})
    return ok


# ---- pages and API ----------------------------------------------------------------------

@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/repos")
def list_repos():
    out = []
    for slug, info in sorted(store.repos().items()):
        b = board_for(slug)
        status = b["map"].get("status") or {}
        out.append({**info, "summary": {
            "open_bugs": len(status.get("open_bugs") or {}),
            "breached": bool(status.get("breached_assets")),
            "waiting": sum(1 for c in b["cases"].values() if c["escalated"] and not c["closed"] and not c["declined"]),
            "busy": any(m["state"] == "working" for m in b["crew"].values()),
            "mapped": bool(b["map"].get("config")),
            "last_event": b["last_event"],
        }})
    return out


@app.post("/api/repos")
async def connect(request: Request):
    require_local(request)
    body = await request.json()
    slug = (body.get("repo") or "").strip().removeprefix("https://github.com/").removesuffix(".git").strip("/")
    token = (body.get("token") or "").strip()
    if not SLUG.match(slug):
        raise HTTPException(400, "Use the owner/name form, e.g. octocat/hello-world")
    if not token:
        raise HTTPException(400, "A fine-grained token for this repository is required")
    resp = await gh(slug, f"/repos/{slug}", token)
    if resp.status_code == 401:
        raise HTTPException(400, "GitHub rejected the token")
    if resp.status_code == 404:
        raise HTTPException(400, f"The token can't see {slug} (check the name and the token's repository access)")
    if resp.status_code >= 400:
        raise HTTPException(502, f"GitHub said {resp.status_code}")
    repo = resp.json()
    perms = repo.get("permissions") or {}
    if perms and not perms.get("push"):
        raise HTTPException(400, "The token needs write access to Contents and Pull requests on this repository")
    note = "" if repo.get("language") in (None, "Python") else \
        f"Night Shift v1 is built for Python; {slug} looks like {repo['language']}."
    info = store.add(slug, {"default_branch": repo.get("default_branch", "main"), "private": repo.get("private"),
                            "description": repo.get("description") or "", "url": repo.get("html_url"),
                            "language": repo.get("language")}, token)
    board_for(slug)
    await emit(slug, "chat", {"bot": "Night Shift", "text": f"Connected {slug}. The crew will map it first."})
    await wake(slug, "map", "a repository was connected: draft its map")
    return {**info, "note": note}


@app.delete("/api/repos/{owner}/{name}")
def disconnect(owner: str, name: str, request: Request):
    require_local(request)
    slug = _slug(owner, name)
    store.remove(slug)
    BOARDS.pop(slug, None)
    _board_file(slug).unlink(missing_ok=True)
    return {"ok": True}


@app.get("/api/repos/{owner}/{name}")
def repo_state(owner: str, name: str):
    slug = _slug(owner, name)
    return {"repo": store.get(slug), "board": board_for(slug)}


@app.post("/api/repos/{owner}/{name}/events")
async def ingest(owner: str, name: str, request: Request):
    require_ingest(request)
    slug = _slug(owner, name)
    event = await request.json()
    await emit(slug, event.get("type", ""), event.get("data") or {}, event.get("ts"))
    return {"ok": True}


ACTIONS = {"sweep": ("full", "a full sweep was requested"),
           "patrol": ("patrol", "a patrol was requested"),
           "map": ("map", "a new map draft was requested")}


@app.post("/api/repos/{owner}/{name}/actions/{action}")
async def act(owner: str, name: str, action: str, request: Request):
    require_local(request)
    slug = _slug(owner, name)
    if action == "fix":
        bug = (await request.json()).get("bug", "")
        if bug not in board_for(slug)["cases"]:
            raise HTTPException(400, f"unknown bug {bug}")
        await emit(slug, "chat", {"bot": "Night Shift", "text": f"Asked the crew to fix {bug}."})
        ok = await wake(slug, "bug", f"fix {bug}", bug=bug)
    elif action in ACTIONS:
        trigger, detail = ACTIONS[action]
        ok = await wake(slug, trigger, detail)
    else:
        raise HTTPException(404, "unknown action")
    if not ok:
        raise HTTPException(502, "Couldn't wake the crew: check the crew webhook in Settings")
    return {"ok": True}


@app.get("/api/repos/{owner}/{name}/hook")
def hook_info(owner: str, name: str, request: Request):
    """What to paste into the repository's GitHub webhook settings."""
    require_local(request)
    slug = _slug(owner, name)
    return {"path": "/api/github/webhook", "secret": store.hook_secret(slug), "events": ["push"],
            "content_type": "application/json"}


@app.post("/api/github/webhook")
async def github_webhook(request: Request):
    """GitHub push webhook: a new commit on the default branch wakes the crew for what it touched."""
    body = await request.body()
    try:
        payload = json.loads(body)
    except ValueError:
        raise HTTPException(400, "bad payload")
    slug = (payload.get("repository") or {}).get("full_name", "")
    info = store.get(slug)
    secret = store.hook_secret(slug) if info else ""
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest() if secret else ""
    if not info or not hmac.compare_digest(expected, request.headers.get("x-hub-signature-256", "")):
        raise HTTPException(401, "bad signature")
    kind = request.headers.get("x-github-event")
    if kind == "ping":
        return {"ok": True}
    if kind != "push" or payload.get("ref") != f"refs/heads/{info.get('default_branch', 'main')}":
        return {"ok": True, "ignored": True}
    head = payload.get("head_commit") or {}
    await emit(slug, "push", {"commit": (head.get("id") or "")[:7], "message": (head.get("message") or "").split("\n")[0],
                              "author": (head.get("author") or {}).get("name") or (payload.get("pusher") or {}).get("name")})
    await wake(slug, "push", f"push to {info.get('default_branch', 'main')}", commit=head.get("id"))
    return {"ok": True}


@app.get("/api/settings")
def get_settings(request: Request):
    require_local(request)
    crew = store.crew()
    return {"crew_webhook_url": crew["webhook_url"], "crew_webhook_auth_set": bool(crew["webhook_auth"]),
            "ingest_token": store.ingest_token()}


@app.get("/api/setup")
def setup_info(request: Request):
    """Everything the crew setup page needs: each Bot's instructions and the values to paste."""
    require_local(request)
    rules = (BOTS / "_rules.txt").read_text() if (BOTS / "_rules.txt").exists() else ""
    prompts = {key: f"{(BOTS / f'{key}.md').read_text().strip()}\n\n{rules.strip()}\n"
               for key in ROLES if (BOTS / f"{key}.md").exists()}
    crew = store.crew()
    return {"public_url": store.public_url(), "ingest_token": store.ingest_token(),
            "bots": [{"key": k, "name": v, "prompt": prompts.get(k, "")} for k, v in ROLES.items()],
            "crew_webhook_url": crew["webhook_url"], "crew_webhook_auth_set": bool(crew["webhook_auth"])}


@app.put("/api/settings")
async def put_settings(request: Request):
    require_local(request)
    body = await request.json()
    store.set_crew(body.get("crew_webhook_url"), body.get("crew_webhook_auth"))
    return {"ok": True}


@app.get("/api/stream")
async def stream(request: Request):
    queue: asyncio.Queue = asyncio.Queue()
    subscribers.add(queue)

    async def gen():
        try:
            yield ": connected\n\n"
            while not await request.is_disconnected():
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            subscribers.discard(queue)

    return StreamingResponse(gen(), media_type="text/event-stream")


# ---- following human reviews ------------------------------------------------------------

async def review_finished(slug, pr):
    """A crew PR left the open state. If it was waiting on a human, record it and resume the crew."""
    case = board_for(slug)["cases"].get(pr["bug"])
    if not case or case.get("closed") or not case.get("escalated"):
        return  # not waiting on a human (e.g. the crew merged it itself)
    if pr["state"] == "merged":
        await emit(slug, "review_merged", {"bug_id": pr["bug"], "number": pr["number"], "url": pr["url"]})
        text = f"A human merged PR #{pr['number']} ({pr['bug']}). The crew carries on."
    else:
        await emit(slug, "review_declined", {"bug_id": pr["bug"], "number": pr["number"], "url": pr["url"]})
        text = f"A human closed PR #{pr['number']} ({pr['bug']}) without merging. The crew won't retry it in this task."
    await emit(slug, "chat", {"bot": "Night Shift", "text": text})
    await wake(slug, "resume", f"a human finished reviewing PR #{pr['number']} ({pr['bug']})",
               bug=pr["bug"], number=pr["number"])


@app.on_event("startup")
async def watch_reviews():
    async def loop():
        known: dict[str, dict] = {}
        while True:
            for slug in list(store.repos()):
                try:
                    resp = await gh(slug, f"/repos/{slug}/pulls?state=all&sort=updated&direction=desc&per_page=30")
                    prs = [_pr_summary(p) for p in resp.json() if p["head"]["ref"].startswith(FIX_BRANCH)] \
                        if resp.status_code == 200 else []
                except (httpx.HTTPError, ValueError, KeyError, TypeError):
                    prs = []
                if slug in known:
                    for pr in prs:
                        if known[slug].get(pr["number"]) == "open" and pr["state"] != "open":
                            await review_finished(slug, pr)
                if prs:
                    known[slug] = {p["number"]: p["state"] for p in prs}
            await asyncio.sleep(REVIEW_EVERY)

    if not os.environ.get("NIGHTSHIFT_NO_WATCH"):
        asyncio.create_task(loop())
