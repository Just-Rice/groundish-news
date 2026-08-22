"""Build a short, non-partisan summary of a story from the coverage itself.

There is no language model here and no API key, so the summary is *extractive*:
it picks sentences already written by the outlets covering the story. The
selection is what makes it non-partisan. A sentence scores well when the facts
it contains are independently repeated by many outlets, and especially when
those outlets sit on different sides of the spectrum — a detail that Fox News,
the Associated Press and Mother Jones all bothered to print is very likely the
uncontested part of the story. Sentences carrying opinion markers, second-person
address or attribution hedges are pushed down, and centre outlets get a small
edge as a tie-breaker.

The result is honest about what it is: a consensus extract, not neutral prose
written from scratch. The UI labels it that way.
"""
import re
from collections import defaultdict

import cluster

# Split on sentence enders followed by something that looks like a new sentence --
# but not after a title or initialism, or "Sen. Graham" becomes two sentences.
_ABBREV = ["Sen", "Rep", "Gov", "Sgt", "Lt", "Col", "Gen", "Maj", "Capt", "Adm",
           "Dr", "Mr", "Mrs", "Ms", "Prof", "Atty", "Jr", "Sr", "St", "Mt",
           "Inc", "Corp", "Ltd", "Co", "No", "vs", "etc", "al", "Jan", "Feb",
           "Mar", "Apr", "Jun", "Jul", "Aug", "Sept", "Sep", "Oct", "Nov", "Dec"]
SENT_SPLIT = re.compile(
    r'(?<=[.!?])'
    + "".join(r"(?<!\b%s\.)" % a for a in _ABBREV)
    + r'(?<!\bU\.S\.)(?<!\bU\.K\.)(?<!\bU\.N\.)(?<!\bD\.C\.)(?<!\ba\.m\.)(?<!\bp\.m\.)'
    + r'(?<![A-Z]\.)'                       # single initials: "John F. Kennedy"
    + r'\s+(?=[A-Z"“‘])')

# Wire-service datelines: "MYRTLE BEACH, S.C. — President Trump…"
DATELINE = re.compile(r'^[A-Z][A-Za-z.\s,\'’-]{1,38}?\s+[—–]\s+')

# Feed boilerplate that is never part of the story.
BOILERPLATE = re.compile(r"""
      ^\s*(read\s+more|continue\s+reading|subscribe|sign\s+up|advertisement|share\s+this)
    | appeared\s+first\s+on
    | click\s+here
    | ^\s*the\s+post\b
    | ^\s*by\s+[A-Z][a-z]+\s+[A-Z]
    | ^\s*photo(graph)?s?\s*:
    | \bgetty\s+images\b
    | ^\s*\(?(reuters|ap|afp)\)?\s*[-–—]\s
    | \bfile\s+photo\b
    | ^\s*updated?\s*:
""", re.I | re.X)

# Markers of commentary rather than reporting.
OPINION = set("""
should must ought shame shameful disgrace disgraceful outrageous outrage stunning
shocking brilliant disaster catastrophe hero villain radical extremist woke thug
regime slams blasts rips destroys eviscerates smears hoax witchhunt insane crazy
pathetic disgusting beautiful terrible awful amazing incredible absurd ridiculous
i we you my our your me us think thinks believe believes feel feels opinion
argues arguing frankly honestly obviously clearly surely simply merely just
""".split())

MIN_LEN, MAX_LEN = 45, 300


def _clean(text):
    text = re.sub(r"\s+", " ", text or "").strip()
    text = re.sub(r"^[\-–—•\s]+", "", text)
    return text


def _jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _candidates(members):
    """Every usable sentence across the cluster, tagged with its outlet."""
    out = []
    for art in members:
        body = DATELINE.sub("", _clean(art.get("summary")))
        if not body:
            continue
        # feeds.py truncates long summaries with an ellipsis; the tail is a fragment.
        truncated = body.endswith("…")
        parts = SENT_SPLIT.split(body)
        if truncated and len(parts) > 1:
            parts = parts[:-1]
        for position, sentence in enumerate(parts):
            sentence = _clean(sentence)
            if not (MIN_LEN <= len(sentence) <= MAX_LEN):
                continue
            if BOILERPLATE.search(sentence):
                continue
            if not re.search(r"[.!?]$", sentence):
                continue
            if sentence.count(",") > 6 or sentence.count('"') % 2:
                continue
            out.append({"text": sentence, "position": position, "article": art})
    return out


def _support(members):
    """token -> (how many outlets used it, how many distinct camps used it)."""
    outlets = defaultdict(set)
    camps = defaultdict(set)
    for art in members:
        camp = "left" if art["lean"] < 0 else ("right" if art["lean"] > 0 else "center")
        seen = set(cluster.tokenize(art["title"] + " " + (art.get("summary") or "")))
        for token in seen:
            outlets[token].add(art["source_id"])
            camps[token].add(camp)
    return outlets, camps


def _score(candidate, outlets, camps, outlet_total):
    tokens = set(cluster.tokenize(candidate["text"]))
    if len(tokens) < 4:
        return 0.0, tokens

    agreement = 0.0
    for token in tokens:
        shared = len(outlets.get(token, ()))
        if shared < 2:
            continue                          # nobody else mentioned it
        span = len(camps.get(token, ()))      # 1..3 sides of the spectrum
        agreement += (shared / outlet_total) * (span ** 1.6)

    score = agreement / (len(tokens) ** 0.5)

    lowered = candidate["text"].lower()
    opinionated = sum(1 for word in re.findall(r"[a-z']+", lowered) if word in OPINION)
    score *= 0.55 ** opinionated

    art = candidate["article"]
    if art["lean"] == 0:
        score *= 1.18                          # centre outlets break ties
    elif abs(art["lean"]) == 1:
        score *= 1.05
    if candidate["position"] == 0:
        score *= 1.12                          # news leads carry the facts
    if re.search(r"\?$", candidate["text"]):
        score *= 0.5                           # headlines-as-questions aren't facts
    return score, tokens


def summarize(members, max_chars=300):
    """-> {text, outlets, camps} for a cluster, or None if there's nothing usable."""
    outlet_total = len({a["source_id"] for a in members}) or 1
    candidates = _candidates(members)
    if not candidates:
        return None

    outlets, camps = _support(members)
    scored = []
    for candidate in candidates:
        score, tokens = _score(candidate, outlets, camps, outlet_total)
        if score > 0:
            scored.append((score, candidate, tokens))
    if not scored:
        return None
    scored.sort(key=lambda row: -row[0])

    best_score, best, best_tokens = scored[0]
    chosen = [best]
    used = set(best_tokens)

    # A second sentence only earns its place if it adds new facts and fits.
    for score, candidate, tokens in scored[1:]:
        if score < best_score * 0.45:
            break
        if candidate["text"] == best["text"]:
            continue
        fresh = tokens - used
        if len(fresh) < max(3, len(tokens) * 0.5):
            continue
        if _jaccard(tokens, best_tokens) > 0.45:
            continue                          # near-duplicate of the lead sentence
        if len(best["text"]) + 1 + len(candidate["text"]) > max_chars:
            continue
        chosen.append(candidate)
        used |= tokens
        break

    chosen.sort(key=lambda c: (c["article"]["source"], c["position"]))
    text = " ".join(c["text"] for c in chosen)
    contributors = sorted({c["article"]["source"] for c in chosen})
    spectrum = sorted({c["article"]["lean_label"] for c in chosen})
    return {"text": text, "outlets": contributors, "leans": spectrum}
