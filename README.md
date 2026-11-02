# Re7 Content Engine

A working automation that collects Re7 Capital and Re7 Labs posts from X, LinkedIn and Telegram into one source, uses Claude to label what each post is and find what performs on each channel, and turns those insights plus Re7's own research into next week's content. One button runs it end to end.

Built by Hamza Khan for the Analyst, Investor Relations & Marketing application, November 2026.

**Live app:** _link added on deployment_  ·  **Each run:** 1–3 minutes, about 20p in API usage

---

## What it does

```
 COLLECT                      SINGLE SOURCE            ANALYSE                          CREATE
 Telegram (public page)  ─┐                            Claude labels each post          Re7's own material
 X (Apify)               ─┼─►  data/posts.csv   ─►     against a fixed taxonomy   ─►    (The Weekly, research)
 LinkedIn (Apify / admin ─┤    one row per post,       per-channel stats on a           + this week's insights
   analytics export)      │    every channel           normalised engagement index      + top posts as voice refs
 Seed (manual captures)  ─┘                            one-page insights memo           = next week's queue,
                                                                                         each post tagged with
                                                                                         the hypothesis it tests
                                                              ▲                                   │
                                                              └── next week's collection scores ──┘
                                                                  the posts this system wrote
```

1. **Collect.** Each channel has its own collector; all write the same row shape (see `pipeline/schema.py`). A manual capture from 1 November 2026 is merged first on every run, so the dashboard never depends on a scraper being up. New rows are added by stable id; engagement counts on existing rows are refreshed.
2. **Analyse.** Claude labels every post with one theme, one format and two attributes from a closed list (`pipeline/taxonomy.py`). Engagement is normalised per channel: `engagement_index = (likes + 2×reposts + 3×comments) / channel median`, so 1.0 is a typical post on that channel and a LinkedIn 3.5× is comparable to an X 3.5×, which raw likes are not. Replies and disclaimers are excluded. Claude then writes a one-page memo from the statistics (not from the posts), with sample sizes, and flags anything under n = 5 as directional.
3. **Create.** The latest Weekly and research posts are fetched from re7.capital. Claude drafts a week of posts for X, LinkedIn and Telegram, steered by the insights and by the best-performing recent posts as voice references. Every draft carries its source, a compliance flag where it quotes yields or names a third party, and an **experiment tag**: the theme-and-format hypothesis it tests. When a draft is published and collected the following week, it is scored against that hypothesis on the dashboard. The loop closes.

## Why these design choices

- **Fixed taxonomy, not free-text labels.** Statistics are only comparable across runs if every post is labelled against the same set.
- **Per-channel index, not raw counts.** Eight LinkedIn likes and four X likes say nothing about each other; multiples of each channel's own median do.
- **Memo from stats, not from posts.** Keeps the model from narrating; forces numbers with sample sizes.
- **Human review queue, never auto-posting.** Re7 is FCA-regulated. Drafts are research-desk register by construction (no yields as returns, no promissory words, no calls to invest) and anything touching yields or third parties is flagged for sign-off.
- **Experiment tags.** Without them this is a report. With them the system measures its own output and the insights improve with use.
- **Cached results and a gated button.** The page always shows the last successful run. Live runs have a 15-minute cooldown and a daily cap; spend is also capped at the API key. Anyone can press the button; nobody can run up a bill; the submission can never be "out of runs".

## Data access, honestly

| Channel | Used here | In production |
|---|---|---|
| Telegram | Public channel preview page, no key | Same, or Bot API if the channel is private |
| X | Apify actor pulling the public timeline (free tier) | Same, or X API if budget allows |
| LinkedIn | Apify actor on the public company page, with the manual capture as fallback | **Page admin analytics export** (`data/imports/`), which also gives impressions; no scraping. `csv_import.py` already reads that file format |

LinkedIn has no public API for company-page posts without admin access, and its terms restrict scraping, so the production path is the admin export or the official API; the third-party collector is for this exercise only and is disclosed as such.

## What a first look at the data says (seed, 1 Nov 2026)

On X (25 posts since 31 Aug), media appearances index at about 4.9× a typical post and the Weekly at about 0.8×; posts that name a partner firm average 3.8× against 1.1× without. On LinkedIn the four best posts of 30 (37% of all engagement) all name Evgeny Gokhberg and tag the firms involved. The firm's people and partners reach an audience; its own research posted under its own logo does not. The content generator is steered accordingly: person-led, partner-tagged, research repackaged rather than linked.

## Running it

**Hosted:** open the live app and press *Run pipeline now*.

**GitHub Actions:** *Actions → Run content engine → Run workflow*. Also runs every Monday 07:00 UTC and commits the outputs.

**Locally:**
```
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...            # required for labelling, memo, content
export APIFY_TOKEN=...                  # optional: live X and LinkedIn
export RE7_TELEGRAM_HANDLE=...          # optional: live Telegram
python -m pipeline.run                  # or --offline for seed data only
streamlit run app.py
```

Secrets live in GitHub (*Settings → Secrets and variables → Actions*) and Streamlit (*App settings → Secrets*); they are never in the code or on the page.

## Files

```
app.py                      dashboard and Run button
pipeline/run.py             orchestrator: collect → analyse → create; writes output/last_run.json
pipeline/schema.py          the single-source row shape
pipeline/taxonomy.py        themes, formats, attributes, compliance triggers
pipeline/store.py           load / merge / save data/posts.csv; seed loader
pipeline/collect/           telegram.py · apify.py (X, LinkedIn) · csv_import.py (admin exports)
pipeline/analyse.py         Claude labelling, per-channel stats, memo
pipeline/create.py          source fetch from re7.capital, content generation, compliance flags
pipeline/llm.py             Claude API wrapper
data/posts.csv              the single source
data/seed/                  manual captures, 1 Nov 2026
data/imports/               drop LinkedIn admin exports here
output/                     insights.json · memo.md · content_queue.csv · last_run.json · run_history.jsonl
.github/workflows/run.yml   scheduled and on-demand runs
```

## Limitations and next steps

- Sample sizes are small (60 posts at launch); the memo says so. Four weeks of collection gives real confidence intervals.
- LinkedIn views/impressions are only available via the admin export; the index therefore uses reactions, reposts and comments on all channels, and views as a secondary signal where present.
- Theme labelling is model-based; a weekly spot-check of ten labels is part of the operating routine, and corrections can be made in `posts.csv` (`label_source` records who labelled).
- Next: post-time analysis once timestamps are available from the live collectors; a Slack digest of the memo; publishing the approved queue through a scheduler (Buffer or Typefully) so the experiment loop needs no manual step.
