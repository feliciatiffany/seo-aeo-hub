#!/usr/bin/env python3
"""Pull Ahrefs Site Explorer data for the SEO/AEO hub, with API unit guards.

Test mode (default) targets firehose.com, which Ahrefs serves for free on any
paid plan, so you can check the whole pipeline without spending API units.
Live mode targets your own domain and DOES consume API units.

Usage:
  python3 fetch_ahrefs.py              # free test run against firehose.com
  python3 fetch_ahrefs.py --live       # real run against AHREFS_TARGET (costs units)
  python3 fetch_ahrefs.py --usage      # show current API usage only (free)
  python3 fetch_ahrefs.py --live --force   # re-run even if today's data exists

Guards (set in .env):
  AHREFS_MONTHLY_BUDGET  stop if workspace usage this month would pass this (default 20000)
  AHREFS_RUN_BUDGET      stop mid-run once a single run has spent this much (default 1500)
  AHREFS_MIN_DAYS_BETWEEN  skip a live run if the last one was fewer than this many days ago (default 6)
  AHREFS_SLOW_EVERY_DAYS   re-pull the slow-changing, costly lists (refdomains) only this often
                           (default 14); in between the dashboard reuses the last pull
  A live run is refused if the usage check itself fails (fail safe). --force skips the
  day-gap and slow-list checks (never the budgets).

Needs AHREFS_API_KEY in the environment or in seo-hub/.env.
Saves raw JSON to seo-hub/data/ahrefs/<date>/<mode>/<endpoint>.json and logs
every run's unit cost to seo-hub/data/ahrefs/usage_log.csv.
"""
import argparse
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
API = "https://api.ahrefs.com/v3/site-explorer"
USAGE_URL = "https://api.ahrefs.com/v3/subscription-info/limits-and-usage"
FREE_TEST_TARGET = "firehose.com"  # free per Ahrefs "Free test queries" docs
LOG_FILE = HERE / "data" / "ahrefs" / "usage_log.csv"
ENDPOINT_LOG = HERE / "data" / "ahrefs" / "endpoint_log.csv"  # units per call, to see what each pull costs
# Lists that change slowly but cost the most (one unit charge per row). Pulled every
# AHREFS_SLOW_EVERY_DAYS; the dashboard falls back to the newest pull that has them.
SLOW_ENDPOINTS = {"refdomains"}
USED = "units_usage_workspace"
LIMIT = "units_limit_workspace"


def load_env():
    env_file = HERE / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def endpoints(target, date):
    """Endpoint name -> query params. Covers the hub's authority, backlink,
    organic and opportunity sections."""
    base = {"target": target, "date": date, "mode": "subdomains", "output": "json"}
    return {
        "domain-rating": {"target": target, "date": date, "output": "json"},
        "backlinks-stats": base,
        "metrics": base,
        "top-pages": {
            **base,
            "select": "url,sum_traffic,keywords,top_keyword,top_keyword_best_position",
            "order_by": "sum_traffic:desc",
            "limit": "50",
        },
        "organic-keywords": {
            **base,
            "select": "keyword,best_position,volume,sum_traffic,best_position_url",
            "order_by": "sum_traffic:desc",
            "limit": "100",
        },
        "refdomains": {
            **{k: v for k, v in base.items() if k != "date"},
            "select": "domain,domain_rating,first_seen,last_seen",
            "order_by": "domain_rating:desc",
            "limit": "100",
        },
    }


def request(url, key, timeout=60):
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {key}", "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read()), None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}: {e.read().decode(errors='replace')[:300]}"
    except urllib.error.URLError as e:
        return None, f"Network error: {e.reason}"
    except ValueError as e:
        return None, f"Bad JSON: {e}"


def call(endpoint, params, key):
    return request(f"{API}/{endpoint}?{urllib.parse.urlencode(params)}", key)


def get_usage(key):
    """Free endpoint: the workspace's API unit limits and usage, or None."""
    data, err = request(USAGE_URL, key, timeout=30)
    if err or not isinstance(data, dict):
        return None
    usage = data.get("limits_and_usage", {})
    return usage if USED in usage else None


def log_run(date, mode, target, spent, month_total, status):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    new = not LOG_FILE.exists()
    with LOG_FILE.open("a") as f:
        if new:
            f.write("timestamp,date,mode,target,units_spent,month_total,status\n")
        ts = dt.datetime.now().isoformat(timespec="seconds")
        f.write(f"{ts},{date},{mode},{target},{spent},{month_total},{status}\n")


def log_endpoint(date, name, rows, spent):
    new = not ENDPOINT_LOG.exists()
    with ENDPOINT_LOG.open("a") as f:
        if new:
            f.write("timestamp,date,endpoint,rows,units_spent\n")
        f.write(f"{dt.datetime.now().isoformat(timespec='seconds')},{date},{name},{rows},{spent}\n")


def last_live_pull(before_date, endpoint=None):
    """Date of the newest earlier live pull (that has `endpoint`, if given), or None."""
    days = []
    for d in (HERE / "data" / "ahrefs").glob("*/live"):
        name = d.parent.name
        if name < before_date and (any(d.glob("*.json")) if endpoint is None else (d / f"{endpoint}.json").exists()):
            days.append(name)
    return dt.date.fromisoformat(max(days)) if days else None


