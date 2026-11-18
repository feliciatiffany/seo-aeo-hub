#!/usr/bin/env python3
"""Pull Google Analytics 4 and Google Search Console data for the SEO/AEO hub.

Usage (run with the hub's virtual env):
  .venv/bin/python fetch_google.py            # GA4 + Search Console
  .venv/bin/python fetch_google.py --only ga4
  .venv/bin/python fetch_google.py --only gsc
  .venv/bin/python fetch_google.py --check    # test access only (1 tiny call each)
  .venv/bin/python fetch_google.py --force    # re-run even if today's data exists
  .venv/bin/python fetch_google.py --login    # one-time lasting read-only sign-in

Both APIs are free; the guards protect your Google quotas so the hub never
crowds out Looker Studio or other tools using the same property:
  - Read-only access (analytics.readonly, webmasters.readonly scopes).
  - At most one pull per day per source (unless --force).
  - Fixed, small set of requests per run (GA4: 14, GSC: 5).
  - Before a run: refuses if a source already ran GOOGLE_MAX_RUNS_PER_DAY times
    today, or if one more run could pass GA4_MONTHLY_TOKEN_BUDGET /
    GSC_MONTHLY_REQUEST_BUDGET (counted from the usage log).
  - GA4 during a run: stops if the run passes GA4_RUN_TOKEN_BUDGET, if the property
    has under GA4_MIN_DAILY_TOKENS_LEFT / GA4_MIN_HOURLY_TOKENS_LEFT left, or if
    Google doesn't report usage.
  - GSC: row caps per request (GSC_MAX_ROWS) and a pause between requests.

Settings come from .env:
  GOOGLE_IMPERSONATE=seo-hub@your-project.iam.gserviceaccount.com   (no key file)
  GOOGLE_CREDENTIALS=secrets/service-account.json                     (only if keys are allowed)
  GA4_PROPERTY_ID=123456789
  GSC_SITE_URL=sc-domain:example.com
Saves raw JSON to data/google/<date>/{ga4,gsc}/ and logs to data/google/usage_log.csv.
"""
import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from fetch_ahrefs import load_env  # noqa: E402

try:
    import requests
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2 import service_account
except ImportError:
    sys.exit("Missing libraries. Run with the hub's env: .venv/bin/python fetch_google.py\n"
             "(first time: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt)")

SCOPES = [
    "https://www.googleapis.com/auth/analytics.readonly",
    "https://www.googleapis.com/auth/webmasters.readonly",
]
GA4_URL = "https://analyticsdata.googleapis.com/v1beta/properties/{pid}:runReport"
GSC_URL = "https://www.googleapis.com/webmasters/v3/sites/{site}/searchAnalytics/query"
DATA_DIR = HERE / "data" / "google"
LOG_FILE = DATA_DIR / "usage_log.csv"
GSC_REQUESTS_PER_RUN = 9  # totals x2, daily, queries, pages, content pages x2, content searches, content daily

# ---------------------------------------------------------------- daily history
# Day-by-day reports are kept in data/google/history/<src>/<report>.json and only the days
# not stored yet are fetched (plus the last few, which Google may still revise). The first
# run backfills as far back as each API allows, so charts can show older data.
HISTORY_DIR = DATA_DIR / "history"
GA4_DAILY = ("organic-daily", "ai-daily", "visitors-daily", "link-clicks-daily",
             "content-daily", "content-ai-daily", "content-clicks-daily")
GSC_DAILY = ("daily", "content-daily")
REFETCH_DAYS = 3


def _row_day(src, r):
    if src == "gsc":
        return r["keys"][0]
    v = r["dimensionValues"][0]["value"]
    return f"{v[:4]}-{v[4:6]}-{v[6:]}"


def _signature(body):
    """What the report asks for, minus the dates: if it changes, the stored history is refetched."""
    return json.dumps({k: v for k, v in body.items() if k not in ("dateRanges", "startDate", "endDate", "limit",
                                                                   "rowLimit", "startRow", "returnPropertyQuota")},
                      sort_keys=True)


