#!/usr/bin/env python3
"""Build the SEO/AEO hub dashboard (dashboard.html) from the latest saved data.

Usage:
  python3 build_dashboard.py

Reads the newest data/ahrefs/<date>/live/ folder, plus current API usage
(a free Ahrefs call; falls back to data/ahrefs/usage_log.csv if offline).
Spends no API units. Open dashboard.html in a browser afterwards.
"""
import csv
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fetch_ahrefs as fa  # noqa: E402
fa.load_env()  # .env settings (brand, site URL, folders) are read at import

AHREFS_DIR = Path(os.environ.get("HUB_AHREFS_DIR", HERE / "data" / "ahrefs"))
OUT = HERE / "dashboard.html"
PUBLIC = HERE / "public"
SHARE = HERE / "share"
TEMPLATE = HERE / "dashboard_template.html"
# Your brand, used in the page and in brand-search advice. Set HUB_BRAND in .env.
BRAND = os.environ.get("HUB_BRAND", "Your brand")


def latest_live_dir():
    dirs = sorted(p for p in AHREFS_DIR.glob("*/live") if any(p.glob("*.json")))
    return dirs[-1] if dirs else None


def read(d, name, key):
    """An endpoint from this pull, or from the newest earlier live pull that has it
    (slow lists like refdomains are only re-pulled every couple of weeks)."""
    f = d / f"{name}.json"
    if not f.exists():
        older = sorted(p for p in AHREFS_DIR.glob(f"*/live/{name}.json") if p.parent.parent.name < d.parent.name)
        if not older:
            return None
        f = older[-1]
    return json.loads(f.read_text()).get(key)


def usage_snapshot():
    fa.load_env()
    import os
    key = os.environ.get("AHREFS_API_KEY")
    u = fa.get_usage(key) if key else None
    if u:
        return {"source": "live", **u}
    if (AHREFS_DIR / "usage_log.csv").exists():
        rows = list(csv.DictReader((AHREFS_DIR / "usage_log.csv").open()))
        if rows:
            return {"source": "log", "units_usage_workspace": rows[-1]["month_total"]}
    return None


def usage_log():
    if not (AHREFS_DIR / "usage_log.csv").exists():
        return []
    return list(csv.DictReader((AHREFS_DIR / "usage_log.csv").open()))[-12:]


GOOGLE_DIR = Path(os.environ.get("HUB_GOOGLE_DIR", HERE / "data" / "google"))


def latest_google_dir(src):
    dirs = sorted(p for p in GOOGLE_DIR.glob(f"*/{src}") if p.parent.name[:1].isdigit() and any(p.glob("*.json")))
    return dirs[-1] if dirs else None


def jload(d, name):
    f = d / f"{name}.json"
    return json.loads(f.read_text()) if f.exists() else {}


def hfile(d, name):
    """The kept day-by-day history of a report (all days fetched so far), else this pull's file."""
    h = d.parent.parent / "history" / d.name / f"{name}.json"
    return h if h.exists() else d / f"{name}.json"


def hload(d, name):
    f = hfile(d, name) if d else None
    return json.loads(f.read_text()) if f and f.exists() else {}


def num(v):
    try:
        f = float(v)
        return int(f) if f.is_integer() else f
    except (TypeError, ValueError):
        return 0


def ga4_rows(report):
    """runReport -> list of dicts keyed by dimension/metric names (incl. dateRange)."""
    dims = [h["name"] for h in report.get("dimensionHeaders", [])]
    mets = [h["name"] for h in report.get("metricHeaders", [])]
    out = []
    for r in report.get("rows", []):
        row = {k: v["value"] for k, v in zip(dims, r.get("dimensionValues", []))}
        row.update({k: num(v["value"]) for k, v in zip(mets, r.get("metricValues", []))})
        out.append(row)
    return out


def split_ranges(rows, key):
    """Pivot rows with a dateRange dimension into {key: {current: row, previous: row}}."""
    out = {}
    for r in rows:
        out.setdefault(r[key], {})[r.get("dateRange", "current")] = r
    return out


def build_ga4():
    d = latest_google_dir("ga4")
    if not d:
        return None
    ch = split_ranges(ga4_rows(jload(d, "channels")), "sessionDefaultChannelGroup")
    ai = split_ranges(ga4_rows(jload(d, "ai-sources")), "sessionSource")

    def tot(groups, rng, metric):
        return sum(g.get(rng, {}).get(metric, 0) for g in groups.values())

    org = ch.get("Organic Search", {})
    ai_sources = sorted(
        [{"source": k, "sessions": v.get("current", {}).get("sessions", 0),
          "prev": v.get("previous", {}).get("sessions", 0),
          "engaged": v.get("current", {}).get("engagedSessions", 0),
          "keyEvents": v.get("current", {}).get("keyEvents", 0)} for k, v in ai.items()],
        key=lambda x: -x["sessions"])
    daily = lambda name: [{"date": f"{r['date'][:4]}-{r['date'][4:6]}-{r['date'][6:]}", "v": r["sessions"]}
                          for r in ga4_rows(hload(d, name))]
    return {
        "pulled": d.parent.name,
        "organic": {k: org.get(rng, {}).get(m, 0) for k, rng, m in [
            ("sessions", "current", "sessions"), ("sessionsPrev", "previous", "sessions"),
            ("keyEvents", "current", "keyEvents"), ("keyEventsPrev", "previous", "keyEvents"),
            ("engaged", "current", "engagedSessions")]},
        "allSessions": tot(ch, "current", "sessions"),
        "allSessionsPrev": tot(ch, "previous", "sessions"),
        "ai": {"sessions": tot(ai, "current", "sessions"), "sessionsPrev": tot(ai, "previous", "sessions"),
               "sources": ai_sources},
        "landingPages": [{"page": r["landingPage"], "sessions": r["sessions"], "engaged": r["engagedSessions"],
                          "keyEvents": r["keyEvents"]} for r in ga4_rows(jload(d, "organic-landing-pages"))],
        "organicDaily": daily("organic-daily"),
        "aiDaily": daily("ai-daily"),
        "tokens": jload(d, "quota").get("tokens_this_run"),
        "site": build_site(d),
    }