def row_count(data):
    lists = [v for v in (data or {}).values() if isinstance(v, list)]
    return len(lists[0]) if lists else 1


def int_env(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        sys.exit(f"{name} must be a whole number.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", action="store_true", help="query your own domain (consumes API units)")
    ap.add_argument("--usage", action="store_true", help="only print current API usage (free)")
    ap.add_argument("--force", action="store_true", help="re-run even if today's live data exists")
    ap.add_argument("--date", default=dt.date.today().isoformat())
    args = ap.parse_args()

    load_env()
    key = os.environ.get("AHREFS_API_KEY")
    if not key:
        sys.exit("AHREFS_API_KEY is not set. Copy .env.example to .env and add your key.")

    monthly_budget = int_env("AHREFS_MONTHLY_BUDGET", 20000)
    run_budget = int_env("AHREFS_RUN_BUDGET", 1500)

    before = get_usage(key)

    if args.usage:
        if before is None:
            sys.exit("Could not read API usage from Ahrefs.")
        print(f"Workspace usage this month: {before[USED]} / {before.get(LIMIT, '?')} units")
        print(f"Your budget (AHREFS_MONTHLY_BUDGET): {monthly_budget}")
        print(json.dumps(before, indent=2))
        return

    if args.live:
        target = os.environ.get("AHREFS_TARGET")
        if not target:
            sys.exit("Set AHREFS_TARGET in .env (e.g. example.com) for --live runs.")
        mode = "live"
    else:
        target, mode = FREE_TEST_TARGET, "test"

    out_dir = HERE / "data" / "ahrefs" / args.date / mode

    # --- Guards before spending anything -------------------------------------
    if args.live:
        if out_dir.exists() and any(out_dir.glob("*.json")) and not args.force:
            print(f"Skipped: live data for {args.date} already exists ({out_dir}). Use --force to re-run.")
            return
        gap = int_env("AHREFS_MIN_DAYS_BETWEEN", 6)
        last = last_live_pull(args.date)
        if last and not args.force and (dt.date.fromisoformat(args.date) - last).days < gap:
            print(f"Skipped: last live pull was {last} (AHREFS_MIN_DAYS_BETWEEN {gap}). "
                  f"Ahrefs data moves slowly, so nothing was spent. Use --force to pull anyway.")
            return
        if before is None:
            sys.exit("Stopped: couldn't read API usage, so the budget can't be checked. Nothing was spent.")
        used = before[USED]
        # Respect the lowest of: our budget, the workspace limit, and any
        # per-key limit set in Ahrefs (Account settings > API keys).
        ceiling = min([monthly_budget] + [v for v in (before.get(LIMIT),) if isinstance(v, int)])
        key_limit = before.get("units_limit_api_key")
        if isinstance(key_limit, int) and before.get("units_usage_api_key", 0) + run_budget > key_limit:
            log_run(args.date, mode, target, 0, used, "blocked_api_key_limit")
            sys.exit(f"Stopped: this API key's Ahrefs limit ({key_limit}) would be passed. Nothing was spent.")
        if used + run_budget > ceiling:
            log_run(args.date, mode, target, 0, used, "blocked_monthly_budget")
            sys.exit(f"Stopped: {used} units used this month; one more run (up to {run_budget}) "
                     f"could pass the budget of {ceiling}. Nothing was spent.")

    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"[{mode}] target={target} date={args.date}  run budget={run_budget}")

    failures, status = 0, "ok"
    slow_every = int_env("AHREFS_SLOW_EVERY_DAYS", 14)
    prev_usage = before
    for name, params in endpoints(target, args.date).items():
        if args.live and name in SLOW_ENDPOINTS and not args.force:
            last = last_live_pull(args.date, name)
            if last and (dt.date.fromisoformat(args.date) - last).days < slow_every:
                print(f"  - {name}: reusing {last} (re-pulled every {slow_every} days)")
                continue
        data, err = call(name, params, key)
        if err:
            failures += 1
            print(f"  ✗ {name}: {err}")
        else:
            (out_dir / f"{name}.json").write_text(json.dumps(data, indent=2))
            print(f"  ✓ {name}")

        # --- Guard during the run: stop once this run's spend hits the cap ---
        now = get_usage(key)
        if prev_usage is not None and now is not None:
            log_endpoint(args.date, name, 0 if err else row_count(data), now[USED] - prev_usage[USED])
            prev_usage = now
        if before is not None and now is not None:
            spent = now[USED] - before[USED]
            if spent >= run_budget:
                status = "stopped_run_budget"
                print(f"  ! Stopped early: this run has spent {spent} units (run budget {run_budget}).")
                break
        elif args.live:
            status = "stopped_usage_unknown"
            print("  ! Stopped early: lost the usage check, so spending can't be tracked.")
            break

    after = get_usage(key)
    spent = after[USED] - before[USED] if before and after else "?"
    month_total = after[USED] if after else "?"
    if failures and status == "ok":
        status = f"{failures}_failed"
    log_run(args.date, mode, target, spent, month_total, status)

    print(f"Saved to {out_dir}")
    print(f"API units this run: {spent} (month so far: {month_total} / budget {monthly_budget})")
    sys.exit(0 if status == "ok" else 1)


if __name__ == "__main__":
    main()