def history_start(src, name, body, backfill_from, end):
    """First day to fetch for a daily report: the day after what's stored (minus a few), or the backfill start."""
    f = HISTORY_DIR / src / f"{name}.json"
    if f.exists():
        h = json.loads(f.read_text())
        if h.get("signature") == _signature(body) and h.get("through"):
            start = dt.date.fromisoformat(h["through"]) - dt.timedelta(days=REFETCH_DAYS - 1)
            return max(start, dt.date.fromisoformat(h.get("from", backfill_from))).isoformat()
    return backfill_from


def merge_history(src, name, body, data, start, end):
    f = HISTORY_DIR / src / f"{name}.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    old = json.loads(f.read_text()) if f.exists() else {}
    if old.get("signature") != _signature(body):
        old = {}
    keep = [r for r in old.get("rows", []) if not (start <= _row_day(src, r) <= end)]
    rows = sorted(keep + data.get("rows", []), key=lambda r: _row_day(src, r))
    out = {k: v for k, v in data.items() if k not in ("rows", "propertyQuota", "rowCount", "metadata")}
    out.update({"signature": _signature(body), "from": min(old.get("from", start), start), "through": end,
                "rows": rows})
    f.write_text(json.dumps(out))
    return len(rows)
# Blog posts and release notes on the site, tracked one by one in the hub's Content section.
CONTENT_PATH_REGEX = r"^/(post|release-notes)(/.*)?$"
CONTENT_URL_REGEX = r"/(post|release-notes)(/|$)"

# Visits from AI answer engines show up in GA4 as these referral sources.
AI_SOURCES_REGEX = (r".*(chatgpt\.com|chat\.openai\.com|openai\.com|perplexity\.ai|"
                    r"gemini\.google\.com|bard\.google\.com|copilot\.microsoft\.com|"
                    r"claude\.ai|you\.com|phind\.com|meta\.ai|deepseek\.com|grok\.com).*")


