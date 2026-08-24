/* Groundish News front end. No framework, no build step. */
"use strict";

const BUCKETS = [
  ["left", "Left"], ["lean_left", "Lean Left"], ["center", "Center"],
  ["lean_right", "Lean Right"], ["right", "Right"],
  // Outlets found by search that aren't in the ratings registry. Counted and
  // shown, but never used to place a story on the spectrum.
  ["unrated", "Unrated"],
];
const LABEL = Object.fromEntries(BUCKETS);
const READS_KEY = "groundish.reads.v1";

const state = { view: "stories", rendered: null, total: 0, meta: null, sources: null };
const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
};


/* ---------------------------------------------------------------- data source
   The same front end runs two ways: against the Python API when server.py is
   serving it, and against a pre-built JSON bundle on GitHub Pages, where there
   is no server and the browser cannot fetch RSS feeds itself (news sites send
   no CORS headers). It probes for the API once on load and falls back.

   Every path here is relative, because Pages serves the site from a
   /<repo-name>/ sub-path where absolute paths would miss. */
const Data = {
  mode: null,
  bundle: null,

  async init() {
    try {
      const res = await fetch("api/meta", { cache: "no-store" });
      if (res.ok) {
        this.mode = "server";
        return this.mode;
      }
    } catch (_) { /* no API here — fall through to the static bundle */ }
    const res = await fetch("data/bundle.json", { cache: "no-store" });
    if (!res.ok) throw new Error("no API and no data bundle");
    this.bundle = await res.json();
    this.mode = "static";
    return this.mode;
  },

  async meta() {
    if (this.mode === "server") return (await fetch("api/meta")).json();
    return this.bundle.meta;
  },

  async sources() {
    if (this.mode === "server") return (await fetch("api/sources")).json();
    return { sources: this.bundle.sources, meta: this.bundle.meta,
             buckets: BUCKETS.map(([slug, label]) => ({ slug, label })) };
  },

  async story(id) {
    const mine = Added.all().find((s) => s.id === id);
    if (mine) return mine;
    if (this.mode === "server") {
      const res = await fetch("api/story/" + encodeURIComponent(id));
      return res.ok ? res.json() : null;
    }
    return this.bundle.stories.find((s) => s.id === id) || null;
  },

  /* Mirrors filter_stories() and _slim() in server.py so both modes agree. */
  async stories(params) {
    if (this.mode === "server") {
      const res = await fetch("api/stories?" + params);
      const payload = await res.json();
      if (!res.ok) throw new Error(payload.error || "request failed");
      // Stories added by search live in this browser, so they are merged in
      // whichever mode is serving the rest.
      const mine = Added.all();
      payload.stories = mine.concat(payload.stories.filter((s) => !s.added_by));
      payload.total += mine.length;
      return payload;
    }
    const q = (params.get("q") || "").trim().toLowerCase();
    const blindspot = params.get("blindspot") || "";
    const minOutlets = parseInt(params.get("min_outlets") || "2", 10);
    const sort = params.get("sort") || "rank";
    const limit = parseInt(params.get("limit") || "60", 10);

    let out = Added.all().concat(this.bundle.stories).filter((s) => {
      if (s.outlet_count < minOutlets) return false;
      if (blindspot === "any" && !s.blindspot) return false;
      if ((blindspot === "left" || blindspot === "right") && s.blindspot !== blindspot) return false;
      if (q) {
        const hay = (s.title + " " + s.summary + " " +
                     s.articles.map((a) => a.source).join(" ")).toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });

    const keys = {
      rank: (a, b) => b.rank - a.rank,
      outlets: (a, b) => b.outlet_count - a.outlet_count,
      newest: (a, b) => (b.last_published || "").localeCompare(a.last_published || ""),
      left: (a, b) => a.skew - b.skew,
      right: (a, b) => b.skew - a.skew,
    };
    out.sort(keys[sort] || keys.rank);
    // A story you went looking for outranks the feed: its rank is just its outlet
    // count, which would otherwise bury it beneath the day's big wire stories.
    out = out.filter((s) => s.added_by).concat(out.filter((s) => !s.added_by));

    return {
      meta: this.bundle.meta,
      total: out.length,
      stories: out.slice(0, limit).map((s) => ({
        ...s,
        articles: undefined,
        framing: s.framing.map((f) => ({
          lean_slug: f.lean_slug, lean_label: f.lean_label, count: f.count,
          title: f.article.title, source: f.article.source, url: f.article.url,
        })),
      })),
    };
  },
};

/* ------------------------------------------------------------- utilities */
function ago(iso) {
  if (!iso) return "";
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return mins + "m ago";
  const hrs = Math.round(mins / 60);
  if (hrs < 36) return hrs + "h ago";
  return Math.round(hrs / 24) + "d ago";
}
function pct(x) { return Math.round(x * 100) + "%"; }
function factLabel(score) {
  if (score >= 0.85) return ["high", "High factuality"];
  if (score >= 0.65) return ["", "Mostly high factuality"];
  if (score >= 0.4) return ["", "Mixed factuality"];
  return ["low", "Low factuality"];
}

/* ------------------------------------------------- reading-habit tracking */
function reads() {
  try { return JSON.parse(localStorage.getItem(READS_KEY)) || { leans: {}, outlets: {}, total: 0 }; }
  catch (_) { return { leans: {}, outlets: {}, total: 0 }; }
}
function recordRead(article) {
  const data = reads();
  data.leans[article.lean_slug] = (data.leans[article.lean_slug] || 0) + 1;
  data.outlets[article.source] = (data.outlets[article.source] || 0) + 1;
  data.total += 1;
  localStorage.setItem(READS_KEY, JSON.stringify(data));
  if (state.view === "bias") renderBias();
}

/* -------------------------------------------------------------- bias bar */
function biasBar(bar, total) {
  const wrap = el("div", "biasbar");
  for (const [slug, label] of BUCKETS) {
    const n = bar[slug] || 0;
    if (!n) continue;
    const seg = el("span", "seg-" + slug);
    seg.style.width = (100 * n / total) + "%";
    seg.title = `${label}: ${n} outlet${n === 1 ? "" : "s"} (${pct(n / total)})`;
    wrap.appendChild(seg);
  }
  if (!wrap.children.length) wrap.appendChild(el("span", "seg-center")).style.width = "100%";
  return wrap;
}
function biasLegend(bar, total) {
  const wrap = el("div", "biaslegend");
  for (const [slug, label] of BUCKETS) {
    const n = bar[slug] || 0;
    if (!n) continue;
    const item = el("span");
    const swatch = el("i");
    swatch.style.background = `var(--${slug})`;
    item.appendChild(swatch);
    item.appendChild(el("b", null, pct(n / total)));
    item.appendChild(document.createTextNode(" " + label));
    wrap.appendChild(item);
  }
  return wrap;
}

/* ----------------------------------------------------------- story cards */
function storyCard(story) {
  const card = el("article", "story");

  if (story.blindspot) {
    card.appendChild(el("div", "badge bs-" + story.blindspot,
      "Blindspot: missing on the " + story.blindspot));
  }
  if (story.added_by) {
    card.appendChild(el("div", "badge added", "Added by search: " + story.added_by));
  }

  card.appendChild(el("h2", null, story.title));

  const meta = el("p", "story-meta");
  meta.appendChild(el("b", null, story.outlet_count + " outlets"));
  meta.appendChild(el("span", "dot"));
  meta.appendChild(document.createTextNode(story.article_count + " articles"));
  if (story.first_outlet) {
    meta.appendChild(el("span", "dot"));
    meta.appendChild(document.createTextNode("first: " + story.first_outlet));
  }
  if (story.last_published) {
    meta.appendChild(el("span", "dot"));
    meta.appendChild(document.createTextNode(ago(story.last_published)));
  }
  card.appendChild(meta);

  const tldr = consensusBlock(story.consensus, false, story);
  if (tldr) card.appendChild(tldr);

  card.appendChild(biasBar(story.bar, story.outlet_count));
  card.appendChild(biasLegend(story.bar, story.outlet_count));

  const pills = el("div", "pills");
  if (story.factuality != null) {
    const [factCls, factText] = factLabel(story.factuality);
    pills.appendChild(el("span", "pill " + factCls, factText));
  }
  if (story.unrated_count) {
    pills.appendChild(el("span", "pill",
      `${story.unrated_count} unrated outlet${story.unrated_count === 1 ? "" : "s"}`));
  }
  if (story.owner_flag) {
    const pill = el("span", "pill flag");
    pill.appendChild(el("b", null, pct(story.owner_concentration)));
    pill.appendChild(document.createTextNode(" of coverage: " + story.owner_top));
    pills.appendChild(pill);
  }
  if (story.countries.length > 1) {
    pills.appendChild(el("span", "pill", story.countries.length + " countries"));
  }
  card.appendChild(pills);

  const actions = el("div", "story-actions");
  const open = el("button", "btn", "Compare coverage");
  open.addEventListener("click", () => { location.hash = "story/" + story.id; });
  actions.appendChild(open);
  if (story.added_by) {
    const drop = el("button", "btn ghost", "Remove");
    drop.addEventListener("click", () => { Added.remove(story.id); show(state.view); });
    actions.appendChild(drop);
  }
  card.appendChild(actions);

  if (story.framing.length > 1) {
    const framing = el("div", "framing");
    for (const frame of story.framing) {
      const box = el("div", "frame " + frame.lean_slug);
      const head = el("h4", null, frame.lean_label + " ");
      head.appendChild(el("span", null, "· " + frame.count));
      box.appendChild(head);
      box.appendChild(articleLink(frame, frame.lean_slug));
      framing.appendChild(box);
    }
    card.appendChild(framing);
  }
  return card;
}


/* ------------------------------------------------- adding a story by search
   The feed list is a fixed set of front pages, so a story they didn't carry
   cannot appear however widely it was reported. This searches GDELT's global
   news index instead — free, no key, and reachable from a browser because it
   sends `Access-Control-Allow-Origin: *`. Google News RSS does not, which is
   why it can't be used here.

   GDELT asks for no more than one request every five seconds per IP — nothing a
   person clicking a button will approach — and answers a breach with plain text
   rather than JSON, so a non-JSON body is treated as an error.

   Domains in the ratings registry keep their lean; everything else is kept but
   marked unrated and left out of the bias bar, because we have no basis for
   placing it. This mirrors gdelt.py on the Python side. */
const ADDED_STORE = "groundish.added.v1";
const GDELT_ENDPOINT = "https://api.gdeltproject.org/api/v2/doc/doc";

const Added = {
  all() {
    try { return JSON.parse(localStorage.getItem(ADDED_STORE)) || []; }
    catch (_) { return []; }
  },
  save(list) {
    try { localStorage.setItem(ADDED_STORE, JSON.stringify(list)); } catch (_) {}
  },
  add(story) {
    const list = this.all().filter((s) => s.id !== story.id);
    list.unshift(story);
    this.save(list);
  },
  remove(id) { this.save(this.all().filter((s) => s.id !== id)); },
};

function ratedDomains() {
  const sources = (Data.bundle && Data.bundle.sources) || state.sourceList || [];
  const map = {};
  for (const source of sources) {
    for (const url of source.urls || []) {
      // Plain regex rather than `new URL()`, so this stays verifiable outside a
      // browser (JavaScriptCore has no URL global).
      const parsed = /^[a-z]+:\/\/([^/?#]+)/i.exec(url);
      if (!parsed) continue;
      let host = parsed[1].toLowerCase().replace(/:\d+$/, "");
      if (host.includes("news.google.com")) {
        const found = url.match(/site:([\w.\-]+)/);
        host = found ? found[1] : "";
      }
      host = host.replace(/^(www|rss|feeds?|api|moxie|search|chaski|feedx|m)\./, "");
      if (host && !(host in map)) map[host] = source;
    }
  }
  return map;
}

const GDELT_MIN_GAP = 6000;        // GDELT allows one request every five seconds
let lastSearchAt = 0;

const sleep = (ms) => new Promise((done) => setTimeout(done, ms));

function isRateLimited(status, body) {
  return status === 429 || /limit requests|one every 5 seconds/i.test(body);
}

/* GDELT rate-limits by IP, and the allowance is shared by everyone on the same
   connection, so a burst from one machine locks out the whole network for a
   while. Requests are spaced client-side and a refusal is retried with backoff
   rather than surfaced as a dead end. */
async function searchNews(query, onStatus = () => {}) {
  const gap = GDELT_MIN_GAP - (Date.now() - lastSearchAt);
  if (gap > 0) {
    onStatus(`Waiting ${Math.ceil(gap / 1000)}s — one search every 5 seconds…`);
    await sleep(gap);
  }

  const params = new URLSearchParams({
    query, mode: "artlist", maxrecords: "60", format: "json",
    sort: "datedesc", timespan: "7d",
  });

  const attempts = 4;
  for (let attempt = 0; attempt < attempts; attempt++) {
    lastSearchAt = Date.now();
    let res, body;
    try {
      res = await fetch(GDELT_ENDPOINT + "?" + params);
      body = await res.text();
    } catch (err) {
      throw new Error("Could not reach GDELT — check your connection. (" + err.message + ")");
    }
    try {
      return JSON.parse(body).articles || [];
    } catch (_) {
      // A refusal comes back as prose, not JSON.
      if (isRateLimited(res.status, body) && attempt < attempts - 1) {
        const wait = 8 * (attempt + 1);
        onStatus(`Rate limited — retrying in ${wait}s (attempt ${attempt + 2} of ${attempts})…`);
        await sleep(wait * 1000);
        continue;
      }
      if (isRateLimited(res.status, body)) {
        throw new Error(
          "GDELT is rate-limiting this network. It allows one search every five " +
          "seconds per IP address, shared by everyone on your connection. " +
          "Give it a minute and try again.");
      }
      throw new Error(body.trim().slice(0, 180) || `HTTP ${res.status}`);
    }
  }
  return [];
}

/* Rows -> articles, mirroring to_articles() in gdelt.py. */
function toArticles(rows, query) {
  const rated = ratedDomains();
  const seen = new Set();
  const out = [];
  for (const row of rows) {
    const url = row.url || "";
    const title = (row.title || "").split(/\s+/).join(" ").trim();
    if (!url || title.length < 15 || seen.has(url.toLowerCase())) continue;
    seen.add(url.toLowerCase());

    const host = (row.domain || "").toLowerCase()
      .replace(/^(www|rss|feeds?|api|moxie|search|chaski|feedx|m)\./, "");
    const source = rated[host];
    let when = null;
    const stamp = /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$/.exec(row.seendate || "");
    if (stamp) {
      when = new Date(Date.UTC(+stamp[1], +stamp[2] - 1, +stamp[3],
                               +stamp[4], +stamp[5], +stamp[6]));
    }
    out.push({
      id: url.toLowerCase().slice(-16),
      title, summary: "", url,
      published: when ? when.toISOString() : null,
      published_ts: when ? when.getTime() / 1000 : null,
      source_id: source ? source.id : "gdelt:" + host,
      source: source ? source.name : host,
      lean: source ? source.lean : null,
      lean_slug: source ? source.lean_slug : "unrated",
      lean_label: source ? source.lean_label : "Unrated",
      factuality: source ? source.factuality : "unrated",
      owner: source ? source.owner : host,
      country: source ? source.country : (row.sourcecountry || "?"),
      rated: Boolean(source),
      added_by: query,
    });
  }
  return out;
}

/* A compact counterpart to analyze.single_story(). The query already defines the
   story, so nothing is clustered — the whole result set is one story. */
function buildStory(articles, query) {
  const bySource = new Map();
  for (const article of articles) {
    if (!bySource.has(article.source_id)) bySource.set(article.source_id, article);
  }
  const outlets = [...bySource.values()];
  const rated = outlets.filter((a) => a.lean !== null);

  const bar = {};
  for (const [slug] of BUCKETS) bar[slug] = 0;
  for (const a of outlets) bar[a.lean_slug] = (bar[a.lean_slug] || 0) + 1;

  const owners = {};
  for (const a of outlets) owners[a.owner] = (owners[a.owner] || 0) + 1;
  const ownerList = Object.entries(owners).sort((x, y) => y[1] - x[1]);

  const framing = [];
  for (const [slug, label] of BUCKETS) {
    const side = outlets.filter((a) => a.lean_slug === slug);
    if (side.length) {
      framing.push({ lean_slug: slug, lean_label: label, count: side.length,
                     article: side[0] });
    }
  }

  const times = articles.map((a) => a.published_ts).filter(Boolean);
  const lead = rated.find((a) => a.lean === 0) || rated[0] || outlets[0];
  const scale = { high: 1, "mostly-high": 0.75, mixed: 0.45, low: 0.1 };
  const factScores = rated.map((a) => (a.factuality in scale ? scale[a.factuality] : 0.5));
  let hash = 7;
  for (const ch of query.toLowerCase()) hash = (hash * 31 + ch.charCodeAt(0)) | 0;

  return {
    id: "q" + Math.abs(hash).toString(16),
    title: lead ? lead.title : query,
    title_source: lead ? lead.source : "",
    summary: "",
    consensus: null,
    added_by: query,
    article_count: articles.length,
    outlet_count: outlets.length,
    rated_count: rated.length,
    unrated_count: outlets.length - rated.length,
    bar,
    shares: {},
    camp_rates: { left: 0, center: 0, right: 0 },
    skew: rated.length
      ? Math.round((rated.reduce((t, a) => t + a.lean, 0) / rated.length) * 1000) / 1000
      : 0,
    // A blindspot compares coverage rates across the whole polled pool; a single
    // search has no pool to compare against, so it abstains.
    blindspot: null,
    factuality: factScores.length
      ? factScores.reduce((a, b) => a + b, 0) / factScores.length : null,
    owner_top: ownerList.length ? ownerList[0][0] : "",
    owner_top_count: ownerList.length ? ownerList[0][1] : 0,
    owner_concentration: ownerList.length ? ownerList[0][1] / outlets.length : 0,
    owner_flag: false,
    owners: ownerList,
    countries: [...new Set(outlets.map((a) => a.country))].sort(),
    first_published: times.length ? new Date(Math.min(...times) * 1000).toISOString() : null,
    last_published: times.length ? new Date(Math.max(...times) * 1000).toISOString() : null,
    first_outlet: lead ? lead.source : null,
    rank: outlets.length,
    framing,
    articles: outlets.sort((a, b) =>
      (a.lean === null ? 9 : a.lean) - (b.lean === null ? 9 : b.lean)),
  };
}

/* --------------------------------------------------- on-demand LLM summaries
   Summaries are written when someone asks for one, not for all 200 stories up
   front — Gemini's free tier allows only ~20 requests per model per day.

   The key is never in this repository. On a static host the visitor supplies
   their own and it lives in their browser's localStorage; when server.py is
   serving, the key stays server-side and the browser just asks /api/summarize.
   Either way, if every model's quota is spent the story keeps the summary that
   summarize.py already produced, with a note saying why. */
const KEY_STORE = "groundish.gemini_key";
const SUMMARY_STORE = "groundish.summaries.v1";
const GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/";

const Keys = {
  get() { try { return localStorage.getItem(KEY_STORE) || ""; } catch (_) { return ""; } },
  set(value) {
    try {
      if (value) localStorage.setItem(KEY_STORE, value);
      else localStorage.removeItem(KEY_STORE);
    } catch (_) { /* private browsing */ }
  },
};

const Written = {
  all() {
    try { return JSON.parse(localStorage.getItem(SUMMARY_STORE)) || {}; }
    catch (_) { return {}; }
  },
  get(id) { return this.all()[id] || null; },
  set(id, entry) {
    const all = this.all();
    all[id] = entry;
    try { localStorage.setItem(SUMMARY_STORE, JSON.stringify(all)); } catch (_) {}
  },
};

/* Can this page write a summary at all? */
function canGenerate() {
  return Data.mode === "server" || Boolean(Keys.get());
}

/* Mirrors build_prompt() in llm_summary.py. */
function buildPrompt(story) {
  const seen = new Set();
  const lines = [];
  for (const article of story.articles || []) {
    if (seen.has(article.source_id)) continue;
    seen.add(article.source_id);
    lines.push(`[${article.lean_label}] ${article.source}: ${article.title}`);
    if (lines.length >= 26) break;
  }
  return "Headlines for one story:\n\n" + lines.join("\n");
}

async function callGemini(model, prompt, system, key) {
  const res = await fetch(GEMINI_ENDPOINT + encodeURIComponent(model) + ":generateContent", {
    method: "POST",
    headers: { "Content-Type": "application/json", "x-goog-api-key": key },
    body: JSON.stringify({
      systemInstruction: { parts: [{ text: system }] },
      contents: [{ parts: [{ text: prompt }] }],
      generationConfig: { temperature: 0.2, maxOutputTokens: 8000 },
    }),
  });
  const payload = await res.json().catch(() => ({}));
  if (!res.ok) {
    const message = (payload.error && payload.error.message) || `HTTP ${res.status}`;
    const err = new Error(message);
    err.quota = res.status === 429 || /quota|exhausted/i.test(message);
    throw err;
  }
  const text = (((payload.candidates || [])[0] || {}).content?.parts || [])
    .map((part) => part.text || "").join(" ").trim();
  if (!text) throw new Error("empty response");
  return text;
}

/* -> {text, model} or throws. Walks the model chain so a spent daily quota on
   one model moves to the next instead of failing outright. */
async function writeSummary(storyId) {
  const story = await Data.story(storyId);
  if (!story) throw new Error("story not found");

  if (Data.mode === "server") {
    const res = await fetch("api/summarize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: storyId }),
    });
    const payload = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(payload.error || `HTTP ${res.status}`);
    return { text: payload.text, model: payload.model };
  }

  const key = Keys.get();
  if (!key) throw new Error("no Gemini key set");
  const system = Data.bundle.prompt;
  const models = Data.bundle.models || ["gemini-3.6-flash"];
  const prompt = buildPrompt(story);

  let last = null;
  for (const model of models) {
    try {
      return { text: await callGemini(model, prompt, system, key), model };
    } catch (err) {
      last = err;
      if (!err.quota) break;      // a real error: stop walking the chain
    }
  }
  throw last || new Error("all models unavailable");
}

/* The summary is stitched together from sentences the outlets themselves wrote,
   picked for cross-spectrum agreement — so the label says exactly that. */
function consensusBlock(consensus, open, story) {
  const written = story ? Written.get(story.id) : null;
  const current = written || consensus;
  const hasText = Boolean(current && current.text);
  // Roughly one story in eight has no extract: summarize.py works from the RSS
  // description field, and some feeds ship none. Those are exactly the stories
  // an LLM is most useful for, so the block still renders — with the button and
  // an explanation — rather than disappearing and taking the button with it.
  const canWrite = Boolean(story && canGenerate() && !(written && written.text));
  if (!hasText && !canWrite) return null;

  const box = el("details", "tldr");
  if (open) box.open = true;

  const head = el("summary", null, "Summary");
  box.appendChild(head);
  const body = el("div", "body");
  box.appendChild(body);

  const paint = (entry, note) => {
    body.textContent = "";
    if (entry && entry.text) {
      body.appendChild(document.createTextNode(entry.text));
      const via = el("span", "via");
      if (entry.source === "claude" || entry.model) {
        via.textContent = `Written by ${entry.model || "an LLM"} from the headlines below, ` +
          "using only what outlets across the spectrum report in common — not any " +
          "single outlet's wording.";
      } else {
        via.textContent = "Sentences reported in common by " +
          (entry.outlets || []).join(" and ") +
          " — chosen because the facts in them recur across the spectrum.";
      }
      body.appendChild(via);
    } else {
      const empty = el("span", "via",
        "No summary yet — the feeds carrying this story publish headlines without " +
        "the description text the extractive summariser needs. Use the button above " +
        "to have one written from the headlines instead.");
      body.appendChild(empty);
    }
    if (note) body.appendChild(el("span", "via note", note));
  };
  paint(current);

  if (canWrite) {
    const button = el("button", "gen", hasText ? "Write with Gemini" : "Write a summary");
    button.addEventListener("click", async (event) => {
      event.preventDefault();
      box.open = true;
      button.disabled = true;
      button.textContent = "Writing";
      button.classList.add("dots");
      try {
        const entry = await writeSummary(story.id);
        Written.set(story.id, entry);
        button.remove();
        paint(entry);
      } catch (err) {
        // Degrade to whatever we already had — the extract where one exists, the
        // explanation where it doesn't — rather than leaving the reader nothing.
        button.disabled = false;
        button.classList.remove("dots");
        button.textContent = "Retry";
        paint(current, "Gemini unavailable (" + err.message.slice(0, 90) + ")" +
                       (hasText ? " — showing the consensus extract instead." : "."));
      }
    });
    head.appendChild(button);
  }
  return box;
}

function articleLink(item, leanSlug) {
  const link = el("a");
  link.href = item.url || "#";
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.appendChild(el("span", "hl", item.title));
  link.appendChild(el("span", "src", item.source));
  link.addEventListener("click", () => recordRead({
    lean_slug: item.lean_slug || leanSlug, source: item.source,
  }));
  return link;
}

/* -------------------------------------------------------------- the modal */
async function openStory(id) {
  const story = await Data.story(id);
  if (!story) return;
  const body = $("#modal-body");
  body.textContent = "";

  if (story.blindspot) {
    body.appendChild(el("div", "badge bs-" + story.blindspot,
      "Blindspot: missing on the " + story.blindspot));
  }
  const heading = el("h2", null, story.title);
  heading.id = "modal-title";
  body.appendChild(heading);

  const meta = el("p", "story-meta",
    `${story.outlet_count} outlets · ${story.article_count} articles · ` +
    `${story.countries.join(", ")} · headline shown from ${story.title_source}`);
  body.appendChild(meta);
  const modalTldr = consensusBlock(story.consensus, true, story);
  if (modalTldr) body.appendChild(modalTldr);
  body.appendChild(biasBar(story.bar, story.outlet_count));
  body.appendChild(biasLegend(story.bar, story.outlet_count));

  if (story.blindspot) {
    const rates = story.camp_rates;
    body.appendChild(el("p", "story-meta",
      `Coverage rate by side: left ${pct(rates.left)} of left-leaning outlets, ` +
      `center ${pct(rates.center)}, right ${pct(rates.right)}. ` +
      `Blindspots compare these rates rather than raw article counts, ` +
      `because the source list itself is not evenly balanced.`));
  }

  body.appendChild(el("h3", null, "How each side headlined it"));
  const framing = el("div", "framing");
  for (const frame of story.framing) {
    const box = el("div", "frame " + frame.lean_slug);
    const head = el("h4", null, frame.lean_label + " ");
    head.appendChild(el("span", null, "· " + frame.count + " outlets"));
    box.appendChild(head);
    box.appendChild(articleLink(frame.article, frame.lean_slug));
    framing.appendChild(box);
  }
  body.appendChild(framing);

  body.appendChild(el("h3", null, "Every outlet carrying this story"));
  for (const article of story.articles) {
    const row = el("div", "outlet-row");
    const tag = el("span", "outlet-tag");
    const swatch = el("i");
    swatch.style.background = `var(--${article.lean_slug})`;
    swatch.title = article.lean_label;
    tag.appendChild(swatch);
    tag.appendChild(document.createTextNode(article.source));
    row.appendChild(tag);

    const link = el("a", null, article.title);
    link.href = article.url || "#";
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.addEventListener("click", () => recordRead(article));
    row.appendChild(link);
    row.appendChild(el("span", "when", ago(article.published)));
    body.appendChild(row);
  }

  body.appendChild(el("h3", null, "Who owns this coverage"));
  const shared = story.owners.filter(([, count]) => count > 1);
  const solo = story.owners.length - shared.length;
  if (!shared.length) {
    body.appendChild(el("p", "story-meta",
      `No parent company owns more than one of the ${story.outlet_count} outlets here — ` +
      `this story's coverage is ownership-diverse.`));
  } else {
    const table = el("table", "owners");
    const widest = shared[0][1];
    for (const [name, count] of shared) {
      const tr = el("tr");
      tr.appendChild(el("td", null, name));
      const cell = el("td");
      const meter = el("div", "meter");
      meter.style.width = (100 * count / widest) + "%";
      cell.appendChild(meter);
      tr.appendChild(cell);
      tr.appendChild(el("td", null, `${count} outlets · ${pct(count / story.outlet_count)}`));
      table.appendChild(tr);
    }
    body.appendChild(table);
    if (solo) {
      body.appendChild(el("p", "story-meta",
        `Plus ${solo} outlet${solo === 1 ? "" : "s"} with no shared parent company.`));
    }
  }

  $("#modal-backdrop").hidden = false;
  document.body.style.overflow = "hidden";
}

function closeModal(keepHash) {
  $("#modal-backdrop").hidden = true;
  document.body.style.overflow = "";
  if (!keepHash && location.hash.startsWith("#story/")) location.hash = state.view;
}

/* ------------------------------------------------------------- the views */
function loading(message) {
  const wrap = el("div", "empty");
  wrap.appendChild(el("div", "spinner"));
  wrap.appendChild(el("p", null, message));
  return wrap;
}

async function renderStories(onlyBlindspots) {
  const view = $("#view");
  view.textContent = "";
  view.appendChild(loading("Reading the feeds…"));

  const params = new URLSearchParams({
    q: $("#q").value,
    sort: $("#sort").value,
    min_outlets: onlyBlindspots ? "2" : $("#min_outlets").value,
    limit: "60",
  });
  if (onlyBlindspots) params.set("blindspot", "any");

  // On a cold start the server is still pulling ~100 feeds and answers 503.
  // Wait it out rather than showing the user an error on their first visit.
  let payload;
  for (let attempt = 0; ; attempt++) {
    try {
      payload = await Data.stories(params);
      break;
    } catch (err) {
      // On a cold start the server is still pulling ~100 feeds and answers 503.
      // Wait it out rather than showing an error on someone's first visit.
      if (/still fetching|503/.test(err.message) && attempt < 45) {
        view.textContent = "";
        view.appendChild(loading("Pulling the first batch of feeds — about ten seconds…"));
        await new Promise((r) => setTimeout(r, 1500));
        continue;
      }
      view.textContent = "";
      view.appendChild(el("div", "empty", err.message));
      return;
    }
  }

  state.meta = payload.meta;
  state.total = payload.total;
  updateChrome();

  view.textContent = "";
  if (onlyBlindspots) {
    const note = el("div", "prose");
    note.appendChild(el("p", null,
      "A blindspot is a story one side of the spectrum is largely not covering. " +
      "Groundish News compares how many of the left-leaning outlets it polled ran the story " +
      "against how many right-leaning ones did — rates, not raw counts, because this " +
      "source list carries more left-of-centre outlets than right-of-centre ones."));
    view.appendChild(note);
    view.appendChild(el("div", null, " "));
  }

  if (!payload.stories.length) {
    view.appendChild(el("div", "empty",
      onlyBlindspots ? "No blindspots in the current batch. Try refreshing the feeds."
                     : "No stories match those filters."));
    return;
  }
  for (const story of payload.stories) view.appendChild(storyCard(story));
  if (payload.total > payload.stories.length) {
    view.appendChild(el("p", "empty",
      `Showing ${payload.stories.length} of ${payload.total} stories.`));
  }
}

async function renderSources() {
  const view = $("#view");
  view.textContent = "";
  view.appendChild(loading("Loading sources…"));
  const payload = await Data.sources();
  state.sources = payload;
  state.meta = payload.meta;
  updateChrome();
  view.textContent = "";

  const meta = payload.meta;
  const stats = el("div", "stat-row");
  const addStat = (value, label) => {
    const box = el("div", "stat");
    box.appendChild(el("b", null, value));
    box.appendChild(el("span", null, label));
    stats.appendChild(box);
  };
  addStat(payload.sources.length, "outlets polled");
  addStat(payload.sources.reduce((n, s) => n + s.feeds, 0), "RSS feeds");
  addStat(meta.article_count, "articles in this batch");
  addStat(meta.story_count, "multi-source stories");
  view.appendChild(stats);

  for (const [slug, label] of BUCKETS) {
    const group = payload.sources.filter((s) => s.lean_slug === slug);
    if (!group.length) continue;
    const head = el("div", "section-head");
    head.appendChild(el("h3", null, label));
    head.appendChild(el("span", null, `${group.length} outlets · ` +
      `${group.reduce((n, s) => n + s.article_count, 0)} articles`));
    view.appendChild(head);

    const grid = el("div", "grid");
    for (const source of group.sort((a, b) => b.article_count - a.article_count)) {
      const card = el("div", "card " + slug);
      card.appendChild(el("span", "n", String(source.article_count)));
      card.appendChild(el("h4", null, source.name));
      card.appendChild(el("p", null,
        `${source.owner} · ${source.country} · ${source.factuality} factuality`));
      if (source.error) {
        card.appendChild(el("p", null, `⚠ ${source.feeds_ok}/${source.feeds} feeds responded`));
      }
      grid.appendChild(card);
    }
    view.appendChild(grid);
  }

  const head = el("div", "section-head");
  head.appendChild(el("h3", null, "Ownership concentration"));
  head.appendChild(el("span", null, "parent companies behind this batch"));
  view.appendChild(head);
  const table = el("table", "owners");
  const top = meta.top_owners[0][1];
  for (const [name, count] of meta.top_owners) {
    const tr = el("tr");
    tr.appendChild(el("td", null, name));
    const cell = el("td");
    const meter = el("div", "meter");
    meter.style.width = (100 * count / top) + "%";
    cell.appendChild(meter);
    tr.appendChild(cell);
    tr.appendChild(el("td", null, count + " articles"));
    table.appendChild(tr);
  }
  view.appendChild(table);
}

async function renderBias() {
  const view = $("#view");
  view.textContent = "";
  const data = reads();

  // The comparison bar needs batch meta; on a direct #bias load it may not
  // have arrived yet, so wait for it rather than silently dropping the bar.
  if (!state.meta) {
    try {
      state.meta = await Data.meta();
      updateChrome();
    } catch (_) { /* comparison bar is optional */ }
  }

  if (!data.total) {
    const box = el("div", "prose");
    box.appendChild(el("h3", null, "My Bias"));
    box.appendChild(el("p", null,
      "Nothing tracked yet. Every time you open an article from Groundish News, the lean of " +
      "the outlet you clicked is tallied here — locally, in this browser only. Read a few " +
      "stories and come back to see the shape of your own diet."));
    view.appendChild(box);
    return;
  }

  const bar = {};
  for (const [slug] of BUCKETS) bar[slug] = data.leans[slug] || 0;

  const box = el("div", "prose");
  box.appendChild(el("h3", null, `Your reading — ${data.total} articles opened`));
  box.appendChild(biasBar(bar, data.total));
  box.appendChild(biasLegend(bar, data.total));

  if (state.meta) {
    box.appendChild(el("h3", null, "What was on offer"));
    box.appendChild(el("p", null,
      "For comparison, this is the lean distribution of every article Groundish News pulled " +
      "in the current batch. A gap between the two bars is the interesting part."));
    const total = Object.values(state.meta.bar).reduce((a, b) => a + b, 0);
    box.appendChild(biasBar(state.meta.bar, total));
    box.appendChild(biasLegend(state.meta.bar, total));
  }

  const leanScore = BUCKETS.reduce((sum, [slug], i) => sum + (bar[slug] || 0) * (i - 2), 0) / data.total;
  const verdict = Math.abs(leanScore) < 0.35 ? "fairly balanced"
    : leanScore < 0 ? "tilted left" : "tilted right";
  box.appendChild(el("h3", null, "Read on the whole"));
  box.appendChild(el("p", null,
    `Your average article sits at ${leanScore.toFixed(2)} on a −2 (left) to +2 (right) ` +
    `scale — ${verdict}.`));

  box.appendChild(el("h3", null, "Outlets you open most"));
  const table = el("table", "owners");
  const ranked = Object.entries(data.outlets).sort((a, b) => b[1] - a[1]).slice(0, 12);
  for (const [name, count] of ranked) {
    const tr = el("tr");
    tr.appendChild(el("td", null, name));
    const cell = el("td");
    const meter = el("div", "meter");
    meter.style.width = (100 * count / ranked[0][1]) + "%";
    cell.appendChild(meter);
    tr.appendChild(cell);
    tr.appendChild(el("td", null, String(count)));
    table.appendChild(tr);
  }
  box.appendChild(table);

  const reset = el("button", "btn ghost", "Reset my history");
  reset.style.marginTop = "18px";
  reset.addEventListener("click", () => { localStorage.removeItem(READS_KEY); renderBias(); });
  box.appendChild(reset);
  view.appendChild(box);
}

function renderAbout() {
  const view = $("#view");
  view.innerHTML = `
<div class="prose">
  <h3>What this does</h3>
  <p>Groundish News pulls <strong>public RSS feeds from ${state.meta ? state.meta.source_count : "60+"} news outlets</strong>
     spanning the political spectrum, groups the articles that describe the same event into a single
     story, and shows you who covered it, how they headlined it, and who is missing.</p>

  <h3>How stories are grouped</h3>
  <p>Headlines are turned into TF-IDF vectors (the summary contributes at a lower weight), and an
     inverted index over rare tokens produces candidate pairs so we never run a full N² comparison.
     Those pairs are merged strongest-first by <strong>average-link agglomerative clustering</strong>:
     two groups only join if their centroids are still similar, which prevents the chaining failure
     where A resembles B and B resembles C, so three unrelated stories collapse into one.</p>

  <h3>Where the summaries come from</h3>
  <p>There is no language model here and no API key, so each summary is <strong>extractive</strong>:
     it reuses sentences the outlets themselves published. The selection is what makes it
     non-partisan. A sentence scores well when the facts in it are repeated independently by many
     outlets, and especially when those outlets sit on <em>different sides</em> of the spectrum —
     a detail Fox News, the Associated Press and Mother Jones all bothered to print is very likely
     the uncontested part of the story. Sentences carrying opinion markers, second-person address
     or rhetorical questions are pushed down; centre outlets break ties. It is a consensus extract,
     not neutral prose written from scratch, and the app says so under every summary.</p>

  <h3>How blindspots are decided</h3>
  <p>A blindspot is a story that one side of the spectrum is largely not running. Rather than compare
     raw shares of coverage, Groundish News compares <strong>coverage rates</strong>: of the right-leaning
     outlets polled, what fraction carried this story, versus the left-leaning ones? Any hand-built
     source list is lopsided — this one has more left-of-centre outlets than right-of-centre — and a
     raw share would flag a blindspot on the right for nearly everything. A story qualifies when it
     has at least 5 outlets, one side's rate is at or below 7%, another side's is at least 12% across
     at least 3 outlets, and the louder side out-covers the quieter one by 2.5×.</p>

  <h3>What the labels are, and are not</h3>
  <p>The lean, factuality and ownership values live in one editable table in <code>sources.py</code>.
     They are <strong>hand-encoded approximations</strong> of ratings published by AllSides, Ad Fontes
     and Media Bias/Fact Check. They are not licensed data, they describe an outlet's overall output
     rather than any single article, and media-bias ratings are contested and US-centric to begin with.
     Disagree with one? Change the number and re-run — everything downstream reads from that table.</p>

  <h3>Honest limits</h3>
  <ul>
    <li>RSS feeds carry recent front-page and section items, not an outlet's full output. An outlet
        can look silent on a story it covered outside the sampling window.</li>
    <li>Several international feeds are world-news oriented while several US partisan feeds are
        politics-oriented, so some apparent blindspots are really topic-mix artefacts.</li>
    <li>Clustering is lexical. Two outlets describing the same event in very different words may
        stay in separate stories.</li>
    <li>The bias bar counts <em>outlets</em>, not audience size. Five small sites and five networks
        weigh the same.</li>
  </ul>

  <h3>Running it</h3>
  <p><code>python3 server.py</code> serves the site and refreshes feeds when the cache is older than
     15 minutes. <code>python3 pipeline.py</code> rebuilds <code>data/stories.json</code> on its own.
     No dependencies beyond the Python standard library.</p>
</div>`;
}

function setupAddPanel() {
  const panel = $("#addpanel");
  const input = $("#addinput");
  const status = $("#addstate");
  const result = $("#addresult");

  $("#addbtn").addEventListener("click", () => {
    panel.hidden = !panel.hidden;
    if (!panel.hidden) input.focus();
  });
  document.addEventListener("click", (event) => {
    if (!panel.hidden && !panel.contains(event.target) && event.target !== $("#addbtn")) {
      panel.hidden = true;
    }
  });

  const run = async () => {
    const query = input.value.trim();
    if (query.length < 3) { status.textContent = "Type a few more characters"; return; }
    $("#addgo").disabled = true;
    status.textContent = "Searching world coverage…";
    result.textContent = "";
    try {
      const articles = toArticles(
        await searchNews(query, (text) => { status.textContent = text; }), query);
      if (!articles.length) {
        status.textContent = "No coverage found in the last 7 days";
        return;
      }
      const story = buildStory(articles, query);
      Added.add(story);
      status.textContent = "";
      result.textContent = "";
      const rated = story.rated_count;
      result.appendChild(el("div", "hit",
        `Added — ${story.outlet_count} outlets` +
        (rated ? `, ${rated} with a bias rating.` :
                 `, none of them in the ratings registry, so this story has no bias bar.`)));
      for (const article of story.articles.slice(0, 8)) {
        const hit = el("div", "hit");
        hit.appendChild(el("b", null, article.source));
        hit.appendChild(document.createTextNode(" — " + article.title.slice(0, 70)));
        result.appendChild(hit);
      }
      show(state.view);
    } catch (err) {
      status.textContent = "";
      result.textContent = "";
      result.appendChild(el("div", "hit", err.message));
    } finally {
      $("#addgo").disabled = false;
    }
  };
  $("#addgo").addEventListener("click", run);
  input.addEventListener("keydown", (event) => { if (event.key === "Enter") run(); });
}

function setupKeyPanel() {
  const panel = $("#keypanel");
  const input = $("#keyinput");
  const state_ = $("#keystate");
  const paint = () => {
    const key = Keys.get();
    state_.textContent = key ? "Key saved in this browser" : "No key set";
    $("#keybtn").textContent = key ? "Gemini key ✓" : "Gemini key";
  };
  paint();

  $("#keybtn").addEventListener("click", () => {
    panel.hidden = !panel.hidden;
    if (!panel.hidden) {
      input.value = Keys.get();
      input.focus();
    }
  });
  $("#keysave").addEventListener("click", () => {
    Keys.set(input.value.trim());
    paint();
    panel.hidden = true;
    show(state.view);                 // re-render so the buttons appear
  });
  $("#keyclear").addEventListener("click", () => {
    Keys.set("");
    input.value = "";
    paint();
    show(state.view);
  });
  document.addEventListener("click", (event) => {
    if (!panel.hidden && !panel.contains(event.target) && event.target !== $("#keybtn")) {
      panel.hidden = true;
    }
  });
}

/* ------------------------------------------------------------- app chrome */
function updateChrome() {
  const meta = state.meta;
  if (!meta) return;
  const count = meta.blindspots.left + meta.blindspots.right;
  $("#blindspot-count").textContent = count ? String(count) : "";
  $("#result-count").textContent = state.view === "stories"
    ? `${state.total} stories · ${meta.article_count} articles from ${meta.sources_ok}/${meta.source_count} outlets`
    : "";
  $("#freshness").textContent = "Updated " + ago(meta.generated);
}

function show(view) {
  state.view = view;
  state.rendered = view;
  for (const tab of document.querySelectorAll(".tab")) {
    tab.classList.toggle("active", tab.dataset.view === view);
  }
  $("#filters").hidden = view !== "stories";
  $("#result-count").textContent = "";
  window.scrollTo(0, 0);
  if (view === "stories") renderStories(false);
  else if (view === "blindspots") renderStories(true);
  else if (view === "sources") renderSources();
  else if (view === "bias") renderBias();
  else renderAbout();
}

async function pollRefresh() {
  $("#refresh").disabled = true;
  $("#freshness").classList.add("busy");
  $("#freshness").textContent = "Fetching feeds…";
  for (let i = 0; i < 90; i++) {
    await new Promise((r) => setTimeout(r, 1000));
    const status = await (await fetch("api/status")).json();
    if (!status.refreshing) {
      state.meta = status.meta;
      break;
    }
  }
  $("#refresh").disabled = false;
  $("#freshness").classList.remove("busy");
  show(state.view);
}

/* Views and open stories live in the URL hash, so any of them can be linked. */
function navigate(view) {
  if (location.hash.replace(/^#/, "") === view) show(view);
  else location.hash = view;
}

function route() {
  const hash = decodeURIComponent(location.hash.replace(/^#/, ""));
  if (hash.startsWith("story/")) {
    openStory(hash.slice("story/".length));
    if (!state.rendered) show(state.view);
    return;
  }
  closeModal(true);
  const view = hash || "stories";
  if (view !== state.rendered) show(view);
}

async function init() {
  try {
    await Data.init();
  } catch (err) {
    $("#view").appendChild(el("div", "empty", "Could not load story data: " + err.message));
    return;
  }
  if (Data.mode === "static") {
    // No server to poke, but the CI build may have deployed newer data since
    // this page loaded — so the button re-fetches the bundle instead.
    $("#refresh").textContent = "Reload data";
    $("#keybtn").hidden = false;
  }
  setupKeyPanel();
  setupAddPanel();

  $("#tabs").addEventListener("click", (event) => {
    const tab = event.target.closest(".tab");
    if (tab) navigate(tab.dataset.view);
  });
  document.querySelectorAll(".inline-tab").forEach((link) => {
    link.addEventListener("click", (event) => { event.preventDefault(); navigate(link.dataset.view); });
  });
  window.addEventListener("hashchange", route);
  $("#refresh").addEventListener("click", async () => {
    if (Data.mode === "static") {
      const button = $("#refresh");
      button.disabled = true;
      button.textContent = "Reloading";
      try {
        const res = await fetch("data/bundle.json?t=" + Date.now(), { cache: "no-store" });
        Data.bundle = await res.json();
        state.meta = Data.bundle.meta;
        updateChrome();
        show(state.view);
      } catch (_) { /* keep whatever is on screen */ }
      button.disabled = false;
      button.textContent = "Reload data";
      return;
    }
    await fetch("api/refresh", { method: "POST" });
    pollRefresh();
  });
  $("#modal-close").addEventListener("click", () => closeModal());
  $("#modal-backdrop").addEventListener("click", (event) => {
    if (event.target === $("#modal-backdrop")) closeModal();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeModal();
  });

  let timer;
  const rerun = () => {
    clearTimeout(timer);
    timer = setTimeout(() => show(state.view === "blindspots" ? "blindspots" : "stories"), 220);
  };
  $("#q").addEventListener("input", rerun);
  $("#sort").addEventListener("change", rerun);
  $("#min_outlets").addEventListener("change", rerun);

  // Header chrome (freshness, blindspot count) should be right on any entry view.
  Data.meta().then((meta) => {
    state.meta = meta;
    updateChrome();
  }).catch(() => {});

  route();
}
init();
