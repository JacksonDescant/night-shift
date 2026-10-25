#!/usr/bin/env bash
# Install Night Shift and start the dashboard with a public tunnel for the crew, then open the
# crew setup page. Run it again any time to start the dashboard; Ctrl-C stops everything.
#
#   ./setup.sh                 install (first run only), start the dashboard and tunnel
#   NIGHTSHIFT_PUBLIC_URL=https://... ./setup.sh   use your own public URL instead of a tunnel
set -euo pipefail
cd "$(dirname "$0")"

PORT=${PORT:-8000}
LOCAL="http://127.0.0.1:$PORT"
LOG_DIR=${NIGHTSHIFT_DATA:-$HOME/.nightshift-dashboard}/logs

say() { printf '\n== %s\n' "$*"; }
die() { printf '\n   [!!]  %s\n' "$*" >&2; exit 1; }

say "Python"
PY=""
for candidate in python3.13 python3.12 python3.11 python3; do
  if command -v "$candidate" >/dev/null && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
    PY=$(command -v "$candidate"); break
  fi
done
[ -n "$PY" ] || die "Night Shift needs Python 3.11 or newer"
[ -x .venv/bin/python ] || "$PY" -m venv .venv
if ! .venv/bin/python -c 'import nightshift, server' 2>/dev/null || [ pyproject.toml -nt .venv/.installed ]; then
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q -e '.[dev]'
  touch .venv/.installed
fi
echo "   [ok]  $(.venv/bin/python --version), Night Shift installed in .venv"

if [ -z "${NIGHTSHIFT_PUBLIC_URL:-}" ]; then
  say "Tunnel"
  if ! command -v cloudflared >/dev/null; then
    command -v brew >/dev/null || die "install cloudflared (https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/) and re-run"
    brew install -q cloudflared
  fi
fi

say "Starting"
mkdir -p "$LOG_DIR"
lsof -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1 && die "port $PORT is busy (another dashboard?). Stop it or run PORT=8010 ./setup.sh"
pids=()
stop() { [ ${#pids[@]} -gt 0 ] && kill "${pids[@]}" 2>/dev/null; true; }
trap stop EXIT
trap 'echo; echo "Stopped."; exit 0' INT TERM

if [ -z "${NIGHTSHIFT_PUBLIC_URL:-}" ]; then
  cloudflared tunnel --no-autoupdate --url "$LOCAL" > "$LOG_DIR/tunnel.log" 2>&1 &
  pids+=($!)
  for _ in $(seq 60); do
    NIGHTSHIFT_PUBLIC_URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG_DIR/tunnel.log" | head -1 || true)
    [ -n "$NIGHTSHIFT_PUBLIC_URL" ] && break
    sleep 0.5
  done
  [ -n "$NIGHTSHIFT_PUBLIC_URL" ] || die "the tunnel didn't start; see $LOG_DIR/tunnel.log"
fi
export NIGHTSHIFT_PUBLIC_URL

.venv/bin/nightshift-dashboard --port "$PORT" > "$LOG_DIR/dashboard.log" 2>&1 &
pids+=($!)
for _ in $(seq 40); do curl -fsS -m 1 "$LOCAL/api/repos" >/dev/null 2>&1 && break; sleep 0.25; done
curl -fsS -m 1 "$LOCAL/api/repos" >/dev/null 2>&1 || die "the dashboard didn't start; see $LOG_DIR/dashboard.log"

cat <<EOF

   Dashboard   $LOCAL
   Crew URL    $NIGHTSHIFT_PUBLIC_URL
   Logs        $LOG_DIR

   First time? The dashboard opens on "Set up the crew": follow it top to bottom.
   The crew follows the tunnel's new address on its own each time you restart.
   Keep this running while the crew works. Ctrl-C stops it.
EOF
command -v open >/dev/null && open "$LOCAL" || true
wait
