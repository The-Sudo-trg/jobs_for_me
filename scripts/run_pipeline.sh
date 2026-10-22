#!/usr/bin/env bash
# Full automation entrypoint: discover jobs -> tailor with Ollama -> commit & push.
# Designed to run unattended (Task Scheduler / cron / systemd timer).
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1
log() { echo "[$(date -Is)] $*"; }

log "Syncing with origin"
git config user.name "Madhav Jadav"
git config user.email "The-Sudo-trg@users.noreply.github.com"
git pull --rebase --autostash origin main || log "WARN: pull failed; continuing with local state"

log "Step 1/3: discovering jobs"
if ! python scripts/discover_jobs.py; then
    log "ERROR: job discovery failed"
    exit 1
fi

log "Step 2/3: tailoring applications with Ollama"
# Tailoring exits 0 with a notice when Ollama is unreachable, so discovery-only runs still push.
python scripts/tailor_applications.py

log "Step 3/3: committing and pushing"
git add jobs/ templates/ scripts/ tests/ 2>/dev/null
if git diff --cached --quiet; then
    log "Nothing to commit"
    exit 0
fi
git commit -m "automation: refresh tracker and tailored applications ($(date -u +%Y-%m-%dT%H:%MZ))"
if ! git push origin main; then
    log "WARN: push failed; rebasing and retrying once"
    git pull --rebase --autostash origin main
    git push origin main || { log "ERROR: push failed permanently"; exit 1; }
fi
log "Pipeline finished successfully"
