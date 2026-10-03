/* Smoke test: the site loads with no console errors at phone and desktop sizes.

   Serves docs/ (the GitHub Pages build) itself, so build it first:

       python3 build_static.py
       npm i --no-save playwright && npx playwright install chromium
       node tests/smoke.cjs

   Set BASE=https://<user>.github.io/groundish-news/ to check the live site
   instead. Exits non-zero on any console error, uncaught exception, failed
   request, or horizontal scrolling. */
"use strict";
const http = require("http");
const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");

const DOCS = path.join(__dirname, "..", "docs");
const TYPES = { ".html": "text/html", ".css": "text/css", ".js": "text/javascript",
                ".json": "application/json" };
const VIEWPORTS = [
  ["phone", { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true }],
  ["desktop", { viewport: { width: 1280, height: 800 } }],
];
const VIEWS = ["stories", "blindspots", "sources", "bias", "about"];

function serve() {
  const server = http.createServer((req, res) => {
    const rel = decodeURIComponent(req.url.split("?")[0]).replace(/\/$/, "/index.html");
    const file = path.join(DOCS, path.normalize(rel));
    if (!file.startsWith(DOCS) || !fs.existsSync(file)) { res.writeHead(404); return res.end(); }
    res.writeHead(200, { "Content-Type": TYPES[path.extname(file)] || "application/octet-stream" });
    fs.createReadStream(file).pipe(res);
  });
  return new Promise((done) => server.listen(0, "127.0.0.1", () => done(server)));
}

(async () => {
  let server = null;
  let base = process.env.BASE;
  if (!base) {
    if (!fs.existsSync(path.join(DOCS, "data", "bundle.json"))) {
      console.error("docs/ is not built — run `python3 build_static.py` first, or set BASE.");
      process.exit(2);
    }
    server = await serve();
    base = `http://127.0.0.1:${server.address().port}/`;
  }

  const browser = await chromium.launch();
  const failures = [];
  for (const [name, options] of VIEWPORTS) {
    const context = await browser.newContext(options);
    const page = await context.newPage();
    const problems = [];
    page.on("console", (msg) => { if (msg.type() === "error") problems.push("console: " + msg.text()); });
    page.on("pageerror", (err) => problems.push("exception: " + err.message));
    page.on("requestfailed", (req) => problems.push("request failed: " + req.url()));
    page.on("response", (res) => { if (res.status() >= 400) problems.push(`HTTP ${res.status()}: ${res.url()}`); });

    for (const view of VIEWS) {
      await page.goto(base + "#" + view, { waitUntil: "networkidle" });
      await page.waitForSelector("#view > *");
      const { scrollWidth, innerWidth } = await page.evaluate(() => ({
        scrollWidth: document.documentElement.scrollWidth, innerWidth: window.innerWidth }));
      if (scrollWidth > innerWidth) problems.push(`#${view} scrolls sideways (${scrollWidth}px > ${innerWidth}px)`);
    }
    // Open one story's comparison dialog too, since it is the densest layout.
    await page.goto(base + "#stories", { waitUntil: "networkidle" });
    const compare = page.locator(".story .btn").first();
    if (await compare.count()) {
      await compare.click();
      await page.waitForSelector("#modal-backdrop:not([hidden])");
    }

    console.log(`${problems.length ? "FAIL" : "ok  "} ${name} (${options.viewport.width}x${options.viewport.height})`);
    for (const problem of problems) console.log("     " + problem);
    failures.push(...problems);
    await context.close();
  }
  await browser.close();
  if (server) server.close();
  process.exit(failures.length ? 1 : 0);
})();
