"""LLM-written story summaries, with the extractive summarizer as the fallback.

This is the only part of Groundish News that is not standard-library-only, and it
is entirely optional. Two providers are supported; pick whichever you have.

    # Google Gemini — has a genuinely free API tier, no credit card
    pip install google-genai
    export GEMINI_API_KEY=...       # from aistudio.google.com/apikey

    # Anthropic Claude — no free tier, pay-as-you-go
    pip install anthropic
    export ANTHROPIC_API_KEY=...    # or `ant auth login`

Whichever key is present is used; set GROUNDISH_LLM=gemini|anthropic to force one.
With neither, `available()` returns False, nothing here runs, and stories keep the
consensus extract that summarize.py produces. Same for an API error, a rate limit,
or a refusal — every failure path falls back rather than breaking a refresh.

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
import random
import threading
import time

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
CACHE_PATH = os.path.join(DATA, "summaries.json")

# Comma-separated chains are tried in order. Google's free-tier quota is scoped
# per project *per model* (the quotaId is literally
# "GenerateRequestsPerDayPerProjectPerModel-FreeTier"), so each model carries its
# own daily allowance and falling through to the next one when a quota is spent
# multiplies the usable budget on a single key. Extra API keys do NOT help —
# they share the project's quota.
DEFAULT_MODELS = {
    "gemini": "gemini-3.6-flash,gemini-3.5-flash,gemini-3-flash-preview,"
              "gemini-3.1-flash-lite,gemini-3.5-flash-lite,gemini-3.7-flash",
    "anthropic": "claude-opus-5",
}
QUOTA_MARKERS = ("resource_exhausted", "quota", "429")
QUOTA_COOLDOWN = 2 * 3600      # stop retrying a spent model for a while
# Google's free tier allows roughly 10 requests/minute. Requests are spaced out
# rather than fired in parallel, because a 429 here costs a whole summary.
PROVIDER_RPM = {"gemini": 10, "anthropic": 0}     # 0 = no client-side throttle
PROVIDER_WORKERS = {"gemini": 2, "anthropic": 8}

# Free tiers return 503/429 under load far more often than paid ones, and a
# transient overload should not cost a story its summary.
RETRY_ATTEMPTS = 4
RETRY_BASE_DELAY = 2.0
RETRYABLE = ("429", "500", "502", "503", "504", "unavailable", "overloaded",
             "resource_exhausted", "rate limit", "timeout", "deadline")

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

_clients = {}
_client_lock = threading.Lock()
_cache_lock = threading.Lock()
_rate_lock = threading.Lock()
_last_call = [0.0]
_exhausted = {}                # model -> time its daily quota ran out


def provider():
    """Which backend to use: an explicit override, else whichever key exists."""
    forced = os.environ.get("GROUNDISH_LLM", "").strip().lower()
    if forced in DEFAULT_MODELS:
        return forced
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        return "gemini"
    return "anthropic"


def default_model(name=None):
    return os.environ.get("GROUNDISH_MODEL") or DEFAULT_MODELS[name or provider()]


def _throttle(rpm):
    """Space requests out so a free-tier rate limit isn't tripped."""
    if not rpm:
        return
    interval = 60.0 / rpm
    with _rate_lock:
        wait = _last_call[0] + interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.monotonic()


def _has_credentials():
    """Is there any credential the SDK could resolve?

    Worth checking up front: `Anthropic()` constructs happily with no credentials
    and only fails when a request is made, so without this every story in a refresh
    would fire a doomed request before falling back.
    """
    if provider() == "gemini":
        return bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return True
    if os.environ.get("ANTHROPIC_IDENTITY_TOKEN_FILE") or os.environ.get("ANTHROPIC_IDENTITY_TOKEN"):
        return True                                # workload identity federation
    config = os.environ.get("ANTHROPIC_CONFIG_DIR") or os.path.expanduser("~/.config/anthropic")
    creds = os.path.join(config, "credentials")
    return os.path.isdir(creds) and any(f.endswith(".json") for f in os.listdir(creds))


