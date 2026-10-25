# Setting up the crew

Night Shift's crew is four [Grok Bots](https://cursor.com/help/grok-bot/faqs): **Watcher**, **Triage Guard**, **Fixer** and **Night Manager**. They share one cloud computer, hand off work in a group chat, and run the `nightshift` CLI. This guide takes about an hour, once.

```
your machine                                  Grok Bot (one shared cloud computer)
  dashboard :8000  <── tunnel ── progress ──   Watcher ─> Triage Guard ─> Fixer ─> Night Manager
  "wake" ─────────── routine webhook ───────>  (group chat "Night Shift")
                                               ~/.nightshift/repos/<owner>/<name>  (one checkout per repo)
GitHub: your repositories  <── PRs, merges ──  crew
```

## 1. Run the dashboard

```bash
make install
make dashboard        # http://127.0.0.1:8000
make tunnel           # in a second terminal: prints https://<random>.trycloudflare.com
```

The crew posts progress through the tunnel URL; only the progress endpoint is meant for it. Every control in the dashboard answers only requests from your own machine.

## 2. Give the crew GitHub access

On the Bots' computer, the crew uses the GitHub CLI. Create a **fine-grained token** at
[github.com/settings/personal-access-tokens/new](https://github.com/settings/personal-access-tokens/new):

- **Repository access:** only the repositories you'll connect
- **Permissions:** Contents (read and write), Pull requests (read and write). Metadata (read) is added automatically.

Don't paste it into a chat. You'll enter it on the Bots' computer in step 4.

## 3. Create the Bots and their group chat

For each of the four roles:

1. In Grok Bot, click **+** → **Create new Bot** and give it the role's name.
2. Copy its instructions: `make prompt BOT=watcher` (then `triage`, `fixer`, `manager`). This copies the role file plus `bots/_rules.txt`.
3. Paste them as the Bot's first message and add: *Remember all of this as lasting rules.*

Then create a group chat named **Night Shift** with all four, and send:

> This is our working channel. Hand off with @Name, one owner per step: Watcher → Triage Guard → Fixer → Night Manager. Remember that.

## 4. Set up the shared computer

In any Bot's chat, choose **Take over** and open a terminal on the computer:

```bash
gh auth login --with-token          # paste the token from step 2, Enter, then Ctrl-D
curl -fsSL https://raw.githubusercontent.com/JacksonDescant/night-shift/main/scripts/install-crew.sh -o install-crew.sh
NIGHTSHIFT_SERVER="<your tunnel URL>" NIGHTSHIFT_TOKEN="<progress token from the dashboard's Settings>" bash install-crew.sh
```

Every line should end `[ok]`. A Docker warning is fine: proofs then run in a cached local virtualenv instead of a container. Re-run the script after any computer update, or when your tunnel URL changes.

## 5. The Watcher's routine

Send the Watcher:

> Create a routine named "Night Shift" that runs when its webhook is called. When it runs: run `~/.nightshift/venv/bin/nightshift wake '<the JSON body exactly as received>'`, post the result in the Night Shift group chat, and hand off exactly as its HAND OFF line says. Give me the webhook URL and its Authorization header.

In the dashboard, open **Settings** and paste the webhook URL and the Authorization value.

## 6. Approvals

Grok Bot reviews risky actions before they run (Auto-review). Routine approvals expire after about ten minutes, so add standing rules in **Settings → General → Auto-review → Custom Rules**, as **Allow automatically**:

- running `~/.nightshift/venv/bin/nightshift` commands for the connected repositories
- file edits under `~/.nightshift/fixes/`

The gate decides what merges; these rules only stop unattended runs from stalling on an approval card.

## 7. Connect a repository

In the dashboard, click **Connect repository** and enter owner/name and a fine-grained token for that repository (same permissions as step 2). The crew wakes up, drafts a map, and opens it as a PR. Review it and merge it.

**Optional, to wake the crew on every push:** use the repository's **Push webhook** button in the dashboard, then add a webhook on GitHub (Settings → Webhooks) with payload URL `<tunnel URL>/api/github/webhook`, content type `application/json`, the secret shown, and the *push* event.

**Optional, to show the gate's verdict on crew PRs:** copy `templates/nightshift-gate.yml` to the repository's `.github/workflows/`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| "Couldn't reach the crew's webhook" | Check the webhook URL and Authorization in Settings. A 401 means the Authorization value is wrong. |
| Dashboard not reachable from the crew | The tunnel stopped or its URL changed. Restart `make tunnel` and re-run `install-crew.sh` with the new `NIGHTSHIFT_SERVER`. |
| A Bot talks about an old commit | Tell it: *run `nightshift sync` and re-check*. All four share one checkout. |
| A Bot is stuck on a leftover branch or a rejected push | It should run `nightshift tidy` itself. Don't close PRs to reset the crew. |
| A fix waits on a human | That's the gate working. Review it from **Waiting for you**; merging or closing it on GitHub resumes the crew. |
