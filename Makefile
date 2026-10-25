PY := .venv/bin/python

.PHONY: install test lint dashboard tunnel prompt

install:
	python3 -m venv .venv
	$(PY) -m pip install -q -e '.[dev]'
	@command -v semgrep >/dev/null || echo "optional: pip install semgrep (or brew install semgrep)"
	@command -v gitleaks >/dev/null || echo "optional: brew install gitleaks"

test:
	$(PY) -m pytest -q

lint:
	$(PY) -m pyflakes nightshift server tests
	node --check server/static/app.js

dashboard:
	$(PY) -m server.run

# A public URL for the crew to reach this dashboard (only /api/.../events is meant for it).
tunnel:
	@command -v cloudflared >/dev/null || (echo "install cloudflared first" && exit 1)
	cloudflared tunnel --no-autoupdate --url http://127.0.0.1:8000

# Copy a Bot's full instructions to the clipboard: make prompt BOT=watcher|triage|fixer|manager
prompt:
	@test -f bots/$(BOT).md || (echo "usage: make prompt BOT=watcher|triage|fixer|manager" && exit 1)
	@{ cat bots/$(BOT).md; echo; cat bots/_rules.txt; } | pbcopy
	@echo "copied the $(BOT) instructions; paste them as the Bot's first message"
