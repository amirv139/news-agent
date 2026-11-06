"""
Morning News Agent
------------------
1. Collects fresh headlines from free RSS feeds (Toronto/Ontario, Canada, world, tech).
2. Sends them to Google Gemini (free tier) in ONE request to pick, group and explain the news.
3. Writes a phone-friendly web page to docs/index.html (served by GitHub Pages).

Runs every morning on GitHub Actions (see .github/workflows/daily-news.yml).
You can also run it on your own computer:  GEMINI_API_KEY=your_key python news_agent.py
"""

import os
import re
import json
import time
import html
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import feedparser
import requests

# ---------------------------------------------------------------------------
# SETTINGS - change these to make the agent yours
# ---------------------------------------------------------------------------

TIMEZONE = ZoneInfo("America/Toronto")
HOURS_BACK = 30            # only keep stories published in the last 30 hours
MAX_PER_FEED = 10          # how many stories to read from each feed
MAX_ITEMS_TO_AI = 160      # cap so the request stays small (free tier friendly)

# "flash-lite" has the biggest free daily quota. You can change the model
# name later in GitHub Secrets/Variables without touching the code.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-lite-latest")

# Google News RSS lets you build a feed for ANY search. Format:
def google_news(query: str) -> str:
    q = requests.utils.quote(query)
    return f"https://news.google.com/rss/search?q={q}+when:1d&hl=en-CA&gl=CA&ceid=CA:en"

FEEDS = {
    "Toronto & Ontario": [
        google_news("Ontario manufacturing"),
        google_news("Toronto business"),
        google_news("GTA factory OR plant jobs"),
    ],
    "Canada business & manufacturing": [
        "https://www.cbc.ca/webfeed/rss/rss-business",
        "https://www.canadianmanufacturing.com/feed/",
        "https://www.plant.ca/feed/",
        "https://financialpost.com/feed",
        google_news("Canada manufacturing"),
        google_news("Canada tariffs trade"),
    ],
    "World business & manufacturing": [
        "https://www.manufacturingdive.com/feeds/news/",
        "https://www.industryweek.com/rss",
        google_news("global manufacturing industry"),
        google_news("supply chain manufacturing"),
    ],
    "Tech": [
        "https://www.cbc.ca/webfeed/rss/rss-technology",
        "https://betakit.com/feed/",
        "https://techcrunch.com/feed/",
        "https://www.theverge.com/rss/index.xml",
        google_news("AI industrial automation"),
    ],
}

READER_PROFILE = """
The reader lives in the Toronto area (Vaughan / Newmarket) and reads this on his phone
every morning. Business news is his top priority, especially MANUFACTURING in Canada,
Ontario and Toronto (factories, machining, automation, auto parts, plant openings and
closures, jobs, tariffs and trade). Tech news is his second priority.
He is not an economist: explain in simple, clear English and briefly explain any
business term that might be unfamiliar.
"""

OUT_DIR = Path("docs")

# ---------------------------------------------------------------------------
# STEP 1 - collect headlines
# ---------------------------------------------------------------------------

def clean(text: str) -> str:
    """Remove HTML tags and extra spaces from feed text."""
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def collect_news() -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=HOURS_BACK)
    items, seen = [], set()

    for section, urls in FEEDS.items():
        for url in urls:
            try:
                feed = feedparser.parse(url, agent="Mozilla/5.0 (news-agent)")
            except Exception as err:          # one broken feed shouldn't stop the agent
                print(f"Skipping {url}: {err}")
                continue

            source = clean(feed.feed.get("title", "")) or url
            for entry in feed.entries[:MAX_PER_FEED]:
                title = clean(entry.get("title", ""))
                key = re.sub(r"\W+", "", title.lower())[:70]
                if not title or key in seen:
                    continue

                stamp = entry.get("published_parsed") or entry.get("updated_parsed")
                if stamp and datetime(*stamp[:6], tzinfo=timezone.utc) < cutoff:
                    continue                    # too old

                seen.add(key)
                items.append({
                    "id": len(items),
                    "feed_section": section,
                    "source": source,
                    "title": title,
                    "snippet": clean(entry.get("summary", ""))[:280],
                    "link": entry.get("link", ""),
                })
            print(f"{len(items):4d} items so far  <- {source}")

    return items[:MAX_ITEMS_TO_AI]

# ---------------------------------------------------------------------------
# STEP 2 - ask the AI to choose and explain the news
# ---------------------------------------------------------------------------

PROMPT = """You are my personal morning news editor.
{profile}

Below are today's headlines as JSON (each has an id). Using ONLY these headlines:
1. Write a short "overview": 3-4 sentences on the big picture this morning.
2. Group the most important stories into these sections, in this order:
   "Toronto & Ontario", "Canada business & manufacturing",
   "World business & manufacturing", "Tech".
   Pick 3-6 stories per section (fewer if there isn't enough real news).
   Merge headlines about the same event into one story.
   Skip celebrity news, sports, opinion fluff and ads.
3. For each story give: a clear headline, a 2-3 sentence summary in plain English,
   one sentence on why it matters to someone working in Ontario, and the ids of
   the headlines you used.
4. Add one "term_of_the_day": a business or manufacturing term from today's news,
   explained simply.
Never invent facts that are not in the headlines.

Reply with JSON only, in exactly this shape:
{{"overview": "...",
  "sections": [{{"title": "...", "stories": [
      {{"headline": "...", "summary": "...", "why_it_matters": "...", "ids": [0, 4]}}]}}],
  "term_of_the_day": {{"term": "...", "explanation": "..."}}}}

HEADLINES:
{headlines}
"""


