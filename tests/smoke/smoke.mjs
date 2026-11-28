// Browser smoke test: serves docs/ locally, opens every league in Chromium, and walks the main views.
//
//   node tests/smoke/smoke.mjs [docs-dir]
//
// docs/ needs the published data first (scripts/site_data.sh pull) and the page shells (python -m <pkg> site).
// A view fails if the page throws, if the app shows its "Could not load data" card, or if it never finishes loading.
// Requests to other hosts (fonts, analytics, logos) are blocked so the test needs no network.
import { createRequire } from "node:module";
import { createServer } from "node:http";
import { readFile, readdir, stat } from "node:fs/promises";
import { extname, join, normalize, resolve } from "node:path";

const { chromium } = createRequire(import.meta.url)("playwright"); // require() also honors NODE_PATH
const ROOT = resolve(process.argv[2] || "docs");
const TYPES = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".json": "application/json",
  ".png": "image/png", ".svg": "image/svg+xml", ".txt": "text/plain", ".xml": "application/xml" };
const VIEWS = ["ratings", "games", "players", "matchup", "styles", "method"];

const server = createServer(async (req, res) => {
  let p = normalize(decodeURIComponent(new URL(req.url, "http://x").pathname)).replace(/^(\.\.[/\\])+/, "");
  let f = join(ROOT, p);
  try {
    if ((await stat(f)).isDirectory()) f = join(f, "index.html");
    res.writeHead(200, { "content-type": TYPES[extname(f)] || "application/octet-stream" });
    res.end(await readFile(f));
  } catch {
    res.writeHead(404); res.end("not found");
  }
});
await new Promise((r) => server.listen(0, "127.0.0.1", r));
const base = `http://127.0.0.1:${server.address().port}`;

const leagues = [];
for (const d of await readdir(ROOT)) {
  try { await stat(join(ROOT, d, "data", "ratings.json")); leagues.push(d); } catch { /* not a league */ }
}
if (!leagues.length) { console.error(`no league data under ${ROOT}: run scripts/site_data.sh pull first`); process.exit(1); }

const browser = await chromium.launch();
const failures = [];
let checked = 0;

async function visit(label, url, check) {
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(`uncaught: ${e.message}`));
  page.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource|ERR_|net::/.test(m.text())) errors.push(`console: ${m.text()}`); });
  await page.route((u) => !u.href.startsWith(base), (r) => r.abort());
  try {
    await page.goto(url, { waitUntil: "load", timeout: 30000 });
    await check(page);
  } catch (e) {
    errors.push(e.message.split("\n")[0]);
  }
  checked++;
  if (errors.length) failures.push(`${label}: ${errors.join("; ")}`);
  console.log(`${errors.length ? "FAIL" : "ok  "} ${label}`);
  await page.close();
}

// the app has rendered a view: the loading spinner is gone and no error card replaced it
async function appRendered(page) {
  await page.waitForFunction(() => {
    const app = document.querySelector("#app");
    return app && !app.querySelector(".loading") && app.textContent.trim().length > 50;
  }, null, { timeout: 30000 });
  const text = await page.locator("#app").innerText();
  if (/Could not load data/.test(text)) throw new Error(`error card: ${text.slice(0, 160)}`);
}

await visit("home", `${base}/`, async (page) => {
  await page.waitForSelector("a.choice", { timeout: 10000 });
});

for (const lg of leagues) {
  const ratings = JSON.parse(await readFile(join(ROOT, lg, "data", "ratings.json"), "utf8"));
  const team = ratings.teams?.[0]?.slug;
  for (const v of VIEWS) {
    await visit(`${lg} #/${v}`, `${base}/${lg}/#/${v}`, async (page) => {
      await appRendered(page);
      if (v === "ratings") {
        const rows = await page.locator("#app table tbody tr").count();
        if (rows < Math.min(5, ratings.teams.length)) throw new Error(`ratings table has ${rows} rows`);
      }
    });
  }
  if (team) {
    await visit(`${lg} #/team/${team}`, `${base}/${lg}/#/team/${team}`, appRendered);
    try {
      await stat(join(ROOT, lg, "team", team, "index.html"));
      await visit(`${lg} team page ${team}`, `${base}/${lg}/team/${team}/`, async (page) => {
        const h1 = await page.locator("h1").innerText();
        if (!h1.trim()) throw new Error("team page has no heading");
      });
    } catch { /* team pages not built */ }
  }
}

await browser.close();
server.close();
console.log(`\n${checked - failures.length}/${checked} passed`);
if (failures.length) {
  console.error("\nFailures:\n" + failures.map((f) => "  " + f).join("\n"));
  process.exit(1);
}