# Which link clicks count as which action. Checked in this order; override in .env.
ACTIONS = [
    ("demo", "Book demo", "HUB_DEMO_LINKS", r"calendly\.com|cal\.com|meetings\.hubspot\.com|/demo"),
    ("signup", "Sign up", "HUB_SIGNUP_LINKS", r"app\.example\.com/sign-?up"),
    ("login", "Log in", "HUB_LOGIN_LINKS", r"app\.example\.com"),
]


def classify(url):
    import re
    for key, _, env, default in ACTIONS:
        if re.search(os.environ.get(env, default), url or "", re.I):
            return key
    return None


def weekly(points):
    """Daily [{date, v}] -> Monday-start weeks [{start, end, v, days}]."""
    import datetime as dt
    weeks = {}
    for p in points:
        d = dt.date.fromisoformat(p["date"])
        start = d - dt.timedelta(days=d.weekday())
        w = weeks.setdefault(start, {"start": start.isoformat(), "end": d.isoformat(), "v": 0, "days": 0})
        w["v"] += p["v"]
        w["days"] += 1
        w["end"] = max(w["end"], d.isoformat())
    return [weeks[k] for k in sorted(weeks)]


def build_site(d):
    """Whole-site visitors, Book demo / Log in / Sign up clicks, sources, pages, places, devices."""
    if not (d / "visitors.json").exists():
        return None
    gdate = lambda s: f"{s[:4]}-{s[4:6]}-{s[6:]}"
    v = {r.get("dateRange", "current"): r for r in ga4_rows(jload(d, "visitors"))}
    cur, prev = v.get("current", {}), v.get("previous", {})
    daily = ga4_rows(hload(d, "visitors-daily"))

    actions = {k: {"label": label, "clicks": 0, "clicksPrev": 0, "people": 0, "links": {}}
               for k, label, _, _ in ACTIONS}
    for r in ga4_rows(jload(d, "link-clicks")):
        k = classify(r["linkUrl"])
        if not k:
            continue
        a = actions[k]
        if r.get("dateRange", "current") == "current":
            a["clicks"] += r["eventCount"]
            a["people"] += r["totalUsers"]  # people per link, summed: close enough for a few links
            a["links"][r["linkUrl"]] = a["links"].get(r["linkUrl"], 0) + r["eventCount"]
        else:
            a["clicksPrev"] += r["eventCount"]
    for a in actions.values():
        a["links"] = sorted(({"url": u, "clicks": c} for u, c in a["links"].items()), key=lambda x: -x["clicks"])

    action_daily = {k: {} for k in actions}
    for r in ga4_rows(hload(d, "link-clicks-daily")):
        k = classify(r["linkUrl"])
        if k:
            day = gdate(r["date"])
            action_daily[k][day] = action_daily[k].get(day, 0) + r["eventCount"]
    days = [gdate(r["date"]) for r in daily]
    action_daily = {k: [{"date": day, "v": m.get(day, 0)} for day in days] for k, m in action_daily.items()}

    by_source = {}
    for r in ga4_rows(jload(d, "link-clicks-by-source")):
        k = classify(r["linkUrl"])
        if k:
            s = by_source.setdefault(r["sessionSourceMedium"], {"source": r["sessionSourceMedium"], "demo": 0, "signup": 0, "login": 0})
            s[k] += r["eventCount"]

    src = split_ranges(ga4_rows(jload(d, "sources")), "sessionSourceMedium")
    sources = sorted(
        [{"source": k, "sessions": x.get("current", {}).get("sessions", 0),
          "prev": x.get("previous", {}).get("sessions", 0),
          "people": x.get("current", {}).get("totalUsers", 0),
          "engaged": x.get("current", {}).get("engagedSessions", 0),
          **{a: by_source.get(k, {}).get(a, 0) for a in ("demo", "signup", "login")}}
         for k, x in src.items()],
        key=lambda x: -x["sessions"])
    pages = [{"page": r["pagePath"], "views": r["screenPageViews"], "people": r["totalUsers"]}
             for r in ga4_rows(jload(d, "pages"))]
    page_people = lambda path: sum(p["people"] for p in pages if p["page"].rstrip("/") == path)
    return {
        "people": cur.get("totalUsers", 0), "peoplePrev": prev.get("totalUsers", 0),
        "newPeople": cur.get("newUsers", 0), "newPeoplePrev": prev.get("newUsers", 0),
        "sessions": cur.get("sessions", 0), "sessionsPrev": prev.get("sessions", 0),
        "engaged": cur.get("engagedSessions", 0), "engagedPrev": prev.get("engagedSessions", 0),
        "views": cur.get("screenPageViews", 0),
        "avgVisitSec": cur.get("averageSessionDuration", 0),
        "actions": actions,
        "pricingPeople": page_people("/pricing"),
        "demoPagePeople": page_people("/demo"),
        "daily": {"people": [{"date": gdate(r["date"]), "v": r["totalUsers"]} for r in daily],
                  "sessions": [{"date": gdate(r["date"]), "v": r["sessions"]} for r in daily],
                  **action_daily},
        "sources": sources,
        "sourcesWithActions": sorted([s for s in sources if s["demo"] or s["signup"] or s["login"]],
                                     key=lambda s: -(s["demo"] * 3 + s["signup"] * 2 + s["login"])),
        "pages": pages,
        "countries": [{"country": r["country"], "people": r["totalUsers"], "sessions": r["sessions"]}
                      for r in ga4_rows(jload(d, "countries"))],
        "devices": [{"device": r["deviceCategory"], "people": r["totalUsers"], "sessions": r["sessions"]}
                    for r in ga4_rows(jload(d, "devices"))],
        "firstDay": days[0] if days else None,
    }