def ask_gemini(items: list[dict]) -> dict:
    api_key = os.environ["GEMINI_API_KEY"]
    compact = [{k: it[k] for k in ("id", "feed_section", "source", "title", "snippet")}
               for it in items]
    prompt = PROMPT.format(profile=READER_PROFILE,
                           headlines=json.dumps(compact, ensure_ascii=False))

    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{GEMINI_MODEL}:generateContent")
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.3,
                             "responseMimeType": "application/json"},
    }

    for attempt in range(4):                 # retry if Google is busy (429/503)
        resp = requests.post(url, json=body, timeout=120,
                             headers={"x-goog-api-key": api_key})
        if resp.status_code in (429, 500, 503):
            wait = 30 * (attempt + 1)
            print(f"Gemini busy ({resp.status_code}), retrying in {wait}s")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text)

    raise RuntimeError("Gemini did not answer after 4 tries")

# ---------------------------------------------------------------------------
# STEP 3 - build the web page
# ---------------------------------------------------------------------------

CSS = """
:root{--bg:#f6f5f2;--card:#fff;--text:#1d1d1f;--muted:#6b6b70;--accent:#c2410c;--line:#e6e4df}
@media (prefers-color-scheme:dark){:root{--bg:#141414;--card:#1f1f21;--text:#ececec;--muted:#9a9aa0;--accent:#fb923c;--line:#2e2e31}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);
font:17px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
main{max-width:680px;margin:0 auto;padding:20px 16px 60px}
h1{font-size:1.6rem;margin:0}.date{color:var(--muted);margin:2px 0 18px}
.overview{background:var(--card);border-left:4px solid var(--accent);padding:14px 16px;border-radius:10px}
h2{font-size:1.15rem;margin:30px 0 10px;color:var(--accent)}
.story{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin:10px 0}
.story h3{font-size:1.05rem;margin:0 0 6px}.story p{margin:6px 0}
.why{color:var(--muted);font-size:.95rem}.links a{font-size:.85rem;color:var(--accent);margin-right:10px}
.term{margin-top:30px;padding:14px 16px;border:1px dashed var(--line);border-radius:12px}
footer{margin-top:30px;color:var(--muted);font-size:.85rem}a{color:var(--accent)}
"""


def page(title: str, body: str) -> str:
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><style>{CSS}</style></head>
<body><main>{body}</main></body></html>"""


def render(digest: dict | None, items: list[dict], now: datetime) -> str:
    e = html.escape
    by_id = {it["id"]: it for it in items}
    parts = [f"<h1>Morning Brief</h1><p class='date'>{now:%A, %B %d, %Y}</p>"]

    if digest:
        parts.append(f"<div class='overview'>{e(digest.get('overview', ''))}</div>")
        for section in digest.get("sections", []):
            parts.append(f"<h2>{e(section.get('title', ''))}</h2>")
            for s in section.get("stories", []):
                links = " ".join(
                    f"<a href='{e(by_id[i]['link'])}'>{e(by_id[i]['source'][:40])}</a>"
                    for i in s.get("ids", []) if i in by_id)
                parts.append(
                    f"<div class='story'><h3>{e(s.get('headline', ''))}</h3>"
                    f"<p>{e(s.get('summary', ''))}</p>"
                    f"<p class='why'>Why it matters: {e(s.get('why_it_matters', ''))}</p>"
                    f"<div class='links'>{links}</div></div>")
        term = digest.get("term_of_the_day") or {}
        if term.get("term"):
            parts.append(f"<div class='term'><strong>Term of the day: {e(term['term'])}</strong>"
                         f"<p>{e(term.get('explanation', ''))}</p></div>")
    else:
        # Backup plan: if the AI failed, still show the raw headlines.
        parts.append("<div class='overview'>The AI summary failed today, "
                     "so here are the raw headlines.</div>")
        for section in FEEDS:
            parts.append(f"<h2>{e(section)}</h2>")
            for it in [x for x in items if x["feed_section"] == section][:12]:
                parts.append(f"<div class='story'><h3><a href='{e(it['link'])}'>"
                             f"{e(it['title'])}</a></h3><p class='why'>{e(it['source'])}</p></div>")

    parts.append(f"<footer>Updated {now:%I:%M %p} Toronto time · "
                 f"{len(items)} headlines read · <a href='archive/'>Past briefs</a></footer>")
    return page(f"Morning Brief {now:%b %d}", "".join(parts))


def save(page_html: str, now: datetime) -> None:
    archive = OUT_DIR / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "index.html").write_text(page_html, encoding="utf-8")
    # archive copy (its "archive/" link should point to the same folder)
    (archive / f"{now:%Y-%m-%d}.html").write_text(
        page_html.replace("href='archive/'", "href='./'"), encoding="utf-8")

    days = sorted((p.stem for p in archive.glob("20*.html")), reverse=True)
    rows = "".join(f"<p><a href='{d}.html'>{d}</a></p>" for d in days)
    (archive / "index.html").write_text(
        page("Past briefs", f"<h1>Past briefs</h1><p><a href='../'>Today</a></p>{rows}"),
        encoding="utf-8")

# ---------------------------------------------------------------------------

def main() -> None:
    now = datetime.now(TIMEZONE)
    items = collect_news()
    print(f"Collected {len(items)} headlines")

    digest = None
    try:
        digest = ask_gemini(items)
    except Exception as err:
        print(f"AI step failed, using headline-only page: {err}")

    save(render(digest, items, now), now)
    print("Saved docs/index.html")


if __name__ == "__main__":
    main()