def int_env(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        sys.exit(f"{name} must be a whole number.")


LOG_HEADER = "timestamp,source,requests,tokens,status,note"


def read_log():
    """Usage log rows; upgrades an older log (no tokens column) in place."""
    if not LOG_FILE.exists():
        return []
    import csv
    lines = LOG_FILE.read_text().splitlines()
    if lines and lines[0] != LOG_HEADER:
        old = list(csv.DictReader(lines))
        with LOG_FILE.open("w") as f:
            f.write(LOG_HEADER + "\n")
            for r in old:
                f.write(f"{r.get('timestamp','')},{r.get('source','')},{r.get('requests','')},,"
                        f"{r.get('status','')},{r.get('note','')}\n")
        lines = LOG_FILE.read_text().splitlines()
    return list(csv.DictReader(lines))


def log_run(source, calls, tokens, status, note=""):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    read_log()  # make sure the header is current
    new = not LOG_FILE.exists()
    with LOG_FILE.open("a") as f:
        if new:
            f.write(LOG_HEADER + "\n")
        ts = dt.datetime.now().isoformat(timespec="seconds")
        f.write(f"{ts},{source},{calls},{tokens},{status},{note}\n")


def used_so_far(src, today):
    """What this source has used today and this month, from the usage log."""
    runs_today = month_requests = month_tokens = 0
    for r in read_log():
        if not r.get("source", "").startswith(src) or r.get("status") == "blocked":
            continue
        ts = r.get("timestamp", "")
        n = lambda k: int(r.get(k) or 0)
        if ts[:7] == today.isoformat()[:7]:
            month_requests += n("requests")
            month_tokens += n("tokens")
            if ts[:10] == today.isoformat():
                runs_today += 1
    return runs_today, month_requests, month_tokens


def guard(src, today):
    """Refuse to start if this source already hit a daily or monthly cap. Returns a reason or None."""
    runs_today, month_requests, month_tokens = used_so_far(src, today)
    max_runs = int_env("GOOGLE_MAX_RUNS_PER_DAY", 5)
    if runs_today >= max_runs:
        return f"blocked: already ran {runs_today} times today (GOOGLE_MAX_RUNS_PER_DAY {max_runs})"
    if src == "ga4":
        cap = int_env("GA4_MONTHLY_TOKEN_BUDGET", 2000)
        need = int_env("GA4_RUN_TOKEN_BUDGET", 100)
        if month_tokens + need > cap:
            return (f"blocked: {month_tokens} GA4 tokens used this month; one more run could pass "
                    f"GA4_MONTHLY_TOKEN_BUDGET {cap}")
    else:
        cap = int_env("GSC_MONTHLY_REQUEST_BUDGET", 100)
        if month_requests + GSC_REQUESTS_PER_RUN > cap:
            return (f"blocked: {month_requests} Search Console requests this month; one more run could pass "
                    f"GSC_MONTHLY_REQUEST_BUDGET {cap}")
    return None


OAUTH_CLIENT = HERE / "secrets" / "oauth-client.json"
OAUTH_TOKEN = HERE / "secrets" / "google-token.json"


def login():
    """One-time sign-in as yourself, read-only, saved to secrets/google-token.json.

    Uses a "Desktop app" sign-in client from Google Cloud (secrets/oauth-client.json).
    It asks only for read-only Analytics and Search Console access, not Google Cloud
    access, so the organisation's short Google Cloud sign-in limit does not apply and
    the saved sign-in keeps working until you remove it.
    """
    if not OAUTH_CLIENT.exists():
        sys.exit(f"Missing {OAUTH_CLIENT.relative_to(HERE)}. Download the Desktop app client "
                 "from Google Cloud (README → Lasting sign-in) and save it there.")
    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_secrets_file(str(OAUTH_CLIENT), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline",
                                  success_message="Signed in. You can close this tab and go back.")
    if not creds.refresh_token:
        sys.exit("Google did not return a lasting sign-in. Run --login again.")
    OAUTH_TOKEN.write_text(creds.to_json())
    OAUTH_TOKEN.chmod(0o600)
    print(f"Saved read-only sign-in to {OAUTH_TOKEN.relative_to(HERE)} (never commit or share it).")


def session():
    """Sign in to Google, read-only.

    Preferred: the saved sign-in from `fetch_google.py --login` (secrets/google-token.json).
    Next: GOOGLE_IMPERSONATE=<robot email>. Your own gcloud sign-in
    (`gcloud auth application-default login`) borrows the robot's identity for
    one hour at a time, so no key file exists. Fallback: a key file at
    GOOGLE_CREDENTIALS, for organisations that allow keys.
    """
    if OAUTH_TOKEN.exists():
        from google.oauth2.credentials import Credentials
        creds = Credentials.from_authorized_user_file(str(OAUTH_TOKEN), SCOPES)
        return AuthorizedSession(creds), "your saved read-only sign-in"
    robot = os.environ.get("GOOGLE_IMPERSONATE", "").strip()
    if robot:
        import google.auth
        from google.auth import impersonated_credentials
        from google.auth.exceptions import DefaultCredentialsError
        try:
            source, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        except DefaultCredentialsError:
            sys.exit("No Google sign-in found on this Mac. Run:\n"
                     "  gcloud auth application-default login\n"
                     "then try again. (README → Google setup, step 5.)")
        creds = impersonated_credentials.Credentials(
            source_credentials=source, target_principal=robot,
            target_scopes=SCOPES, lifetime=3600)
        return AuthorizedSession(creds), f"{robot} (borrowed, no key file)"

    path = os.environ.get("GOOGLE_CREDENTIALS", "secrets/service-account.json")
    p = Path(path) if Path(path).is_absolute() else HERE / path
    if not p.exists():
        sys.exit("No Google sign-in configured. Set GOOGLE_IMPERSONATE=<robot email> in .env "
                 "(README → Google setup).")
    creds = service_account.Credentials.from_service_account_file(str(p), scopes=SCOPES)
    return AuthorizedSession(creds), creds.service_account_email


def post(s, url, body):
    from google.auth.exceptions import RefreshError
    try:
        r = s.post(url, json=body, timeout=60)
    except requests.RequestException as e:
        return None, f"Network error: {e}"
    except RefreshError as e:
        msg = str(e)
        if "getAccessToken" in msg or "PERMISSION_DENIED" in msg:
            return None, ("Can't borrow the robot's identity. Give your own Google account the "
                          "'Service Account Token Creator' role on the robot (README step 3), "
                          "wait 2 minutes, then retry.")
        if "iamcredentials" in msg or "SERVICE_DISABLED" in msg:
            return None, "Turn on the 'IAM Service Account Credentials API' (README step 2), then retry."
        if "reauth" in msg.lower() or "invalid_grant" in msg:
            if OAUTH_TOKEN.exists():
                return None, ("Your saved Google sign-in was removed or revoked. Run: "
                              ".venv/bin/python fetch_google.py --login")
            return None, "Your Google sign-in on this Mac expired. Run: gcloud auth application-default login"
        return None, f"Sign-in error: {msg[:300]}"
    if r.status_code != 200:
        try:
            msg = r.json().get("error", {}).get("message", r.text)
        except ValueError:
            msg = r.text
        return None, f"HTTP {r.status_code}: {msg[:300]}"
    return r.json(), None


# ---------------------------------------------------------------- date ranges
def ranges(today, lag=1):
    """Last 28 days vs the 28 before. GSC uses lag=3: its numbers settle ~3 days late."""
    cur_end = today - dt.timedelta(days=lag)
    cur_start = cur_end - dt.timedelta(days=27)
    prev_end = cur_start - dt.timedelta(days=1)
    prev_start = prev_end - dt.timedelta(days=27)
    trend_start = cur_end - dt.timedelta(days=89)
    f = lambda d: d.isoformat()
    return {"cur": (f(cur_start), f(cur_end)), "prev": (f(prev_start), f(prev_end)),
            "trend": (f(trend_start), f(cur_end))}


# ---------------------------------------------------------------- GA4
def ga4_reports(r):
    cur = {"startDate": r["cur"][0], "endDate": r["cur"][1], "name": "current"}
    prev = {"startDate": r["prev"][0], "endDate": r["prev"][1], "name": "previous"}
    trend = {"startDate": r["trend"][0], "endDate": r["trend"][1]}
    organic = {"filter": {"fieldName": "sessionDefaultChannelGroup",
                          "stringFilter": {"matchType": "EXACT", "value": "Organic Search"}}}
    ai = {"filter": {"fieldName": "sessionSource",
                     "stringFilter": {"matchType": "FULL_REGEXP", "value": AI_SOURCES_REGEX,
                                      "caseSensitive": False}}}
    clicks = {"filter": {"fieldName": "eventName", "stringFilter": {"matchType": "EXACT", "value": "click"}}}
    core = [{"name": "sessions"}, {"name": "totalUsers"}, {"name": "engagedSessions"}, {"name": "keyEvents"}]
    content = lambda field: {"filter": {"fieldName": field,
                                        "stringFilter": {"matchType": "FULL_REGEXP", "value": CONTENT_PATH_REGEX}}}
    both = lambda *fs: {"andGroup": {"expressions": list(fs)}}
    return {
        "channels": {"dateRanges": [cur, prev], "dimensions": [{"name": "sessionDefaultChannelGroup"}],
                     "metrics": core, "limit": 25},
        "ai-sources": {"dateRanges": [cur, prev], "dimensions": [{"name": "sessionSource"}],
                       "metrics": core, "dimensionFilter": ai, "limit": 50},
        "organic-landing-pages": {"dateRanges": [cur], "dimensions": [{"name": "landingPage"}],
                                  "metrics": core, "dimensionFilter": organic, "limit": 50,
                                  "orderBys": [{"metric": {"metricName": "sessions"}, "desc": True}]},
        "organic-daily": {"dateRanges": [trend], "dimensions": [{"name": "date"}],
                          "metrics": [{"name": "sessions"}], "dimensionFilter": organic, "limit": 100,
                          "orderBys": [{"dimension": {"dimensionName": "date"}}]},
        "ai-daily": {"dateRanges": [trend], "dimensions": [{"name": "date"}],
                     "metrics": [{"name": "sessions"}], "dimensionFilter": ai, "limit": 100,
                     "orderBys": [{"dimension": {"dimensionName": "date"}}]},
        # --- whole-site visitors and the clicks that matter (Book demo, Log in, Sign up) ---
        "visitors": {"dateRanges": [cur, prev],
                     "metrics": [{"name": "totalUsers"}, {"name": "newUsers"}, {"name": "sessions"},
                                 {"name": "engagedSessions"}, {"name": "screenPageViews"},
                                 {"name": "averageSessionDuration"}], "limit": 5},
        "visitors-daily": {"dateRanges": [trend], "dimensions": [{"name": "date"}],
                           "metrics": [{"name": "totalUsers"}, {"name": "newUsers"}, {"name": "sessions"}],
                           "limit": 100, "orderBys": [{"dimension": {"dimensionName": "date"}}]},
        # GA4 records a "click" event (with the link address) when someone clicks a link to
        # another site, e.g. a booking page for demos or your app for log in / sign up.
        "link-clicks": {"dateRanges": [cur, prev], "dimensions": [{"name": "linkUrl"}],
                        "metrics": [{"name": "eventCount"}, {"name": "totalUsers"}],
                        "dimensionFilter": clicks, "limit": 200},
        "link-clicks-daily": {"dateRanges": [trend], "dimensions": [{"name": "date"}, {"name": "linkUrl"}],
                              "metrics": [{"name": "eventCount"}], "dimensionFilter": clicks, "limit": 3000},
        "link-clicks-by-source": {"dateRanges": [cur],
                                  "dimensions": [{"name": "sessionSourceMedium"}, {"name": "linkUrl"}],
                                  "metrics": [{"name": "eventCount"}], "dimensionFilter": clicks, "limit": 500},
        "sources": {"dateRanges": [cur, prev], "dimensions": [{"name": "sessionSourceMedium"}],
                    "metrics": [{"name": "sessions"}, {"name": "totalUsers"}, {"name": "engagedSessions"}],
                    "limit": 50},
        "pages": {"dateRanges": [cur], "dimensions": [{"name": "pagePath"}],
                  "metrics": [{"name": "screenPageViews"}, {"name": "totalUsers"}], "limit": 100,
                  "orderBys": [{"metric": {"metricName": "screenPageViews"}, "desc": True}]},
        "countries": {"dateRanges": [cur], "dimensions": [{"name": "country"}],
                      "metrics": [{"name": "totalUsers"}, {"name": "sessions"}], "limit": 25,
                      "orderBys": [{"metric": {"metricName": "totalUsers"}, "desc": True}]},
        "devices": {"dateRanges": [cur], "dimensions": [{"name": "deviceCategory"}],
                    "metrics": [{"name": "totalUsers"}, {"name": "sessions"}], "limit": 5},
        # Content section: every blog post and release note, one row each.
        "content-pages": {"dateRanges": [cur, prev], "dimensions": [{"name": "pagePath"}],
                          "metrics": [{"name": "screenPageViews"}, {"name": "totalUsers"},
                                      {"name": "userEngagementDuration"}],
                          "dimensionFilter": content("pagePath"), "limit": 1000},
        "content-entrances": {"dateRanges": [cur], "dimensions": [{"name": "landingPage"},
                                                                 {"name": "sessionDefaultChannelGroup"}],
                              "metrics": [{"name": "sessions"}, {"name": "engagedSessions"}],
                              "dimensionFilter": content("landingPage"), "limit": 2000},
        "content-ai": {"dateRanges": [cur], "dimensions": [{"name": "landingPage"}, {"name": "sessionSource"}],
                       "metrics": [{"name": "sessions"}, {"name": "engagedSessions"}],
                       "dimensionFilter": both(content("landingPage"), ai), "limit": 1000},
        "content-clicks": {"dateRanges": [cur], "dimensions": [{"name": "pagePath"}, {"name": "linkUrl"}],
                           "metrics": [{"name": "eventCount"}],
                           "dimensionFilter": both(content("pagePath"), clicks), "limit": 1000},
        # Per-post charts: the same numbers day by day over the 90-day trend window.
        "content-daily": {"dateRanges": [trend], "dimensions": [{"name": "date"}, {"name": "pagePath"}],
                          "metrics": [{"name": "screenPageViews"}, {"name": "totalUsers"}],
                          "dimensionFilter": content("pagePath"), "limit": 25000},
        "content-ai-daily": {"dateRanges": [trend], "dimensions": [{"name": "date"}, {"name": "landingPage"}],
                             "metrics": [{"name": "sessions"}],
                             "dimensionFilter": both(content("landingPage"), ai), "limit": 10000},
        "content-clicks-daily": {"dateRanges": [trend], "dimensions": [{"name": "date"}, {"name": "pagePath"},
                                                                       {"name": "linkUrl"}],
                                 "metrics": [{"name": "eventCount"}],
                                 "dimensionFilter": both(content("pagePath"), clicks), "limit": 10000},
    }


def run_ga4(s, out, r, check):
    pid = os.environ.get("GA4_PROPERTY_ID", "").strip()
    if not pid.isdigit():
        return "GA4_PROPERTY_ID is missing or not a number (find it in GA4 → Admin → Property details).", 0, 0
    run_budget = int_env("GA4_RUN_TOKEN_BUDGET", 100)
    min_left = int_env("GA4_MIN_DAILY_TOKENS_LEFT", 100000)
    min_left_hour = int_env("GA4_MIN_HOURLY_TOKENS_LEFT", 20000)
    url = GA4_URL.format(pid=pid)
    reports = ga4_reports(r)
    if check:
        reports = {"check": {"dateRanges": [{"startDate": "yesterday", "endDate": "yesterday"}],
                             "metrics": [{"name": "sessions"}], "limit": 1}}
    spent_tokens, calls = 0, 0
    end = r["trend"][1]
    backfill = os.environ.get("GA4_BACKFILL_FROM") or (dt.date.fromisoformat(end) - dt.timedelta(days=730)).isoformat()
    for name, body in reports.items():
        span = None
        if name in GA4_DAILY and not check:
            start = history_start("ga4", name, body, backfill, end)
            if start > end:
                print(f"  - ga4/{name}: already stored through {end}")
                continue
            span = (start, end)
            if start == backfill:
                run_budget = max(run_budget, int_env("GA4_BACKFILL_TOKEN_BUDGET", 400))  # one-time backfill
            body = {**body, "dateRanges": [{"startDate": start, "endDate": end}], "limit": 100000}
        data, err = post(s, url, {**body, "returnPropertyQuota": True})
        calls += 1
        if err:
            return f"{name}: {err}", calls, spent_tokens
        q = data.get("propertyQuota", {})
        day, hour = q.get("tokensPerDay", {}), q.get("tokensPerHour", {})
        if "consumed" not in day:
            return "stopped: Google didn't report token usage, so the hub can't track it", calls, spent_tokens
        spent_tokens += day["consumed"]
        if not check:
            (out / f"{name}.json").write_text(json.dumps(data, indent=2))
        stored = f", {span[0]} → {span[1]}, history {merge_history('ga4', name, body, data, *span)} rows" if span else ""
        left = day.get("remaining")
        print(f"  ✓ ga4/{name}  (tokens: {day.get('consumed', '?')}, left today: {left if left is not None else '?'}{stored})")
        if spent_tokens > run_budget:
            return f"stopped: run used {spent_tokens} tokens (GA4_RUN_TOKEN_BUDGET {run_budget})", calls, spent_tokens
        if left is not None and left < min_left:
            return f"stopped: only {left} daily tokens left (GA4_MIN_DAILY_TOKENS_LEFT {min_left})", calls, spent_tokens
        hour_left = hour.get("remaining")
        if hour_left is not None and hour_left < min_left_hour:
            return (f"stopped: only {hour_left} tokens left this hour "
                    f"(GA4_MIN_HOURLY_TOKENS_LEFT {min_left_hour})"), calls, spent_tokens
    if not check:
        (out / "quota.json").write_text(json.dumps({"tokens_this_run": spent_tokens}, indent=2))
    return None, calls, spent_tokens


# ---------------------------------------------------------------- Search Console
def run_gsc(s, out, r, check):
    site = os.environ.get("GSC_SITE_URL", "").strip()
    if not site:
        return "GSC_SITE_URL is missing (e.g. sc-domain:example.com or https://www.example.com/).", 0, 0
    max_rows = min(int_env("GSC_MAX_ROWS", 1000), 25000)
    url = GSC_URL.format(site=urllib.parse.quote(site, safe=""))
    cur = {"startDate": r["cur"][0], "endDate": r["cur"][1]}
    prev = {"startDate": r["prev"][0], "endDate": r["prev"][1]}
    content = [{"filters": [{"dimension": "page", "operator": "includingRegex", "expression": CONTENT_URL_REGEX}]}]
    queries = {
        "totals-current": {**cur},
        "totals-previous": {**prev},
        "daily": {"startDate": r["trend"][0], "endDate": r["trend"][1], "dimensions": ["date"], "rowLimit": 100},
        "queries": {**cur, "dimensions": ["query"], "rowLimit": max_rows},
        "pages": {**cur, "dimensions": ["page"], "rowLimit": min(max_rows, 500)},
        "content-pages": {**cur, "dimensions": ["page"], "dimensionFilterGroups": content, "rowLimit": 1000},
        "content-pages-previous": {**prev, "dimensions": ["page"], "dimensionFilterGroups": content, "rowLimit": 1000},
        "content-queries": {**cur, "dimensions": ["page", "query"], "dimensionFilterGroups": content,
                            "rowLimit": max_rows},
        "content-daily": {"startDate": r["trend"][0], "endDate": r["trend"][1], "dimensions": ["date", "page"],
                          "dimensionFilterGroups": content, "rowLimit": 25000},
    }
    if check:
        queries = {"check": {**cur, "rowLimit": 1}}
    calls = 0
    end = r["trend"][1]
    # Search Console keeps 16 months of data.
    backfill = (dt.date.fromisoformat(end) - dt.timedelta(days=480)).isoformat()
    for name, body in queries.items():
        span = None
        if name in GSC_DAILY and not check:
            start = history_start("gsc", name, body, backfill, end)
            if start > end:
                print(f"  - gsc/{name}: already stored through {end}")
                continue
            span = (start, end)
            body = {**body, "startDate": start, "endDate": end, "rowLimit": 25000}
        data, rows = None, []
        for page in range(8):  # page through big backfills, 25,000 rows at a time
            data, err = post(s, url, {**body, "dataState": "final", **({"startRow": len(rows)} if page else {})})
            calls += 1
            if err:
                return f"{name}: {err}", calls, 0
            got = data.get("rows", [])
            rows += got
            if not span or len(got) < body.get("rowLimit", 1000):
                break
            time.sleep(0.5)
        data["rows"] = rows
        if not check:
            (out / f"{name}.json").write_text(json.dumps(data, indent=2))
        stored = f", {span[0]} → {span[1]}, history {merge_history('gsc', name, body, data, *span)} rows" if span else ""
        print(f"  ✓ gsc/{name}  ({len(rows)} rows{stored})")
        time.sleep(0.5)  # stay far below Google's per-minute limits
    (out / "ranges.json").write_text(json.dumps(r, indent=2)) if not check else None
    return None, calls, 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", choices=["ga4", "gsc"])
    ap.add_argument("--check", action="store_true", help="test access with one tiny request each")
    ap.add_argument("--force", action="store_true", help="re-run even if today's data exists")
    ap.add_argument("--login", action="store_true", help="one-time lasting read-only sign-in (see README)")
    args = ap.parse_args()

    load_env()
    if args.login:
        login()
        return
    s, email = session()
    today = dt.date.today()
    print(f"Signed in as: {email}")

    sources = [args.only] if args.only else ["ga4", "gsc"]
    failed = False
    for src in sources:
        out = DATA_DIR / today.isoformat() / src
        if not args.check and out.exists() and any(out.glob("*.json")) and not args.force:
            print(f"[{src}] skipped: today's data already exists ({out}). Use --force to re-run.")
            continue
        blocked = guard(src, today)
        if blocked:
            log_run(src + ("-check" if args.check else ""), 0, 0, "blocked", blocked.replace(",", ";")[:200])
            print(f"[{src}] ✗ {blocked}")
            failed = True
            continue
        if not args.check:
            out.mkdir(parents=True, exist_ok=True)
        r = ranges(today, lag=1 if src == "ga4" else 3)
        print(f"[{src}]" + (" access check" if args.check else f" {r['cur'][0]} → {r['cur'][1]}"))
        err, calls, tokens = (run_ga4 if src == "ga4" else run_gsc)(s, out, r, args.check)
        status = "ok" if not err else ("stopped" if err.startswith("stopped") else "error")
        log_run(src + ("-check" if args.check else ""), calls, tokens, status, (err or "").replace(",", ";")[:200])
        if err:
            failed = True
            print(f"  ✗ {err}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
