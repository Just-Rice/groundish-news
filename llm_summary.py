"""LLM-written story summaries, with the extractive summarizer as the fallback.

This is the only part of Groundish News that is not standard-library-only, and it
is entirely optional. It activates when both of these are true:

    pip install anthropic          # the official Anthropic SDK
    export ANTHROPIC_API_KEY=...   # or `ant auth login`

Without either, `available()` returns False, nothing here runs, and stories keep
the consensus extract that summarize.py produces. Same for an API error, a rate
limit, or a refusal — every failure path falls back rather than breaking a refresh.

COST CONTROL
    Summaries are cached on disk and keyed to a story's identity, not to the
    refresh that produced it. Feeds are re-pulled every 15 minutes but only ~90
    genuinely new stories appear per day, so a refresh typically makes a handful
    of calls rather than a few hundred. Without this cache the same 200 stories
    would be re-summarized 96 times a day for no benefit.
"""
import concurrent.futures as futures
import hashlib
import json
import os
import threading
import time

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
CACHE_PATH = os.path.join(DATA, "summaries.json")

DEFAULT_MODEL = os.environ.get("GROUNDISH_MODEL", "claude-opus-5")
MAX_HEADLINES = 26          # bounds prompt size on very widely-covered stories
CACHE_MAX_AGE = 14 * 86400  # forget summaries for stories that fell out of the feeds

SYSTEM = """\
You write the short neutral summary that sits under a story on Groundish News, an \
aggregator that shows the same event as reported across the political spectrum.

You are given the headlines every outlet used for one story, each labelled with that \
outlet's political lean. Write two sentences, around 45 words, describing what happened.

- Include only what outlets on different sides report in common. Where they conflict, \
either leave it out or attribute it ("Ukrainian officials say...").
- Use the plainest available wording for people, groups and actions. Avoid any term that \
appears on only one side of the spectrum.
- How many outlets sit on each side reflects which feeds this app polls, not what is true. \
A detail carried by many outlets on one side and none on the other is contested framing, \
not consensus.
- No opinion, no claims about significance, nothing about what a story "raises" or "sparks".
- Do not mention outlets, headlines, coverage, or the spectrum itself. Describe the event.

Return the summary text and nothing else."""

_client = None
_client_lock = threading.Lock()
_cache_lock = threading.Lock()


def _has_credentials():
    """Is there any credential the SDK could resolve?

    Worth checking up front: `Anthropic()` constructs happily with no credentials
    and only fails when a request is made, so without this every story in a refresh
    would fire a doomed request before falling back.
    """
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return True
    if os.environ.get("ANTHROPIC_IDENTITY_TOKEN_FILE") or os.environ.get("ANTHROPIC_IDENTITY_TOKEN"):
        return True                                # workload identity federation
    config = os.environ.get("ANTHROPIC_CONFIG_DIR") or os.path.expanduser("~/.config/anthropic")
    creds = os.path.join(config, "credentials")
    return os.path.isdir(creds) and any(f.endswith(".json") for f in os.listdir(creds))


def _get_client():
    """Build the SDK client once. Returns None if unavailable — never raises."""
    global _client
    with _client_lock:
        if _client is None:
            try:
                import anthropic
                _client = anthropic.Anthropic() if _has_credentials() else False
            except Exception:                      # noqa: BLE001 - absence is a valid state
                _client = False
    return _client or None


def available():
    return _get_client() is not None


# ------------------------------------------------------------------ the cache
def _cache_key(members):
    """Identify a story by the articles that started it.

    Keying on the whole membership would miss the cache every time one more
    outlet picked the story up — which is constantly. The earliest few articles
    are the stable seed: they stay put as coverage grows, so the summary is
    written once and reused for the life of the story.
    """
    seeds = sorted(members, key=lambda a: (a.get("published_ts") or 0))[:3]
    ident = "|".join(sorted((a.get("url") or a["title"]).lower() for a in seeds))
    return hashlib.sha1(ident.encode()).hexdigest()[:20]


def load_cache():
    try:
        with open(CACHE_PATH) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_cache(cache):
    cutoff = time.time() - CACHE_MAX_AGE
    fresh = {k: v for k, v in cache.items() if v.get("created", 0) > cutoff}
    os.makedirs(DATA, exist_ok=True)
    tmp = CACHE_PATH + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(fresh, fh, separators=(",", ":"))
    os.replace(tmp, CACHE_PATH)
    return fresh


