# Re7 Content Engine

A working automation that collects Re7 Capital's and Re7 Labs' social posts from X, LinkedIn and Telegram into one source, uses Claude to label and analyse what performs on each channel, and turns those findings plus Re7's own published material into a week of draft posts. It runs end to end from one button.

**Live app:** press **Run pipeline now** and wait one to three minutes. The dashboard always shows the last successful run, so there is something to read before you press anything.

Built by Hamza Khan for Re7 Capital's take-home task (Analyst, Investor Relations & Marketing), November 2026. Claude (Anthropic) was used both inside the pipeline and as a coding assistant while building it.

## What one run does

1. **Collect.** Pulls the most recent posts into `data/posts.csv`, one row per post with date, format, likes, reposts, comments and views where the platform exposes them.
   - X: `@Re7Capital` and `@Re7Labs` via an Apify scraper (about 60 posts each), with a public-syndication fallback if the scraper is unavailable.
   - LinkedIn: the Re7 Capital company page via an Apify scraper (100 posts). Reshares of employees' posts are kept but marked, so they do not distort the page's own numbers. A LinkedIn admin export dropped into `data/imports/` is also read, which is the proper production route.
   - Telegram: the public Re7 Labs Alpha channel via its web preview (about 80 posts).
   - A hand-captured seed from 1 November 2026 is the floor; live rows supersede matching seed rows.
2. **Analyse.** Claude labels every new post against a fixed taxonomy (theme, format, whether a person or partner is named, entities mentioned). Engagement is scored as likes + 2×reposts + 3×comments (views on Telegram) and expressed relative to a typical post on the same channel and account, so "3.0×" means three times a typical post there. Second-order views: where the effort goes versus what it earns, how the same piece of content travels across channels, which named people and partners move the needle, reach versus resonance on X, an eight-week trend, day of week, best and worst posts. Claude then writes a short memo in plain language.
3. **Create.** Claude discovers this week's source material from Re7's own posts (The Weekly, research reports, announcements), fetches the pages from re7.capital, and drafts seven posts: three for Re7 Capital on X, one LinkedIn post for Evgeny's profile, one Re7 Labs X post, one Telegram update and one labelled experiment. Each carries the finding it acts on, a source, a distribution note, a compliance flag where wording needs sign-off, and an experiment tag so next week's collection can score it. Nothing posted on the same channel in the last three weeks is re-announced unless it is reframed and labelled as a re-promotion.
4. **Persist.** Results are committed to the `data` branch of this repository and reloaded when the app restarts, so nothing is lost when the host recycles the machine.

## Reading the numbers

Every figure is relative to a typical post on the same channel and account. The "verdict" column in the effort-versus-return table says whether a theme brings back more engagement than its share of posts (outperforms), about the same (pulls its weight) or less (under-delivers). Groups with fewer than three posts are marked as small samples and should be read as hints, not findings.

## Guard-rails

- Runs are rate-limited (cooldown between runs, daily cap) and each run costs roughly 20p in API usage; the API key has a monthly spend limit.
- No yields or performance figures are quoted in generated content. Promissory wording and third-party names are flagged for review. Everything is a draft; nothing is posted automatically.
- No credentials are in the repository. Keys live in the host's secrets store.

## Running it yourself

```
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...        # required for labelling, memo and drafts
export APIFY_TOKEN=...              # optional; X and LinkedIn collection
export APIFY_X_ACTORS=xquik~x-tweet-scraper   # optional; X scraper to use
export GITHUB_TOKEN=...             # optional; persistence to the data branch
python -m pipeline.run              # full run
python -m pipeline.run --offline    # seed data only, no network or API
streamlit run app.py                # dashboard with the Run button
```

## Layout

```
app.py                  Streamlit dashboard and Run button
pipeline/run.py         orchestrator
pipeline/collect/       telegram.py, apify.py (X + LinkedIn), x_public.py, csv_import.py
pipeline/analyse.py     labelling, engagement index, stats, memo
pipeline/insights.py    second-order analysis
pipeline/create.py      source discovery and content drafting
pipeline/persist.py     GitHub data-branch persistence
pipeline/taxonomy.py    themes, formats, compliance triggers
data/seed/              hand-captured posts from 1 Nov 2026
output/                 memo.md, insights.json, content_queue.csv, last_run.json
```

## Known limits

- LinkedIn exposes no views and no per-post impressions to non-admins; a page-admin export fills that gap.
- X scraping depends on a third-party actor and can be rate-limited on free tiers; the fallback gives fewer posts.
- Engagement does not measure what matters most to a fund (allocator conversations started). The experiment tags are the start of closing that loop: publish, collect, compare against the hypothesis.
