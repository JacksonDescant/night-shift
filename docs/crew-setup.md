# Setting up the crew

Night Shift's crew is four [Grok Bots](https://cursor.com/help/grok-bot/faqs): **Watcher**, **Triage Guard**, **Fixer** and **Night Manager**. They share one cloud computer, hand off work in a group chat, and run the `nightshift` CLI. You set them up once.

```
your machine                                  Grok Bot (one shared cloud computer)
  dashboard :8000  <── tunnel ── progress ──   Watcher ─> Triage Guard ─> Fixer ─> Night Manager
  "wake" ─────────── routine webhook ───────>  (group chat "Night Shift")
                                               ~/.nightshift/repos/<owner>/<name>  (one checkout per repo)
GitHub: your repositories  <── PRs, merges ──  crew
```

## Start the dashboard

```bash
./setup.sh
```

It installs Night Shift into `.venv`, starts the dashboard at http://127.0.0.1:8000 and a [Cloudflare quick tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/trycloudflare/) for the crew, and opens the dashboard. To use your own public URL instead of a tunnel, run `NIGHTSHIFT_PUBLIC_URL=https://... ./setup.sh`.

The tunnel's address changes every time you start it. You don't need to tell the crew: the dashboard sends its current address each time it wakes them.

The crew only uses the progress endpoint through the tunnel. Every control in the dashboard answers only requests from your own machine.

## Follow "Set up the crew"

The dashboard opens on **Set up the crew** (also under **Crew setup** in the top bar). Each step has the text to paste, already filled in for your dashboard:

1. **A GitHub token for the crew.** Create a [fine-grained token](https://github.com/settings/personal-access-tokens/new) with access to only the repositories you'll connect: Contents and Pull requests, read and write. Don't paste it into a chat.
2. **The four Bots.** For each, in Grok Bot: **+ → Create new Bot** with its name, and paste its instructions (the **Copy instructions** button) as its first message.
3. **Their group chat.** Create one named **Night Shift** with all four and send the message shown.
4. **The Bots' computer.** In any Bot's chat choose **Take over**, open a terminal, and run the two commands shown: `gh auth login --with-token` (paste the token, Enter, Ctrl-D) and the install command. Every line should end `[ok]`. A Docker warning is fine: proofs then run in a cached local virtualenv instead of a container. Re-run the install command after any computer update.
5. **The Watcher's routine.** Send the Watcher the message shown, then paste the webhook URL and Authorization header it gives you, and save.
6. **Approvals.** Grok Bot reviews risky actions before they run, and routine approvals expire after about ten minutes. In **Settings → General → Auto-review → Custom Rules**, add the rule shown as **Allow automatically**. The gate still decides what merges; this only stops unattended runs from stalling on an approval card.

## Connect a repository

Click **Connect repository** and enter owner/name and a fine-grained token for that repository (the same permissions as step 1). The crew wakes up, drafts a map, and opens it as a PR. Review it and merge it.

**Optional, to wake the crew on every push:** use the repository's **Push webhook** button in the dashboard, then add a webhook on GitHub (Settings → Webhooks) with payload URL `<crew URL>/api/github/webhook`, content type `application/json`, the secret shown, and the *push* event. The crew URL is printed by `setup.sh`; with a quick tunnel it changes per run, so use your own public URL if you want this to keep working.

**Optional, to show the gate's verdict on crew PRs:** copy `templates/nightshift-gate.yml` to the repository's `.github/workflows/`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| "Couldn't reach the crew's webhook" | Check the webhook URL and Authorization under Settings. A 401 means the Authorization value is wrong. |
| The dashboard shows no progress from the crew | Make sure `setup.sh` is still running. If the crew hasn't been woken since a restart, use **Full sweep** so it picks up the new address. |
| `setup.sh` says the port is busy | Another dashboard is running. Stop it, or run `PORT=8010 ./setup.sh`. |
| A Bot talks about an old commit | Tell it: *run `nightshift sync` and re-check*. All four share one checkout. |
| A Bot is stuck on a leftover branch or a rejected push | It should run `nightshift tidy` itself. Don't close PRs to reset the crew. |
| A fix waits on a human | That's the gate working. Review it from **Waiting for you**; merging or closing it on GitHub resumes the crew. |
