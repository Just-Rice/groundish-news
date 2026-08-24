"""Search world news coverage for a story the RSS feeds missed.

The feed list in sources.py is a fixed set of front pages, so a story it didn't
carry can't appear no matter how widely it was reported elsewhere. This module
searches GDELT's global news index instead, which covers far more outlets and —
unlike Google News RSS — is reachable from a browser (it sends
`Access-Control-Allow-Origin: *`) as well as from Python.

Two things to know about the API:

  * It is free and needs no key, but it allows roughly **one request every five
    seconds per IP** and answers a breach with a plain-text notice rather than
    JSON, so callers must space requests out and cope with a non-JSON body.
  * A plain keyword query returns mostly long-tail and aggregator domains; the
    big outlets are indexed but rarely surface. Adding `domain:` filters brings
    them back, which is why `search_rated` exists.

Domains that match sources.py keep their lean, factuality and owner. Anything
else is kept but marked **unrated**: it still shows who covered the story, and
is deliberately excluded from the bias bar and blindspot maths, because we have
no basis for placing it on the spectrum.
"""
import hashlib
import json
import re
import socket
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import sources

ENDPOINT = "https://api.gdeltproject.org/api/v2/doc/doc"
MIN_INTERVAL = 5.5          # GDELT asks for one request per five seconds
# A rate-limited IP gets tarpitted: the connection is accepted but the TLS
# handshake never completes, and urlopen's own timeout does not reliably bound
# that. One hung request once stalled a build for fourteen minutes, so the socket
# default is pinned too and the timeout is deliberately short.
TIMEOUT = 12
UA = "groundish-news/1.0 (+https://github.com/Just-Rice/groundish-news)"
_last_call = [0.0]

STRIP_HOST = re.compile(r"^(www|rss|feeds?|api|moxie|search|chaski|feedx|m)\.")


def domain_map():
    """host -> source id, for every outlet in the registry."""
    out = {}
    for source in sources.SOURCES:
        for url in source["urls"]:
            host = urllib.parse.urlparse(url).netloc.lower()
            if "news.google.com" in host:          # proxy feed: use the real site
                match = re.search(r"site:([\w.\-]+)", url)
                host = match.group(1) if match else ""
            host = STRIP_HOST.sub("", host)
            if host:
                out.setdefault(host, source["id"])
    return out


def _throttle():
    wait = _last_call[0] + MIN_INTERVAL - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last_call[0] = time.monotonic()


def raw_search(query, limit=60, timespan=None):
    """-> (articles, error). Never raises."""
    params = {"query": query, "mode": "artlist", "maxrecords": str(limit),
              "format": "json", "sort": "datedesc"}
    if timespan:
        params["timespan"] = timespan
    url = ENDPOINT + "?" + urllib.parse.urlencode(params)
    _throttle()
    previous = socket.getdefaulttimeout()
    socket.setdefaulttimeout(TIMEOUT)
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": UA}), timeout=TIMEOUT) as resp:
            body = resp.read()
    except Exception as exc:                       # noqa: BLE001 - degrade, never break
        return [], f"{type(exc).__name__}: {exc}"
    finally:
        socket.setdefaulttimeout(previous)
    try:
        return json.loads(body).get("articles", []), None
    except ValueError:
        # A rate-limit breach comes back as prose, not JSON.
        return [], " ".join(body.decode("utf-8", "replace").split())[:120]


def _parse_seen(value):
    try:
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def to_articles(found, query):
    """GDELT rows -> the article shape the rest of the pipeline expects."""
    mapping = domain_map()
    by_id = sources.BY_ID
    seen, out = set(), []

    for row in found:
        url = row.get("url") or ""
        title = " ".join((row.get("title") or "").split())
        if not url or len(title) < 15:
            continue
        key = url.lower()
        if key in seen:
            continue
        seen.add(key)

        host = STRIP_HOST.sub("", (row.get("domain") or "").lower())
        source = by_id.get(mapping.get(host, ""), None)
        when = _parse_seen(row.get("seendate"))

        if source:
            meta = {"source_id": source["id"], "source": source["name"],
                    "lean": source["lean"], "lean_slug": source["lean_slug"],
                    "lean_label": source["lean_label"], "factuality": source["factuality"],
                    "owner": source["owner"], "country": source["country"], "rated": True}
        else:
            meta = {"source_id": "gdelt:" + host, "source": host,
                    "lean": None, "lean_slug": "unrated", "lean_label": "Unrated",
                    "factuality": "unrated", "owner": host,
                    "country": row.get("sourcecountry") or "?", "rated": False}

        out.append(dict(meta, **{
            "id": hashlib.sha1(key.encode()).hexdigest()[:16],
            "title": title,
            "summary": "",
            "url": url,
            "published": when.isoformat() if when else None,
            "published_ts": when.timestamp() if when else None,
            "added_by": query,
        }))
    return out


def search(query, limit=60, timespan="7d"):
    """Broad search: whatever GDELT has, rated or not."""
    found, error = raw_search(query, limit=limit, timespan=timespan)
    return to_articles(found, query), error


def search_rated(query, domains=None, limit=75, timespan="7d"):
    """Search restricted to outlets we have ratings for, so the bias bar means
    something. Kept separate because it is a second request against an API that
    allows one every five seconds."""
    domains = domains or list(domain_map())
    scoped = f"{query} (" + " OR ".join("domain:" + d for d in domains) + ")"
    found, error = raw_search(scoped, limit=limit, timespan=timespan)
    return to_articles(found, query), error


def make_story(query, limit=60, timespan="7d", pools=None):
    """Search, then build one story from everything the search returned.

    -> (story or None, error). The caller merges the story into the feed; it is
    marked with `added_by` so the interface can say where it came from.
    """
    import analyze

    articles, error = search(query, limit=limit, timespan=timespan)
    if not articles:
        return None, error or "no coverage found"
    story = analyze.single_story(articles, pools=pools)
    if story:
        story["added_by"] = query
        story["id"] = "q" + hashlib.sha1(query.lower().encode()).hexdigest()[:12]
    return story, error


if __name__ == "__main__":
    import sys
    q = " ".join(sys.argv[1:]) or "Venkata Vasamsetty"
    arts, err = search(q)
    print(f"query: {q!r}  ->  {len(arts)} articles" + (f"  [{err}]" if err else ""))
    rated = [a for a in arts if a["rated"]]
    print(f"  rated outlets: {len(rated)}   unrated: {len(arts) - len(rated)}")
    for a in arts[:12]:
        tag = a["lean_label"] if a["rated"] else "unrated"
        print(f"    {tag:<11} {a['source'][:24]:<24} {a['title'][:58]}")
