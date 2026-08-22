"""Source registry: the feeds Groundish News pulls, plus the metadata that makes
the aggregation meaningful (political lean, factuality, owner, country).

BIAS SCALE
    -2 left   -1 lean left   0 center   +1 lean right   +2 right

The lean and factuality values here are hand-encoded approximations of the
published ratings that AllSides, Ad Fontes and Media Bias/Fact Check give these
outlets. They are NOT licensed data and they are not gospel: media-bias ratings
are contested, US-centric, and they describe an outlet's overall output rather
than any single article. Treat them as a starting point for comparison, not a
verdict. Edit freely -- everything downstream reads from this one table.
"""

# lean -> (slug, display label) used everywhere in the UI
BUCKETS = [
    (-2, "left", "Left"),
    (-1, "lean_left", "Lean Left"),
    (0, "center", "Center"),
    (1, "lean_right", "Lean Right"),
    (2, "right", "Right"),
]
LEAN_SLUG = {lean: slug for lean, slug, _ in BUCKETS}
LEAN_LABEL = {lean: label for lean, _, label in BUCKETS}
SLUG_LEAN = {slug: lean for lean, slug, _ in BUCKETS}


def S(id, name, urls, lean, factuality, owner, country="US"):
    """One outlet. `urls` is a single feed URL or a list of them -- big outlets
    split their output across section feeds, and sampling only the front page
    makes an outlet look like it ignored a story it actually covered."""
    if isinstance(urls, str):
        urls = [urls]
    return {
        "id": id,
        "name": name,
        "urls": urls,
        "url": urls[0],
        "lean": lean,
        "lean_slug": LEAN_SLUG[lean],
        "lean_label": LEAN_LABEL[lean],
        "factuality": factuality,      # high | mostly-high | mixed | low
        "owner": owner,
        "country": country,
    }


