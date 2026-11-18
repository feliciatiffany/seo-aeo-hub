#!/usr/bin/env python3
"""Build the public demo: a made-up company's data, run through the real dashboard build.

Everything here is invented (company, domain, posts, numbers). No API keys and no
network calls are needed.

  python3 demo/make_demo.py     # writes docs/index.html (served by GitHub Pages)
"""
import datetime as dt
import json
import math
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
WORK = HERE / "_build"          # generated sample data (not committed)
OUT = ROOT / "docs"             # GitHub Pages folder
BRAND, DOMAIN = "Northwind Analytics", "northwind.example"
SITE = f"https://www.{DOMAIN}"
TODAY = dt.date.today()
rnd = random.Random(42)

POSTS = [  # (folder, slug, title, days ago)
    ("blog", "what-is-product-analytics", "What Is Product Analytics? A Plain-English Guide", 420),
    ("blog", "retention-metrics-that-matter", "7 Retention Metrics That Actually Predict Growth", 300),
    ("releases", "dashboards-2-0", "Dashboards 2.0: Faster, Shareable, Embeddable", 240),
    ("blog", "event-tracking-checklist", "The Event Tracking Checklist for New Products", 190),
    ("releases", "ai-insights-beta", "AI Insights Beta: Ask Your Data in Plain English", 120),
    ("blog", "aeo-for-saas", "AEO for SaaS: How to Get Recommended by AI Assistants", 75),
    ("releases", "warehouse-sync", "Warehouse Sync for Snowflake and BigQuery", 40),
    ("blog", "funnel-benchmarks-2026", "Funnel Benchmarks 2026: What Good Looks Like", 18),
    ("releases", "slack-alerts", "Slack Alerts for Metric Changes", 6),
]
QUERIES = ["product analytics", "retention metrics", "northwind analytics", "event tracking checklist",
           "funnel benchmarks", "saas retention benchmarks", "product analytics tools", "what is aeo",
           "ai insights analytics", "embeddable dashboards", "snowflake product analytics", "churn metrics"]
REFDOMAINS = ["saasweekly.example", "productled.example", "analyticsdigest.example", "growthnotes.example",
              "devtools-review.example", "startupstack.example", "datadriven.example", "metricsmonthly.example",
              "founderforum.example", "techradar.example", "launchboard.example", "uxcollective.example"]
AI_SOURCES = ["chatgpt.com", "perplexity.ai", "gemini.google.com", "claude.ai", "copilot.microsoft.com"]


def days(n_back, end):
    return [end - dt.timedelta(days=i) for i in range(n_back - 1, -1, -1)]


def wave(d, base, growth=0.0, weekly=0.25, noise=0.2, start=None):
    """Smooth made-up traffic: slow growth, a weekday rhythm and some noise."""
    t = (d - (start or d)).days
    v = base * (1 + growth * t) * (1 + weekly * math.sin(d.weekday() / 7 * 2 * math.pi + 1))
    return max(0, round(v * (1 + rnd.uniform(-noise, noise))))


def ga4(dims, mets, rows):
    return {"dimensionHeaders": [{"name": d} for d in dims], "metricHeaders": [{"name": m, "type": "TYPE_INTEGER"} for m in mets],
            "rows": [{"dimensionValues": [{"value": str(v)} for v in r[:len(dims)]],
                      "metricValues": [{"value": str(v)} for v in r[len(dims):]]} for r in rows],
            "rowCount": len(rows)}


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1))


