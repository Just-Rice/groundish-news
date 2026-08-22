# Groundish News

**Live: https://just-rice.github.io/groundish-news/**

A news aggregator in the spirit of [Ground News](https://ground.news): it pulls the
same story from across the political spectrum, shows you who covered it, how each
side headlined it, who owns the outlets doing the covering — and which side isn't
running the story at all.

Pure Python standard library on the back end, vanilla JS on the front. **No pip
install, no npm, no API keys.**

```bash
python3 server.py          # → http://127.0.0.1:8000
```

The first load fetches ~100 RSS feeds from 66 outlets (about 10 seconds) and caches
the result. Feeds refresh automatically when the cache passes 15 minutes, or on demand
from the **Refresh feeds** button.

## What it does

| Feature | What you get |
| --- | --- |
| **Story clustering** | Articles describing the same event are merged into one story across outlets |
| **Bias distribution** | A five-segment bar — Left / Lean Left / Center / Lean Right / Right — counted by *outlet*, not article |
| **Consensus summary** | A short, collapsible summary under each headline, built from sentences the outlets themselves published |
| **Blindspots** | Stories one side of the spectrum is largely not covering, in both directions |
| **Side-by-side framing** | The actual headline each side ran, next to each other |
| **Ownership** | The parent company behind every outlet, flagged when one company owns a big share of a story's coverage |
| **Factuality** | An aggregate factuality reading for the outlets covering a story |
| **My Bias** | A local-only tally of the lean of every article you click through to |

## Hosting it on GitHub Pages

Pages serves files, not programs, so `server.py` cannot run there — and a browser
cannot fetch the RSS feeds itself, because news sites send no CORS headers. The
work is done ahead of time instead:

```bash
python3 build_static.py        # writes docs/ — the site plus a data bundle
```

`docs/` holds the same `index.html`, `styles.css` and `app.js` as `web/`, next to
`data/bundle.json` (~1.1 MB: 207 stories, 66 sources). `app.js` probes for the
live API on load and falls back to that bundle, so one front end serves both
modes — its static filtering and sorting mirror `filter_stories()` and `_slim()`
in `server.py` so both return the same stories in the same order. Every request
path is relative, since Pages serves from a `/<repo-name>/` sub-path.

**Keeping it fresh.** Committed data is a snapshot. `.github/workflows/pages.yml`
rebuilds it every 30 minutes and deploys via Pages artifacts, so nothing is
committed back to the repository — a 1 MB bundle landing in git every half hour
would bloat history for nothing. Adding that workflow needs a token with the
`workflow` scope, or you can paste the file straight into GitHub's web UI. Once
it is in place, set **Settings → Pages → Source** to **GitHub Actions**.

Note that GitHub disables scheduled workflows after 60 days without repository
activity, and runs them on a best-effort basis rather than exactly on the minute.

## Layout

```
sources.py    the one table you edit: 66 outlets, their feeds, lean, factuality, owner
feeds.py      RSS/Atom/RDF fetching and parsing, threaded, with an on-disk cache
cluster.py    TF-IDF vectors, inverted-index blocking, average-link clustering
build_static.py builds docs/ for GitHub Pages: site files plus a data bundle
summarize.py  consensus summaries: extractive, scored on cross-spectrum agreement
llm_summary.py optional Claude-written summaries, cached on disk (needs a key)
bakeoff.py    compare summary models on real stories before picking one
analyze.py    bias distribution, blindspot detection, ownership concentration
pipeline.py   fetch → cluster → analyze → data/stories.json
server.py     stdlib HTTP server and JSON API
web/          index.html, styles.css, app.js
```

Run any stage on its own:

```bash
python3 sources.py           # list the source registry by lean
python3 feeds.py             # fetch everything, print a per-source report
python3 pipeline.py          # rebuild data/stories.json
python3 pipeline.py 600      # ...reusing any cached feed younger than 600s
python3 server.py 9000       # serve on a different port
```

## API

| Endpoint | Purpose |
| --- | --- |
| `GET /api/stories` | `q`, `sort`, `min_outlets`, `blindspot=left\|right\|any`, `owner`, `country`, `limit`, `offset` |
| `GET /api/story/<id>` | One story with every article, framing and ownership breakdown |
| `GET /api/sources` | The source registry plus this batch's per-source article counts |
| `GET /api/meta` | Batch summary: counts, blindspot totals, top owners |
| `GET /api/status` | Whether a refresh is in flight |
| `POST /api/refresh` | Kick off a refresh |

Views are addressable: `#blindspots`, `#sources`, `#bias`, `#method`, and
`#story/<id>` opens a specific story.

## How clustering works

Headlines become TF-IDF vectors, with the article summary contributing at a lower
weight. An inverted index over *discriminative* tokens — the ones rare enough to
mean something — produces candidate pairs, so a full N² comparison never happens
(1,600 articles cluster in about 0.2s).

Those pairs are then merged strongest-first using **average-link agglomerative
clustering**: two groups only join if their centroids are still similar enough.
That centroid check is what prevents the classic single-link failure where A
resembles B and B resembles C, so three unrelated stories collapse into one.

## How the summaries work

There is no language model here and no API key, so each summary is **extractive**:
it reuses sentences the outlets themselves published. The *selection* is what makes
it non-partisan. A sentence scores well when the facts in it are repeated
independently by many outlets, and especially when those outlets sit on different
sides of the spectrum — a detail Fox News, the AP and Mother Jones all bothered to
print is very likely the uncontested part of the story.

Sentences carrying opinion markers, second-person address or rhetorical questions are
pushed down; centre outlets break ties; datelines and feed boilerplate are stripped;
and a Jaccard check stops the second sentence from restating the first. In testing,
195 of 212 stories produced a usable summary.

It is a consensus extract, **not** neutral prose written from scratch, and the app
says so under every summary.

## Optional: LLM-written summaries

The extractive summary is the default and needs nothing. If you want summaries
written from scratch rather than stitched from outlet sentences, set one key.
Two providers work; use whichever you have.

```bash
# Google Gemini — free tier, no credit card
pip install google-genai
export GEMINI_API_KEY=...                # from aistudio.google.com/apikey
python3 pipeline.py

# Anthropic Claude — no free tier, pay-as-you-go
pip install anthropic
export ANTHROPIC_API_KEY=sk-ant-...      # or: ant auth login
python3 pipeline.py
```

Whichever key is present is used. Force one with `GROUNDISH_LLM=gemini` or
`GROUNDISH_LLM=anthropic`, and pick a model with `GROUNDISH_MODEL=...`.

**On the free tier.** Google gives Gemini Flash models away up to roughly 1,000
requests a day, which comfortably covers this project's ~90 new stories a day.
The tradeoff is real though: Pro-tier Gemini models left the free tier in April
2026, so you get Flash; free-tier requests may be used to improve Google's
products (irrelevant here — the input is public headlines); and the ~10
requests/minute limit means the initial backfill of ~200 stories takes about 20
minutes. Requests are spaced out automatically to stay under it.

The SDKs are the only non-stdlib dependencies in the project and both are optional.
With no key, no SDK, an API error, a rate limit, or a refusal, stories keep their
extract — every failure path falls back rather than breaking a refresh, and a run
of failures trips a circuit breaker so a bad key costs one request, not one per story.

**Why it is affordable.** Summaries are cached on disk and keyed to a story's
*identity* — the earliest few articles in its cluster, which stay put as coverage
grows. Feeds are re-pulled every 15 minutes, but only about **90 genuinely new
stories appear per day**, so a refresh makes a handful of calls, not a few hundred.
Re-summarizing all 212 stories on every refresh would cost ~$440/month on the
cheapest model and produce identical text; the cache brings that to a few dollars.

| Model | Price (in/out per Mtok) | ~Cost/month |
| --- | --- | --- |
| `gemini-3.7-flash` (default when a Gemini key is set) | free tier | **free** |
| `gemini-3.5-flash-lite` | free tier | **free** |
| `claude-opus-5` (default when an Anthropic key is set) | $5 / $25 | ~$15 |
| `claude-sonnet-5` | $2 / $10 intro through 2026-08-31, then $3 / $15 | ~$6, then ~$9 |
| `claude-haiku-4-5` | $1 / $5 | ~$3 |

Compare them on your own stories before committing — models from different
providers can go head to head in one run:

```bash
python3 bakeoff.py 20 gemini-3.7-flash gemini-3.5-flash-lite
python3 bakeoff.py 20 claude-opus-5 claude-sonnet-5
```

`bakeoff.py` picks the hardest stories it can find — most outlets, widest spread
across the spectrum, blindspots first — runs each model on all of them, prints the
summaries side by side next to the extractive one, and reports real token counts
and the monthly cost each model implies. Easy stories all read alike; the
differences show up where outlets disagree.

**What the prompt asks for.** Only facts that outlets on different sides report in
common; the plainest available wording; attribution where sources conflict; and an
explicit instruction that the number of outlets on a side reflects which feeds this
app polls, not what is true — the source list leans left 31 to 17, so a summary that
simply follows the majority would quietly defeat the point of the app.

## How blindspots work

A blindspot is a story one side of the spectrum is largely not running.

Groundish News compares **coverage rates**, not raw shares. Any hand-built source list
is lopsided — this one carries more left-of-centre outlets than right-of-centre —
so a raw share of coverage would flag a blindspot on the right for nearly every
story. Instead: of the right-leaning outlets polled, what fraction ran this story,
versus the left-leaning ones?

A story is flagged when all of these hold:

- at least **5 outlets** cover it
- one side's coverage rate is at or below **7%**
- another side's rate is at least **12%**, across at least **3 outlets**
- the louder side out-covers the quieter one by at least **2.5×**

All four thresholds live at the top of `analyze.py`.

## About the ratings

The lean, factuality and ownership values in `sources.py` are **hand-encoded
approximations** of ratings published by AllSides, Ad Fontes Media and Media
Bias/Fact Check. They are not licensed data. They describe an outlet's overall
output rather than any individual article, and media-bias ratings are contested
and US-centric to begin with. Disagree with one? Change the number and re-run —
everything downstream reads from that single table.

## Known limits

- RSS carries recent front-page and section items, not an outlet's full output. An
  outlet can look silent on a story it covered outside the sampling window. (Adding
  section feeds per outlet reduced this a lot — it turned one apparent blindspot in
  testing into a story the right had covered after all.)
- Some international feeds are world-news oriented while some US partisan feeds are
  politics-oriented, so a few apparent blindspots are really topic-mix artefacts.
- Clustering is lexical: two outlets describing one event in very different words
  may stay in separate stories.
- The bias bar counts outlets, not audience. Five small sites weigh the same as
  five networks.
- Feeds are fetched directly from publishers. Be a good citizen with the refresh
  button; responses are cached on disk in `data/cache/`.
