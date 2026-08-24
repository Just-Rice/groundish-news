"""Turn clusters of articles into stories with bias, blindspot and ownership data.

One deliberate departure from how Ground News presents things: blindspots here
are computed against *coverage rates*, not raw article shares. Any feed list is
lopsided -- this one carries more left-of-centre outlets than right-of-centre
ones -- so a raw share of coverage would flag a blindspot on the right for
almost every story. Instead we ask: of the right-leaning outlets we actually
polled, what fraction ran this story? A blindspot is when one side's rate is
near zero while another side's is substantial. Raw counts still drive the
visible bar, because that is what a reader's own feed would look like.
"""
from collections import Counter, defaultdict
from datetime import datetime, timezone

import cluster as clustering
import sources
import summarize

FACTUALITY_SCORE = {"high": 1.0, "mostly-high": 0.75, "mixed": 0.45, "low": 0.1}

MIN_OUTLETS_FOR_BLINDSPOT = 5
BLINDSPOT_SILENT_RATE = 0.07     # a side is "silent" at/below this coverage rate
BLINDSPOT_LOUD_RATE = 0.12       # ...and only if another side is at least this loud
BLINDSPOT_LOUD_OUTLETS = 3       # ...backed by real outlets, not one stray hit
BLINDSPOT_RATIO = 2.5            # ...and clearly out-covering it
OWNER_CONCENTRATION = 0.30       # one parent company above this share is notable


def _camp(lean):
    """Side of the spectrum, or None for an outlet we have no rating for."""
    if lean is None:
        return None
    return "left" if lean < 0 else ("right" if lean > 0 else "center")


def _rated(articles):
    """Only outlets in the registry can carry bias maths — see gdelt.py."""
    return [a for a in articles if a.get("lean") is not None]


def pool_sizes(articles):
    """How many outlets per camp actually returned anything this run."""
    live = defaultdict(set)
    for art in _rated(articles):
        live[_camp(art["lean"])].add(art["source_id"])
    return {camp: len(live.get(camp, ())) or 1 for camp in ("left", "center", "right")}