def main():
    shutil.rmtree(WORK, ignore_errors=True)
    pulled = TODAY.isoformat()
    ah = WORK / "ahrefs" / pulled / "live"
    ga, gs = WORK / "google" / pulled / "ga4", WORK / "google" / pulled / "gsc"

    # ---- content (Markdown front matter, like a static site) ----
    for folder, slug, title, ago in POSTS:
        p = WORK / "content" / folder / f"{slug}.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f'---\ntitle: "{title}"\ndate: {(TODAY - dt.timedelta(days=ago)).isoformat()}\n'
                     f'author: "Demo Author"\n---\n\nSample post.\n')
    paths = {slug: ("/post/" if f == "blog" else "/release-notes/") + slug for f, slug, _, _ in POSTS}
    published = {slug: TODAY - dt.timedelta(days=ago) for _, slug, _, ago in POSTS}

    # ---- Ahrefs ----
    write(ah / "domain-rating.json", {"domain_rating": {"domain_rating": 38, "ahrefs_rank": 1843210}})
    write(ah / "backlinks-stats.json", {"metrics": {"live": 1260, "all_time": 1890, "live_refdomains": 214, "all_time_refdomains": 263}})
    write(ah / "metrics.json", {"metrics": {"org_keywords": 412, "org_keywords_1_3": 23, "org_traffic": 3150, "org_cost": 418000,
                                            "paid_keywords": 0, "paid_pages": 0, "paid_traffic": 0, "paid_cost": 0}})
    write(ah / "top-pages.json", {"pages": [
        {"url": SITE + "/", "sum_traffic": 1210, "keywords": 64, "top_keyword": "northwind analytics", "top_keyword_best_position": 1},
        {"url": SITE + paths["what-is-product-analytics"], "sum_traffic": 780, "keywords": 96, "top_keyword": "product analytics", "top_keyword_best_position": 6},
        {"url": SITE + paths["retention-metrics-that-matter"], "sum_traffic": 420, "keywords": 51, "top_keyword": "retention metrics", "top_keyword_best_position": 4},
        {"url": SITE + "/pricing", "sum_traffic": 260, "keywords": 12, "top_keyword": "northwind analytics pricing", "top_keyword_best_position": 1},
        {"url": f"https://{DOMAIN}" + paths["event-tracking-checklist"], "sum_traffic": 190, "keywords": 33, "top_keyword": "event tracking checklist", "top_keyword_best_position": 3},
    ]})
    write(ah / "organic-keywords.json", {"keywords": [
        {"keyword": q, "best_position": pos, "volume": vol, "sum_traffic": tr, "best_position_url": SITE + url}
        for q, pos, vol, tr, url in [
            ("northwind analytics", 1, 880, 610, "/"), ("product analytics", 6, 9900, 390, paths["what-is-product-analytics"]),
            ("retention metrics", 4, 2400, 210, paths["retention-metrics-that-matter"]), ("event tracking checklist", 3, 590, 95, paths["event-tracking-checklist"]),
            ("product analytics tools", 11, 6600, 60, "/"), ("funnel benchmarks", 8, 1300, 40, paths["funnel-benchmarks-2026"]),
            ("what is aeo", 14, 3600, 25, paths["aeo-for-saas"]), ("saas retention benchmarks", 9, 880, 22, paths["retention-metrics-that-matter"])]]})
    write(ah / "refdomains.json", {"refdomains": [
        {"domain": d, "domain_rating": dr, "first_seen": f"{(TODAY - dt.timedelta(days=fs)).isoformat()}T08:00:00Z",
         "last_seen": None if not lost else f"{(TODAY - dt.timedelta(days=lost)).isoformat()}T08:00:00Z"}
        for d, dr, fs, lost in zip(REFDOMAINS, [82, 74, 69, 63, 61, 58, 55, 51, 47, 44, 40, 36],
                                   [600, 410, 380, 300, 220, 150, 95, 60, 33, 21, 12, 5], [0, 0, 0, 45, 0, 0, 0, 0, 0, 10, 0, 0])]})
    (WORK / "ahrefs" / "usage_log.csv").write_text(
        "timestamp,date,mode,target,units_spent,month_total,status\n" +
        "".join(f"{(TODAY - dt.timedelta(days=d)).isoformat()}T08:10:00,{(TODAY - dt.timedelta(days=d)).isoformat()},live,{DOMAIN},{u},{m},ok\n"
                for d, u, m in [(21, 668, 668), (14, 248, 916), (7, 671, 1587), (0, 250, 1837)]))

    # ---- Search Console (lags ~3 days; 16 months of daily history) ----
    g_end = TODAY - dt.timedelta(days=3)
    g_days = days(480, g_end)
    cur = (g_end - dt.timedelta(days=27), g_end)
    prev = (cur[0] - dt.timedelta(days=28), cur[0] - dt.timedelta(days=1))
    write(gs / "ranges.json", {"cur": [cur[0].isoformat(), cur[1].isoformat()], "prev": [prev[0].isoformat(), prev[1].isoformat()],
                               "trend": [g_days[0].isoformat(), g_end.isoformat()]})
    daily = []
    for d in g_days:
        boost = 1.35 if d >= published["aeo-for-saas"] else 1.0
        imp = wave(d, 140, growth=0.004, start=g_days[0]) * boost
        daily.append({"keys": [d.isoformat()], "impressions": round(imp), "clicks": max(0, round(imp * rnd.uniform(0.025, 0.05))),
                      "ctr": 0, "position": round(rnd.uniform(9, 16), 1)})
    write(gs / "daily.json", {"rows": daily})
    def total(a, b):
        rows = [r for r in daily if a.isoformat() <= r["keys"][0] <= b.isoformat()]
        c, i = sum(r["clicks"] for r in rows), sum(r["impressions"] for r in rows)
        return {"rows": [{"clicks": c, "impressions": i, "ctr": c / i if i else 0, "position": 11.8 if a == cur[0] else 13.1}]}
    write(gs / "totals-current.json", total(*cur))
    write(gs / "totals-previous.json", total(*prev))
    qrows = [{"keys": [q], "clicks": c, "impressions": i, "ctr": c / i, "position": p} for q, c, i, p in [
        ("northwind analytics", 210, 640, 1.4), ("product analytics", 48, 5200, 6.8), ("retention metrics", 31, 1900, 4.6),
        ("product analytics tools", 9, 2600, 11.2), ("event tracking checklist", 22, 410, 3.1), ("funnel benchmarks", 12, 820, 8.4),
        ("what is aeo", 6, 1500, 13.7), ("saas retention benchmarks", 4, 640, 9.3), ("churn metrics", 3, 520, 12.6),
        ("embeddable dashboards", 2, 310, 7.9)]]
    write(gs / "queries.json", {"rows": qrows})
    write(gs / "pages.json", {"rows": [{"keys": [SITE + u], "clicks": c, "impressions": i, "ctr": c / i, "position": p}
                                       for u, c, i, p in [("/", 260, 2100, 3.2), ("/pricing", 40, 380, 2.1)] +
                                       [(paths[s], c, i, p) for s, c, i, p in [("what-is-product-analytics", 61, 6400, 7.1),
                                                                             ("retention-metrics-that-matter", 39, 2800, 5.0)]]]})
    post_daily, post_cur, post_prev, post_q = [], {}, {}, []
    for slug, url in paths.items():
        weight = {"what-is-product-analytics": 1.0, "retention-metrics-that-matter": 0.6, "event-tracking-checklist": 0.35,
                  "aeo-for-saas": 0.5, "funnel-benchmarks-2026": 0.3}.get(slug, 0.1)
        for d in g_days:
            if d < published[slug]:
                continue
            imp = round(wave(d, 60 * weight, growth=0.003, start=published[slug]))
            clk = round(imp * rnd.uniform(0.01, 0.04))
            post_daily.append({"keys": [d.isoformat(), SITE + url], "clicks": clk, "impressions": imp, "ctr": 0, "position": 8.0})
            bucket = post_cur if d >= cur[0] else post_prev if d >= prev[0] else None
            if bucket is not None:
                b = bucket.setdefault(url, [0, 0]); b[0] += clk; b[1] += imp
        for q in QUERIES:
            if rnd.random() < 0.3 and url in post_cur:
                post_q.append({"keys": [SITE + url, q], "clicks": rnd.randint(0, 9), "impressions": rnd.randint(20, 900), "ctr": 0,
                               "position": round(rnd.uniform(3, 18), 1)})
    write(gs / "content-daily.json", {"rows": post_daily})
    for name, src in (("content-pages", post_cur), ("content-pages-previous", post_prev)):
        write(gs / f"{name}.json", {"rows": [{"keys": [SITE + u], "clicks": c, "impressions": i, "ctr": c / i if i else 0,
                                              "position": round(rnd.uniform(5, 15), 1)} for u, (c, i) in src.items()]})
    write(gs / "content-queries.json", {"rows": post_q})

    # ---- GA4 (tracking started ~5 months ago; data through yesterday) ----
    a_end = TODAY - dt.timedelta(days=1)
    a_days = days(150, a_end)
    c28 = a_end - dt.timedelta(days=27)
    fmt = lambda d: d.strftime("%Y%m%d")
    people = {d: wave(d, 55, growth=0.006, start=a_days[0]) for d in a_days}
    write(ga / "visitors-daily.json", ga4(["date"], ["totalUsers", "newUsers", "sessions"],
                                         [[fmt(d), v, round(v * 0.8), round(v * 1.25)] for d, v in people.items()]))
    write(ga / "organic-daily.json", ga4(["date"], ["sessions"], [[fmt(d), round(v * 0.38)] for d, v in people.items()]))
    write(ga / "ai-daily.json", ga4(["date"], ["sessions"],
                                   [[fmt(d), max(0, round(v * (0.05 if d >= published["aeo-for-saas"] else 0.015)))] for d, v in people.items()]))
    s_cur = sum(v for d, v in people.items() if d >= c28)
    s_prev = sum(v for d, v in people.items() if c28 - dt.timedelta(days=28) <= d < c28)
    write(ga / "visitors.json", ga4(["dateRange"], ["totalUsers", "newUsers", "sessions", "engagedSessions", "screenPageViews", "averageSessionDuration"],
                                   [["current", s_cur, round(s_cur * .8), round(s_cur * 1.25), round(s_cur * .62), round(s_cur * 2.4), 96],
                                    ["previous", s_prev, round(s_prev * .8), round(s_prev * 1.25), round(s_prev * .58), round(s_prev * 2.3), 88]]))
    chans = [("Organic Search", .38), ("Direct", .3), ("Referral", .12), ("Organic Social", .1), ("Email", .05), ("Unassigned", .05)]
    write(ga / "channels.json", ga4(["sessionDefaultChannelGroup", "dateRange"], ["sessions", "totalUsers", "engagedSessions", "keyEvents"],
                                   [[c, r, round(t * f), round(t * f * .85), round(t * f * .6), round(t * f * .02)]
                                    for c, f in chans for r, t in (("current", s_cur * 1.25), ("previous", s_prev * 1.25))]))
    aiw = [.55, .25, .1, .07, .03]
    write(ga / "ai-sources.json", ga4(["sessionSource", "dateRange"], ["sessions", "totalUsers", "engagedSessions", "keyEvents"],
                                     [[s, r, round(t * w), round(t * w * .9), round(t * w * .7), 0]
                                      for s, w in zip(AI_SOURCES, aiw) for r, t in (("current", s_cur * .05), ("previous", s_prev * .03))]))
    landing = ["/", paths["what-is-product-analytics"], paths["retention-metrics-that-matter"], "/pricing", paths["event-tracking-checklist"]]
    write(ga / "organic-landing-pages.json", ga4(["landingPage"], ["sessions", "totalUsers", "engagedSessions", "keyEvents"],
                                                [[p, v, round(v * .9), round(v * .6), round(v * .02)] for p, v in zip(landing, [420, 260, 140, 90, 70])]))
    demo_url, signup_url, login_url = "https://calendly.com/northwind-demo/30min", "https://app.northwind.example/signup", "https://app.northwind.example/login"
    link_daily = []
    for d, v in people.items():
        for url, rate in ((demo_url, .012), (signup_url, .02), (login_url, .05)):
            n = round(v * rate * rnd.uniform(.4, 1.6))
            if n:
                link_daily.append([fmt(d), url, n])
    write(ga / "link-clicks-daily.json", ga4(["date", "linkUrl"], ["eventCount"], link_daily))
    lc = lambda url, a, b: sum(r[2] for r in link_daily if r[1] == url and a <= r[0] <= b)
    rows = []
    for url in (demo_url, signup_url, login_url):
        c = lc(url, fmt(c28), fmt(a_end)); p_ = lc(url, fmt(c28 - dt.timedelta(days=28)), fmt(c28 - dt.timedelta(days=1)))
        rows += [[url, "current", c, round(c * .8)], [url, "previous", p_, round(p_ * .8)]]
    write(ga / "link-clicks.json", ga4(["linkUrl", "dateRange"], ["eventCount", "totalUsers"], rows))
    srcs = [("google / organic", .36), ("(direct) / (none)", .3), ("linkedin.com / referral", .1), ("chatgpt.com / referral", .04),
            ("newsletter / email", .05), ("producthunt.com / referral", .04), ("bing / organic", .03)]
    write(ga / "sources.json", ga4(["sessionSourceMedium", "dateRange"], ["sessions", "totalUsers", "engagedSessions"],
                                  [[s, r, round(t * f), round(t * f * .85), round(t * f * .6)] for s, f in srcs
                                   for r, t in (("current", s_cur * 1.25), ("previous", s_prev * 1.25))]))
    write(ga / "link-clicks-by-source.json", ga4(["sessionSourceMedium", "linkUrl"], ["eventCount"],
                                                [[s, u, max(1, round(lc(u, fmt(c28), fmt(a_end)) * f))] for s, f in srcs[:5]
                                                 for u in (demo_url, signup_url, login_url)]))
    pages = [("/", 2400), ("/pricing", 640), (paths["what-is-product-analytics"], 520), (paths["retention-metrics-that-matter"], 310),
             ("/demo", 180), (paths["aeo-for-saas"], 170), (paths["funnel-benchmarks-2026"], 140)]
    write(ga / "pages.json", ga4(["pagePath"], ["screenPageViews", "totalUsers"], [[p, v, round(v * .7)] for p, v in pages]))
    write(ga / "countries.json", ga4(["country"], ["totalUsers", "sessions"],
                                    [[c, round(s_cur * f), round(s_cur * f * 1.25)] for c, f in
                                     [("United States", .42), ("United Kingdom", .1), ("Germany", .08), ("India", .08), ("Canada", .06), ("Australia", .04)]]))
    write(ga / "devices.json", ga4(["deviceCategory"], ["totalUsers", "sessions"],
                                  [["desktop", round(s_cur * .71), round(s_cur * .9)], ["mobile", round(s_cur * .27), round(s_cur * .33)], ["tablet", round(s_cur * .02), round(s_cur * .02)]]))

    cd, cad, ccd, cp, ce, ca, cc = [], [], [], {}, [], [], []
    for slug, url in paths.items():
        w = {"what-is-product-analytics": 9, "retention-metrics-that-matter": 6, "aeo-for-saas": 5, "funnel-benchmarks-2026": 7,
             "event-tracking-checklist": 3, "slack-alerts": 8}.get(slug, 1.5)
        for d in a_days:
            if d < published[slug]:
                continue
            fresh = 3 if (d - published[slug]).days < 5 else 1
            v = wave(d, w * fresh, noise=.5)
            if v:
                cd.append([fmt(d), url, v, round(v * .8)])
                rng = "current" if d >= c28 else "previous" if d >= c28 - dt.timedelta(days=28) else None
                if rng:
                    x = cp.setdefault((url, rng), [0, 0, 0]); x[0] += v; x[1] += round(v * .8); x[2] += v * 70
            ai = round(v * (.15 if slug == "aeo-for-saas" else .03) * rnd.uniform(0, 2))
            if ai:
                cad.append([fmt(d), url, ai])
            if v and rnd.random() < .08:
                ccd.append([fmt(d), url, demo_url, 1])
        views = sum(x[0] for (u, r), x in cp.items() if u == url and r == "current")
        if views:
            ce += [[url, "Organic Search", round(views * .4), round(views * .25)], [url, "Direct", round(views * .2), round(views * .1)]]
            ca += [[url, s, max(1, round(views * w_ * .1)), 0] for s, w_ in zip(AI_SOURCES[:3], (.6, .3, .1)) if rnd.random() < .7]
            cc += [[url, demo_url, rnd.randint(0, 4)], [url, signup_url, rnd.randint(0, 3)]]
    write(ga / "content-daily.json", ga4(["date", "pagePath"], ["screenPageViews", "totalUsers"], cd))
    write(ga / "content-ai-daily.json", ga4(["date", "landingPage"], ["sessions"], cad))
    write(ga / "content-clicks-daily.json", ga4(["date", "pagePath", "linkUrl"], ["eventCount"], ccd))
    write(ga / "content-pages.json", ga4(["pagePath", "dateRange"], ["screenPageViews", "totalUsers", "userEngagementDuration"],
                                        [[u, r, *x] for (u, r), x in cp.items()]))
    write(ga / "content-entrances.json", ga4(["landingPage", "sessionDefaultChannelGroup"], ["sessions", "engagedSessions"], ce))
    write(ga / "content-ai.json", ga4(["landingPage", "sessionSource"], ["sessions", "engagedSessions"], ca))
    write(ga / "content-clicks.json", ga4(["pagePath", "linkUrl"], ["eventCount"], cc))
    write(ga / "quota.json", {"tokens_this_run": 6})

    # ---- site changes for the "Traffic and what changed" markers ----
    changes = [{"date": (TODAY - dt.timedelta(days=d)).isoformat(), "kind": k, "text": t} for d, k, t in [
        (300, "site", "Site change: New homepage hero and pricing table"), (210, "site", "Site change: Added FAQ structured data"),
        (160, "edit", "Edited blog: What Is Product Analytics? A Plain-English Guide"), (150, "site", "Site change: Added GA4 tracking tag"),
        (95, "site", "Site change: Faster image loading on mobile"), (60, "edit", "Edited blog: 7 Retention Metrics That Actually Predict Growth"),
        (33, "site", "Site change: Allowed AI crawlers in robots.txt"), (12, "site", "Site change: New Book demo button on blog posts")]]
    write(WORK / "changes.json", changes)

    # ---- build the real dashboard from the sample data ----
    OUT.mkdir(exist_ok=True)
    env = {**os.environ, "HUB_AHREFS_DIR": str(WORK / "ahrefs"), "HUB_GOOGLE_DIR": str(WORK / "google"),
           "HUB_CONTENT_DIR": str(WORK / "content"), "HUB_CHANGES_FILE": str(WORK / "changes.json"),
           "HUB_OUT_DIR": str(OUT), "HUB_SKIP_SITEMAP": "1", "HUB_BRAND": BRAND, "HUB_BRAND_TERMS": "northwind analytics,northwind",
           "HUB_SITE_URL": SITE, "AHREFS_TARGET": DOMAIN, "AHREFS_API_KEY": "", "HUB_SIGNUP_LINKS": r"app\.northwind\.example/sign-?up",
           "HUB_LOGIN_LINKS": r"app\.northwind\.example"}
    subprocess.run([sys.executable, str(ROOT / "build_dashboard.py")], env=env, check=True, cwd=str(HERE))


if __name__ == "__main__":
    main()