SOURCES = [
    # ---------------------------------------------------------------- left
    S("motherjones", "Mother Jones", "https://www.motherjones.com/feed/", -2, "mostly-high", "Foundation for National Progress"),
    S("intercept", "The Intercept", "https://theintercept.com/feed/?rss", -2, "mostly-high", "First Look Institute"),
    S("vox", "Vox", "https://www.vox.com/rss/index.xml", -2, "mostly-high", "Vox Media"),
    S("slate", "Slate", "https://slate.com/feeds/all.rss", -2, "mixed", "Graham Holdings"),
    S("dailybeast", "The Daily Beast", "https://www.thedailybeast.com/arc/outboundfeeds/rss/", -2, "mixed", "IAC"),
    S("huffpost", "HuffPost", "https://www.huffpost.com/section/politics/feed", -2, "mixed", "BuzzFeed, Inc."),
    S("newyorker", "The New Yorker", "https://www.newyorker.com/feed/news", -2, "high", "Advance Publications"),
    S("jacobin", "Jacobin", "https://jacobin.com/feed/", -2, "mixed", "Jacobin Foundation"),
    S("commondreams", "Common Dreams", "https://www.commondreams.org/feeds/news.rss", -2, "mixed", "Common Dreams"),
    S("truthout", "Truthout", "https://truthout.org/feed/", -2, "mixed", "Truthout"),

    # ----------------------------------------------------------- lean left
    S("nyt", "The New York Times", ["https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml", "https://rss.nytimes.com/services/xml/rss/nyt/Politics.xml", "https://rss.nytimes.com/services/xml/rss/nyt/US.xml", "https://rss.nytimes.com/services/xml/rss/nyt/World.xml"], -1, "high", "The New York Times Company"),
    S("wapo", "The Washington Post", ["https://feeds.washingtonpost.com/rss/national", "https://feeds.washingtonpost.com/rss/politics", "https://feeds.washingtonpost.com/rss/world"], -1, "high", "Nash Holdings"),
    S("npr", "NPR", ["https://feeds.npr.org/1001/rss.xml", "https://feeds.npr.org/1014/rss.xml", "https://feeds.npr.org/1003/rss.xml"], -1, "high", "NPR (nonprofit)"),
    S("pbs", "PBS NewsHour", "https://www.pbs.org/newshour/feeds/rss/headlines", -1, "high", "PBS (nonprofit)"),
    S("cnn", "CNN", ["http://rss.cnn.com/rss/cnn_topstories.rss", "http://rss.cnn.com/rss/cnn_allpolitics.rss", "http://rss.cnn.com/rss/cnn_us.rss", "http://rss.cnn.com/rss/cnn_world.rss"], -1, "mostly-high", "Warner Bros. Discovery"),
    S("nbc", "NBC News", ["https://feeds.nbcnews.com/nbcnews/public/news", "https://feeds.nbcnews.com/nbcnews/public/politics", "https://feeds.nbcnews.com/nbcnews/public/world"], -1, "mostly-high", "Comcast / NBCUniversal"),
    S("cbs", "CBS News", ["https://www.cbsnews.com/latest/rss/main", "https://www.cbsnews.com/latest/rss/politics", "https://www.cbsnews.com/latest/rss/world"], -1, "mostly-high", "Paramount Skydance"),
    S("abc", "ABC News", ["https://abcnews.go.com/abcnews/topstories", "https://abcnews.go.com/abcnews/politicsheadlines", "https://abcnews.go.com/abcnews/internationalheadlines"], -1, "mostly-high", "The Walt Disney Company"),
    S("politico", "Politico", ["https://rss.politico.com/politics-news.xml", "https://rss.politico.com/congress.xml"], -1, "mostly-high", "Axel Springer"),
    S("businessinsider", "Business Insider", "https://www.businessinsider.com/rss", -1, "mixed", "Axel Springer"),
    S("atlantic", "The Atlantic", "https://www.theatlantic.com/feed/all/", -1, "mostly-high", "Emerson Collective"),
    S("time", "TIME", "https://time.com/feed/", -1, "mostly-high", "Marc & Lynne Benioff"),
    S("latimes", "Los Angeles Times", "https://www.latimes.com/rss2.0.xml", -1, "mostly-high", "Patrick Soon-Shiong"),
    S("axios", "Axios", "https://api.axios.com/feed/", -1, "high", "Cox Enterprises"),
    S("bloomberg", "Bloomberg", "https://feeds.bloomberg.com/politics/news.rss", -1, "high", "Bloomberg L.P."),
    S("guardian", "The Guardian", ["https://www.theguardian.com/us-news/rss", "https://www.theguardian.com/world/rss", "https://www.theguardian.com/politics/rss"], -1, "mostly-high", "Scott Trust Limited", "UK"),
    S("independent", "The Independent", "https://www.independent.co.uk/news/uk/rss", -1, "mixed", "Independent Digital News & Media", "UK"),
    S("channel4", "Channel 4 News", "https://www.channel4.com/news/feed", -1, "high", "Channel Four Television Corp", "UK"),
    S("aljazeera", "Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml", -1, "mixed", "Qatar Media Corporation (state)", "QA"),
    S("cbc", "CBC News", "https://www.cbc.ca/webfeed/rss/rss-topstories", -1, "high", "CBC (Canadian public)", "CA"),
    S("globalnews", "Global News", "https://globalnews.ca/feed/", -1, "mostly-high", "Corus Entertainment", "CA"),

    # -------------------------------------------------------------- center
    S("ap", "Associated Press", ["https://news.google.com/rss/search?q=when:2d+site:apnews.com&hl=en-US&gl=US&ceid=US:en", "https://news.google.com/rss/search?q=when:2d+site:apnews.com+politics&hl=en-US&gl=US&ceid=US:en"], 0, "high", "AP (nonprofit cooperative)"),
    S("reuters", "Reuters", ["https://news.google.com/rss/search?q=when:2d+site:reuters.com&hl=en-US&gl=US&ceid=US:en", "https://news.google.com/rss/search?q=when:2d+site:reuters.com+US&hl=en-US&gl=US&ceid=US:en"], 0, "high", "Thomson Reuters"),
    S("bbc", "BBC News", ["https://feeds.bbci.co.uk/news/world/rss.xml", "https://feeds.bbci.co.uk/news/rss.xml", "https://feeds.bbci.co.uk/news/politics/rss.xml"], 0, "high", "BBC (UK public)", "UK"),
    S("csmonitor", "Christian Science Monitor", "https://rss.csmonitor.com/feeds/usa", 0, "high", "Christian Science Publishing Society"),
    S("thehill", "The Hill", ["https://thehill.com/news/feed/", "https://thehill.com/homenews/feed/", "https://thehill.com/policy/feed/"], 0, "mostly-high", "Nexstar Media Group"),
    S("newsweek", "Newsweek", "https://www.newsweek.com/rss", 0, "mixed", "Newsweek Publishing LLC"),
    S("cnbc", "CNBC", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114", 0, "mostly-high", "Comcast / NBCUniversal"),
    S("marketwatch", "MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories", 0, "mostly-high", "News Corp"),
    S("forbes", "Forbes", "https://www.forbes.com/business/feed/", 0, "mixed", "Integrated Whale Media"),
    S("upi", "UPI", ["https://rss.upi.com/news/top_news.rss", "https://rss.upi.com/news/us_news.rss"], 0, "mostly-high", "News World Communications"),
    S("dw", "Deutsche Welle", "https://rss.dw.com/rdf/rss-en-all", 0, "high", "DW (German public)", "DE"),
    S("france24", "France 24", "https://www.france24.com/en/rss", 0, "high", "France Medias Monde (state)", "FR"),
    S("sky", "Sky News", "https://feeds.skynews.com/feeds/rss/world.xml", 0, "mostly-high", "Comcast / NBCUniversal", "UK"),
    S("abcau", "ABC News (Australia)", "https://www.abc.net.au/news/feed/2942460/rss.xml", 0, "high", "ABC (Australian public)", "AU"),
    S("straitstimes", "The Straits Times", "https://www.straitstimes.com/news/world/rss.xml", 0, "mostly-high", "SPH Media Trust", "SG"),
    S("scmp", "South China Morning Post", "https://www.scmp.com/rss/91/feed", 0, "mixed", "Alibaba Group", "HK"),
    S("thehindu", "The Hindu", "https://www.thehindu.com/news/international/feeder/default.rss", 0, "mostly-high", "Kasturi & Sons", "IN"),
    S("toi", "Times of India", "https://timesofindia.indiatimes.com/rssfeedstopstories.cms", 0, "mixed", "Bennett, Coleman & Co.", "IN"),

    # ---------------------------------------------------------- lean right
    S("wsj", "The Wall Street Journal", "https://feeds.a.dj.com/rss/RSSWorldNews.xml", 1, "high", "News Corp"),
    S("nypost", "New York Post", ["https://nypost.com/feed/", "https://nypost.com/us-news/feed/", "https://nypost.com/politics/feed/"], 1, "mixed", "News Corp"),
    S("washtimes", "The Washington Times", ["https://www.washingtontimes.com/rss/headlines/news/politics/", "https://www.washingtontimes.com/rss/headlines/news/national/", "https://www.washingtontimes.com/rss/headlines/news/world/"], 1, "mixed", "Operations Holdings"),
    S("examiner", "Washington Examiner", ["https://www.washingtonexaminer.com/feed", "https://www.washingtonexaminer.com/section/politics/feed", "https://www.washingtonexaminer.com/section/news/feed"], 1, "mostly-high", "Clarity Media Group"),
    S("nationalreview", "National Review", ["https://www.nationalreview.com/feed/", "https://www.nationalreview.com/corner/feed/"], 1, "mostly-high", "National Review Institute"),
    S("reason", "Reason", "https://reason.com/latest/feed/", 1, "mostly-high", "Reason Foundation"),
    S("dispatch", "The Dispatch", "https://thedispatch.com/feed/", 1, "high", "The Dispatch"),
    S("nationalpost", "National Post", "https://nationalpost.com/feed/", 1, "mostly-high", "Postmedia Network", "CA"),

    # --------------------------------------------------------------- right
    S("foxnews", "Fox News", ["https://moxie.foxnews.com/google-publisher/latest.xml", "https://moxie.foxnews.com/google-publisher/politics.xml", "https://moxie.foxnews.com/google-publisher/us.xml", "https://moxie.foxnews.com/google-publisher/world.xml"], 2, "mixed", "Fox Corporation"),
    S("breitbart", "Breitbart", "https://feeds.feedburner.com/breitbart", 2, "mixed", "Breitbart News Network"),
    S("dailywire", "The Daily Wire", "https://www.dailywire.com/feeds/rss.xml", 2, "mixed", "Bentkey Ventures"),
    S("dailycaller", "The Daily Caller", "https://dailycaller.com/feed/", 2, "mixed", "The Daily Caller Inc."),
    S("federalist", "The Federalist", "https://thefederalist.com/feed/", 2, "mixed", "FDRLST Media"),
    S("theblaze", "The Blaze", ["https://www.theblaze.com/feeds/feed.rss", "https://www.theblaze.com/feeds/news.rss"], 2, "mixed", "Blaze Media"),
    S("freebeacon", "Washington Free Beacon", "https://freebeacon.com/feed/", 2, "mixed", "Beacon Media"),
    S("justthenews", "Just the News", "https://justthenews.com/rss.xml", 2, "mixed", "Just the News Inc."),
    S("oann", "One America News", "https://www.oann.com/feed/", 2, "low", "Herring Networks"),
]

BY_ID = {s["id"]: s for s in SOURCES}


def summary():
    counts = {}
    for s in SOURCES:
        counts[s["lean_slug"]] = counts.get(s["lean_slug"], 0) + 1
    return counts


if __name__ == "__main__":
    print(f"{len(SOURCES)} sources")
    for lean, slug, label in BUCKETS:
        names = [s["name"] for s in SOURCES if s["lean"] == lean]
        print(f"\n{label} ({len(names)})\n  " + "\n  ".join(names))