def _iso(ts):
    if not ts:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def build_story(articles, indices, pools, now_ts):
    members = [articles[i] for i in indices]
    ranked = clustering.centrality(articles, indices)

    # One entry per outlet -- an outlet that ran five versions shouldn't count five times.
    by_source = {}
    for i in ranked:
        by_source.setdefault(articles[i]["source_id"], articles[i])
    outlets = list(by_source.values())

    # Unrated outlets (added by search — see gdelt.py) are counted and shown, but
    # kept out of every calculation that places a story on the spectrum: we have
    # no basis for placing them, and guessing would be worse than abstaining.
    rated = _rated(outlets)
    bar = {slug: 0 for _, slug, _ in sources.BUCKETS}
    bar["unrated"] = 0
    for art in outlets:
        bar[art.get("lean_slug", "unrated")] = bar.get(art.get("lean_slug", "unrated"), 0) + 1
    total = len(rated)                      # drives skew, factuality, blindspots
    total_outlets = len(outlets)

    camp_counts = Counter(_camp(a["lean"]) for a in rated)
    rates = {camp: camp_counts.get(camp, 0) / pools[camp] for camp in pools}

    def _silent(quiet, loud):
        """Is `quiet` side missing a story the `loud` side is running?"""
        return (rates[quiet] <= BLINDSPOT_SILENT_RATE
                and rates[loud] >= BLINDSPOT_LOUD_RATE
                and camp_counts.get(loud, 0) >= BLINDSPOT_LOUD_OUTLETS
                and rates[loud] >= BLINDSPOT_RATIO * max(rates[quiet], 1e-6))

    blindspot = None
    if total >= MIN_OUTLETS_FOR_BLINDSPOT:
        if _silent("right", "left"):
            blindspot = "right"
        elif _silent("left", "right"):
            blindspot = "left"

    owners = Counter(a["owner"] for a in outlets)
    top_owner, top_owner_n = owners.most_common(1)[0]
    concentration = top_owner_n / total_outlets

    # Headline for the story: most central article, preferring a centre outlet
    # so the card itself doesn't inherit one side's framing.
    # Prefer a centre outlet's wording so the card doesn't inherit one side's
    # framing. Unrated outlets sort last, and are only used when nothing in the
    # story is rated — as happens for stories found purely by search.
    def _lead_key(a):
        position = ranked.index(next(i for i in ranked if articles[i]["id"] == a["id"]))
        return (9 if a.get("lean") is None else abs(a["lean"]), position)

    lead = min(outlets, key=_lead_key)

    # How each side titled it -- the actual point of the exercise.
    framing = []
    for lean, slug, label in list(sources.BUCKETS) + [(None, "unrated", "Unrated")]:
        side = [a for a in outlets if a.get("lean_slug") == slug]
        if side:
            framing.append({"lean_slug": slug, "lean_label": label,
                            "count": len(side), "article": side[0]})

    times = [a["published_ts"] for a in members if a.get("published_ts")]
    first_ts, last_ts = (min(times), max(times)) if times else (None, None)
    first_outlet = None
    if first_ts:
        first_outlet = min((a for a in members if a.get("published_ts")),
                           key=lambda a: a["published_ts"])["source"]

    fact = [FACTUALITY_SCORE.get(a["factuality"], 0.5) for a in rated]
    age_hours = ((now_ts - last_ts) / 3600.0) if last_ts else 48.0
    rank = total_outlets * (0.5 ** (max(age_hours, 0) / 36.0))

    consensus = summarize.summarize(members)

    return {
        "id": "s" + members[0]["id"][:12],
        "title": lead["title"],
        "title_source": lead["source"],
        "consensus": consensus,
        "summary": next((a["summary"] for a in outlets if a.get("summary")), ""),
        "article_count": len(members),
        "outlet_count": total_outlets,
        "rated_count": total,
        "unrated_count": total_outlets - total,
        "bar": bar,
        # Denominator is every outlet, because the bar counts every outlet —
        # dividing by the rated subset made the shares sum to well over 1.
        "shares": {k: (v / total_outlets if total_outlets else 0) for k, v in bar.items()},
        "added_by": next((a["added_by"] for a in members if a.get("added_by")), None),
        "camp_counts": dict(camp_counts),
        "camp_rates": {k: round(v, 4) for k, v in rates.items()},
        "skew": round(sum(a["lean"] for a in rated) / total, 3) if total else 0,
        "blindspot": blindspot,
        # None, not 0: with no rated outlet there is nothing to report, and 0
        # would render as "low factuality", which is a claim we cannot make.
        "factuality": round(sum(fact) / len(fact), 3) if fact else None,
        "owner_top": top_owner,
        "owner_top_count": top_owner_n,
        "owner_concentration": round(concentration, 3),
        "owner_flag": concentration >= OWNER_CONCENTRATION and top_owner_n >= 2,
        "owners": owners.most_common(),
        "countries": sorted({a["country"] for a in outlets}),
        "first_published": _iso(first_ts),
        "last_published": _iso(last_ts),
        "first_outlet": first_outlet,
        "rank": round(rank, 4),
        "framing": framing,
        # Unrated outlets have no place on the spectrum, so they sort last.
        "articles": sorted(outlets, key=lambda a: (
            9 if a.get("lean") is None else a["lean"], a["source"])),
        "all_articles": [articles[i] for i in ranked],
    }


def analyze(articles, min_outlets=2):
    now_ts = datetime.now(timezone.utc).timestamp()
    pools = pool_sizes(articles)
    groups = clustering.cluster(articles)

    stories = []
    for indices in groups:
        if len({articles[i]["source_id"] for i in indices}) < min_outlets:
            continue
        stories.append(build_story(articles, indices, pools, now_ts))
    stories.sort(key=lambda s: -s["rank"])
    return stories, pools


def single_story(articles, pools=None, now_ts=None):
    """Turn a set of search results straight into one story.

    Stories found by search are not clustered: the query already defines the
    story, and clustering a handful of documents produces degenerate IDF anyway
    (every token looks rare in a corpus of seven). So the whole result set is
    treated as one pre-formed cluster.
    """
    if not articles:
        return None
    now_ts = now_ts or datetime.now(timezone.utc).timestamp()
    pools = pools or pool_sizes(articles)
    return build_story(articles, list(range(len(articles))), pools, now_ts)


def overview(articles, stories, pools, report):
    bar = {slug: 0 for _, slug, _ in sources.BUCKETS}
    bar["unrated"] = 0
    for art in articles:
        bar[art.get("lean_slug", "unrated")] = bar.get(art.get("lean_slug", "unrated"), 0) + 1
    owners = Counter(a["owner"] for a in articles)
    return {
        "generated": datetime.now(timezone.utc).isoformat(),
        "article_count": len(articles),
        "story_count": len(stories),
        "source_count": len(sources.SOURCES),
        "sources_ok": sum(1 for r in report if not r["error"]),
        "sources_failed": [r["source"] for r in report if r["error"]],
        "pools": pools,
        "bar": bar,
        "blindspots": {
            "left": sum(1 for s in stories if s["blindspot"] == "left"),
            "right": sum(1 for s in stories if s["blindspot"] == "right"),
        },
        "top_owners": owners.most_common(8),
        "widest_story": max(stories, key=lambda s: s["outlet_count"])["title"] if stories else None,
    }