def build_growth(data):
    """Weekly series and this-period-vs-last comparisons for the Growth section."""
    g, a = data.get("gsc"), data.get("ga4")
    site = a.get("site") if a else None
    weeks = {}
    if g:
        weeks["searchClicks"] = weekly([{"date": r["date"], "v": r["clicks"]} for r in g["daily"]])
        weeks["searchImpr"] = weekly([{"date": r["date"], "v": r["impressions"]} for r in g["daily"]])
    if site:
        weeks["visits"] = weekly(site["daily"]["sessions"])
        weeks["demo"] = weekly(site["daily"]["demo"])
        weeks["appClicks"] = weekly([{"date": x["date"], "v": x["v"] + y["v"]}
                                     for x, y in zip(site["daily"]["login"], site["daily"]["signup"])])
    compare = []
    if site:
        ac = site["actions"]
        compare += [
            ["Website visitors", site["people"], site["peoplePrev"], "people"],
            ["Book demo clicks", ac["demo"]["clicks"], ac["demo"]["clicksPrev"], "clicks"],
            ["Log in + sign up clicks", ac["login"]["clicks"] + ac["signup"]["clicks"],
             ac["login"]["clicksPrev"] + ac["signup"]["clicksPrev"], "clicks"],
        ]
    if g:
        compare += [["Google search clicks", g["cur"]["clicks"], g["prev"]["clicks"], "clicks"],
                    ["Google impressions", g["cur"]["impressions"], g["prev"]["impressions"], "times shown"]]
    if a:
        compare += [["Visits from AI assistants", a["ai"]["sessions"], a["ai"]["sessionsPrev"], "visits"]]
    return {"weeks": weeks, "compare": [{"label": c[0], "cur": c[1], "prev": c[2], "unit": c[3]} for c in compare]}


