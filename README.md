# Groundish News

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

## Layout

```
sources.py    the one table you edit: 66 outlets, their feeds, lean, factuality, owner
feeds.py      RSS/Atom/RDF fetching and parsing, threaded, with an on-disk cache
cluster.py    TF-IDF vectors, inverted-index blocking, average-link clustering
summarize.py  consensus summaries: extractive, scored on cross-spectrum agreement
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
