"""Groundish web server. Stdlib http.server -- no framework, no install step.

    python3 server.py            # serve on :8000, refreshing if data is stale
    python3 server.py 9000       # pick a port
"""
import json
import mimetypes
import os
import posixpath
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pipeline
import sources

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(ROOT, "web")
STALE_AFTER = 15 * 60          # refresh automatically if the cache is older

_state = {"data": None, "refreshing": False, "error": None, "last_run": 0}
_lock = threading.Lock()


def _log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def refresh(max_age=0):
    with _lock:
        if _state["refreshing"]:
            return
        _state["refreshing"] = True
    try:
        _state["data"] = pipeline.run(max_age=max_age, log=_log)
        _state["error"] = None
        _state["last_run"] = time.time()
    except Exception as exc:                              # noqa: BLE001
        _state["error"] = f"{type(exc).__name__}: {exc}"
        _log(f"refresh failed: {_state['error']}")
    finally:
        _state["refreshing"] = False


def ensure_data():
    if _state["data"] is None:
        cached = pipeline.load()
        if cached:
            _state["data"] = cached
            _state["last_run"] = os.path.getmtime(pipeline.OUT)
    stale = time.time() - _state["last_run"] > STALE_AFTER
    if (_state["data"] is None or stale) and not _state["refreshing"]:
        threading.Thread(target=refresh, kwargs={"max_age": 600}, daemon=True).start()
    return _state["data"]


def _slim(story):
    """List view doesn't need every article body -- keep responses small."""
    out = {k: v for k, v in story.items() if k not in ("all_articles", "articles", "framing")}
    out["framing"] = [{"lean_slug": f["lean_slug"], "lean_label": f["lean_label"],
                       "count": f["count"], "title": f["article"]["title"],
                       "source": f["article"]["source"], "url": f["article"]["url"]}
                      for f in story["framing"]]
    return out


def filter_stories(stories, q):
    text = (q.get("q", [""])[0] or "").strip().lower()
    blindspot = q.get("blindspot", [""])[0]
    min_outlets = int(q.get("min_outlets", ["2"])[0] or 2)
    owner = q.get("owner", [""])[0]
    country = q.get("country", [""])[0]
    sort = q.get("sort", ["rank"])[0]

    out = []
    for story in stories:
        if story["outlet_count"] < min_outlets:
            continue
        if blindspot in ("left", "right") and story["blindspot"] != blindspot:
            continue
        if blindspot == "any" and not story["blindspot"]:
            continue
        if owner and not any(owner == name for name, _ in story["owners"]):
            continue
        if country and country not in story["countries"]:
            continue
        if text:
            hay = story["title"].lower() + " " + story["summary"].lower() + " " + \
                  " ".join(a["source"].lower() for a in story["articles"])
            if text not in hay:
                continue
        out.append(story)

    keys = {
        "rank": lambda s: -s["rank"],
        "outlets": lambda s: -s["outlet_count"],
        "left": lambda s: s["skew"],          # most left-covered first
        "right": lambda s: -s["skew"],
    }
    if sort == "newest":
        out.sort(key=lambda s: (s["last_published"] or ""), reverse=True)
    else:
        out.sort(key=keys.get(sort, keys["rank"]))
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = "Groundish"

    def log_message(self, fmt, *args):
        pass                                             # quiet; we log our own

    def _send(self, code, body, ctype="application/json; charset=utf-8", cache=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache or "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    # ------------------------------------------------------------------ routes
    def do_POST(self):
        if urllib.parse.urlparse(self.path).path == "/api/refresh":
            threading.Thread(target=refresh, daemon=True).start()
            return self._send(202, {"status": "refreshing"})
        self._send(404, {"error": "not found"})

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path.startswith("/api/"):
            return self.api(path, query)
        return self.static(path)

    def api(self, path, query):
        if path == "/api/status":
            data = _state["data"]
            return self._send(200, {
                "refreshing": _state["refreshing"],
                "error": _state["error"],
                "has_data": bool(data),
                "meta": data["meta"] if data else None,
            })

        data = ensure_data()
        if data is None:
            return self._send(503, {"error": "still fetching the first batch — retry shortly",
                                    "refreshing": _state["refreshing"]})

        if path == "/api/stories":
            matched = filter_stories(data["stories"], query)
            limit = int(query.get("limit", ["60"])[0])
            offset = int(query.get("offset", ["0"])[0])
            return self._send(200, {
                "meta": data["meta"],
                "total": len(matched),
                "stories": [_slim(s) for s in matched[offset:offset + limit]],
            })

        if path.startswith("/api/story/"):
            wanted = path.rsplit("/", 1)[-1]
            for story in data["stories"]:
                if story["id"] == wanted:
                    return self._send(200, story)
            return self._send(404, {"error": "no such story"})

        if path == "/api/sources":
            counts = {r["source_id"]: r for r in data["report"]}
            return self._send(200, {
                "sources": [dict(s, **{
                    "article_count": counts.get(s["id"], {}).get("count", 0),
                    "feeds_ok": counts.get(s["id"], {}).get("feeds_ok", 0),
                    "feeds": counts.get(s["id"], {}).get("feeds", len(s["urls"])),
                    "error": counts.get(s["id"], {}).get("error"),
                }) for s in data["sources"]],
                "buckets": [{"lean": l, "slug": s, "label": n} for l, s, n in sources.BUCKETS],
                "meta": data["meta"],
            })

        if path == "/api/meta":
            return self._send(200, data["meta"])

        return self._send(404, {"error": "not found"})

    def static(self, path):
        if path == "/":
            path = "/index.html"
        clean = posixpath.normpath(urllib.parse.unquote(path)).lstrip("/")
        target = os.path.join(WEB, clean)
        if not os.path.abspath(target).startswith(WEB) or not os.path.isfile(target):
            return self._send(404, "not found", "text/plain; charset=utf-8")
        ctype = mimetypes.guess_type(target)[0] or "application/octet-stream"
        with open(target, "rb") as fh:
            body = fh.read()
        if ctype.startswith("text/") or ctype.endswith(("javascript", "json")):
            ctype += "; charset=utf-8"
        self._send(200, body, ctype, cache="no-cache")


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    cached = pipeline.load()
    if cached:
        _state["data"] = cached
        _state["last_run"] = os.path.getmtime(pipeline.OUT)
        age = int((time.time() - _state["last_run"]) / 60)
        _log(f"loaded {len(cached['stories'])} stories from cache ({age} min old)")
    else:
        _log("no cache yet — first request will trigger a fetch")

    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    _log(f"Groundish serving at http://127.0.0.1:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        _log("bye")


if __name__ == "__main__":
    main()
