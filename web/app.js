/* Groundish News front end. No framework, no build step. */
"use strict";

const BUCKETS = [
  ["left", "Left"], ["lean_left", "Lean Left"], ["center", "Center"],
  ["lean_right", "Lean Right"], ["right", "Right"],
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
    const badge = el("div", "badge bs-" + story.blindspot,
      "Blindspot: missing on the " + story.blindspot);
    card.appendChild(badge);
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

  const tldr = consensusBlock(story.consensus, false);
  if (tldr) card.appendChild(tldr);

  card.appendChild(biasBar(story.bar, story.outlet_count));
  card.appendChild(biasLegend(story.bar, story.outlet_count));

  const pills = el("div", "pills");
  const [factCls, factText] = factLabel(story.factuality);
  pills.appendChild(el("span", "pill " + factCls, factText));
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

/* The summary is stitched together from sentences the outlets themselves wrote,
   picked for cross-spectrum agreement — so the label says exactly that. */
function consensusBlock(consensus, open) {
  if (!consensus || !consensus.text) return null;
  const box = el("details", "tldr");
  if (open) box.open = true;
  box.appendChild(el("summary", null, "Summary"));
  const body = el("div", "body");
  body.appendChild(document.createTextNode(consensus.text));
  const via = el("span", "via", consensus.source === "claude"
    ? `Written by ${consensus.model || "Claude"} from the headlines below, using only what ` +
      "outlets across the spectrum report in common — not any single outlet's wording."
    : "Sentences reported in common by " + consensus.outlets.join(" and ") +
      " — chosen because the facts in them recur across the spectrum, not written by Groundish News.");
  body.appendChild(via);
  box.appendChild(body);
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
  const res = await fetch("/api/story/" + encodeURIComponent(id));
  if (!res.ok) return;
  const story = await res.json();
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
  const modalTldr = consensusBlock(story.consensus, true);
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
      const res = await fetch("/api/stories?" + params);
      payload = await res.json();
      if (res.status === 503 && attempt < 45) {
        view.textContent = "";
        view.appendChild(loading("Pulling the first batch of feeds — about ten seconds…"));
        await new Promise((r) => setTimeout(r, 1500));
        continue;
      }
      if (!res.ok) throw new Error(payload.error || "request failed");
      break;
    } catch (err) {
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
  const payload = await (await fetch("/api/sources")).json();
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
      state.meta = await (await fetch("/api/meta")).json();
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
    const status = await (await fetch("/api/status")).json();
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

function init() {
  $("#tabs").addEventListener("click", (event) => {
    const tab = event.target.closest(".tab");
    if (tab) navigate(tab.dataset.view);
  });
  document.querySelectorAll(".inline-tab").forEach((link) => {
    link.addEventListener("click", (event) => { event.preventDefault(); navigate(link.dataset.view); });
  });
  window.addEventListener("hashchange", route);
  $("#refresh").addEventListener("click", async () => {
    await fetch("/api/refresh", { method: "POST" });
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
  fetch("/api/meta").then((r) => r.json()).then((meta) => {
    state.meta = meta;
    updateChrome();
  }).catch(() => {});

  route();
}
init();
