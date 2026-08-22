"""Build the static site in docs/ for GitHub Pages.

GitHub Pages serves files, not programs, so the Python API can't run there — and
a browser can't fetch the RSS feeds itself, because news sites don't send CORS
headers. The way round both is to do the work ahead of time: a GitHub Actions
job runs the pipeline on a schedule, this script writes the result into docs/,
and the page loads that JSON instead of calling an API.

    python3 build_static.py          # uses data/stories.json, refreshing if stale

The same web/ files serve both modes. app.js probes for the live API on load and
falls back to the static bundle, so nothing is forked or duplicated.
"""
import json
import os
import shutil
import sys
import time

import pipeline

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(ROOT, "web")
DOCS = os.path.join(ROOT, "docs")


def build(payload):
    """Trim the payload to what the front end actually reads."""
    stories = []
    for story in payload["stories"]:
        # all_articles duplicates `articles` and is only used to rank centrality
        # during analysis — dropping it removes a third of the bundle.
        stories.append({k: v for k, v in story.items() if k != "all_articles"})

    counts = {r["source_id"]: r for r in payload["report"]}
    sources = [dict(s, **{
        "article_count": counts.get(s["id"], {}).get("count", 0),
        "feeds_ok": counts.get(s["id"], {}).get("feeds_ok", 0),
        "feeds": counts.get(s["id"], {}).get("feeds", len(s["urls"])),
        "error": counts.get(s["id"], {}).get("error"),
    }) for s in payload["sources"]]

    return {"meta": payload["meta"], "stories": stories, "sources": sources,
            "static": True, "built": time.time()}


def main():
    max_age = int(sys.argv[1]) if len(sys.argv) > 1 else 900
    payload = pipeline.load()
    stale = not payload or (time.time() - os.path.getmtime(pipeline.OUT)) > max_age
    if stale:
        payload = pipeline.run(max_age=max_age)

    os.makedirs(os.path.join(DOCS, "data"), exist_ok=True)
    for name in ("index.html", "styles.css", "app.js"):
        shutil.copy2(os.path.join(WEB, name), os.path.join(DOCS, name))
    # Tell GitHub Pages not to run the output through Jekyll.
    open(os.path.join(DOCS, ".nojekyll"), "w").close()

    bundle = build(payload)
    out = os.path.join(DOCS, "data", "bundle.json")
    with open(out, "w") as fh:
        json.dump(bundle, fh, separators=(",", ":"))

    size = os.path.getsize(out) / 1048576
    print(f"docs/ built — {len(bundle['stories'])} stories, "
          f"{len(bundle['sources'])} sources, bundle {size:.2f} MB")


if __name__ == "__main__":
    main()
