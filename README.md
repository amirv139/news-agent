# Morning News Agent (100% free)

Every morning this agent:
1. Reads ~20 free news feeds (Toronto/Ontario, Canada, world manufacturing, tech)
2. Sends the headlines to Google Gemini (free API key) to pick, group and explain them
3. Publishes a phone-friendly page on GitHub Pages that you open each morning

Cost: $0. GitHub Actions + GitHub Pages are free for public repos, RSS feeds are free,
and the agent makes only 1 Gemini call per day (the free tier allows hundreds).

## Files
- `news_agent.py` – the agent (collect → AI summary → web page)
- `requirements.txt` – Python packages it needs
- `.github/workflows/daily-news.yml` – the alarm clock that runs it every morning
- `docs/` – where the finished pages go (created automatically)

## Setup (about 20 minutes)
1. **Get a free Gemini API key**: go to aistudio.google.com → "Get API key" → Create API key.
   No credit card needed. Don't add billing, so it can never charge you.
2. **Create a GitHub repo**: github.com → New repository → name it `news-agent` → Public → Create.
3. **Upload the files**: "Add file" → "Upload files" → drag in everything from this folder,
   keeping the `.github/workflows/` folder structure (see note below).
4. **Add your key as a secret**: repo → Settings → Secrets and variables → Actions →
   New repository secret → Name: `GEMINI_API_KEY`, Value: your key.
5. **Run it once**: Actions tab → "Daily news brief" → "Run workflow". Wait ~2 minutes.
   Green check = success. Red X = click it to read the error.
6. **Turn on the website**: Settings → Pages → Source: "Deploy from a branch" →
   Branch: `main`, folder: `/docs` → Save. After a minute your page is at
   `https://YOUR-USERNAME.github.io/news-agent/`
7. **Put it on your phone**: open that link in Safari/Chrome → Share/menu →
   "Add to Home Screen". Now it looks like an app.

Note on step 3: the web uploader sometimes hides folders starting with a dot. If
`.github` doesn't upload, click "Add file" → "Create new file", type the name
`.github/workflows/daily-news.yml` and paste the file's contents.

## Customize
- Change topics: edit the `FEEDS` list. `google_news("any search words")` makes a feed for any topic.
- Change what the AI focuses on: edit `READER_PROFILE` and `PROMPT`.
- Change the time: edit the `cron` line (it's in UTC; Toronto is UTC-5 in winter, UTC-4 in summer).
- If Google renames the model and the run fails: Settings → Secrets and variables →
  Actions → Variables → add `GEMINI_MODEL` with a current Flash-Lite model name from AI Studio.