def build_strategies(data):
    """Plain-language SEO/AEO actions, each backed by a number from this week's data.

    Rules only fire when the data supports them, so the list changes as things improve.
    Each item: priority (1 high .. 3 low), area, title, why, steps, watch, see (section id).
    """
    out = []
    add = lambda pr, area, title, why, steps, watch, see: out.append(
        {"priority": pr, "area": area, "title": title, "why": why, "steps": steps, "watch": watch, "see": see})
    target = data.get("target") or "your site"
    m, bl = data.get("metrics") or {}, data.get("backlinks") or {}
    g, a = data.get("gsc"), data.get("ga4")
    site = a.get("site") if a else None
    fmt = lambda v: f"{v:,.0f}"

    # 1. Searches already close to page one
    if g and g["opportunities"]:
        top = g["opportunities"][:3]
        names = "; ".join(f'"{q["query"]}" (position {q["position"]:.1f}, seen {fmt(q["impressions"])} times)' for q in top)
        add(1, "SEO", "Push the searches that are almost on top",
            f"Google already shows you near the top for {len(g['opportunities'])} searches but few people click: {names}.",
            ["Find the page that ranks for each search (Search Console → Quick wins shows the search; the Pages table shows the page).",
             "Put the exact search words in that page's title and first heading, and answer the question in the first paragraph.",
             "Add 2–3 links to that page from your homepage and related pages, using the search words as the link text."],
            "Clicks and position for these searches in Search Console", "gsc")

    # 2. Pages seen a lot but rarely clicked
    if g:
        low = [p for p in g["pages"] if p["impressions"] >= 100 and p["ctr"] < 0.01][:3]
        if low:
            names = "; ".join(f'{p["page"].replace("https://", "")} ({fmt(p["impressions"])} views in Google, {p["ctr"] * 100:.1f}% clicked)' for p in low)
            add(1, "SEO", "Rewrite titles on pages people see but skip",
                f"These pages appear in Google often but almost nobody clicks: {names}.",
                ["Rewrite each page's title (the blue link in Google) so it says what the reader gets, under 60 characters.",
                 "Rewrite the description (the grey text under it) as a one-sentence promise with a number or outcome.",
                 "Check again in 4 weeks: the click-through rate should rise."],
                "Click-through rate for these pages in Search Console", "gsc")

    # 3. Strong links, almost no rankings
    if (bl.get("live_refdomains") or 0) > 100 and (m.get("org_keywords") or 0) < 50:
        add(1, "SEO", "Write pages for what buyers search",
            f"{fmt(bl['live_refdomains'])} websites link to {target}, which is a strong base, but Ahrefs finds you ranking for only "
            f"{fmt(m.get('org_keywords') or 0)} searches. Google trusts the site; it just has few pages that match what people type.",
            ["List the problems buyers search for (the jobs your product does, in their words) and check their monthly searches in Ahrefs Keywords Explorer.",
             "Turn each existing use-case page into a page built around one of those searches: the search words in the title, a clear answer, examples and a Book demo button.",
             "Publish 2 such pages a month and link each from the homepage or use-case menu."],
            "Organic keywords and organic traffic in the Overview", "keywords")

    # 4. Brand name not on top
    if g:
        terms = [t.strip().lower() for t in os.environ.get("HUB_BRAND_TERMS", BRAND.lower()).split(",") if t.strip()]
        brand = next((q for q in g["opportunities"] + g["queries"] if q["query"].strip().lower() in terms
                      and q["position"] > 3), None)
        if brand:
            add(2, "SEO", "Own your brand name in Google",
                f'People searching "{brand["query"]}" see you at position {brand["position"]:.1f} ({fmt(brand["impressions"])} times, '
                f'{fmt(brand["clicks"])} clicks). Other "{BRAND}" results come first.',
                ["Add Organization structured data to the homepage with your logo and links to LinkedIn, Crunchbase and G2.",
                 f"Use the same name everywhere (\"{BRAND}\") on the site, LinkedIn, G2 and directories.",
                 f"Ask partners and press to link with the words \"{BRAND}\"."],
                f'Position for "{brand["query"]}" in Search Console → Top searches', "gsc")

    # 5. Lost links from strong sites
    lost = [r for r in data.get("refdomains", []) if r.get("last_seen")]
    if lost:
        names = ", ".join(r["domain"] for r in lost[:5])
        add(2, "SEO", "Win back lost links",
            f"{len(lost)} of your strongest linking websites no longer link to you, including {names}.",
            ["Check each one: a profile, event or listing page that was removed or changed.",
             "Update or recreate the profile (for example a directory or event listing) and ask the site owner to restore the link.",
             "Start with the highest Domain Rating sites; they are worth the most."],
            "Referring domains in the Overview and the Lost status in Authority & links", "authority")

    # 6. www vs non-www
    hosts = {(p.get("url") or "").split("/")[2] for p in data.get("topPages", []) if (p.get("url") or "").count("/") >= 2}
    if len(hosts) > 1:
        add(2, "SEO", "Make one address the only address",
            f"Your pages rank under {len(hosts)} addresses ({', '.join(sorted(hosts))}), which can split ranking strength.",
            ["Pick one (usually the www address) and make the other redirect to it permanently (a 301 redirect, set in your website host).",
             "Make sure every page's canonical tag points to the chosen address.",
             "In Search Console, check that only the chosen address appears in the Pages table after a few weeks."],
            "Pages table in Search Console", "gsc")

    # 7. AEO: AI assistants
    if a:
        ai = a["ai"]["sessions"]
        answers = sum(1 for p in (site or {}).get("pages", []) if p["page"].startswith("/answers"))
        add(1 if ai < 10 else 2, "AEO", "Get recommended by AI assistants",
            (f"AI assistants (ChatGPT, Perplexity, Gemini, Claude, Copilot) sent {fmt(ai)} visits in the last 28 days. "
             + (f"You already have {answers} /answers pages, a good start. " if answers else "")
             + "AI assistants quote pages that answer one question clearly and that other sites mention."),
            ["Write answer-first pages for the questions buyers ask AI (\"best tools for X\", \"how to do Y\"): the answer in the first 2 sentences, then detail.",
             "Add FAQ and Organization structured data, and keep facts (pricing model, integrations, customers) the same on your site, G2, LinkedIn and Crunchbase.",
             "Make sure robots.txt does not block GPTBot, PerplexityBot, ClaudeBot or Google-Extended.",
             f"Get {BRAND} into \"best tools\" lists and comparison articles; AI answers lean on them.",
             f"Use Ahrefs Brand Radar (if your plan has it) to see when AI answers mention {BRAND}."],
            "Visits from AI assistants in Growth and Traffic & AI referrals", "ga4")

    # 8. Demo clicks from search
    if site:
        demo = site["actions"]["demo"]["clicks"]
        organic = next((s for s in site["sources"] if s["source"] == "google / organic"), None)
        if demo and organic and organic["demo"]:
            add(1, "Conversion", "Put Book demo where search visitors land",
                f"Google search brought {fmt(organic['demo'])} of your {fmt(demo)} Book demo clicks from only "
                f"{fmt(organic['sessions'])} visits, so search visitors are your best demo source.",
                ["Add a Book demo button near the top of the pages people land on from Google (see Traffic & AI referrals → Top pages from Google search).",
                 "Add one on the pricing page too: " + fmt(site["pricingPeople"]) + " people viewed pricing in the last 28 days.",
                 "Track the result: Book demo clicks per week in Growth."],
                "Book demo clicks per week in Growth", "visitors")
        if site["sessions"] and site["engaged"] / site["sessions"] < 0.4:
            add(2, "Conversion", "Keep more visitors past the first screen",
                f"Only {site['engaged'] / site['sessions'] * 100:.0f}% of visits were engaged: most visitors leave after one page "
                f"without really looking around.",
                [f"Make the homepage's first screen say who {BRAND} is for and what it does in one sentence, with Book demo next to it.",
                 "Check page speed on mobile with PageSpeed Insights and fix anything marked red.",
                 "Match each page's opening line to the search or ad that brought people there."],
                "Engaged visits in Visitors & actions", "visitors")

    # 9. Tracking gaps
    if a:
        steps = []
        if not a["organic"]["keyEvents"]:
            steps.append("In GA4 → Admin → Events, create an event from clicks on your booking links (name it book_demo) and mark it as a key event, so GA4 counts demo clicks as conversions.")
        if site and not site["actions"]["login"]["clicks"]:
            steps.append("Click your site's Log in button once and check GA4 → Reports → Realtime: if no click event appears, the log in link is not being tracked and needs a tag in Google Tag Manager.")
        if steps:
            add(3, "Tracking", "Close the gaps in what GA4 counts",
                "Some actions that matter are not counted as conversions yet, so reports undercount results.",
                steps, "Key events in Traffic & AI referrals; Log in clicks in Visitors & actions", "visitors")

    # 10. Content: posts Google hasn't picked up, and posts close to page one
    c = data.get("content")
    if c and c["hasContentSearch"]:
        import datetime as dt
        old = (dt.date.today() - dt.timedelta(days=14)).isoformat()
        unseen = [x for x in c["items"] if not x["scheduled"] and x["live"] is not False
                  and x["date"] and x["date"] <= old and not x["impressions"]]
        if unseen:
            names = "; ".join(f'"{x["title"]}"' for x in unseen[:3])
            add(2, "SEO", "Get Google to show posts it is ignoring",
                f"{len(unseen)} blog posts or release notes published over 2 weeks ago were not shown in Google once "
                f"in the last 28 days, including {names}.",
                ["In Search Console, paste each post's address into the search bar at the top (URL Inspection) and click Request indexing.",
                 "Link to each post from at least one page Google already shows (see Content → the posts with impressions).",
                 "If a post has no clear search it answers, retitle it around the question a buyer would type."],
                "Impressions per post in Content", "content")
        close = sorted([x for x in c["items"] if x["position"] and 4 <= x["position"] <= 20 and x["impressions"] >= 20],
                       key=lambda x: -x["impressions"])[:3]
        if close:
            names = "; ".join(f'"{x["title"]}" (position {x["position"]:.1f}, seen {fmt(x["impressions"])} times)' for x in close)
            add(2, "SEO", "Lift posts that are almost on page one",
                f"These posts already rank on page one or two of Google: {names}.",
                ["Open the post in Content to see the searches it is shown for, and put the top search in its title and first paragraph.",
                 "Answer that search in the first two sentences, so Google and AI assistants can quote it.",
                 "Link to the post from a related use-case page or a newer post."],
                "Position per post in Content", "content")

    out.sort(key=lambda s: s["priority"])
    return out


