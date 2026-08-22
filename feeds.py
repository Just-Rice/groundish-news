"""Fetching and parsing. Stdlib only -- no feedparser, no requests.

Handles the three feed dialects in the wild (RSS 2.0, Atom, RDF/RSS 1.0),
normalises everything into one flat Article dict, and caches raw bytes on disk
so repeated runs during development don't hammer anyone's servers.
"""
import concurrent.futures as futures
import email.utils
import hashlib
import html
import json
import os
import re
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import sources

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "cache")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
TIMEOUT = 20
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
# Google News wraps titles as "Real headline - Outlet Name"
GNEWS_SUFFIX_RE = re.compile(r"\s+-\s+[A-Z][\w.&' ]{2,30}$")


def _strip_ns(tag):
    return tag.split("}", 1)[1] if "}" in tag else tag


def _text(el):
    if el is None:
        return ""
    raw = "".join(el.itertext())
    raw = html.unescape(raw)
    raw = TAG_RE.sub(" ", raw)
    raw = html.unescape(raw)
    return WS_RE.sub(" ", raw).strip()


def _find(node, *names):
    """First child matching any of `names`, namespace-insensitive."""
    for child in node:
        if _strip_ns(child.tag) in names:
            return child
    return None


def _link(node):
    # RSS: <link>url</link>.  Atom: <link rel="alternate" href="url"/>
    best = ""
    for child in node:
        if _strip_ns(child.tag) != "link":
            continue
        href = child.get("href")
        if href:
            rel = child.get("rel", "alternate")
            if rel == "alternate":
                return href
            best = best or href
        elif (child.text or "").strip():
            return child.text.strip()
    if not best:
        guid = _find(node, "guid", "id")
        if guid is not None and (guid.text or "").startswith("http"):
            return guid.text.strip()
    return best


def _published(node):
    for name in ("pubDate", "published", "updated", "date", "dc:date"):
        el = _find(node, name.split(":")[-1])
        if el is None or not (el.text or "").strip():
            continue
        raw = el.text.strip()
        try:                                     # RFC 822 (RSS)
            dt = email.utils.parsedate_to_datetime(raw)
            if dt:
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            pass
        try:                                     # ISO 8601 (Atom)
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _cache_path(url):
    return os.path.join(CACHE_DIR, hashlib.sha1(url.encode()).hexdigest() + ".xml")


def fetch_raw(url, max_age=0):
    """Return feed bytes, reusing a cached copy younger than `max_age` seconds."""
    path = _cache_path(url)
    if max_age and os.path.exists(path) and time.time() - os.path.getmtime(path) < max_age:
        with open(path, "rb") as fh:
            return fh.read()
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
        "Accept-Language": "en-US,en;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        data = resp.read()
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    return data


def parse(data, source):
    """Feed bytes -> list of article dicts stamped with their source metadata."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        # A few publishers emit stray control characters; scrub and retry once.
        cleaned = re.sub(rb"[\x00-\x08\x0b\x0c\x0e-\x1f]", b"", data)
        root = ET.fromstring(cleaned)

    nodes = [el for el in root.iter() if _strip_ns(el.tag) in ("item", "entry")]
    is_gnews = "news.google.com" in source["url"]
    out, seen = [], set()

    for node in nodes:
        title = _text(_find(node, "title"))
        if not title:
            continue
        if is_gnews:
            title = GNEWS_SUFFIX_RE.sub("", title).strip()

        link = _link(node)
        key = link or title.lower()
        if key in seen:
            continue
        seen.add(key)

        summary = _text(_find(node, "description", "summary", "content", "encoded"))
        if is_gnews:
            summary = ""              # Google News summaries are just link markup
        if len(summary) > 400:
            summary = summary[:400].rsplit(" ", 1)[0] + "…"

        published = _published(node)
        out.append({
            "id": hashlib.sha1((source["id"] + "|" + key).encode()).hexdigest()[:16],
            "title": title,
            "summary": summary,
            "url": link,
            "published": published.astimezone(timezone.utc).isoformat() if published else None,
            "published_ts": published.timestamp() if published else None,
            "source_id": source["id"],
            "source": source["name"],
            "lean": source["lean"],
            "lean_slug": source["lean_slug"],
            "lean_label": source["lean_label"],
            "factuality": source["factuality"],
            "owner": source["owner"],
            "country": source["country"],
        })
    return out


def fetch_all(source_list=None, max_age=0, workers=20, per_source_limit=70, log=print):
    """Fetch every feed of every source in parallel.

    Sources with several section feeds are merged back into one stream and
    de-duplicated, so an outlet counts once per story no matter how many of its
    sections carried it. Returns (articles, report) with one report row per
    source. A dead feed is recorded, never raised.
    """
    source_list = source_list or sources.SOURCES
    tasks = [(src, url) for src in source_list for url in src.get("urls", [src["url"]])]

    def one(task):
        src, url = task
        started = time.time()
        try:
            items = parse(fetch_raw(url, max_age=max_age), src)
            return src, url, items, None, time.time() - started
        except Exception as exc:                          # noqa: BLE001 - report, never crash
            return src, url, [], f"{type(exc).__name__}: {exc}", time.time() - started

    per_source = {src["id"]: {"articles": [], "seen": set(), "errors": [], "feeds": 0,
                              "ok": 0, "seconds": 0.0, "source": src}
                  for src in source_list}

    with futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for src, url, items, err, secs in pool.map(one, tasks):
            slot = per_source[src["id"]]
            slot["feeds"] += 1
            slot["seconds"] += secs
            if err:
                slot["errors"].append(f"{url.rsplit('/', 1)[-1] or url}: {err}")
                if log:
                    log(f"  ! {src['name']} <{url}>: {err}")
                continue
            slot["ok"] += 1
            for art in items:
                key = (art["url"] or art["title"]).lower()
                if key in slot["seen"]:
                    continue
                slot["seen"].add(key)
                slot["articles"].append(art)

    articles, report = [], []
    for src in source_list:
        slot = per_source[src["id"]]
        # Freshest first, then trim -- an outlet with four feeds shouldn't
        # dominate the corpus just because it publishes more sections.
        kept = sorted(slot["articles"], key=lambda a: -(a["published_ts"] or 0))[:per_source_limit]
        articles.extend(kept)
        report.append({
            "source_id": src["id"], "source": src["name"], "count": len(kept),
            "feeds": slot["feeds"], "feeds_ok": slot["ok"],
            "error": "; ".join(slot["errors"]) or None,
            "seconds": round(slot["seconds"], 2),
        })

    ok = sum(1 for r in report if r["count"])
    if log:
        log(f"  fetched {len(articles)} articles from {ok}/{len(report)} sources "
            f"({len(tasks)} feeds)")
    return articles, report


if __name__ == "__main__":
    arts, rep = fetch_all(max_age=600)
    for r in sorted(rep, key=lambda r: -r["count"]):
        print(f"{r['count']:4}  {r['source']:32} {r['error'] or ''}")
    print(json.dumps(arts[0], indent=2)[:600])