def _get_client(name=None):
    """Build the SDK client once per provider. None if unavailable — never raises."""
    name = name or provider()
    with _client_lock:
        if name not in _clients:
            client = False
            try:
                if not _has_credentials():
                    raise RuntimeError("no credentials")
                if name == "gemini":
                    from google import genai
                    key = os.environ.get("GEMINI_API_KEY") or os.environ["GOOGLE_API_KEY"]
                    client = genai.Client(api_key=key)
                else:
                    import anthropic
                    client = anthropic.Anthropic()
            except Exception:                      # noqa: BLE001 - absence is a valid state
                client = False
            _clients[name] = client
    return _clients[name] or None


def available(name=None):
    return _get_client(name) is not None


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


def parse_model(spec):
    """"gemini-3.7-flash#think" -> ("gemini-3.7-flash", True).

    Thinking is off by default for this task: a 45-word factual summary rarely
    needs it, and on Gemini the thinking budget competes with the answer for the
    same output-token allowance. The suffix turns it back on so the two can be
    compared directly.
    """
    if spec and "#" in spec:
        base, _, flag = spec.partition("#")
        return base, flag.lower() in ("think", "thinking", "extended")
    return spec, False


def summarize_one(story, model=None, effort="low", name=None, thinking=False):
    """One story -> summary dict, or {"error": ...} on failure. Never raises.

    `model` may be a comma-separated chain. Each entry is tried in turn; a model
    whose daily quota is spent is remembered and skipped for a couple of hours so
    later stories in the same run don't waste a request rediscovering it.
    """
    name = name or provider()
    chain = model_chain(model or default_model(name))
    if not chain:
        return {"error": "daily quota spent on every model in the chain"}
    client = _get_client(name)
    if client is None:
        return {"error": "no credentials"}

    last = None
    for spec in chain:
        base_model, spec_thinking = parse_model(spec)
        want_thinking = thinking or spec_thinking
        for attempt in range(RETRY_ATTEMPTS):
            _throttle(PROVIDER_RPM.get(name, 0))
            try:
                if name == "gemini":
                    return _call_gemini(client, base_model, story, want_thinking)
                return _call_anthropic(client, base_model, story, effort)
            except Exception as exc:               # noqa: BLE001 - degrade, never break
                last = f"{type(exc).__name__}: {exc}"
                if _is_quota(last):
                    _exhausted[spec] = time.monotonic()
                    break                          # move to the next model
                if attempt == RETRY_ATTEMPTS - 1 or not _retryable(last):
                    break
                # Backoff with jitter so parallel workers don't retry in lockstep.
                time.sleep(RETRY_BASE_DELAY * (2 ** attempt) + random.uniform(0, 1.5))
        else:
            continue
        if not _is_quota(last or ""):
            break                                  # a real error: stop walking
    return {"error": last or "unknown failure"}


def _retryable(message):
    low = message.lower()
    if _is_quota(message):
        return False           # a spent daily quota will not recover on retry
    return any(token in low for token in RETRYABLE)


def _is_quota(message):
    low = message.lower()
    return any(token in low for token in QUOTA_MARKERS)


def model_chain(spec):
    """"a,b,c" -> ["a", "b", "c"], dropping models with a spent daily quota."""
    models = [m.strip() for m in (spec or "").split(",") if m.strip()]
    now = time.monotonic()
    # When every model in the chain is spent, return nothing rather than burning
    # another request to rediscover it — daily quotas reset on Google's clock,
    # not ours.
    return [m for m in models if now - _exhausted.get(m, -1e9) > QUOTA_COOLDOWN]


def _result(text, model, tin, tout):
    return {"text": " ".join(text.split()), "model": model, "source": "claude",
            "created": time.time(),
            "usage": {"input": tin or 0, "output": tout or 0, "cache_read": 0}}


def _call_anthropic(client, model, story, effort):
    response = client.messages.create(
        model=model,
        max_tokens=3000,              # room for adaptive thinking plus ~45 words
        system=SYSTEM,
        output_config={"effort": effort},
        messages=[{"role": "user", "content": build_prompt(story)}],
    )
    if response.stop_reason == "refusal":
        return {"error": "refusal"}
    text = " ".join(b.text for b in response.content if b.type == "text").strip()
    if not text:
        return {"error": f"empty response (stop_reason={response.stop_reason})"}
    out = _result(text, response.model, response.usage.input_tokens,
                  response.usage.output_tokens)
    out["usage"]["cache_read"] = getattr(response.usage, "cache_read_input_tokens", 0) or 0
    return out


