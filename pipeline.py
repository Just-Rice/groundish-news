"""fetch -> cluster -> analyze -> data/stories.json"""
import json
import os
import sys
import time

import analyze
import feeds
import gdelt
import llm_summary
import sources

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
OUT = os.path.join(DATA, "stories.json")
PINNED = os.path.join(ROOT, "pinned.json")


def pinned_queries():
    """Searches to run on every build, for stories the feeds don't carry."""
    try:
        with open(PINNED) as fh:
            return [q for q in json.load(fh).get("queries", []) if q.strip()]
    except (OSError, ValueError):
        return []


PINNED_DEADLINE = 90        # seconds; a third-party outage must not stall a build


def add_pinned(stories, log=print):
    """Search for each pinned query and append the result as its own story.

    These are not clustered with the feed articles: the query already defines
    the story, and its outlets are mostly outside the ratings registry, so it
    would only add noise to the clustering corpus.
    """
    queries = pinned_queries()
    if not queries:
        return 0
    log(f"searching {len(queries)} pinned quer" + ("y…" if len(queries) == 1 else "ies…"))
    added = 0
    deadline = time.time() + PINNED_DEADLINE
    for query in queries:
        if time.time() > deadline:
            log(f"  ! skipped {len(queries) - added} quer"
                + ("y" if len(queries) - added == 1 else "ies")
                + f" — {PINNED_DEADLINE}s budget spent")
            break
        story, error = gdelt.make_story(query)
        if not story:
            log(f"  ! '{query[:40]}': {error}")
            continue
        stories.append(story)
        added += 1
        log(f"  + '{query[:40]}' -> {story['outlet_count']} outlets "
            f"({story['rated_count']} rated)")
    return added


def run(max_age=0, min_outlets=2, log=print):
    started = time.time()
    log("fetching…")
    articles, report = feeds.fetch_all(max_age=max_age, log=log)
    if not articles:
        raise SystemExit("no articles fetched — check your network connection")

    log("clustering…")
    stories, pools = analyze.analyze(articles, min_outlets=min_outlets)

    add_pinned(stories, log=log)
    stories.sort(key=lambda s: -s["rank"])

    # Optional upgrade: an LLM writes the summary where credentials allow it.
    # Cached stories cost nothing and failures keep the extract, so this is safe
    # to call unconditionally.
    summary_stats = llm_summary.apply(stories, log=log)
    for story in stories:
        story.pop("_summary_key", None)

    meta = analyze.overview(articles, stories, pools, report)
    meta["summaries"] = summary_stats
    meta["seconds"] = round(time.time() - started, 1)

    payload = {"meta": meta, "stories": stories,
               "sources": sources.SOURCES, "report": report}
    os.makedirs(DATA, exist_ok=True)
    tmp = OUT + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(payload, fh, separators=(",", ":"))
    os.replace(tmp, OUT)

    log(f"{meta['article_count']} articles → {meta['story_count']} multi-source stories "
        f"({meta['sources_ok']}/{meta['source_count']} feeds) in {meta['seconds']}s")
    log(f"blindspots: {meta['blindspots']['right']} on the right, "
        f"{meta['blindspots']['left']} on the left")
    if summary_stats["written"] or summary_stats["cached"]:
        log(f"summaries: {summary_stats['written']} written, "
            f"{summary_stats['cached']} from cache, {summary_stats['failed']} failed "
            f"({summary_stats['input_tokens']}+{summary_stats['output_tokens']} tokens)")
    return payload


def load():
    if not os.path.exists(OUT):
        return None
    with open(OUT) as fh:
        return json.load(fh)


if __name__ == "__main__":
    run(max_age=int(sys.argv[1]) if len(sys.argv) > 1 else 0)