# ------------------------------------------------------------------ the prompt
def build_prompt(story):
    members = story.get("all_articles") or story.get("articles") or []
    by_source, lines = {}, []
    for art in members:
        if art["source_id"] in by_source:
            continue
        by_source[art["source_id"]] = art
        lines.append(f"[{art['lean_label']}] {art['source']}: {art['title']}")
        if len(lines) >= MAX_HEADLINES:
            break
    # A few summaries add detail the headlines alone don't carry.
    detail = [a["summary"] for a in by_source.values() if a.get("summary")][:4]
    prompt = "Headlines for one story:\n\n" + "\n".join(lines)
    if detail:
        prompt += "\n\nOpening lines from some of those reports:\n\n" + "\n\n".join(detail)
    return prompt


def summarize_one(story, model=DEFAULT_MODEL, effort="low"):
    """One story -> summary dict, or None on any failure."""
    client = _get_client()
    if client is None:
        return None
    try:
        response = client.messages.create(
            model=model,
            max_tokens=3000,          # room for adaptive thinking plus ~45 words
            system=SYSTEM,
            output_config={"effort": effort},
            messages=[{"role": "user", "content": build_prompt(story)}],
        )
    except Exception as exc:                       # noqa: BLE001 - degrade, never break
        return {"error": f"{type(exc).__name__}: {exc}"}

    if response.stop_reason == "refusal":
        return {"error": "refusal"}
    text = " ".join(b.text for b in response.content if b.type == "text").strip()
    if not text:
        return {"error": f"empty response (stop_reason={response.stop_reason})"}

    usage = response.usage
    return {
        "text": text,
        "model": response.model,
        "source": "claude",
        "created": time.time(),
        "usage": {
            "input": usage.input_tokens,
            "output": usage.output_tokens,
            "cache_read": getattr(usage, "cache_read_input_tokens", 0) or 0,
        },
    }


# ------------------------------------------------------------------ the driver
def apply(stories, model=DEFAULT_MODEL, max_workers=8, limit=None, log=print):
    """Upgrade each story's `consensus` to an LLM summary where possible.

    Cached stories cost nothing. Anything that fails keeps the extract it already
    had. Returns a stats dict; never raises.
    """
    stats = {"cached": 0, "written": 0, "failed": 0, "skipped": 0,
             "input_tokens": 0, "output_tokens": 0, "model": model}
    if not available():
        stats["skipped"] = len(stories)
        return stats

    cache = load_cache()
    todo = []
    for story in stories:
        members = story.get("all_articles") or story.get("articles") or []
        key = _cache_key(members)
        story["_summary_key"] = key
        hit = cache.get(key)
        if hit and hit.get("text"):
            story["consensus"] = {"text": hit["text"], "outlets": [], "leans": [],
                                  "source": "claude", "model": hit.get("model", model)}
            stats["cached"] += 1
        else:
            todo.append(story)

    if limit is not None:
        stats["skipped"] += max(0, len(todo) - limit)
        todo = todo[:limit]
    if not todo:
        return stats

    log(f"  summarizing {len(todo)} new stories with {model}…")

    # If the API is refusing everything — bad key, hard rate limit, outage — stop
    # early rather than firing one doomed request per story.
    abort = threading.Event()
    consecutive = [0]
    first_error = [None]

    def work(story):
        if abort.is_set():
            return story, None
        result = summarize_one(story, model=model)
        with _cache_lock:
            if result and "error" in result:
                consecutive[0] += 1
                if first_error[0] is None:
                    first_error[0] = result["error"]
                if consecutive[0] >= 3:
                    abort.set()
            else:
                consecutive[0] = 0
        return story, result

    with futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        for story, result in pool.map(work, todo):
            if not result or "error" in result:
                stats["failed"] += 1
                continue          # story keeps its extractive consensus
            stats["written"] += 1
            stats["input_tokens"] += result["usage"]["input"]
            stats["output_tokens"] += result["usage"]["output"]
            with _cache_lock:
                cache[story["_summary_key"]] = result
            story["consensus"] = {"text": result["text"], "outlets": [], "leans": [],
                                  "source": "claude", "model": result["model"]}

    if first_error[0]:
        stats["error"] = first_error[0]
        if log:
            log(f"  ! {stats['failed']} summaries fell back to the extract "
                f"({'gave up early: ' if abort.is_set() else ''}{first_error[0]})")
    save_cache(cache)
    return stats