# ---------------------------------------------------------------- Content (blog + release notes)
# The list of posts comes from the marketing site's Markdown files, so every post shows up,
# including ones with no traffic yet. Override the folder with HUB_CONTENT_DIR.
CONTENT_DIR = Path(os.environ.get("HUB_CONTENT_DIR", HERE.parent / "website" / "src" / "content"))
CONTENT_KINDS = [("blog", "Blog", "/post/"), ("releases", "Release note", "/release-notes/")]
SITE_BASE = os.environ.get("HUB_SITE_URL", "https://example.com").rstrip("/")


def frontmatter(text):
    """The simple `key: value` frontmatter the marketing site uses (no nesting)."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    out = {}
    for line in text[3:end].splitlines():
        if ":" in line and not line.startswith((" ", "\t", "#")):
            k, v = line.split(":", 1)
            v = v.strip()
            if v.startswith("[") and v.endswith("]"):
                v = [t.strip().strip("\"'") for t in v[1:-1].split(",") if t.strip()]
            else:
                v = v.strip("\"'")
            out[k.strip()] = v
    return out


def content_inventory():
    import datetime as dt
    today = dt.date.today().isoformat()
    docs = []
    for folder, label, prefix in CONTENT_KINDS:
        for f in sorted((CONTENT_DIR / folder).glob("*.md")):
            fm = frontmatter(f.read_text(encoding="utf-8"))
            if str(fm.get("draft", "")).lower() == "true":
                continue
            date = str(fm.get("date", ""))[:10]
            docs.append({"kind": label, "path": prefix + f.stem, "title": fm.get("title") or f.stem,
                         "date": date, "author": fm.get("author", ""),
                         "tags": fm.get("tags") if isinstance(fm.get("tags"), list) else [],
                         "scheduled": bool(date) and date > today})
    return docs


def live_paths():
    """Paths in the live sitemap, to flag posts that are written but not deployed yet. None if offline."""
    if os.environ.get("HUB_SKIP_SITEMAP"):
        return None
    import re
    import urllib.request
    try:
        req = urllib.request.Request(SITE_BASE + "/sitemap.xml", headers={"User-Agent": "seo-aeo-hub"})
        xml = urllib.request.urlopen(req, timeout=10).read().decode("utf-8", "replace")
    except Exception:
        return None
    return {page_path(u) for u in re.findall(r"<loc>([^<]+)</loc>", xml)}


def page_path(url):
    """https://www.example.com/post/x/?a=1 -> /post/x (so GA4, Search Console and Ahrefs rows line up)."""
    import re
    p = re.sub(r"^https?://[^/]+", "", str(url or "")).split("?")[0].split("#")[0]
    return (p.rstrip("/") or "/").lower()