def _call_gemini(client, model, story, thinking=False):
    from google.genai import types

    base = {
        "system_instruction": SYSTEM,
        # Gemini counts thinking toward this budget, so leave real headroom when
        # thinking is on or the answer gets squeezed out entirely.
        "max_output_tokens": 8000 if thinking else 1200,
        "temperature": 0.2,
    }
    # -1 lets the model decide how much to think; 0 turns it off. Not every model
    # accepts the field — several reject a 0 budget with 400 INVALID_ARGUMENT
    # because thinking is mandatory for them — so fall back to the model default.
    configs = []
    try:
        configs.append(dict(base, thinking_config=types.ThinkingConfig(
            thinking_budget=-1 if thinking else 0)))
    except Exception:                              # noqa: BLE001 - older SDKs lack it
        pass
    configs.append(dict(base, max_output_tokens=8000))

    response, last = None, None
    for index, config in enumerate(configs):
        try:
            response = client.models.generate_content(
                model=model, contents=build_prompt(story),
                config=types.GenerateContentConfig(**config))
            break
        except Exception as exc:                   # noqa: BLE001
            last = exc
            if index == len(configs) - 1 or "INVALID_ARGUMENT" not in str(exc):
                raise
    if response is None:
        raise last

    text = (getattr(response, "text", None) or "").strip()
    if not text:
        reason = getattr(response, "prompt_feedback", None)
        return {"error": f"empty response ({reason})"}
    usage = getattr(response, "usage_metadata", None)
    thought = getattr(usage, "thoughts_token_count", 0) or 0
    out = _result(text, model + ("#think" if thinking else ""),
                  getattr(usage, "prompt_token_count", 0),
                  (getattr(usage, "candidates_token_count", 0) or 0) + thought)
    out["usage"]["thinking"] = thought
    return out


# ------------------------------------------------------------------ the driver
def apply(stories, model=None, max_workers=None, limit=None, log=print):
    """Upgrade each story's `consensus` to an LLM summary where possible.

    Cached stories cost nothing. Anything that fails keeps the extract it already
    had. Returns a stats dict; never raises.
    """
    name = provider()
    model = model or default_model(name)
    if max_workers is None:
        max_workers = PROVIDER_WORKERS.get(name, 4)
    stats = {"cached": 0, "written": 0, "failed": 0, "skipped": 0, "input_tokens": 0,
             "output_tokens": 0, "model": model, "provider": name, "models_used": {}}
    if not available(name):
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

    rpm = PROVIDER_RPM.get(name, 0)
    eta = f", ~{len(todo) * 60 // rpm // 60 or 1} min at {rpm}/min" if rpm else ""
    log(f"  summarizing {len(todo)} new stories with {model}{eta}…")

    # If the API is refusing everything — bad key, hard rate limit, outage — stop
    # early rather than firing one doomed request per story.
    abort = threading.Event()
    consecutive = [0]
    first_error = [None]

    def work(story):
        if abort.is_set():
            return story, None
        result = summarize_one(story, model=model, name=name)
        with _cache_lock:
            if result and "error" in result:
                consecutive[0] += 1
                if first_error[0] is None:
                    first_error[0] = result["error"]
                # Only give up early on errors that retrying cannot fix — a bad
                # key or a bad model name. Overloads already exhausted retries.
                if consecutive[0] >= 3 and not _retryable(result["error"]):
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
            served = result.get("model", "?")
            stats["models_used"][served] = stats["models_used"].get(served, 0) + 1
            stats["input_tokens"] += result["usage"]["input"]
            stats["output_tokens"] += result["usage"]["output"]
            with _cache_lock:
                cache[story["_summary_key"]] = result
            story["consensus"] = {"text": result["text"], "outlets": [], "leans": [],
                                  "source": "claude", "model": result["model"]}

    if first_error[0]:
        brief = " ".join(first_error[0].split())[:140]
        stats["error"] = brief
        if log:
            log(f"  ! {stats['failed']} summaries fell back to the extract "
                f"({'gave up early — ' if abort.is_set() else ''}{brief})")
    save_cache(cache)
    return stats
