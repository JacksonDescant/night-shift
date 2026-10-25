#!/usr/bin/env bash
# Set up (or repair) Night Shift on the crew's computer. Safe to re-run, e.g. after a
# computer update removes installed packages.
#
#   NIGHTSHIFT_SERVER=https://<your dashboard> NIGHTSHIFT_TOKEN=<progress token> bash install-crew.sh
#
# Leave the two variables unset to keep the values already saved.
set -uo pipefail

SOURCE=${NIGHTSHIFT_SOURCE:-"git+https://github.com/JacksonDescant/night-shift.git"}
HOME_DIR="$HOME/.nightshift"
VENV="$HOME_DIR/venv"
GITLEAKS_VERSION=8.30.1

say() { printf '\n== %s\n' "$*"; }
ok() { printf '   [ok]  %s\n' "$*"; }
warn() { printf '   [!!]  %s\n' "$*"; }

os=$(uname -s | tr '[:upper:]' '[:lower:]')
case "$(uname -m)" in x86_64|amd64) arch=x64 ;; aarch64|arm64) arch=arm64 ;; *) arch=$(uname -m) ;; esac
mkdir -p "$HOME_DIR"

say "Python"
PY=""
for candidate in python3.13 python3.12 python3.11 python3; do
  if command -v "$candidate" >/dev/null && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
    PY=$(command -v "$candidate"); break
  fi
done
[ -n "$PY" ] || { warn "need Python 3.11+"; exit 1; }
ok "$("$PY" --version)"
[ -x "$VENV/bin/python" ] || "$PY" -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip >/dev/null
"$VENV/bin/pip" install -q --upgrade "$SOURCE" semgrep && ok "night shift + scanners installed in $VENV" || { warn "pip install failed"; exit 1; }

say "gitleaks"
if [ -x "$VENV/bin/gitleaks" ] || command -v gitleaks >/dev/null; then
  ok "present"
else
  url="https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_${os}_${arch}.tar.gz"
  curl -fsSL "$url" | tar -xz -C "$VENV/bin" gitleaks && ok "installed" || warn "couldn't download gitleaks; secret scanning will be skipped"
fi

say "GitHub"
if command -v gh >/dev/null && gh auth status >/dev/null 2>&1; then
  ln -sf "$(command -v gh)" "$VENV/bin/gh"
  gh auth setup-git >/dev/null 2>&1
  ok "gh signed in (the crew can push branches and open PRs)"
else
  warn "gh missing or not signed in: install GitHub CLI and run 'gh auth login' with a token scoped to your repositories"
fi

say "Mimic"
if command -v docker >/dev/null && docker info >/dev/null 2>&1; then
  ok "Docker works: proofs run in a no-network container"
else
  warn "no Docker: proofs run in a cached local virtualenv per repository (set NIGHTSHIFT_MIMIC=local)"
  grep -q '^NIGHTSHIFT_MIMIC=' "$HOME_DIR/env" 2>/dev/null || echo "NIGHTSHIFT_MIMIC=local" >> "$HOME_DIR/env"
fi

say "Dashboard"
touch "$HOME_DIR/env" && chmod 600 "$HOME_DIR/env"
setkey() {
  [ -z "$2" ] && return
  grep -v "^$1=" "$HOME_DIR/env" > "$HOME_DIR/env.tmp" || true
  echo "$1=$2" >> "$HOME_DIR/env.tmp" && mv "$HOME_DIR/env.tmp" "$HOME_DIR/env" && chmod 600 "$HOME_DIR/env"
}
setkey NIGHTSHIFT_SERVER "${NIGHTSHIFT_SERVER:-}"
setkey NIGHTSHIFT_TOKEN "${NIGHTSHIFT_TOKEN:-}"
server=$(grep '^NIGHTSHIFT_SERVER=' "$HOME_DIR/env" | cut -d= -f2-)
if [ -n "$server" ] && curl -fsS -m 5 "$server/api/repos" >/dev/null 2>&1; then
  ok "dashboard reachable at $server"
else
  warn "dashboard not reachable at '${server:-unset}'"
fi

say "Self-check"
# The same lookups the crew gets: a bare, non-interactive shell with no ~/.bashrc.
missing=$(env -i HOME="$HOME" PATH=/usr/bin:/bin "$VENV/bin/python" -c '
import shutil
from nightshift import scan
print(" ".join(t for t in ("semgrep", "bandit", "pip-audit", "gitleaks", "gh") if not (scan._bin(t) or shutil.which(t))))' 2>&1)
[ -z "$missing" ] && ok "every tool is found from a bare shell" || warn "a bare shell can't find: $missing"
"$VENV/bin/nightshift" --help >/dev/null && ok "nightshift CLI: $VENV/bin/nightshift"
say "Done"