def build_content(data):
    docs = content_inventory()
    if not docs:
        return None
    g_dir, a_dir = latest_google_dir("gsc"), latest_google_dir("ga4")
    live = live_paths()
    rows = {d["path"]: {**d, "views": 0, "viewsPrev": 0, "people": 0, "engageSec": 0,
                        "entrances": 0, "fromSearch": 0, "fromAi": 0, "aiSources": {},
                        "demo": 0, "signup": 0,
                        "clicks": 0, "impressions": 0, "ctr": 0, "position": None,
                        "clicksPrev": 0, "impressionsPrev": 0, "queries": [],
                        "ahrefsTraffic": 0, "ahrefsKeywords": 0, "ahrefsTopKeyword": None,
                        "live": None if live is None else page_path(d["path"]) in live}
            for d in docs}
    get = lambda path: rows.get(page_path(path))

    if a_dir:
        for r in ga4_rows(jload(a_dir, "content-pages")):
            x = get(r["pagePath"])
            if not x:
                continue
            if r.get("dateRange", "current") == "current":
                x["views"] += r["screenPageViews"]
                x["people"] += r["totalUsers"]
                x["engageSec"] += r["userEngagementDuration"]
            else:
                x["viewsPrev"] += r["screenPageViews"]
        for r in ga4_rows(jload(a_dir, "content-entrances")):
            x = get(r["landingPage"])
            if x:
                x["entrances"] += r["sessions"]
                if r["sessionDefaultChannelGroup"] == "Organic Search":
                    x["fromSearch"] += r["sessions"]
        for r in ga4_rows(jload(a_dir, "content-ai")):
            x = get(r["landingPage"])
            if x:
                x["fromAi"] += r["sessions"]
                x["aiSources"][r["sessionSource"]] = x["aiSources"].get(r["sessionSource"], 0) + r["sessions"]
        for r in ga4_rows(jload(a_dir, "content-clicks")):
            x, k = get(r["pagePath"]), classify(r["linkUrl"])
            if x and k in ("demo", "signup"):
                x[k] += r["eventCount"]

    if g_dir:
        def add_pages(name, suffix):
            for r in jload(g_dir, name).get("rows", []):
                x = get(r["keys"][0])
                if not x:
                    continue
                # www and apex URLs both count: weight position by impressions when merging.
                imp = x["impressions" + suffix] + r["impressions"]
                if not suffix:
                    x["position"] = ((x["position"] or 0) * x["impressions"] + r["position"] * r["impressions"]) / imp if imp else r["position"]
                x["clicks" + suffix] += r["clicks"]
                x["impressions" + suffix] = imp
        add_pages("content-pages", "")
        add_pages("content-pages-previous", "Prev")
        for r in jload(g_dir, "content-queries").get("rows", []):
            x = get(r["keys"][0])
            if x:
                x["queries"].append({"query": r["keys"][1], "clicks": r["clicks"], "impressions": r["impressions"],
                                     "position": r["position"]})

    for p in data.get("topPages", []):
        x = get(p.get("url"))
        if x:
            x["ahrefsTraffic"] += p.get("sum_traffic") or 0
            x["ahrefsKeywords"] += p.get("keywords") or 0
            x["ahrefsTopKeyword"] = x["ahrefsTopKeyword"] or p.get("top_keyword")

    # Day-by-day series for each post's chart: {path: {metric: {date: value}}}.
    gdate = lambda s: f"{s[:4]}-{s[4:6]}-{s[6:]}"  # GA4 20261113 -> 2026-11-13
    daily, days = {}, set()
    def add_day(path, metric, day, v):
        x = get(path)
        if x:
            per = daily.setdefault(x["path"], {}).setdefault(metric, {})
            per[day] = per.get(day, 0) + v
            days.add(day)
    has_daily = bool(a_dir and hfile(a_dir, "content-daily").exists())
    if has_daily:
        for r in ga4_rows(hload(a_dir, "content-daily")):
            add_day(r["pagePath"], "views", gdate(r["date"]), r["screenPageViews"])
            add_day(r["pagePath"], "people", gdate(r["date"]), r["totalUsers"])
        for r in ga4_rows(hload(a_dir, "content-ai-daily")):
            add_day(r["landingPage"], "ai", gdate(r["date"]), r["sessions"])
        for r in ga4_rows(hload(a_dir, "content-clicks-daily")):
            k = classify(r["linkUrl"])
            if k in ("demo", "signup"):
                add_day(r["pagePath"], k, gdate(r["date"]), r["eventCount"])
    if g_dir and hfile(g_dir, "content-daily").exists():
        for r in hload(g_dir, "content-daily").get("rows", []):
            add_day(r["keys"][1], "clicks", r["keys"][0], r["clicks"])
            add_day(r["keys"][1], "impressions", r["keys"][0], r["impressions"])
    if days:
        import datetime as dt
        first, last = dt.date.fromisoformat(min(days)), dt.date.fromisoformat(max(days))
        axis = [(first + dt.timedelta(d)).isoformat() for d in range((last - first).days + 1)]
        for x in rows.values():
            per = daily.get(x["path"], {})
            # Start at the publish date when it falls inside the window, so new posts aren't mostly zeros.
            start = max(axis[0], x["date"]) if x["date"] else axis[0]
            span = [d for d in axis if d >= start]
            x["daily"] = {"start": span[0], **{m: [per.get(m, {}).get(d, 0) for d in span]
                                                for m in ("views", "people", "ai", "demo", "signup",
                                                          "clicks", "impressions")}} if span else None

    out = []
    for x in rows.values():
        x["ctr"] = x["clicks"] / x["impressions"] if x["impressions"] else 0
        x["queries"].sort(key=lambda q: (-q["clicks"], -q["impressions"]))
        x["queryCount"] = len(x["queries"])
        x["queries"] = x["queries"][:10]
        x["aiSources"] = sorted(({"source": k, "sessions": v} for k, v in x["aiSources"].items()), key=lambda s: -s["sessions"])
        out.append(x)
    out.sort(key=lambda x: x["date"], reverse=True)
    published = [x for x in out if not x["scheduled"]]
    total = lambda k, xs=published: sum(x[k] for x in xs)
    queries = sorted(({"path": x["path"], "title": x["title"], **q} for x in published for q in x["queries"]),
                     key=lambda q: (-q["clicks"], -q["impressions"]))
    ai_visits = sorted(({"path": x["path"], "title": x["title"], **s} for x in published for s in x["aiSources"]),
                       key=lambda s: -s["sessions"])
    return {
        "items": out,
        "counts": {"blog": sum(1 for x in published if x["kind"] == "Blog"),
                   "releases": sum(1 for x in published if x["kind"] == "Release note"),
                   "scheduled": sum(1 for x in out if x["scheduled"]),
                   "notLive": sum(1 for x in published if x["live"] is False)},
        "totals": {k: total(k) for k in ("views", "viewsPrev", "people", "clicks", "clicksPrev", "impressions",
                                         "impressionsPrev", "fromSearch", "fromAi", "demo", "signup")},
        "queries": queries[:200],
        "aiVisits": ai_visits,
        "hasContentData": bool(a_dir and (a_dir / "content-pages.json").exists()),
        "hasContentSearch": bool(g_dir and (g_dir / "content-pages.json").exists()),
        "hasDaily": has_daily,
        "hasDailySearch": bool(g_dir and hfile(g_dir, "content-daily").exists()),
        "liveChecked": live is not None,
        "source": f"{CONTENT_DIR.parent.name}/{CONTENT_DIR.name}",  # folder name only, no local path
    }


