"""Group articles from different outlets into stories.

This is the piece everything else depends on: if the clustering is wrong, the
bias bars and blindspots describe nothing. The approach is deliberately simple
and dependency-free:

  1. TF-IDF vectors over headline tokens (the summary contributes at a lower
     weight -- headlines carry the signal, summaries add recall).
  2. An inverted index restricted to *discriminative* tokens gives candidate
     pairs, so we never do the full N^2 comparison.
  3. Average-link agglomerative merging over those candidates: pairs are
     considered strongest-first, and two clusters only join if their centroids
     are still similar enough. Centroid checking is what stops the classic
     single-link failure where A~B and B~C chain unrelated stories together.
"""
import math
import re
from collections import defaultdict

TOKEN_RE = re.compile(r"[a-z0-9']+")

STOPWORDS = set("""
a about above after again against all am an and any are aren't as at be because been
before being below between both but by can cannot could couldn't did didn't do does
doesn't doing don't down during each few for from further had hadn't has hasn't have
haven't having he he'd he'll he's her here here's hers herself him himself his how
how's i i'd i'll i'm i've if in into is isn't it it's its itself let's me more most
mustn't my myself no nor not of off on once only or other ought our ours ourselves out
over own same shan't she she'd she'll she's should shouldn't so some such than that
that's the their theirs them themselves then there there's these they they'd they'll
they're they've this those through to too under until up very was wasn't we we'd we'll
we're we've were weren't what what's when when's where where's which while who who's
whom why why's with won't would wouldn't you you'd you'll you're you've your yours
yourself yourselves
says said say saying new news report reports reported amid over back may might will
just like get gets got make makes made take takes taken first last one two three
year years day days week weeks month months time times today yesterday tomorrow
top best worst big biggest live update updates latest video watch photos opinion
analysis exclusive breaking heres whats trump's people man woman
""".split())

# A token seen in more than this fraction of the corpus is too common to block on.
MAX_BLOCK_DF = 0.06
# Tokens rarer than this many docs can't help either -- they appear in one article.
MIN_BLOCK_DF = 2
SIM_THRESHOLD = 0.32
MIN_SHARED = 2          # candidate pairs must share at least this many tokens
MAX_GAP_DAYS = 6.0      # never merge articles this far apart in time


def tokenize(text):
    return [t for t in TOKEN_RE.findall(text.lower())
            if t not in STOPWORDS and (len(t) > 2 or t.isdigit())]


def build_vectors(articles, summary_weight=0.35):
    """-> (vectors, df) where each vector is an L2-normalised {token: weight}."""
    raw = []
    df = defaultdict(int)
    for art in articles:
        counts = defaultdict(float)
        for tok in tokenize(art["title"]):
            counts[tok] += 1.0
        for tok in tokenize(art.get("summary") or ""):
            counts[tok] += summary_weight
        raw.append(counts)
        for tok in counts:
            df[tok] += 1

    n = max(len(articles), 1)
    vectors = []
    for counts in raw:
        vec = {}
        for tok, tf in counts.items():
            idf = math.log(1.0 + n / df[tok])
            vec[tok] = (1.0 + math.log(tf)) * idf
        norm = math.sqrt(sum(w * w for w in vec.values())) or 1.0
        vectors.append({t: w / norm for t, w in vec.items()})
    return vectors, df


def cosine(a, b):
    if len(a) > len(b):
        a, b = b, a
    return sum(w * b[t] for t, w in a.items() if t in b)


def candidate_pairs(articles, vectors, df):
    """Inverted-index blocking: only compare docs sharing a discriminative token."""
    n = len(articles)
    hi = max(MIN_BLOCK_DF + 1, int(n * MAX_BLOCK_DF))
    postings = defaultdict(list)
    for i, vec in enumerate(vectors):
        for tok in vec:
            if MIN_BLOCK_DF <= df[tok] <= hi:
                postings[tok].append(i)

    shared = defaultdict(int)
    for docs in postings.values():
        for a_idx in range(len(docs)):
            for b_idx in range(a_idx + 1, len(docs)):
                shared[(docs[a_idx], docs[b_idx])] += 1

    pairs = []
    for (i, j), count in shared.items():
        if count < MIN_SHARED:
            continue
        ti, tj = articles[i].get("published_ts"), articles[j].get("published_ts")
        if ti and tj and abs(ti - tj) > MAX_GAP_DAYS * 86400:
            continue
        sim = cosine(vectors[i], vectors[j])
        if sim >= SIM_THRESHOLD:
            pairs.append((sim, i, j))
    pairs.sort(reverse=True)
    return pairs


def _merge_centroids(a, b, size_a, size_b):
    out = defaultdict(float)
    for tok, w in a.items():
        out[tok] += w * size_a
    for tok, w in b.items():
        out[tok] += w * size_b
    norm = math.sqrt(sum(w * w for w in out.values())) or 1.0
    return {t: w / norm for t, w in out.items()}


def cluster(articles, threshold=SIM_THRESHOLD):
    """-> list of clusters, each a list of indices into `articles`."""
    if not articles:
        return []
    vectors, df = build_vectors(articles)
    pairs = candidate_pairs(articles, vectors, df)

    parent = list(range(len(articles)))
    centroid = [dict(v) for v in vectors]
    size = [1] * len(articles)

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for sim, i, j in pairs:
        ri, rj = find(i), find(j)
        if ri == rj:
            continue
        # Average-link guard: the merged groups must still resemble each other.
        if cosine(centroid[ri], centroid[rj]) < threshold * 0.92:
            continue
        merged = _merge_centroids(centroid[ri], centroid[rj], size[ri], size[rj])
        big, small = (ri, rj) if size[ri] >= size[rj] else (rj, ri)
        parent[small] = big
        centroid[big] = merged
        size[big] = size[ri] + size[rj]

    groups = defaultdict(list)
    for idx in range(len(articles)):
        groups[find(idx)].append(idx)
    return sorted(groups.values(), key=len, reverse=True)


def centrality(articles, indices):
    """Rank a cluster's articles by how typical they are -> best headline first."""
    vectors, _ = build_vectors([articles[i] for i in indices])
    scores = []
    for pos, i in enumerate(indices):
        others = [v for k, v in enumerate(vectors) if k != pos]
        mean = sum(cosine(vectors[pos], o) for o in others) / len(others) if others else 1.0
        scores.append((mean, i))
    scores.sort(reverse=True)
    return [i for _, i in scores]
