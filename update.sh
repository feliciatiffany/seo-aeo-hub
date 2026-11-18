#!/bin/bash
# Weekly SEO/AEO hub refresh: Ahrefs + GA4 + Search Console, then rebuild the dashboard.
# Run by launchd every Friday morning (see README → Weekly update).
#   ./update.sh          full refresh (Ahrefs ~700 units, GA4 ~5 tokens, GSC 5 requests)
#   ./update.sh --test   free: only checks the schedule can reach this folder and Ahrefs usage
# All the usage guards in fetch_ahrefs.py / fetch_google.py still apply.
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
cd "$(dirname "$0")" || exit 1
mkdir -p data
LOG=data/update.log
exec >>"$LOG" 2>&1
echo "===== $(date '+%Y-%m-%d %H:%M:%S') update ${1:-full} ====="

if [ "$1" = "--test" ]; then
  python3 fetch_ahrefs.py --usage && echo "test ok"
  exit $?
fi

status=0
python3 fetch_ahrefs.py --live || { echo "!! Ahrefs step failed"; status=1; }
.venv/bin/python fetch_google.py || { echo "!! Google step failed (if sign-in expired: gcloud auth application-default login)"; status=1; }
python3 build_dashboard.py || { echo "!! dashboard build failed"; status=1; }
echo "===== done (status $status) ====="
exit $status