# ---------------------------------------------------------------- Site changes (for the traffic chart)
# What changed on the site each day, from the website's git repo: new posts (by publish date),
# edited posts and other site changes (by the day they landed on main, i.e. went live).
MARKETING_REPO = Path(os.environ.get("HUB_SITE_REPO", CONTENT_DIR.parent.parent))
SITE_FILES = ("public/site/", "src/components/", "src/pages/", "src/App.tsx", "src/index.css", "src/blog.css",
              "index.html", "public/llms.txt", "public/robots.txt", "public/sitemap.xml")


def git_changes(since):
    """[(date, subject, [(status, path)])] for each change that landed on main since `since`."""
    import subprocess
    git = lambda *a: subprocess.run(["git", "-C", str(MARKETING_REPO), *a], capture_output=True, text=True, timeout=30)
    ref = next((r for r in ("origin/main", "main") if git("rev-parse", "-q", "--verify", r).returncode == 0), None)
    if not ref:
        return None, None
    # --first-parent -m: one entry per change on main, diffed against main's previous state,
    # dated when it reached main (a merged branch counts on its merge day).
    out = git("log", ref, "--first-parent", "-m", f"--since={since}", "--name-status", "--date=short",
              "--format=\x1e%cd\x1f%s\x1f%b").stdout
    commits = []
    for chunk in out.split("\x1e")[1:]:
        head, _, files = chunk.partition("\n\n") if "\x1f" in chunk else (chunk, "", "")
        parts = head.split("\x1f")
        day, subject, body = parts[0].strip(), parts[1].strip(), (parts[2] if len(parts) > 2 else "").strip()
        if subject.startswith("Merge pull request") and body:
            subject = body.splitlines()[0]  # GitHub puts the PR title in the body
        elif subject.startswith("Merge branch"):
            import re
            m = re.search(r"Merge branch '([^']+)'", subject)
            subject = "Merged " + (m.group(1) if m else "branch")
        changed = [tuple(l.split("\t")[:2]) for l in files.splitlines() if "\t" in l]
        commits.append((day, subject, changed))
    return commits, ref


def build_changes(data):
    import datetime as dt
    content = data.get("content") or {}
    titles = {x["path"]: x["title"] for x in content.get("items", [])}
    since = (dt.date.today() - dt.timedelta(days=500)).isoformat()  # as far back as Search Console history
    days = {}
    add = lambda day, kind, text: days.setdefault(day, []).append({"kind": kind, "text": text}) \
        if {"kind": kind, "text": text} not in days.get(day, []) else None
    for x in content.get("items", []):
        if x["date"] and x["date"] >= since and not x["scheduled"] and x["live"] is not False:
            add(x["date"], "post", ("New release note: " if x["kind"] == "Release note" else "New blog post: ") + x["title"])
    if os.environ.get("HUB_CHANGES_FILE"):  # a JSON list of {date, kind, text}, e.g. for the demo
        for c in json.loads(Path(os.environ["HUB_CHANGES_FILE"]).read_text()):
            add(c["date"], c["kind"], c["text"])
        return {"days": [{"date": d, "items": days[d]} for d in sorted(days)], "source": "sample changes"}
    commits, ref = git_changes(since)
    for day, subject, changed in commits or []:
        site = False
        for status, path in changed:
            for folder, label, prefix in CONTENT_KINDS:
                if path.startswith(f"src/content/{folder}/") and path.endswith(".md"):
                    if status.startswith("M"):
                        slug = prefix + Path(path).stem
                        add(day, "edit", f"Edited {label.lower()}: " + titles.get(slug, Path(path).stem))
                    break  # new posts are shown on their publish date instead
            else:
                if path.startswith(SITE_FILES):
                    site = True
        if site:
            add(day, "site", "Site change: " + subject)
    return {"days": [{"date": d, "items": days[d]} for d in sorted(days)], "source": ref and f"{MARKETING_REPO.name} {ref}"}


