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
   Every section MUST contain 3-6 stories. Each section has plenty of headlines
   (see "feed_section"), so never leave a section empty or with only 1 story.
   Aim for 14-20 stories in total.
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


# Forces Gemini to answer in exactly this shape, so the JSON is always valid.
RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "overview": {"type": "STRING"},
        "sections": {"type": "ARRAY", "items": {
            "type": "OBJECT",
            "properties": {
                "title": {"type": "STRING"},
                "stories": {"type": "ARRAY", "items": {
                    "type": "OBJECT",
                    "properties": {
                        "headline": {"type": "STRING"},
                        "summary": {"type": "STRING"},
                        "why_it_matters": {"type": "STRING"},
                        "ids": {"type": "ARRAY", "items": {"type": "INTEGER"}},
                    },
                    "required": ["headline", "summary", "why_it_matters", "ids"],
                }},
            },
            "required": ["title", "stories"],
        }},
        "term_of_the_day": {"type": "OBJECT", "properties": {
            "term": {"type": "STRING"}, "explanation": {"type": "STRING"}},
            "required": ["term", "explanation"]},
    },
    "required": ["overview", "sections", "term_of_the_day"],
}


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
                             "maxOutputTokens": 8192,
                             "responseMimeType": "application/json",
                             "responseSchema": RESPONSE_SCHEMA},
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

MIN_STORIES = 10          # if Gemini returns fewer, ask once more


def story_count(digest: dict) -> int:
    return sum(len(sec.get("stories") or []) for sec in digest.get("sections") or [])

# ---------------------------------------------------------------------------
# STEP 3 - build the web page
# ---------------------------------------------------------------------------

CSS = """
/* Always dark, warm and dim - made for reading early in the morning */
:root{
  --bg:#141210;          /* warm near-black */
  --bg-glow:#1d1915;
  --card:#1e1b18;
  --card-hi:#25211d;
  --text:#d9d1c5;        /* soft cream, not bright white */
  --soft:#b3aa9d;
  --muted:#857c71;
  --accent:#d9a066;      /* gentle amber */
  --accent-dim:rgba(217,160,102,.13);
  --line:rgba(255,235,210,.06);
  color-scheme:dark;
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;min-height:100vh;color:var(--text);
  background:radial-gradient(120% 60% at 50% 0%,var(--bg-glow) 0%,var(--bg) 60%) fixed,var(--bg);
  font:17px/1.7 -apple-system,BlinkMacSystemFont,"SF Pro Text","Segoe UI",Roboto,sans-serif;
  -webkit-font-smoothing:antialiased}
main{max-width:640px;margin:0 auto;padding:34px 16px 70px}
a{color:var(--accent);text-decoration:none}

.hello{font-size:.95rem;color:var(--muted);margin:0;letter-spacing:.2px}
h1{font-size:1.85rem;font-weight:650;margin:2px 0 4px;letter-spacing:-.3px;color:var(--text)}
.meta{color:var(--muted);font-size:.9rem;margin:0 0 24px}

.overview{position:relative;background:linear-gradient(145deg,#2a221a 0%,var(--card) 70%);
  border-radius:24px;padding:20px 20px 20px;color:var(--text);
  box-shadow:0 10px 30px rgba(0,0,0,.25)}
.overview .label{display:inline-block;font-size:.75rem;font-weight:600;letter-spacing:.6px;
  text-transform:uppercase;color:var(--accent);margin-bottom:6px}
.overview p{margin:0}

.section{display:flex;align-items:center;gap:10px;margin:40px 4px 14px}
.section .icon{width:38px;height:38px;border-radius:14px;background:var(--accent-dim);
  display:grid;place-items:center;font-size:1.15rem}
.section h2{font-size:1.1rem;font-weight:600;margin:0;color:var(--text)}

.story{background:var(--card);border-radius:22px;padding:18px 20px;margin:12px 0;
  border:1px solid var(--line);box-shadow:0 6px 20px rgba(0,0,0,.18);
  animation:rise .5s ease-out both}
.story h3{font-size:1.05rem;line-height:1.45;font-weight:600;margin:0 0 8px;color:var(--text)}
.story p{margin:0 0 10px;color:var(--soft)}
.why{display:flex;gap:10px;background:var(--card-hi);border-radius:14px;padding:10px 12px;
  font-size:.92rem;color:var(--soft);margin:12px 0 4px}
.why span{flex:none}
.links{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}
.links a{font-size:.8rem;padding:6px 12px;border-radius:999px;background:var(--accent-dim);
  color:var(--accent);transition:background .25s ease}
.links a:active,.links a:hover{background:rgba(217,160,102,.22)}

.term{margin-top:40px;border-radius:22px;padding:18px 20px;background:var(--card);
  border:1px dashed rgba(217,160,102,.25)}
.term .label{font-size:.75rem;font-weight:600;letter-spacing:.6px;text-transform:uppercase;color:var(--accent)}
.term h3{margin:4px 0 6px;font-size:1.05rem}
.term p{margin:0;color:var(--soft)}

.day{display:flex;justify-content:space-between;align-items:center;background:var(--card);
  border-radius:18px;padding:14px 18px;margin:10px 0;color:var(--text);border:1px solid var(--line)}
.day span{color:var(--muted);font-size:.9rem}

footer{margin-top:44px;text-align:center;color:var(--muted);font-size:.85rem;line-height:1.9}
.btn{display:inline-block;margin-top:8px;padding:10px 20px;border-radius:999px;
  background:var(--accent-dim);color:var(--accent);font-weight:500}

@keyframes rise{from{opacity:.35;transform:translateY(8px)}to{opacity:1;transform:none}}
@media (prefers-reduced-motion:reduce){.story{animation:none}}
"""