def build_gsc():
    d = latest_google_dir("gsc")
    if not d:
        return None
    def total(name):
        rows = jload(d, name).get("rows", [])
        return rows[0] if rows else {"clicks": 0, "impressions": 0, "ctr": 0, "position": 0}
    queries = [{"query": r["keys"][0], "clicks": r["clicks"], "impressions": r["impressions"],
                "ctr": r["ctr"], "position": r["position"]} for r in jload(d, "queries").get("rows", [])]
    # Opportunities: already close to page one, seen often, but rarely clicked.
    opps = sorted([q for q in queries if 4 <= q["position"] <= 15 and q["impressions"] >= 20],
                  key=lambda q: -q["impressions"])[:25]
    return {
        "pulled": d.parent.name,
        "ranges": jload(d, "ranges"),
        "cur": total("totals-current"),
        "prev": total("totals-previous"),
        "daily": [{"date": r["keys"][0], "clicks": r["clicks"], "impressions": r["impressions"]}
                  for r in hload(d, "daily").get("rows", [])],
        "queries": queries[:200],
        "queryCount": len(queries),
        "opportunities": opps,
        "pages": [{"page": r["keys"][0], "clicks": r["clicks"], "impressions": r["impressions"],
                   "ctr": r["ctr"], "position": r["position"]} for r in jload(d, "pages").get("rows", [])][:100],
    }


def main():
    d = latest_live_dir()
    if not d:
        sys.exit("No live Ahrefs data yet. Run: python3 fetch_ahrefs.py --live")
    import os
    fa.load_env()
    data = {
        "target": os.environ.get("AHREFS_TARGET", ""),
        "pulled": d.parent.name,
        "domainRating": read(d, "domain-rating", "domain_rating"),
        "backlinks": read(d, "backlinks-stats", "metrics"),
        "metrics": read(d, "metrics", "metrics"),
        "topPages": read(d, "top-pages", "pages") or [],
        "keywords": read(d, "organic-keywords", "keywords") or [],
        "refdomains": read(d, "refdomains", "refdomains") or [],
        "usage": usage_snapshot(),
        "usageLog": usage_log(),
        "budget": int(os.environ.get("AHREFS_MONTHLY_BUDGET", 20000)),
    }
    data["brand"], data["siteUrl"] = BRAND, SITE_BASE
    # When each source was last pulled, for the "Data last updated" strip at the top.
    import datetime as _dt
    ref = sorted(x.parent.parent.name for x in AHREFS_DIR.glob("*/live/refdomains.json") if x.parent.parent.name <= d.parent.name)
    data["freshness"] = {"built": _dt.datetime.now().isoformat(timespec="minutes"),
                         "refdomainsPulled": ref[-1] if ref else None,
                         "refresh": os.environ.get("HUB_REFRESH_NOTE", "Refreshes every Friday morning")}
    data["gsc"] = build_gsc()
    data["ga4"] = build_ga4()
    data["content"] = build_content(data)
    data["changes"] = build_changes(data)
    data["growth"] = build_growth(data)
    data["strategies"] = build_strategies(data)
    data["sources"] = {"ahrefs": True, "gsc": bool(data["gsc"]), "ga4": bool(data["ga4"]),
                       "content": bool(data["content"])}
    blob = json.dumps(data).replace("</", "<\\/")
    html = TEMPLATE.read_text().replace("__DATA__", blob)
    if os.environ.get("HUB_OUT_DIR"):  # used for testing with sample data
        out = Path(os.environ["HUB_OUT_DIR"]) / "index.html"
        out.write_text(html)
        print(f"Built {out}")
        return
    OUT.write_text(html)
    # public/ holds only the page, so a local server never exposes .env or data/
    PUBLIC.mkdir(exist_ok=True)
    (PUBLIC / "index.html").write_text(html)
    # share/ holds the copy published as the team's private claude.ai link. The
    # Artifact host adds its own <html>/<head>/<body>, so those wrapper tags go.
    SHARE.mkdir(exist_ok=True)
    drop = ("<!doctype html>", '<html lang="en">', "<head>", "</head>", "<body>", "</body>", "</html>",
            '<meta charset="utf-8">', '<meta name="viewport" content="width=device-width, initial-scale=1">')
    (SHARE / "seo-aeo-hub.html").write_text(
        "\n".join(l for l in html.splitlines() if l.strip() not in drop) + "\n")
    print(f"Built {OUT} (and {PUBLIC / 'index.html'}, {SHARE / 'seo-aeo-hub.html'}) from {d}")


if __name__ == "__main__":
    main()