SECTION_ICONS = {"toronto": "🏙️", "canada": "🍁", "world": "🌍", "tech": "💡"}


def section_header(title: str) -> str:
    icon = next((v for k, v in SECTION_ICONS.items() if k in title.lower()), "📰")
    return (f"<div class='section'><div class='icon'>{icon}</div>"
            f"<h2>{html.escape(title)}</h2></div>")


def greeting(now: datetime) -> str:
    if now.hour < 12:
        return "Good morning ☕"
    if now.hour < 18:
        return "Good afternoon"
    return "Good evening"


def page(title: str, body: str) -> str:
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#141210">
<meta name="color-scheme" content="dark">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Morning Brief">
<title>{html.escape(title)}</title><style>{CSS}</style></head>
<body><main>{body}</main></body></html>"""


def render(digest: dict | None, items: list[dict], now: datetime) -> str:
    e = html.escape
    by_id = {it["id"]: it for it in items}

    stories = sum(len(s.get("stories", [])) for s in (digest or {}).get("sections", []))
    minutes = max(2, round(stories * 0.6)) if stories else None
    meta = f"{now:%A, %B} {now.day}" + (f" · {stories} stories · ~{minutes} min read" if stories else "")
    parts = [f"<p class='hello'>{greeting(now)}</p><h1>Your Morning Brief</h1>"
             f"<p class='meta'>{meta}</p>"]

    if digest:
        parts.append(f"<div class='overview'><div class='label'>Today at a glance</div>"
                     f"<p>{e(digest.get('overview', ''))}</p></div>")
        delay = 0
        for section in digest.get("sections", []):
            if not section.get("stories"):
                continue                      # hide empty sections
            parts.append(section_header(section.get("title", "")))
            for s in section.get("stories", []):
                links = "".join(
                    f"<a href='{e(by_id[i]['link'])}'>{e(by_id[i]['source'][:32])} ↗</a>"
                    for i in s.get("ids", []) if i in by_id)
                parts.append(
                    f"<div class='story' style='animation-delay:{min(delay, 8) * 0.05:.2f}s'>"
                    f"<h3>{e(s.get('headline', ''))}</h3>"
                    f"<p>{e(s.get('summary', ''))}</p>"
                    f"<div class='why'><span>👉</span><div>{e(s.get('why_it_matters', ''))}</div></div>"
                    f"<div class='links'>{links}</div></div>")
                delay += 1
        term = digest.get("term_of_the_day") or {}
        if term.get("term") and term.get("explanation"):
            parts.append(f"<div class='term'><div class='label'>📘 Term of the day</div>"
                         f"<h3>{e(term['term'])}</h3><p>{e(term.get('explanation', ''))}</p></div>")
    else:
        # Backup plan: if the AI failed, still show the raw headlines.
        parts.append("<div class='overview'><div class='label'>Heads up</div>"
                     "<p>The AI summary didn't work today, so here are the raw headlines.</p></div>")
        for section in FEEDS:
            parts.append(section_header(section))
            for it in [x for x in items if x["feed_section"] == section][:12]:
                parts.append(f"<div class='story'><h3><a href='{e(it['link'])}'>"
                             f"{e(it['title'])}</a></h3><p>{e(it['source'])}</p></div>")

    parts.append(f"<footer>Updated {now:%-I:%M %p} · {len(items)} headlines read<br>"
                 f"<a class='btn' href='archive/'>📅 Past briefs</a></footer>")
    return page(f"Morning Brief · {now:%b} {now.day}", "".join(parts))


def save(page_html: str, now: datetime) -> None:
    archive = OUT_DIR / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "index.html").write_text(page_html, encoding="utf-8")
    # archive copy (its "archive/" link should point to the same folder)
    (archive / f"{now:%Y-%m-%d}.html").write_text(
        page_html.replace("href='archive/'", "href='./'"), encoding="utf-8")

    days = sorted((p.stem for p in archive.glob("20*.html")), reverse=True)
    rows = "".join(
        f"<a class='day' href='{d}.html'>"
        f"{datetime.strptime(d, '%Y-%m-%d'):%A, %B} {int(d[-2:])}<span>{d[:4]} →</span></a>"
        for d in days)
    (archive / "index.html").write_text(
        page("Past briefs",
             f"<p class='hello'>Archive</p><h1>Past briefs</h1>"
             f"<p class='meta'>{len(days)} day{'s' if len(days) != 1 else ''} saved</p>{rows}"
             f"<footer><a class='btn' href='../'>← Today's brief</a></footer>"),
        encoding="utf-8")

# ---------------------------------------------------------------------------

def main() -> None:
    now = datetime.now(TIMEZONE)
    items = collect_news()
    print(f"Collected {len(items)} headlines")

    digest = None
    for attempt in range(3):                 # retry if the answer is broken or too thin
        try:
            result = ask_gemini(items)
        except Exception as err:
            print(f"AI step failed: {err}")
            continue
        count = story_count(result)
        print(f"Gemini returned {count} stories")
        if digest is None or count > story_count(digest):
            digest = result
        if count >= MIN_STORIES:
            break
        print(f"Fewer than {MIN_STORIES} stories, asking again")
    if digest is None:
        print("AI step failed, using headline-only page")

    save(render(digest, items, now), now)
    print("Saved docs/index.html")


if __name__ == "__main__":
    main()
