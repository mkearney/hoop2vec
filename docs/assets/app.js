/* hoop2vec front end: one static SPA for ratings, players, prospects, team identities, the matchup lab, games, bracket and method.
   Shared by docs/mens/ and docs/womens/ (body[data-league]); each league's data lives in docs/<league>/data: meta, ratings, teams/<slug>, games, bracket, model + vectors (in-browser game model),
   style (style dictionary), season_<S> (identity cards), seasons, backtest, power_history. */
(() => {
  "use strict";
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const app = $("#app");
  const tip = $("#tooltip");
  const cache = {};
  // league: the same app serves the men's and women's sites
  const LEAGUE = document.body.dataset.league || "mens";
  const IS_W = LEAGUE === "womens";                       // women's college
  const IS_PRO = LEAGUE === "nba" || LEAGUE === "wnba";   // pro leagues: playoffs instead of a bracket, no prospects
  const FEMALE = LEAGUE === "womens" || LEAGUE === "wnba";
  const ESPN_ONLY = LEAGUE !== "mens";                    // every stat from ESPN box scores (no Torvik)
  const LG = IS_PRO ? LEAGUE.toUpperCase() : "D-I";       // "an average D-I team" / "an average NBA team"
  const PRO = FEMALE ? "WNBA" : "NBA";
  const he = FEMALE ? "she" : "he", his = FEMALE ? "her" : "his";
  const RATE_SRC = ESPN_ONLY ? "ESPN box scores" : "Torvik";
  const TOPN = IS_PRO ? 8 : 25;
  const seasonLabel = (x) => (LEAGUE === "wnba" ? String(x) : `${x - 1}–${String(x).slice(2)}`);
  const ROUND_NAMES = ["Opening Round", "Round of 64", "Round of 32", "Sweet 16", "Elite 8", "Final Four", "Championship"];
  const BLOCKS = [["offense", "Offense", "of"], ["defense", "Defense", "df"], ["roster", "Personnel", "ro"]];

  // ---------------------------------------------------------------- utils
  async function get(name, optional = false) {
    if (name in cache) return cache[name];
    try {
      const r = await fetch(`data/${name}`, { cache: "no-cache" });
      if (!r.ok) throw new Error(`${name}: HTTP ${r.status}`);
      return (cache[name] = await r.json());
    } catch (e) {
      if (optional) return (cache[name] = null);
      throw e;
    }
  }
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const isNum = (v) => v !== null && v !== undefined && !Number.isNaN(v);
  const fmt = (v, d = 1) => (isNum(v) ? Number(v).toFixed(d) : "–");
  const sgn = (v, d = 1) => (isNum(v) ? (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v).toFixed(d) : "–");
  const pct = (p, d = 0) => {
    if (!isNum(p)) return "–";
    const lim = d === 0 ? 0.99 : 0.999;
    if (p > lim) return ">" + (100 * lim).toFixed(d) + "%";
    if (p < 1 - lim && p > 0) return "<" + (100 * (1 - lim)).toFixed(d) + "%";
    return (100 * p).toFixed(d) + "%";
  };
  const ord = (n) => { n = Math.round(n); const s = ["th", "st", "nd", "rd"], v = n % 100; return n + (s[(v - 20) % 10] || s[v] || s[0]); };
  const slugify = (s) => String(s).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  const logo = (t, cls = "") => (t && t.logo ? `<img class="logo ${cls}" src="${esc(t.logo)}" alt="" loading="lazy" onerror="this.style.visibility='hidden'">` : `<span class="logo ${cls}"></span>`);
  // logo mark and spot art: <template id="art-*"> in the page shell (hoop2vec/brand.py), colored by theme tokens
  const art = (k, cls = "") => { const t = document.getElementById("art-" + k); return t ? (cls ? t.innerHTML.replace('class="art spot"', `class="art spot ${cls}"`) : t.innerHTML) : ""; };
  const locWord = (l) => (l > 0 ? "vs" : l < 0 ? "@" : "N");
  // game times in the visitor's own time zone (Eastern, Detroit, when the browser doesn't say)
  const TZ = (() => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || "America/Detroit"; } catch (e) { return "America/Detroit"; } })();
  const inTZ = (d, opts, f = "toLocaleTimeString") => { try { return d[f](undefined, { ...opts, timeZone: TZ }); } catch (e) { return d[f](undefined, { ...opts, timeZone: "America/Detroit" }); } };
  const timeFmt = (iso) => { const d = new Date(iso); return iso && !isNaN(d) ? inTZ(d, { hour: "numeric", minute: "2-digit", timeZoneName: "short" }) : ""; };
  const dayKey = (d) => { const p = {}; new Intl.DateTimeFormat("en-US", { year: "numeric", month: "2-digit", day: "2-digit", timeZone: TZ }).formatToParts(d).forEach((x) => (p[x.type] = x.value)); return `${p.year}-${p.month}-${p.day}`; };
  const gameDay = (x) => (x.start && !isNaN(new Date(x.start)) ? dayKey(new Date(x.start)) : x.date);  // local calendar day of tip-off
  const boardDay = () => dayKey(new Date(Date.now() - 4 * 3600e3));  // "today" on the Games page rolls over at 4 a.m., so late finals stay up overnight
  const erf = (x) => { const t = 1 / (1 + 0.3275911 * Math.abs(x)); const y = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-x * x); return x >= 0 ? y : -y; };
  const Phi = (z) => 0.5 * (1 + erf(z / Math.SQRT2));
  function pctRank(values, v) {
    if (!isNum(v)) return null;
    let below = 0;
    for (const x of values) if (x < v) below++;
    return below / Math.max(values.length - 1, 1);
  }
  // diverging heat: blue = good, red = bad, transparent at the median
  function heat(p, invert = false) {
    if (p === null || p === undefined) return "";
    if (invert) p = 1 - p;
    const a = Math.abs(p - 0.5) * 2;
    const col = p >= 0.5 ? "var(--heat-pos)" : "var(--heat-neg)";
    return `background: color-mix(in srgb, ${col} ${Math.round(a * 24)}%, transparent)`;
  }
  function showTip(html, ev) {
    tip.innerHTML = html; tip.classList.add("show");
    tip.style.left = Math.min(ev.clientX + 14, innerWidth - tip.offsetWidth - 8) + "px";
    tip.style.top = Math.min(ev.clientY + 14, innerHeight - tip.offsetHeight - 8) + "px";
  }
  const hideTip = () => tip.classList.remove("show");
  const seg = (id, opts, cur) => `<div class="seg" id="${id}" role="group">${opts.map(([v, l]) => `<button data-v="${v}" class="${v === cur ? "on" : ""}">${esc(l)}</button>`).join("")}</div>`;
  function onSeg(id, fn) {
    $("#" + id).addEventListener("click", (e) => {
      const b = e.target.closest("button"); if (!b) return;
      $$(`#${id} button`).forEach((x) => x.classList.toggle("on", x === b)); fn(b.dataset.v);
    });
  }

  // ---------------------------------------------------------------- shared data
  let TEAMS = {}, BY_ESPN = {}, RATINGS = null;
  async function teams() {
    if (RATINGS) return TEAMS;
    RATINGS = await get("ratings.json");
    RATINGS.teams.forEach((t) => { TEAMS[t.team] = t; if (t.espn_id) BY_ESPN[t.espn_id] = t; });
    return TEAMS;
  }
  // availability vs usual participation, in points per game: negative = injuries/absences, positive = players back
  const availChip = (t) => Math.abs(t.injury_pts || 0) < 0.1 ? `<span class="pill">usual lineup</span>`
    : t.injury_pts < 0 ? `<span class="pill bad" title="Players out vs their usual participation">✚ injuries ${sgn(t.injury_pts)} pts</span>`
      : `<span class="pill good" title="Players back who missed earlier games">↑ returning ${sgn(t.injury_pts)} pts</span>`;
  const teamLink = (name, t = TEAMS[name]) => `<a href="#/team/${slugify(name)}" class="teamcell" title="${esc(name)}">${logo(t)}<span class="nm">${esc(name)}</span></a>`;
  let STYLE = null;
  async function style() {
    if (STYLE) return STYLE;
    STYLE = await get("style.json", true);
    if (STYLE) BLOCKS.forEach(([b]) => STYLE.blocks[b].archetypes.forEach((a, i) => (a.idx = i)));
    return STYLE;
  }
  async function identities(season) {
    const S = await get("seasons.json", true);
    const s = season || (S && S.current);
    if (!s) return null;
    const d = await get(`season_${s}.json`, true);
    if (d && !d.byId) d.byId = Object.fromEntries(d.teams.map((t) => [String(t.id), t]));
    return d;
  }
  const arch = (b, i) => (STYLE && i !== null && i !== undefined ? STYLE.blocks[b].archetypes[i] : null);

  // ---------------------------------------------------------------- theme & router
  const root = document.documentElement;
  try { const t = localStorage.getItem("hoop2vec-theme"); if (t) root.dataset.theme = t; } catch (e) { /* storage blocked */ }
  $("#theme").addEventListener("click", () => {
    const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    root.dataset.theme = dark ? "light" : "dark";
    try { localStorage.setItem("hoop2vec-theme", root.dataset.theme); } catch (e) { /* ignore */ }
  });
  const routes = { playoffs: viewPlayoffs, ratings: viewRatings, players: viewPlayers, prospects: viewProspects, team: viewTeam, bracket: viewBracket, matchup: viewMatchup, games: viewGames,
    styles: viewStyles, guide: viewGuide, method: viewMethod, player: viewPlayer };
  const NAV = { team: "ratings", guide: "styles", player: "players" };
  async function route() {
    hideTip();
    const h = location.hash.replace(/^#\/?/, "") || "ratings";
    const [path, query] = h.split("?");
    const [name, arg, arg2] = path.split("/");
    const params = new URLSearchParams(query || "");
    $$("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.route === (NAV[name] || name)));
    const fn = routes[name] || viewRatings;
    // tab title per section (link previews use the static tags in the page head)
    const sec = { ratings: "Ratings", players: "Players", prospects: "Prospects", bracket: "Bracket", playoffs: "Playoffs",
      matchup: "Matchup Lab", games: "Games", styles: "Styles", guide: "Style guide", method: "Method" }[name];
    const lgName = { mens: "Men's college", womens: "Women's college", nba: "NBA", wnba: "WNBA" }[LEAGUE];
    document.title = [name === "team" && arg ? decodeURIComponent(arg).replace(/-/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()) : sec,
      lgName, "hoop2vec"].filter(Boolean).join(" · ");
    app.innerHTML = `<div class="loading">${art("mark")}<span>Loading…</span></div>`;
    try {
      await fn(arg ? decodeURIComponent(arg) : null, params, arg2 ? decodeURIComponent(arg2) : null);
    } catch (e) {
      console.error(e);
      app.innerHTML = `<div class="card">Could not load data (${esc(e.message)}). The daily pipeline may not have run yet.</div>`;
    }
    window.scrollTo({ top: 0 });
  }
  window.addEventListener("hashchange", route);
  // structured data (page head) follows the data actually loaded: HTML can be cached, data/*.json never is
  function syncLd(updated, ratings) {
    const el = document.getElementById("ld");
    if (!el) return;
    try {
      const ld = JSON.parse(el.textContent), page = (ld["@graph"] || []).find((x) => x["@type"] === "WebPage");
      if (!page) return;
      const m = /^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2})/.exec(updated || "");
      if (m) page.dateModified = `${m[1]}T${m[2]}:00Z`;
      if (ratings && ratings.teams) {
        const top = ratings.teams.slice().sort((a, b) => a.rank - b.rank).slice(0, 5);
        page.mainEntity = { "@type": "ItemList", name: "Power ratings: top 5", itemListOrder: "https://schema.org/ItemListOrderAscending", numberOfItems: top.length,
          itemListElement: top.map((t) => ({ "@type": "ListItem", position: t.rank, item: { "@type": "SportsTeam", name: t.team } })) };
      }
      el.textContent = JSON.stringify(ld).replace(/<\//g, "<\\/");
    } catch (e) { /* leave the published version */ }
  }
  Promise.all([get("meta.json", true), get("ratings.json", true)]).then(([m, r]) => syncLd(r && r.updated, r));
  get("meta.json", true).then((m) => {
    if (!m) return;
    const src = ESPN_ONLY || m.source !== "espn" ? "" : m.torvik_through ? ` · Torvik thru ${m.torvik_through.slice(5)}, ESPN after` : " · ESPN data (backup)";
    $("#updated").textContent = `Updated ${m.updated}${src}`;
  });

  // ================================================================ RATINGS
  const VIEWS = {
    core: [
      ["power", "Power", 1, false, `Points per 100 possessions better than an average ${LG} team on a neutral floor (final game model; 0 = average)`],
      ["k_em", "Dynamic", 1, false, "Game-by-game Kalman rating: offense − defense, pts / 100 possessions", true],
      ["eff_o", "AdjO", 1, false, "Adjusted offensive efficiency (pts / 100 poss)"],
      ["eff_d", "AdjD", 1, true, "Adjusted defensive efficiency (lower is better)"],
      ["adjem", "AdjEM", 1, false, "AdjO − AdjD"],
      ["adjt", "Tempo", 1, null, "Adjusted possessions / 40 min", true],
      ["roster", "Roster", 1, false, "Player-based strength: Σ minutes share × player value", true],
      ["injury_pts", "Inj", 1, false, "Availability adjustment in points (injuries / absences)"],
      ["sos", "SOS", 1, false, `Average Power of opponents faced (0 = average ${LG} schedule)`, true],
    ],
    offense: [
      ["efg_o", "eFG%", 1, false], ["tov_o", "TO%", 1, true], ["orb_o", "OR%", 1, false], ["ftr_o", "FTR", 1, false],
      ["tpar_o", "3PA%", 1, null], ["tp_o", "3P%", 1, false], ["twop_o", "2P%", 1, false], ["ast_o", "Ast%", 1, false],
    ],
    defense: [
      ["efg_d", "eFG%", 1, true], ["tov_d", "TO%", 1, false], ["orb_d", "OR%", 1, true], ["ftr_d", "FTR", 1, true],
      ["tpar_d", "3PA%", 1, null], ["tp_d", "3P%", 1, true], ["blk_d", "Blk%", 1, false], ["stl_d", "Stl%", 1, false],
    ],
    roster: [
      ["roster", "Roster", 1, false], ["star", "Best player", 1, false], ["height", "Height", 1, false],
      ["experience", "Exp.", 2, false], ["continuity", "Continuity", 2, false], ["bench", "Bench min", 2, null],
      ["elo", "Elo", 0, false], ["form", "Form", 1, false],
    ],
  };
  const R = { sort: "rank", dir: 1, view: "core", conf: "", q: "" };

  async function viewRatings() {
    await teams();
    await style();
    const r = RATINGS;
    const confs = [...new Set(r.teams.map((t) => t.conf).filter(Boolean))].sort();
    const views = [["core", "Ratings"], ["offense", "Offense"], ["defense", "Defense"], ["roster", "Roster"]];
    if (STYLE) views.push(["identity", "Identity"]);
    app.innerHTML = `
      <div class="page-head"><div>
        <h1>Power Ratings</h1>
        <p class="sub">${seasonLabel(r.season)} · ${r.teams.length} teams · updated ${esc(r.updated)}. <b>Power</b> is how many points per 100 possessions a team would beat an average ${LG} team by on a neutral floor (0 = average), from the final game model: a matchup network blended with a dynamic-rating linear model, checked for leakage against betting lines.</p>
      </div>${art("vector")}</div>
      <div class="controls">
        <input type="search" id="q" placeholder="Search teams…" value="${esc(R.q)}" style="width:220px" aria-label="Search teams">
        <select id="conf" aria-label="Conference"><option value="">All conferences</option>${confs.map((c) => `<option ${c === R.conf ? "selected" : ""}>${esc(c)}</option>`).join("")}</select>
        ${seg("view", views, R.view)}
      </div>
      <div class="card flush tbl-wrap"><table id="tbl" class="ratings-table"></table></div>
      <p class="note">Small numbers are national ranks. Shading runs from red (bottom) through clear (median) to blue (top). ▲▼ = change in Power rank since yesterday.</p>`;
    $("#q").addEventListener("input", (e) => { R.q = e.target.value; drawRatings(); });
    $("#conf").addEventListener("change", (e) => { R.conf = e.target.value; drawRatings(); });
    onSeg("view", (v) => { R.view = v; if (!["rank", "team", "conf", "wins"].includes(R.sort)) { R.sort = "rank"; R.dir = 1; } drawRatings(); });
    drawRatings();
  }

  function drawRatings() {
    const all = RATINGS.teams;
    const idView = R.view === "identity";
    const cols = idView ? [] : VIEWS[R.view];
    const dist = {};
    cols.forEach(([k]) => (dist[k] = all.map((t) => t[k]).filter(isNum)));
    const q = R.q.toLowerCase();
    let rows = all.filter((t) => (!R.conf || t.conf === R.conf) && (!q || t.team.toLowerCase().includes(q) || (t.location || "").toLowerCase().includes(q)));
    const key = R.sort, d = R.dir;
    const val = (t) => (key.startsWith("arch_") ? (arch({ arch_o: "offense", arch_d: "defense", arch_r: "roster" }[key], (t.arch || {})[key.slice(5)]) || {}).name : t[key]);
    rows = rows.slice().sort((a, b) => {
      const x = val(a), y = val(b);
      if (typeof x === "string" || typeof y === "string") return d * String(x ?? "~").localeCompare(String(y ?? "~"));
      return d * ((x ?? -1e9) - (y ?? -1e9));
    });
    const th = (k, label, cls = "", title = "") => `<th data-k="${k}" class="${cls} ${key === k ? "sorted" : ""}" title="${esc(title)}" scope="col">${label}${key === k ? (d > 0 ? " ↑" : " ↓") : ""}</th>`;
    const extra = idView ? th("arch_o", "Offense identity", "l") + th("arch_d", "Defense identity", "l") + th("arch_r", "Personnel", "l hide-sm") :
      cols.map(([k, lab, , , title, low]) => th(k, lab, low ? "lowprio" : "", title || "")).join("");
    const head = `<thead><tr>${th("rank", "#", "rankcol")}${th("team", "Team", "l")}${th("conf", "Conf", "l hide-sm")}${th("wins", "W-L")}${idView ? th("power", "Power") : ""}${extra}</tr></thead>`;
    const body = rows.map((t) => {
      let mv = "";
      if (isNum(t.rank_prev) && t.rank_prev !== t.rank) {
        const ch = t.rank_prev - t.rank;
        mv = `<span class="mv ${ch > 0 ? "up" : "dn"}" title="Power rank change since yesterday">${ch > 0 ? "▲" : "▼"}${Math.abs(ch)}</span>`;
      }
      let cells;
      if (idView) {
        const a = t.arch || {};
        const tag = (b, i, c) => { const x = arch(b, i); return x ? `<span class="tag ${c}">${esc(x.name)}</span>` : `<span class="faint">–</span>`; };
        cells = `<td><b>${sgn(t.power)}</b></td><td class="l">${tag("offense", a.o, "o")}</td><td class="l">${tag("defense", a.d, "d")}</td><td class="l hide-sm">${tag("roster", a.r, "")}</td>`;
      } else {
        cells = cols.map(([k, , dg, inv, , low]) => {
          const v = t[k];
          const p = pctRank(dist[k], v);
          const rk = t[k + "_rank"] ? `<span class="rk">${t[k + "_rank"]}</span>` : "";
          const txt = k === "injury_pts" ? (Math.abs(v || 0) < 0.05 ? "<span class='faint'>—</span>" : `<span class="${v < 0 ? "neg" : "pos"}">${sgn(v)}</span>`)
            : k === "power" ? `<b>${sgn(v)}</b>` : fmt(v, dg);
          const st = k === "power" || k === "injury_pts" || inv === null ? "" : heat(p, inv);
          return `<td class="${low ? "lowprio" : ""}"><span class="heat" style="${st}">${txt}</span>${rk}</td>`;
        }).join("");
      }
      return `<tr><td class="rankcol">${t.rank}${mv}</td><td class="l">${teamLink(t.team, t)}</td><td class="l hide-sm faint">${esc(t.conf || "")}</td><td class="faint">${t.wins}-${t.losses}</td>${cells}</tr>`;
    }).join("");
    const tbl = $("#tbl");
    tbl.innerHTML = head + `<tbody>${body}</tbody>`;
    $$("th", tbl).forEach((h) => h.addEventListener("click", () => {
      const k = h.dataset.k;
      if (R.sort === k) R.dir *= -1;
      else { R.sort = k; R.dir = ["rank", "team", "conf", "eff_d"].includes(k) || k.startsWith("arch_") || (cols.find((c) => c[0] === k) || [])[3] === true ? 1 : -1; }
      drawRatings();
    }));
  }


  // ================================================================ PLAYERS
  const PCOLS = {
    overview: [["impact", "Impact", 1, `Team points per 100 possessions ${he} adds over a replacement-level player (value above replacement × minutes share)`],
      ["value", "Value", 1, `Points per 100 possessions vs an average ${LG} player when on the floor: box plus/minus measured against pre-game ratings, shrunk toward a prior`],
      ["o", "Off", 1, "Offensive part of Value"], ["d", "Def", 1, "Defensive part of Value"], ["mpg", "MPG", 1, "Minutes per game"],
      ["ppg", "PPG", 1, "Points per game (ESPN box scores)"], ["rpg", "RPG", 1], ["apg", "APG", 1], ["usg", "Usg%", 1, `Usage rate (${RATE_SRC})`],
      ["ts", "TS%", 1, `True shooting (${RATE_SRC})`], ["avail", "Avail", 0, `Chance ${he} plays next game`]],
    scoring: [["ppg", "PPG", 1], ["fg_pct", "FG%", 1], ["tpm", "3PM", 1, "Threes made per game"], ["tp_pct", "3P%", 1], ["ft_pct", "FT%", 1],
      ["ts", "TS%", 1, `True shooting (${RATE_SRC})`], ["efg", "eFG%", 1, `Effective FG% (${RATE_SRC})`], ["usg", "Usg%", 1]].concat(ESPN_ONLY ? [] : [["ortg", "ORtg", 1, "Points produced per 100 possessions used (Torvik)"]]),
    playmaking: [["apg", "APG", 1], ["tpg", "TOPG", 1, "Turnovers per game"], ["ast_pct", "Ast%", 1, `Assist rate (${RATE_SRC})`], ["to_pct", "TO%", 1, `Turnover rate (${RATE_SRC})`, true],
      ["rpg", "RPG", 1], ["orb_pct", "OR%", 1], ["drb_pct", "DR%", 1], ["spg", "SPG", 1], ["bpg", "BPG", 1], ["stl_pct", "Stl%", 1], ["blk_pct", "Blk%", 1]],
    profile: [["cls", "Yr", null], ["ht", "Ht", null], ["pos", "Pos", null]].concat(ESPN_ONLY ? [] : [["role", "Role", null, "Torvik role"], ["rec", "Recruit", 1, "Recruiting score (Torvik, 0–100)"]]).concat([
      ["g", "G", 0], ["gs", "GS", 0, "Games started"], ["mpg", "MPG", 1], ["min_pct", "Min%", 1, "Share of team minutes"]]),
  };
  const PF = { sort: "impact", dir: -1, view: "overview", conf: "", team: "", q: "", pool: "rotation", limit: 100 };
  const POOLS = [["stars", "Starters (25+ mpg)"], ["rotation", "Rotation (15+ mpg)"], ["regulars", "Regulars (8+ mpg)"], ["all", "Everyone"]];
  const inPool = (p) => PF.pool === "all" ? true : p.g >= 3 && p.mpg >= ({ stars: 25, rotation: 15, regulars: 8 }[PF.pool]);
  const htFmt = (h) => (isNum(h) && h > 0 ? `${Math.floor(h / 12)}'${Math.round(h % 12)}"` : "");

  async function viewPlayers(_, params) {
    const [d] = await Promise.all([get("players.json"), teams()]);
    if (params.get("team")) { PF.team = params.get("team"); PF.pool = "all"; }
    const P = d.players;
    const confs = [...new Set(P.map((p) => p.conf).filter(Boolean))].sort();
    const tnames = [...new Set(P.map((p) => p.team))].sort();
    const qual = P.filter((p) => p.g >= 3 && p.mpg >= 15);
    const board = (key, title, note) => {
      const top = qual.slice().sort((a, b) => b[key] - a[key]).slice(0, 10);
      const mx = Math.max(...top.map((p) => p[key]));
      return `<div class="card"><h2>${title}</h2><p class="note" style="margin-top:-4px">${note}</p>${top.map((p, i) => `
        <div class="odds-row lb-row"><span class="faint num">${i + 1}</span>
          <span class="teamcell" style="font-weight:500">${logo(TEAMS[p.team])}<span class="nm">${playerLink(p)} <span class="faint small">${esc(p.team)}</span></span></span>
          <div class="pbar lb-bar"><span style="width:${(100 * Math.max(0, p[key]) / mx).toFixed(1)}%"></span></div><div class="num" style="text-align:right;font-weight:600">${sgn(p[key])}</div></div>`).join("")}</div>`;
    };
    app.innerHTML = `
      <div class="page-head"><div><h1>Players</h1>
        <p class="sub">${P.length.toLocaleString()} ${LG} players this season · updated ${esc(d.updated)}. <b>Value</b> is a player's points per 100 possessions vs an average ${LG} player when ${he}'s on the floor: box-score plus/minus, re-based on pre-game opponent and team strength so it can't see the future, and shrunk toward a prior (${IS_PRO ? "last season and years in the league" : IS_W ? "last season and class" : "last season, class, recruiting"}) until minutes pile up. <b>Impact</b> also weighs how much ${he} plays. It's what ${he} adds to ${his} team's rating over a replacement-level player (value ${d.replacement}).</p></div></div>
      <div class="grid g2">${board("value", "Best players", "Highest Value, 15+ minutes per game")}${board("impact", "Most important players", "Highest Impact: quality × minutes")}</div>
      <div class="section controls">
        <input type="search" id="pq" placeholder="Search players or teams…" value="${esc(PF.q)}" style="width:230px" aria-label="Search players">
        <select id="pconf" aria-label="Conference"><option value="">All conferences</option>${confs.map((c) => `<option ${c === PF.conf ? "selected" : ""}>${esc(c)}</option>`).join("")}</select>
        <select id="pteam" aria-label="Team"><option value="">All teams</option>${tnames.map((c) => `<option ${c === PF.team ? "selected" : ""}>${esc(c)}</option>`).join("")}</select>
        <select id="ppool" aria-label="Minutes cutoff">${POOLS.map(([v, l]) => `<option value="${v}" ${v === PF.pool ? "selected" : ""}>${l}</option>`).join("")}</select>
        ${seg("pview", [["overview", "Overview"], ["scoring", "Scoring"], ["playmaking", "Playmaking & D"], ["profile", "Profile"]], PF.view)}
      </div>
      <div class="card flush tbl-wrap"><table id="ptbl" class="ratings-table"></table></div>
      <div id="pmore" style="margin-top:10px"></div>
      <p class="note">${ESPN_ONLY ? "Per-game stats and rates (usage, TS%, assist/turnover/rebound/block/steal rates) are computed from ESPN box scores." : "Per-game stats come from ESPN box scores; rates (usage, TS%, assist/turnover/rebound/block/steal rates, ORtg) from barttorvik.com."} Shading is a percentile among players in the current filter. ✚ = on an injury report.</p>`;
    const redraw = () => { PF.limit = 100; drawPlayers(P); };
    $("#pq").addEventListener("input", (e) => { PF.q = e.target.value; redraw(); });
    $("#pconf").addEventListener("change", (e) => { PF.conf = e.target.value; redraw(); });
    $("#pteam").addEventListener("change", (e) => { PF.team = e.target.value; if (PF.team && PF.pool !== "all") { PF.pool = "all"; $("#ppool").value = "all"; } redraw(); });
    $("#ppool").addEventListener("change", (e) => { PF.pool = e.target.value; redraw(); });
    onSeg("pview", (v) => { PF.view = v; redraw(); });
    drawPlayers(P);
  }

  function drawPlayers(P) {
    const cols = PCOLS[PF.view];
    const q = PF.q.toLowerCase();
    let rows = P.filter((p) => inPool(p) && (!PF.conf || p.conf === PF.conf) && (!PF.team || p.team === PF.team)
      && (!q || p.name.toLowerCase().includes(q) || p.team.toLowerCase().includes(q)));
    const dist = {};
    cols.forEach(([k]) => (dist[k] = rows.map((p) => p[k]).filter(isNum)));
    const k = PF.sort, dr = PF.dir;
    rows.sort((a, b) => {
      const x = a[k], y = b[k];
      if (typeof x === "string" || typeof y === "string") return dr * String(x ?? "~").localeCompare(String(y ?? "~"));
      return dr * ((x ?? -1e9) - (y ?? -1e9));
    });
    const n = rows.length;
    const shown = rows.slice(0, PF.limit);
    const th = (key, label, cls = "", title = "") => `<th data-k="${key}" class="${cls} ${k === key ? "sorted" : ""}" title="${esc(title)}" scope="col">${label}${k === key ? (dr > 0 ? " ↑" : " ↓") : ""}</th>`;
    const head = `<thead><tr><th class="rankcol nosort">#</th>${th("name", "Player", "l")}${th("team", "Team", "l")}${cols.map(([c, l, , t, inv]) => th(c, l, "", t || "")).join("")}</tr></thead>`;
    const body = shown.map((p, i) => {
      const cells = cols.map(([c, , dg, , inv]) => {
        const v = p[c];
        if (c === "ht") return `<td class="faint">${htFmt(v)}</td>`;
        if (dg === null) return `<td class="faint">${esc(v ?? "")}</td>`;
        if (c === "avail") return `<td class="num ${isNum(v) && v < 0.5 ? "neg" : "faint"}">${isNum(v) ? pct(v) : ""}</td>`;
        const txt = ["impact", "value", "o", "d"].includes(c) ? sgn(v, dg) : fmt(v, dg);
        return `<td><span class="heat num" style="${heat(pctRank(dist[c], v), inv === true)}">${isNum(v) ? txt : "–"}</span></td>`;
      }).join("");
      return `<tr><td class="rankcol">${i + 1}</td><td class="l">${playerLink(p)}${p.status ? ` <span class="pill bad" title="${esc(p.status)}">✚</span>` : ""}<div class="small faint">${esc([p.cls, htFmt(p.ht), p.pos].filter(Boolean).join(" · "))}</div></td>
        <td class="l">${teamLink(p.team)}</td>${cells}</tr>`;
    }).join("");
    const tbl = $("#ptbl");
    tbl.innerHTML = head + `<tbody>${body || `<tr><td colspan="${cols.length + 3}" class="l faint">No players match.</td></tr>`}</tbody>`;
    $$("th[data-k]", tbl).forEach((h) => h.addEventListener("click", () => {
      const key = h.dataset.k;
      if (PF.sort === key) PF.dir *= -1;
      else { PF.sort = key; const spec = cols.find((c) => c[0] === key); PF.dir = key === "name" || key === "team" || (spec && (spec[2] === null || spec[4] === true)) ? 1 : -1; }
      drawPlayers(P);
    }));
    $("#pmore").innerHTML = n > shown.length ? `<span class="small muted">Showing ${shown.length} of ${n.toLocaleString()} players.</span> <button class="chip" id="pm1">Show 200 more</button> <button class="chip" id="pm2">Show all</button>`
      : `<span class="small muted">${n.toLocaleString()} players.</span>`;
    if (n > shown.length) {
      $("#pm1").addEventListener("click", () => { PF.limit += 200; drawPlayers(P); });
      $("#pm2").addEventListener("click", () => { PF.limit = n; drawPlayers(P); });
    }
  }


  // ================================================================ PLAYER PAGES (career + shot chart)
  const playerHref = (team, pid) => `#/player/${slugify(team)}/${pid}`;
  const playerLink = (p) => (p.pid ? `<a href="${playerHref(p.team, p.pid)}" class="plink"><b>${esc(p.name)}</b></a>` : `<b>${esc(p.name)}</b>`);
  // court geometry in feet, basket at (25, 0), y toward half court (ESPN's shot coordinates)
  function courtSpec(season) {
    const s = Number(season);
    if (IS_PRO) return LEAGUE === "nba" ? { arc: 23.75, corner: 22, lane: 16, ra: 4 } : (s >= 2013 ? { arc: 22.15, corner: 21.65, lane: 16, ra: 4 } : { arc: 20.5, corner: 20.5, lane: 16, ra: 4 });
    const pro3 = IS_W ? s >= 2022 : s >= 2020;   // NCAA moved to the 22' 1.75" line: men 2019-20, women 2021-22
    return pro3 ? { arc: 22.15, corner: 21.65, lane: 12, ra: 4 } : { arc: 20.75, corner: 20.75, lane: 12, ra: 4 };
  }
  const BASE = -5.25, TOP = 34, CORNER_Y = 8;   // baseline and top of the drawn floor (feet from the basket); corner-3 cutoff
  const sy = (y) => +(TOP - y).toFixed(3);       // court y -> svg y (basket near the bottom)
  const ZONE_INFO = { rim: ["At the rim", "Layups, dunks, tips and shots within 4 ft"], smid: ["Short mid-range", "Twos from 5–14 ft"],
    lmid: ["Long mid-range", "Twos from 15 ft out to the 3-point line"], c3: ["Corner 3", "Threes from the corners"], ab3: ["Above-the-break 3", "Threes outside the corners"],
    mid: ["Other 2s", "Twos away from the rim"], three: ["3-pointers", "Every three"] };
  const SCHEME_ZONES = { full: ["rim", "smid", "lmid", "c3", "ab3"], dist: ["rim", "smid", "lmid", "three"], coarse: ["rim", "mid", "three"] };
  function courtPaths(c) {
    const yc = Math.sqrt(Math.max(c.arc ** 2 - c.corner ** 2, 0));   // where the straight corner line meets the arc
    const xl = 25 - c.corner, xr = 25 + c.corner;
    const inside = `M${xl} ${sy(BASE)}L${xl} ${sy(yc)}A${c.arc} ${c.arc} 0 0 1 ${xr} ${sy(yc)}L${xr} ${sy(BASE)}Z`;
    const circ = (r, cy = 0) => `M${25 - r} ${sy(cy)}a${r} ${r} 0 1 0 ${2 * r} 0a${r} ${r} 0 1 0 ${-2 * r} 0Z`;
    const court = `M0 0H50V${sy(BASE)}H0Z`;
    const corners = `M0 ${sy(BASE)}H${xl}V${sy(CORNER_Y)}H0Z M${xr} ${sy(BASE)}H50V${sy(CORNER_Y)}H${xr}Z`;
    return { inside, court, corners, yc, xl, xr,
      zone: { rim: circ(c.ra), smid: circ(14) + circ(c.ra), lmid: inside + circ(14), c3: corners, ab3: court + inside + corners,
        mid: inside + circ(c.ra), three: court + inside } };
  }
  function courtLines(c, P) {
    const lw = c.lane / 2, ft = 19 + BASE;   // free-throw line 19 ft from the baseline
    return `<g class="court-lines">
      <rect x="0" y="0" width="50" height="${sy(BASE)}"/>
      <path d="M${P.xl} ${sy(BASE)}L${P.xl} ${sy(P.yc)}A${c.arc} ${c.arc} 0 0 1 ${P.xr} ${sy(P.yc)}L${P.xr} ${sy(BASE)}"/>
      <rect x="${25 - lw}" y="${sy(ft)}" width="${c.lane}" height="${(ft - BASE).toFixed(2)}"/>
      <path d="M19 ${sy(ft)}A6 6 0 0 1 31 ${sy(ft)}"/><path d="M19 ${sy(ft)}A6 6 0 0 0 31 ${sy(ft)}" stroke-dasharray=".8 .8"/>
      <path d="M${25 - c.ra} ${sy(0)}A${c.ra} ${c.ra} 0 0 1 ${25 + c.ra} ${sy(0)}"/>
      <line x1="22" y1="${sy(-1.25)}" x2="28" y2="${sy(-1.25)}" class="board"/><circle cx="25" cy="${sy(0)}" r=".75"/></g>`;
  }
  // FG% vs league in points -> diverging fill (blue above, red below, gray near average)
  const zoneFill = (d, floor = 18) => {
    if (!isNum(d)) return "var(--surface-3)";
    const t = Math.max(-1, Math.min(1, d / 10));
    return `color-mix(in srgb, ${t >= 0 ? "var(--heat-pos)" : "var(--heat-neg)"} ${Math.round(floor + (80 - floor) * Math.abs(t))}%, var(--surface-2))`;
  };
  function zoneStats(pz, lz, keys) {
    const tot = keys.reduce((s, k) => s + (pz[k] ? pz[k][0] : 0), 0), ltot = keys.reduce((s, k) => s + (lz[k] ? lz[k][0] : 0), 0);
    const out = {};
    keys.forEach((k) => {
      const [a, m] = pz[k] || [0, 0], [la, lm] = lz[k] || [0, 0];
      const fg = a ? (100 * m) / a : null, lfg = la ? (100 * lm) / la : null;
      out[k] = { a, m, fg, lfg, d: isNum(fg) && isNum(lfg) && a >= 5 ? fg - lfg : null, share: tot ? a / tot : 0, lshare: ltot ? la / ltot : 0 };
    });
    return { z: out, tot };
  }
  function shotChart(season, mode, rec, lg) {
    const L = (lg && lg.seasons[season]) || { scheme: "full", zones: {} };
    const keys = SCHEME_ZONES[L.scheme] || SCHEME_ZONES.full;
    const { z } = zoneStats((rec.zones || {})[season] || {}, L.zones || {}, keys);
    const c = courtSpec(season), P = courtPaths(c);
    const located = mode === "loc" && rec.shots && rec.shots[season];
    const zones = keys.map((k) => `<path d="${P.zone[k]}" fill-rule="evenodd" style="fill:${zoneFill(z[k].d)}" opacity="${located ? 0.22 : 0.92}"><title>${esc(ZONE_INFO[k][0])}: ${z[k].m}/${z[k].a}${isNum(z[k].fg) ? ` (${fmt(z[k].fg)}%, league ${fmt(z[k].lfg)}%)` : ""}</title></path>`).join("");
    let marks = "";
    if (located) {
      const cells = rec.shots[season];
      const mx = Math.max(...cells.map((x) => x[3]), 1);
      const order = SCHEME_ZONES.full;
      marks = cells.map(([x, y, zi, a, m]) => {
        const zk = keys.includes(order[zi]) ? order[zi] : (zi >= 3 ? "three" : zi === 0 ? "rim" : keys.includes("mid") ? "mid" : order[zi]);
        const r = (0.24 + 0.52 * Math.sqrt(a / mx)).toFixed(2);
        return `<circle cx="${x}" cy="${sy(y)}" r="${r}" style="fill:${zoneFill(z[zk] ? z[zk].d : null, 42)}" class="shot"><title>${m}/${a} from ${Math.round(Math.hypot(x - 25, y))} ft</title></circle>`;
      }).join("");
    } else {
      // label spots (x list, y of the FG% baseline) clear of the court lines for every league's geometry
      const anchor = { rim: [[25], 2.4], smid: [[14.3, 35.7], 5.6], lmid: [[25], 17.4], c3: [[1.5, 48.5], 2.4], ab3: [[25], 28.5], mid: [[25], 17.4], three: [[25], 28.5] };
      marks = keys.map((k) => {
        const s = z[k]; if (!s.a) return "";
        const [xs, y] = anchor[k], sm = k === "c3" ? " sm" : "";
        return xs.map((x) => `<text x="${x}" y="${sy(y)}" class="zlab${sm}">${fmt(s.fg, 0)}%</text><text x="${x}" y="${sy(y - (sm ? 1.2 : 1.5))}" class="zsub${sm}">${s.m}/${s.a}</text>`).join("");
      }).join("");
    }
    return `<svg class="court" viewBox="-0.5 -0.5 51 ${(sy(BASE) + 1).toFixed(2)}" role="img" aria-label="Shot chart, ${seasonLabel(season)}">
      <defs><clipPath id="courtclip"><rect x="0" y="0" width="50" height="${sy(BASE)}"/></clipPath></defs>
      <rect x="0" y="0" width="50" height="${sy(BASE)}" class="court-bg"/>
      <g clip-path="url(#courtclip)">${zones}</g>${courtLines(c, P)}<g clip-path="url(#courtclip)">${marks}</g></svg>`;
  }
  function zoneTable(season, rec, lg) {
    const L = (lg && lg.seasons[season]) || { scheme: "full", zones: {} };
    const keys = SCHEME_ZONES[L.scheme] || SCHEME_ZONES.full;
    const { z, tot } = zoneStats((rec.zones || {})[season] || {}, L.zones || {}, keys);
    if (!tot) return `<p class="muted">No play-by-play shots for this season.</p>`;
    const rows = keys.map((k) => { const s = z[k]; return `<tr><td class="l wrap"><span class="zdot" style="background:${zoneFill(s.d)}"></span>${esc(ZONE_INFO[k][0])}<div class="small faint hide-sm">${esc(ZONE_INFO[k][1])}</div></td>
      <td class="num">${s.m}/${s.a}</td><td class="num"><b>${isNum(s.fg) ? fmt(s.fg) : "–"}</b></td><td class="num faint">${isNum(s.lfg) ? fmt(s.lfg) : "–"}</td>
      <td class="num ${isNum(s.d) ? (s.d >= 0 ? "pos" : "neg") : "faint"}">${isNum(s.d) ? sgn(s.d) : "–"}</td><td class="num">${(100 * s.share).toFixed(0)}%<span class="faint small hide-sm"> / ${(100 * s.lshare).toFixed(0)}%</span></td></tr>`; }).join("");
    return `<div class="tbl-wrap"><table class="zones"><thead><tr><th class="l nosort">Zone</th><th class="nosort">FGM/A</th><th class="nosort">FG%</th><th class="nosort" title="League FG% from this zone">Lg</th><th class="nosort" title="FG% minus the league's, in points">+/−</th><th class="nosort" title="Share of ${his} attempts / the league's">Diet</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }
  function careerTable(rows, cur) {
    if (!rows || !rows.length) return `<p class="muted">No season lines yet.</p>`;
    const valLab = ESPN_ONLY ? "Value" : "BPM";
    const cols = [["g", "G", 0], ["mpg", "MPG", 1], ["ppg", "PTS", 1], ["rpg", "REB", 1], ["apg", "AST", 1], ["spg", "STL", 1], ["bpg", "BLK", 1],
      ["fg", "FG%", 1], ["tp", "3P%", 1], ["tpa", "3PA", 1, "Threes attempted per game"], ["ft", "FT%", 1], ["ts", "TS%", 1, "True shooting"]]
      .concat(ESPN_ONLY ? [] : [["usg", "Usg%", 1, "Usage rate (Torvik)"]])
      .concat([["val", valLab, 1, ESPN_ONLY ? `Season box plus/minus from hoop2vec's game values (points per 100 vs an average ${LG} player), before the prior` : "Torvik's box plus/minus for the season (points per 100 vs an average D-I player)"]]);
    const body = rows.slice().reverse().map((r) => `<tr><td class="l">${seasonLabel(r.season)}${r.season === cur ? ` <span class="faint small">so far</span>` : ""}</td>
      <td class="l">${TEAMS[r.team] ? teamLink(r.team) : esc(r.team || "")}</td>${IS_PRO ? "" : `<td class="faint">${esc(r.cls || "")}</td>`}
      ${cols.map(([k, , d]) => `<td class="num">${k === "val" ? sgn(r[k], d) : fmt(r[k], d)}</td>`).join("")}</tr>`).join("");
    // career per-game averages, weighted by games (minutes for MPG-weighted rates)
    const G = rows.reduce((s, r) => s + (r.g || 0), 0);
    const wavg = (k, w = (r) => r.g || 0) => { let n = 0, d = 0; rows.forEach((r) => { if (isNum(r[k])) { n += w(r) * r[k]; d += w(r); } }); return d ? n / d : null; };
    const totals = rows.length > 1 ? `<tr class="total"><td class="l"><b>Career</b></td><td class="l faint small">${rows.length} seasons</td>${IS_PRO ? "" : "<td></td>"}
      ${cols.map(([k, , d]) => `<td class="num">${k === "g" ? G : ["fg", "tp", "ft", "ts", "usg"].includes(k) ? "" : k === "val" ? sgn(wavg(k, (r) => (r.g || 0) * (r.mpg || 0)), d) : fmt(wavg(k), d)}</td>`).join("")}</tr>` : "";
    return `<div class="tbl-wrap"><table class="career"><thead><tr><th class="l nosort">Season</th><th class="l nosort">Team</th>${IS_PRO ? "" : `<th class="nosort">Yr</th>`}${cols.map(([, l, , t]) => `<th class="nosort" title="${esc(t || "")}">${l}</th>`).join("")}</tr></thead><tbody>${body}${totals}</tbody></table></div>`;
  }
  async function viewPlayer(slug, params, pidArg) {
    const [d] = await Promise.all([get("players.json"), teams()]);
    const pid = Number(pidArg);
    const p = d.players.find((x) => x.pid === pid && slugify(x.team) === slug) || d.players.find((x) => x.pid === pid);
    const [shard, lg] = await Promise.all([get(`players/${slug}.json`, true), get("shots_league.json", true)]);
    const rec = (shard && shard.players[String(pid)]) || { career: [], zones: {}, shots: {} };
    if (!p && !rec.career.length) { app.innerHTML = `<div class="card">Player not found. <a href="#/players">All players</a></div>`; return; }
    const name = p ? p.name : `Player ${pid}`, team = p ? p.team : (shard && shard.team);
    document.title = [name, team, "hoop2vec"].filter(Boolean).join(" · ");
    const t = TEAMS[team];
    const color = t && t.color && /^[0-9a-f]{6}$/i.test(t.color) && t.color.toLowerCase() !== "ffffff" ? `#${t.color}` : "var(--accent)";
    const stat = (k, v, r = "") => `<div class="tile"><span class="k">${k}</span><span class="v num">${v}</span><span class="r">${r}</span></div>`;
    const tiles = p ? [
      stat("Value", sgn(p.value), p.value_rank ? `#${p.value_rank} · per 100 vs avg ${LG}` : `per 100 vs avg ${LG}`),
      stat("Impact", sgn(p.impact), p.impact_rank ? `#${p.impact_rank} · team pts per 100` : "team pts per 100"),
      stat("Points", fmt(p.ppg), "per game"), stat("Rebounds", fmt(p.rpg), "per game"), stat("Assists", fmt(p.apg), "per game"),
      stat("True shooting", fmt(p.ts), `${fmt(p.fg_pct)} FG% · ${fmt(p.tp_pct)} 3P%`), stat("Minutes", fmt(p.mpg), `${p.g} games`),
      stat("Availability", isNum(p.avail) ? pct(p.avail) : "–", "chance to play next game"),
    ].join("") : "";
    const seasons = [...new Set([...Object.keys(rec.zones || {}), ...Object.keys(rec.shots || {})])].map(Number).sort((a, b) => b - a);
    let season = Number(params.get("season")) || seasons[0];
    if (!seasons.includes(season)) season = seasons[0];
    let mode = params.get("view") === "zones" ? "zones" : "loc";
    const bio = p ? [p.pos, htFmt(p.ht), IS_PRO ? (p.cls ? (p.cls === "R" ? "Rookie" : `Year ${p.cls}`) : "") : p.cls, p.role, p.conf].filter(Boolean) : [];
    app.innerHTML = `
      <div class="card" style="background:linear-gradient(120deg, color-mix(in srgb, ${color} 22%, var(--surface)), var(--surface) 55%)">
        <div class="team-hero">${logo(t, "lg")}<div class="meta"><h1>${esc(name)}</h1>
          <div class="badges">${team ? `<a class="pill acc" href="#/team/${slugify(team)}">${esc(team)}</a>` : ""}${bio.map((b) => `<span class="pill">${esc(b)}</span>`).join("")}${p && p.status ? `<span class="pill bad">✚ ${esc(p.status)}</span>` : ""}</div></div>
          <a href="#/players${team ? `?team=${encodeURIComponent(team)}` : ""}">All ${esc(team || "")} players →</a></div>
      </div>
      ${tiles ? `<div class="section tiles">${tiles}</div>` : ""}
      <div class="section grid g2 top">
        <div class="card"><div class="chart-head"><h2>Shot chart</h2><div class="controls">
          ${seasons.length ? `<select id="shseason" aria-label="Season">${seasons.map((s) => `<option value="${s}" ${s === season ? "selected" : ""}>${seasonLabel(s)}</option>`).join("")}</select>` : ""}
          <span id="shmode"></span></div></div>
          <div id="shchart"></div><p class="note" id="shnote"></p></div>
        <div class="card"><h2>By zone</h2><div id="shzones"></div>
          <p class="note">From ESPN play-by-play, ${IS_PRO ? "regular season and playoffs" : "every game"}. Lg = the ${LG} average from that zone that season; Diet = share of ${his} shots / the league's. +/− needs 5+ attempts.</p></div>
      </div>
      <div class="section card flush"><div style="padding:16px 20px 6px"><h2>Career</h2>
        <p class="note" style="margin-top:-4px">${ESPN_ONLY ? `Per-game averages from ESPN box scores (${IS_PRO ? `${LEAGUE.toUpperCase()} since 2008, regular season and playoffs` : "since 2018–19"}).` : "Every D-I season since 2007–08, from barttorvik.com's player tables."} A player who changed teams mid-season has a line for each team.</p></div>
        ${careerTable(rec.career, d.season)}</div>`;
    const draw = () => {
      const has = rec.shots && rec.shots[season];
      if (!has && mode === "loc") mode = "zones";
      $("#shmode").innerHTML = has ? seg("shm", [["loc", "Locations"], ["zones", "Zones"]], mode) : "";
      if (has) onSeg("shm", (v) => { mode = v; draw(); });
      $("#shchart").innerHTML = seasons.length ? shotChart(String(season), mode, rec, lg) : `<p class="muted">No play-by-play shots yet.</p>`;
      $("#shzones").innerHTML = seasons.length ? zoneTable(String(season), rec, lg) : `<p class="muted">No play-by-play shots yet.</p>`;
      const L = lg && lg.seasons[String(season)];
      $("#shnote").innerHTML = !seasons.length ? "" : (mode === "loc" && has
        ? `Each dot is a one-foot spot on the floor: bigger = more attempts. Color = ${his} FG% in that zone vs the ${LG} average (blue above, red below, ±10 points at full color).`
        : `Zones colored by ${his} FG% vs the ${LG} average from there (blue above, red below, ±10 points at full color).`)
        + (L && L.scheme !== "full" ? ` ESPN didn't record ${L.scheme === "coarse" ? "shot distances or locations" : "court locations"} this season, so zones are broader.` : "")
        + (!has && L && L.scheme === "full" ? " Exact spots weren't kept for this season, so the chart shows zones." : "");
    };
    draw();
    const sel = $("#shseason");
    if (sel) sel.addEventListener("change", () => { season = Number(sel.value); mode = "loc"; draw(); });
  }

  // ================================================================ PRO (NBA / WNBA) PROSPECTS
  const MONTH = IS_W ? "April" : "June";
  let XV = {};
  function prospectViews(d) {
    const T = d.thresholds || [5, 14, 30, 60], sw = d.success_ws, st = d.star_ws, w = d.window;
    const tierLab = IS_W ? ["Top " + T[0], "1st round", "Top " + T[2], "Drafted"] : ["Top 5", "Lottery", "1st round", "Drafted"];
    const tierKey = T.map((t) => "p" + t);
    const yrs = `${his} first ${w} ${PRO} seasons`;
    XV = {
      draft: tierKey.map((k, i) => [k, tierLab[i], "pct", i === 3 ? `P(drafted at all this ${MONTH})` : `P(picked in the top ${T[i]} this ${MONTH})`]).concat([
        ["p_success", `${PRO} success`, "pct", `P(${sw}+ Win Shares in ${yrs})`], ["bpm", "BPM", 1, `Box plus/minus this season (${RATE_SRC})`],
        ["pts", "PTS", 1], ["reb", "REB", 1], ["ast", "AST", 1]]),
      success: [["p_success", `${PRO} success`, "pct", `P(${sw}+ Win Shares in ${yrs}): an established rotation player`],
        ["p_star", "Star start", "pct", `P(${st}+ Win Shares in ${yrs})`], ["exp_ws", "Exp. WS", 1, `Expected Win Shares, first ${w} ${PRO} seasons`],
        [tierKey[1], tierLab[1], "pct"], ["rate", "Comp hit rate", "rate", `Of the 25 most similar finished college careers at the same stage: share that became ${PRO} rotation players`],
        ["college", `Closest ${PRO} comps`, "comps", `College players with the most similar projected ${PRO} profile at the same stage (click a row for details)`]],
      stats: [["mpg", "MPG", 1], ["pts", "PTS", 1], ["reb", "REB", 1], ["ast", "AST", 1], ["stl", "STL", 1], ["blk", "BLK", 1],
        ["bpm", "BPM", 1], ["obpm", "OBPM", 1], ["dbpm", "DBPM", 1], ["usg", "Usg%", 1], ["ts", "TS%", 1], ["ast_pct", "Ast%", 1],
        ["to_pct", "TO%", 1, "", true], ["blk_pct", "Blk%", 1], ["stl_pct", "Stl%", 1], ["tp_pct", "3P%", 1], ["tpa", "3PA", 1]].concat(IS_W ? [["ht", "Ht", 0, "Height (inches)"]] : [["rec", "Recruit", 1]]),
    };
    return { T, tierKey, tierLab };
  }
  const XF = { sort: "board", dir: 1, view: "draft", q: "", cls: "", conf: "", limit: 60 };
  const XOPEN = new Set();
  const NBA_DIM = [["usg_pct", "Usage"], ["ts_pct", "True shooting"], ["tpar", "3PA rate"], ["ftr", "FT rate"], ["ast_pct", "Assist%"],
    ["tov_pct", "Turnover%"], ["orb_pct", "Off. reb%"]].concat(IS_W ? [["trb_pct", "Total reb%"], ["stl_pct", "Steal%"], ["blk_pct", "Block%"], ["ws_per_40", "WS / 40"]]
    : [["drb_pct", "Def. reb%"], ["stl_pct", "Steal%"], ["blk_pct", "Block%"], ["bpm", "BPM"]]);
  let XD = { window: 4, first: 2009, last: 2026 };
  const statLine = (l) => !l ? "" : `${fmt(l.pts)} pts · ${fmt(l.trb)} reb · ${fmt(l.ast)} ast${isNum(l.fg3_pct) && l.fg3a >= 0.5 ? ` · ${(100 * l.fg3_pct).toFixed(0)}% 3P` : ""}`;
  function prospectDetail(p) {
    const w = XD.window, wy = `first ${w} yrs`;
    const prof = NBA_DIM.map(([k, lab]) => {
      const v = p.proj_pct ? p.proj_pct[k] : null;
      return `<div class="profile-row" style="grid-template-columns:110px minmax(0,1fr) 40px"><span class="lab">${lab}</span><div class="pct-track"><span style="width:${Math.max(3, 100 * (v ?? 0))}%;background:var(--accent)"></span></div><span class="small faint num" style="text-align:right">${v == null ? "" : ord(100 * v)}</span></div>`;
    }).join("");
    const r = p.rate || {};
    const col = (p.college || []).map((c) => `<tr><td class="l"><b>${esc(c.name)}</b><div class="small faint">${esc(c.cls || "")} at ${esc(c.team)}, ${seasonLabel(c.season)} · similarity ${c.sim}</div></td>
      <td class="num">${c.pick ? "#" + c.pick : "undrafted"}</td><td class="l small">${esc(c.pos || "")} · ${statLine(c.line)}</td><td class="num"><b>${fmt(c.ws, 1)}</b><div class="small faint">${c.seasons < w ? c.seasons + (c.seasons === 1 ? " season" : " seasons") + " so far" : wy}</div></td></tr>`).join("");
    const imp = IS_W ? "ws_per_40" : "bpm";
    const sty = (p.style || []).map((c) => `<tr><td class="l"><b>${esc(c.name)}</b><div class="small faint">${esc(c.pos || "")} · ${esc(c.years.split("–")[0] === c.years.split("–")[1] ? c.years.split("–")[0] : c.years)}</div></td><td class="l small">${statLine(c.line)}</td><td class="num">${IS_W ? fmt(c[imp], 3) : sgn(c[imp])}</td></tr>`).join("");
    return `<div class="grid xdet" style="padding:6px 2px">
      <div><h3>Projected ${PRO} profile</h3><p class="note" style="margin:-2px 0 8px">What ${his} college game translates to in ${his} first ${PRO} seasons, as a percentile among ${PRO} players (${IS_W ? "1,000" : "2,000"}+ minutes).</p>${prof}
        <p class="note">Comp hit rate: of the ${r.n || 25} most similar finished college careers at this stage, <b>${pct(r.nba)}</b> reached the ${PRO} and <b>${pct(r.success)}</b> became rotation players.</p></div>
      <div><h3>College comps → what they became</h3><p class="note" style="margin:-2px 0 8px">Past college players at the same stage whose game projected most like ${his}. ${PRO} line = first ${w} ${PRO} seasons.</p>
        <div class="tbl-wrap"><table><thead><tr><th class="l nosort">Player</th><th class="nosort">Pick</th><th class="l nosort">${PRO}</th><th class="nosort">WS</th></tr></thead><tbody>${col || `<tr><td class="faint l" colspan="4">No comps.</td></tr>`}</tbody></table></div></div>
      <div><h3>Plays like</h3><p class="note" style="margin:-2px 0 8px">${PRO} players (incl. internationals) whose actual ${PRO} profile is closest to ${his} projected one. Line = career average in ${XD.first}–${XD.last}.</p>
        <div class="tbl-wrap"><table><thead><tr><th class="l nosort">${PRO} player</th><th class="l nosort">Line</th><th class="nosort">${IS_W ? "WS/40" : "BPM"}</th></tr></thead><tbody>${sty}</tbody></table></div></div></div>`;
  }

  async function viewProspects() {
    const [d] = await Promise.all([get("prospects.json"), teams()]);
    const P = d.players;
    const { tierKey, tierLab } = prospectViews(d);
    // projected draft order = expected draft value (same as prospects.py draft_value): each tier's probability times
    // the average value of its picks on a steep pick-value curve, so the likeliest high picks come first
    const T = d.thresholds || [5, 14, 30, 60], vs = (4 * T[T.length - 1]) / 60, val = (k) => 1 / (1 + (k - 1) / vs);
    const tierVal = T.map((t, i) => { const lo = i ? T[i - 1] : 0; let a = 0; for (let k = lo + 1; k <= t; k++) a += val(k); return a / (t - lo); });
    P.forEach((p) => { if (!isNum(p.draft_value)) p.draft_value = T.reduce((s, t, i) => s + (p["p" + t] - (i ? p["p" + T[i - 1]] : 0)) * tierVal[i], 0); });
    P.slice().sort((a, b) => b.draft_value - a.draft_value).forEach((p, i) => { p.board = i + 1; });
    XD = { window: d.window, first: d.pro_first || 2009, last: d.pro_last || 2026 };
    const confs = [...new Set(P.map((p) => p.conf).filter(Boolean))].sort();
    // each board is ranked by the number it shows (ties: overall draft score)
    const board = (key, title, note, fmtv) => `<div class="card"><h2>${title}</h2><p class="note" style="margin-top:-4px">${note}</p>${P.slice().sort((a, b) => b[key] - a[key] || b.draft_score - a.draft_score).slice(0, 10).map((p, i) => `
        <div class="odds-row lb-row"><span class="faint num">${i + 1}</span>
          <span class="teamcell" style="font-weight:500">${logo(TEAMS[p.team])}<span class="nm lb-nm"><b>${esc(p.name)}</b><span class="faint small">${esc(p.cls || "")} · ${esc(p.team)}</span></span></span>
          <div class="pbar lb-bar"><span style="width:${(100 * p[key] / Math.max(...P.map((x) => x[key]))).toFixed(1)}%"></span></div><div class="num" style="text-align:right;font-weight:600">${fmtv(p)}</div></div>`).join("")}</div>`;
    // projected draft order, with the three tier probabilities that explain it
    const tiers = [0, 1, 2];
    const draftBoard = () => `<div class="card"><h2>Projected draft order</h2><p class="note" style="margin-top:-4px">College players ranked by where they're likeliest to be picked this ${MONTH}, weighting high picks most. Columns: chance of going in each range.</p>
        <div class="odds-row db-row db-head"><span></span><span></span>${tiers.map((i) => `<span class="num">${tierLab[i].replace("1st round", "1st rd")}</span>`).join("")}</div>
        ${P.slice().sort((a, b) => a.board - b.board).slice(0, 10).map((p) => `
        <div class="odds-row db-row"><span class="faint num">${p.board}</span>
          <span class="teamcell" style="font-weight:500">${logo(TEAMS[p.team])}<span class="nm lb-nm"><b>${esc(p.name)}</b><span class="faint small">${esc(p.cls || "")} · ${esc(p.team)}</span></span></span>
          ${tiers.map((i) => `<span class="num db-p${i === 0 ? " lead" : ""}">${pct(p[tierKey[i]])}</span>`).join("")}</div>`).join("")}</div>`;
    const bt = d.backtest;
    app.innerHTML = `
      <div class="page-head"><div><h1>${d.draft_year} ${PRO} Prospects</h1>
        <p class="sub">Two models built on every D-I player-season since ${d.train ? d.train.first || (IS_W ? 2019 : 2008) : IS_W ? 2019 : 2008}. The <b>draft model</b> predicts where college players will be picked this ${MONTH}. The <b>success model</b> predicts whether a player becomes an established ${PRO} rotation player: ${d.success_ws}+ Win Shares in ${his} first ${d.window} ${PRO} seasons. Both are trained only on completed seasons and checked out-of-sample against real drafts and real ${PRO} careers. ${IS_W ? "College players only: international prospects aren't modeled. The WNBA has only 36 picks and 144 roster spots, so even very good college players often don't stick; the women's history is also shorter (box scores since 2019), so these models are less certain than the men's." : "College players only: international and G League prospects aren't modeled, so board slots run ahead of real picks. Early in the season, ratings lean on last season and recruiting."}</p></div></div>
      <div class="grid g2">${draftBoard()}
        ${board("p_success", `Best ${PRO} bets`, `P(${d.success_ws}+ Win Shares in ${his} first ${d.window} ${PRO} seasons)`, (p) => pct(p.p_success))}</div>
      <div class="section controls">
        <input type="search" id="xq" placeholder="Search prospects or teams…" style="width:230px" value="${esc(XF.q)}" aria-label="Search prospects">
        <select id="xcls" aria-label="Class"><option value="">All classes</option>${["Fr", "So", "Jr", "Sr"].map((c) => `<option ${c === XF.cls ? "selected" : ""}>${c}</option>`).join("")}</select>
        <select id="xconf" aria-label="Conference"><option value="">All conferences</option>${confs.map((c) => `<option ${c === XF.conf ? "selected" : ""}>${esc(c)}</option>`).join("")}</select>
        ${seg("xview", [["draft", "Draft"], ["success", `${PRO} success`], ["stats", "Stats"]], XF.view)}
      </div>
      <div class="card flush tbl-wrap"><table id="xtbl" class="ratings-table"></table></div>
      <div id="xmore" style="margin-top:10px"></div>
      ${prospectMethod(d, bt)}`;
    const redraw = () => { XF.limit = 60; drawProspects(P); };
    $("#xq").addEventListener("input", (e) => { XF.q = e.target.value; redraw(); });
    $("#xcls").addEventListener("change", (e) => { XF.cls = e.target.value; redraw(); });
    $("#xconf").addEventListener("change", (e) => { XF.conf = e.target.value; redraw(); });
    onSeg("xview", (v) => { XF.view = v; XF.sort = v === "success" ? "p_success" : "board"; XF.dir = v === "success" ? -1 : 1; redraw(); });
    drawProspects(P);
  }

  function drawProspects(P) {
    const cols = XV[XF.view];
    const q = XF.q.toLowerCase();
    let rows = P.filter((p) => (!XF.cls || p.cls === XF.cls) && (!XF.conf || p.conf === XF.conf) && (!q || p.name.toLowerCase().includes(q) || p.team.toLowerCase().includes(q)));
    const dist = {};
    cols.forEach(([k]) => (dist[k] = rows.map((p) => p[k]).filter(isNum)));
    const k = XF.sort, dr = XF.dir;
    rows.sort((a, b) => {
      const x = a[k], y = b[k];
      if (typeof x === "string" || typeof y === "string") return dr * String(x ?? "~").localeCompare(String(y ?? "~"));
      return dr * ((x ?? -1e9) - (y ?? -1e9));
    });
    const n = rows.length, shown = rows.slice(0, XF.limit);
    const th = (key, label, title = "", cls = "") => `<th data-k="${key}" class="${cls} ${k === key ? "sorted" : ""}" title="${esc(title)}" scope="col">${label}${k === key ? (dr > 0 ? " ↑" : " ↓") : ""}</th>`;
    const head = `<thead><tr>${th("board", "Board", "Projected order among college players (draft model)", "rankcol")}${th("name", "Player", "", "l")}${th("team", "Team", "", "l")}${IS_W ? "" : th("age", "Age", "Age on draft day")}${cols.map(([c, l, , t]) => c === "comps" ? `<th class="l nosort" title="${esc(t)}">${l}</th>` : th(c, l, t || "")).join("")}</tr></thead>`;
    const ncol = cols.length + (IS_W ? 3 : 4);
    const body = shown.map((p) => {
      const cells = cols.map(([c, , f, , inv]) => {
        const v = p[c];
        if (c === "comps") return `<td class="l small wrap" style="min-width:220px">${(v || []).slice(0, 3).map((x) => esc(x.name)).join(", ")}</td>`;
        if (f === "rate") return `<td class="num">${v && isNum(v.success) ? pct(v.success) : "–"}</td>`;
        if (f === "pct") return `<td><span class="heat num" style="background:color-mix(in srgb, var(--accent) ${Math.round(Math.min(1, v || 0) * 40)}%, transparent)">${v < 0.005 ? "<1%" : pct(v)}</span></td>`;
        return `<td><span class="heat num" style="${heat(pctRank(dist[c], v), inv === true)}">${fmt(v, f)}</span></td>`;
      }).join("");
      return `<tr class="xrow" data-pid="${p.pid}" style="cursor:pointer" title="Click for ${PRO} comps"><td class="rankcol">${p.board}</td><td class="l"><b>${esc(p.name)}</b> <span class="faint small">${XOPEN.has(p.pid) ? "▾" : "▸"}</span><div class="small faint">${esc([p.cls, typeof p.ht === "number" ? htFmt(p.ht) : p.ht, p.role].filter(Boolean).join(" · "))}</div></td>
        <td class="l">${TEAMS[p.team] ? teamLink(p.team) : esc(p.team)}</td>${IS_W ? "" : `<td class="num">${fmt(p.age, 1)}</td>`}${cells}</tr>`
        + (XOPEN.has(p.pid) ? `<tr class="xdetail"><td colspan="${ncol}" class="l" style="white-space:normal;background:var(--surface-2)">${prospectDetail(p)}</td></tr>` : "");
    }).join("");
    const tbl = $("#xtbl");
    tbl.innerHTML = head + `<tbody>${body || `<tr><td class="l faint" colspan="9">No prospects match.</td></tr>`}</tbody>`;
    $$("tr.xrow", tbl).forEach((tr) => tr.addEventListener("click", (e) => {
      if (e.target.closest("a")) return;
      const id = +tr.dataset.pid;
      if (XOPEN.has(id)) XOPEN.delete(id); else XOPEN.add(id);
      drawProspects(P);
    }));
    $$("th[data-k]", tbl).forEach((h) => h.addEventListener("click", () => {
      const key = h.dataset.k;
      if (XF.sort === key) XF.dir *= -1;
      else { XF.sort = key; const spec = cols.find((c) => c[0] === key); XF.dir = ["board", "name", "team", "age"].includes(key) || (spec && spec[4] === true) ? 1 : -1; }
      drawProspects(P);
    }));
    $("#xmore").innerHTML = n > shown.length ? `<span class="small muted">Showing ${shown.length} of ${n} prospects.</span> <button class="chip" id="xm1">Show 100 more</button> <button class="chip" id="xm2">Show all</button>` : `<span class="small muted">${n} prospects.</span>`;
    if (n > shown.length) {
      $("#xm1").addEventListener("click", () => { XF.limit += 100; drawProspects(P); });
      $("#xm2").addEventListener("click", () => { XF.limit = n; drawProspects(P); });
    }
  }

  function prospectMethod(d, bt) {
    if (!bt) return "";
    const D = bt.draft, S = bt.success;
    const T = d.thresholds || [5, 14, 30, 60], w = S.window;
    const slotT = IS_W ? `Top ${T[0]} (AUC)` : "Lottery picks (AUC)", t1 = IS_W ? "First-round" : "Actual";
    const yrs = `${w}-yr`;
    const row = (lab, a, b, c, best) => `<tr><td class="l">${lab}</td><td class="num ${best === 0 ? "best" : ""}">${a}</td><td class="num ${best === 1 ? "best" : ""}">${b}</td>${c !== undefined ? `<td class="num ${best === 2 ? "best" : ""}">${c}</td>` : ""}</tr>`;
    const slots = Object.entries(d.by_slot).map(([k, v]) => `<tr><td class="l">Picks ${k}</td><td class="num">${v.n}</td><td class="num">${pct(v.success)}</td><td class="num">${pct(v.star)}</td><td class="num">${fmt(v.median_ws)}</td></tr>`).join("");
    return `<div class="section prose" style="max-width:none">
      <h2>How the prospect models work</h2>
      <div class="grid g2">
        <div class="card"><h2>Draft model</h2>
          <p class="small muted">Gradient-boosted classifiers give, for every player-season, the probability of being picked in the top ${T.slice(0, 3).join(", ")} or ${T[3]} that ${MONTH}. Labels come from ${IS_W ? "basketball-reference.com's WNBA draft pages, matched to college players by name, timing and school," : "barttorvik.com's draft-pick field"} and count only on a player's final college season. The board orders players by the sum of the four probabilities. Backtest: each draft ${D.years[0]}–${D.years[1]} predicted by a model trained only on earlier drafts.</p>
          <div class="tbl-wrap"><table><thead><tr><th class="l nosort">Test</th><th class="nosort">Model</th><th class="nosort">Baseline</th></tr></thead><tbody>
            ${row("Who gets drafted (AUC)", fmt(D.model.auc_drafted, 3), fmt(D.baseline.auc_drafted, 3), undefined, D.model.auc_drafted >= D.baseline.auc_drafted ? 0 : 1)}
            ${row(slotT, fmt(D.model.auc_lottery, 3), fmt(D.baseline.auc_lottery, 3), undefined, D.model.auc_lottery >= D.baseline.auc_lottery ? 0 : 1)}
            ${row("Draft order of picked players (Spearman)", fmt(D.model.spearman_pick, 2), fmt(D.baseline.spearman_pick, 2), undefined, D.model.spearman_pick >= D.baseline.spearman_pick ? 0 : 1)}
            ${row(IS_W ? `Actual draftees in the top ${T[1]} (avg)` : "Actual draftees in the top 30 (avg)", fmt(D.model.top30_hits, 1), fmt(D.baseline.top30_hits, 1), undefined, D.model.top30_hits >= D.baseline.top30_hits ? 0 : 1)}
          </tbody></table></div><p class="note">Baseline: a logistic model on ${IS_W ? "class, BPM and height (there are no free recruiting or birth-date data for women's college players)" : "recruiting score, BPM and age"}.</p></div>
        <div class="card"><h2>${PRO} success model</h2>
          <p class="small muted">Regularized logistic and Poisson models on ~70 college features. Success means ${S.success_ws}+ Win Shares in the first ${S.window} ${PRO} seasons ${IS_W ? "(the rookie season starts a month after the draft)" : "after college"}; a "star start" is ${S.star_ws}+. ${PRO} careers come from basketball-reference.com, matched by ${IS_W ? "draft records, then name and timing" : "name, timing and age"}; a player who never reaches the ${PRO} counts as zero. Backtest: classes ${S.years[0]}–${S.years[1]}, each predicted at its final college season by a model trained on earlier classes.</p>
          <div class="tbl-wrap"><table><thead><tr><th class="l nosort">Test</th><th class="nosort">Model</th><th class="nosort">Baseline</th><th class="nosort">Draft slot</th></tr></thead><tbody>
            ${row("All players: log loss ↓", fmt(S.model.ll_all, 4), fmt(S.baseline.ll_all, 4), "–", S.model.ll_all <= S.baseline.ll_all ? 0 : 1)}
            ${row("Drafted players: success (AUC)", fmt(S.model.auc_drafted, 3), fmt(S.baseline.auc_drafted, 3), fmt(S.draft_slot.auc_drafted, 3), [S.model.auc_drafted, S.baseline.auc_drafted, S.draft_slot.auc_drafted].indexOf(Math.max(S.model.auc_drafted, S.baseline.auc_drafted, S.draft_slot.auc_drafted)))}
            ${row(`Drafted players: ${yrs} Win Shares (Spearman)`, fmt(S.model.spearman_ws_drafted, 2), "–", fmt(S.draft_slot.spearman_ws_drafted, 2), S.model.spearman_ws_drafted >= S.draft_slot.spearman_ws_drafted ? 0 : 2)}
          </tbody></table></div>
          <p class="note">${S.draft_slot.auc_drafted > S.model.auc_drafted ? `Honest result: actual draft position is still the better predictor of ${PRO} success (AUC ${fmt(S.draft_slot.auc_drafted, 3)} vs ${fmt(S.model.auc_drafted, 3)}). Teams see workouts, medicals and context that box scores don't` : `The model ranks drafted players' ${PRO} success about as well as or better than draft position (AUC ${fmt(S.model.auc_drafted, 3)} vs ${fmt(S.draft_slot.auc_drafted, 3)}), though with few classes the gap is noisy`}; model + slot together scored ${fmt(S.combined.auc_drafted, 3)}. Before the draft the model is a strong statistical read, and it often flags players teams later passed on.</p></div>
      </div>
      <div class="section grid g2">
        <div class="card"><h2>Out of sample: the ${D.last_draft.year} draft</h2><p class="note" style="margin-top:-4px">The model's pre-draft college board (trained only on earlier drafts) vs where players actually went.</p>
          <div class="tbl-wrap"><table><thead><tr><th class="nosort">Board</th><th class="l nosort">Player</th><th class="l nosort">Team</th><th class="nosort">P(${IS_W ? "top " + T[1] : "1st rd"})</th><th class="nosort">Actual pick</th></tr></thead><tbody>
          ${D.last_draft.board.map((r) => `<tr><td class="num">${r.rank}</td><td class="l">${esc(r.name)}</td><td class="l faint">${esc(r.team)}</td><td class="num">${pct(r.p30 ?? r["p" + T[1]])}</td><td class="num ${r.pick == null ? "faint" : r.pick <= T[1] * (IS_W ? 1 : 30 / 14) ? "pos" : ""}">${r.pick == null ? "undrafted / returned" : "#" + r.pick}</td></tr>`).join("")}</tbody></table></div></div>
        <div class="card"><h2>Out of sample: the ${S.last_class.year} class, ${S.window} years on</h2><p class="note" style="margin-top:-4px">The success model's top 20 at draft time (trained on earlier classes) and what happened.</p>
          <div class="tbl-wrap"><table><thead><tr><th class="nosort">#</th><th class="l nosort">Player</th><th class="nosort">P(success)</th><th class="nosort">Pick</th><th class="nosort">${yrs} WS</th></tr></thead><tbody>
          ${S.last_class.board.map((r) => `<tr><td class="num">${r.rank}</td><td class="l">${esc(r.name)} <span class="faint small">${esc(r.team)}</span></td><td class="num">${pct(r.p_success)}</td><td class="num">${r.pick == null ? "undrafted" : "#" + r.pick}</td><td class="num ${r.success ? "pos" : "faint"}">${fmt(r.ws, 1)}</td></tr>`).join("")}</tbody></table></div></div>
      </div>
      ${bt.comps ? `<div class="section card"><h2>How the ${PRO} comps work</h2>
        <p class="small muted">A translation model (multi-output ridge regression) learns how a college profile turns into a ${PRO} profile over a player's first ${S.window} ${PRO} seasons. It's trained on ${bt.comps.n_players.toLocaleString()} college players who reached the ${PRO}, ${bt.comps.classes[0]}–${bt.comps.classes[1]}. College comps are the past college players at the same stage (same class${IS_W ? "" : ", age within 1.5 years"}) whose <i>projected</i> ${PRO} profile, plus height, is closest. "Plays like" comps are ${PRO} players whose <i>actual</i> profile is closest to the projection. Validation: each held-out class's five comps (chosen from earlier classes) predict the player's real NBA profile. ${(() => { const m = (o) => Object.values(o).reduce((a, b) => a + b, 0) / Object.keys(o).length, l = m(bt.comps.learned), r = m(bt.comps.raw);
          return `Learned comps ${l > r + 0.01 ? "beat" : l < r - 0.01 ? "trail" : "are about as good as"} comps picked on raw college stats on average (R² ${fmt(l, 2)} vs ${fmt(r, 2)})${l <= r + 0.01 ? ", but they add the projected pro profile that raw stats can't" : ""}.`; })()}</p>
        <div class="tbl-wrap"><table><thead><tr><th class="l nosort">${PRO} dimension</th>${Object.keys(bt.comps.translation_r2).map((k) => `<th class="nosort">${esc(bt.comps.labels[k] || k)}</th>`).join("")}</tr></thead><tbody>
          <tr><td class="l">Translation model R²</td>${Object.values(bt.comps.translation_r2).map((v) => `<td class="num">${fmt(v, 2)}</td>`).join("")}</tr>
          <tr><td class="l">Learned comps R²</td>${Object.values(bt.comps.learned).map((v) => `<td class="num">${fmt(v, 2)}</td>`).join("")}</tr>
          <tr><td class="l">Raw-stat comps R²</td>${Object.values(bt.comps.raw).map((v) => `<td class="num">${fmt(v, 2)}</td>`).join("")}</tr></tbody></table></div>
        <p class="note">Out-of-sample. Rebounding, blocks, assists and 3-point volume carry over from college. Efficiency and overall impact (true shooting, ${IS_W ? "WS/40" : "BPM"}) are much harder to see coming.</p></div>` : ""}
      <div class="section grid g3">
        <div class="card"><h2>Calibration</h2><p class="note" style="margin-top:-4px">Out-of-sample P(success) vs what happened (before the small Platt correction the live model applies).</p>
          <div class="tbl-wrap"><table><thead><tr><th class="l nosort">Bin</th><th class="nosort">Players</th><th class="nosort">Pred.</th><th class="nosort">Obs.</th></tr></thead><tbody>
          ${S.calibration.map((c) => `<tr><td class="l">${Math.round(100 * c.lo)}–${Math.round(100 * c.hi)}%</td><td class="num">${c.n.toLocaleString()}</td><td class="num">${pct(c.pred, 1)}</td><td class="num">${pct(c.obs, 1)}</td></tr>`).join("")}</tbody></table></div></div>
        <div class="card"><h2>What drives ${PRO} success</h2><p class="note" style="margin-top:-4px">Standardized weights in the success model (log-odds per SD).</p>
          <ul class="clean plus">${d.drivers.up.map((x) => `<li>${esc(x.f)} <span class="faint num">${sgn(x.w, 2)}</span></li>`).join("")}</ul>
          <ul class="clean minus" style="margin-top:8px">${d.drivers.down.map((x) => `<li>${esc(x.f)} <span class="faint num">${sgn(x.w, 2)}</span></li>`).join("")}</ul>
          <p class="note">${IS_W ? "Class stands in for age: the same production as a freshman or sophomore is worth far more than as a senior." : "Youth matters most: the same production at 19 is worth far more than at 23."}</p></div>
        <div class="card"><h2>History by draft slot</h2><p class="note" style="margin-top:-4px">College players drafted ${d.train.success_classes[0]}–${d.train.success_classes[1]}.</p>
          <div class="tbl-wrap"><table><thead><tr><th class="l nosort">Slot</th><th class="nosort">n</th><th class="nosort">Success</th><th class="nosort">Star</th><th class="nosort">Med. WS</th></tr></thead><tbody>${slots}</tbody></table></div></div>
      </div></div>`;
  }

  // ================================================================ TEAM
  function lineChart(el, pts, color, label) {
    if (pts.length < 2) { el.innerHTML = `<p class="faint small">Not enough games yet.</p>`; return; }
    const W = 560, H = 210, P = { l: 34, r: 14, t: 14, b: 24 };
    const ys = pts.map((p) => p.y);
    let lo = Math.min(...ys), hi = Math.max(...ys);
    const pad = Math.max(2, (hi - lo) * 0.15); lo -= pad; hi += pad;
    const x = (i) => P.l + (i / (pts.length - 1)) * (W - P.l - P.r);
    const y = (v) => P.t + (1 - (v - lo) / (hi - lo)) * (H - P.t - P.b);
    const path = pts.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.y).toFixed(1)}`).join("");
    let grid = "";
    for (let i = 0; i <= 4; i++) {
      const v = lo + ((hi - lo) * i) / 4;
      grid += `<line class="grid-line" x1="${P.l}" x2="${W - P.r}" y1="${y(v)}" y2="${y(v)}"/><text x="${P.l - 6}" y="${y(v) + 3}" text-anchor="end">${v.toFixed(0)}</text>`;
    }
    const labels = [0, Math.floor(pts.length / 2), pts.length - 1].map((i) => `<text x="${x(i)}" y="${H - 6}" text-anchor="middle">${pts[i].x.slice(5)}</text>`).join("");
    const last = pts[pts.length - 1];
    el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(label)}">
      ${grid}<path d="${path}" fill="none" stroke="${color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
      <line class="hover-line" id="hl" x1="0" x2="0" y1="${P.t}" y2="${H - P.b}" style="display:none"/>
      <circle id="hd" r="4" fill="${color}" stroke="var(--surface)" stroke-width="2" style="display:none"/>
      <circle cx="${x(pts.length - 1)}" cy="${y(last.y)}" r="4" fill="${color}" stroke="var(--surface)" stroke-width="2"/>${labels}
      <text x="${x(pts.length - 1) - 8}" y="${y(last.y) - 9}" text-anchor="end" style="fill:var(--text);font-weight:600">${last.y.toFixed(1)} · #${last.r}</text>
      <rect x="${P.l}" y="0" width="${W - P.l - P.r}" height="${H}" fill="transparent" id="hz"/></svg>`;
    const svg = $("svg", el), hz = $("#hz", el), hl = $("#hl", el), hd = $("#hd", el);
    hz.addEventListener("mousemove", (e) => {
      const b = svg.getBoundingClientRect();
      const fx = ((e.clientX - b.left) / b.width) * W;
      const i = Math.max(0, Math.min(pts.length - 1, Math.round(((fx - P.l) / (W - P.l - P.r)) * (pts.length - 1))));
      hl.setAttribute("x1", x(i)); hl.setAttribute("x2", x(i)); hl.style.display = "";
      hd.setAttribute("cx", x(i)); hd.setAttribute("cy", y(pts[i].y)); hd.style.display = "";
      showTip(`<b>${pts[i].x}</b><br>${esc(label)}: ${pts[i].y.toFixed(1)}<br>Rank #${pts[i].r}`, e);
    });
    hz.addEventListener("mouseleave", () => { hl.style.display = hd.style.display = "none"; hideTip(); });
  }

  // legend drawn with the same marks the sliders use
  const sliderKey = (recent) => `<div class="slider-key"><span><i class="k-pt"></i>season</span>${recent ? `<span><i class="k-pt r"></i>last ~8 games</span>` : ""}<span><i class="k-band"></i>uncertainty</span><span><i class="k-sig">★</i>signature trait</span></div>`;
  function slider(f, score, sd, p, strong, score2, cls2 = "b") {
    const lo = Phi(score - sd) * 100, hi = Phi(score + sd) * 100;
    const extra = isNum(score2) ? `<span class="pt ${cls2}" style="left:${(Phi(score2) * 100).toFixed(1)}%"></span>` : "";
    return `<div class="slider${strong ? " strong" : ""}"><div class="lab"><b>${esc(f.name)}</b><span class="pct num">${ord(p * 100)} pct</span></div>
      <div class="track"><span class="mid"></span>${sd ? `<span class="band" style="left:${lo.toFixed(1)}%;width:${Math.max(hi - lo, 1).toFixed(1)}%"></span>` : ""}<span class="pt" style="left:${(Phi(score) * 100).toFixed(1)}%"></span>${extra}</div>
      <div class="poles"><span>${esc(f.low)}</span><span>${esc(f.high)}</span></div></div>`;
  }
  function idCard(card, b, lab) {
    const B = card.blocks[b], a = arch(b, B.primary), a2 = arch(b, B.secondary), p1 = B.members[B.primary], p2 = B.members[B.secondary];
    return `<div class="id-card" style="--c:${b === "offense" ? "var(--hot)" : b === "defense" ? "var(--accent)" : "var(--text-3)"}"><div class="kind">${lab} identity</div><div class="arch">${esc(a.name)}</div>
      <div class="small muted">${Math.round(p1 * 100)}% match${p2 > 0.12 ? ` · also <b>${esc(a2.name)}</b> (${Math.round(p2 * 100)}%)` : ""}</div>
      <div class="memb"><i style="width:${p1 * 100}%"></i><i style="width:${p2 * 100}%"></i></div><p class="desc">${esc(a.desc)}</p></div>`;
  }

  async function viewTeam(slug, params) {
    const T = await teams();
    await style();
    const name = Object.keys(T).find((n) => slugify(n) === slug);
    if (!name) { app.innerHTML = `<div class="card">Team not found. <a href="#/ratings">All teams</a></div>`; return; }
    const t = T[name];
    const seasons = await get("seasons.json", true);
    const idSeason = Number(params.get("season")) || (seasons && seasons.current);
    const [d, ids] = await Promise.all([get(`teams/${slug}.json`), identities(idSeason)]);
    const card = ids && t.espn_id ? ids.byId[String(t.espn_id)] : null;
    const all = Object.values(T);
    const color = t.color && /^[0-9a-f]{6}$/i.test(t.color) && t.color.toLowerCase() !== "ffffff" ? `#${t.color}` : "var(--accent)";
    const injChip = availChip(t);
    const stat = (k, v, r) => `<div class="tile"><span class="k">${k}</span><span class="v num">${v}</span><span class="r">${r}</span></div>`;
    const tiles = [
      stat("Power", sgn(t.power), `#${t.rank} · per 100 vs avg ${LG}`), stat("Adj. offense", fmt(t.eff_o), `#${t.eff_o_rank}`),
      stat("Adj. defense", fmt(t.eff_d), `#${t.eff_d_rank}`), stat("Tempo", fmt(t.adjt), `#${t.adjt_rank}`),
      stat("Dynamic rating", sgn(t.k_em), `#${t.k_em_rank} · ±${fmt(t.k_sd)}`), stat("Roster strength", fmt(t.roster), `#${t.roster_rank}`),
      stat("Win vs avg team", pct(t.win_vs_avg), "neutral floor"), stat(`Win vs top ${TOPN}`, pct(t.win_vs_top25), "neutral floor"),
    ].join("");
    const profile = [
      ["Shooting (eFG%)", "efg_o", false, "efg_d", true], ["Turnovers", "tov_o", true, "tov_d", false],
      ["Off. rebounding", "orb_o", false, "orb_d", true], ["Free-throw rate", "ftr_o", false, "ftr_d", true],
      ["3PA rate", "tpar_o", null, "tpar_d", null], ["3P%", "tp_o", false, "tp_d", true], ["2P%", "twop_o", false, "twop_d", true],
      ["Assist rate", "ast_o", false, "ast_d", true], ["Blocks", "blk_o", true, "blk_d", false], ["Steals", "stl_o", true, "stl_d", false],
    ];
    const bar = (key, inv) => {
      const vals = all.map((x) => x[key]).filter(isNum);
      const p = pctRank(vals, t[key]);
      if (p === null) return `<div></div><div></div>`;
      const shown = inv === null ? p : inv ? 1 - p : p;
      const col = inv === null ? "var(--text-3)" : shown >= 0.5 ? "var(--heat-pos)" : "var(--heat-neg)";
      return `<div class="pct-track" title="${ord(shown * 100)} percentile"><span style="width:${Math.max(3, shown * 100)}%;background:${col}"></span></div><div class="small muted num" style="text-align:right">${fmt(t[key])}</div>`;
    };
    const profileHtml = `<div class="profile-row profile-head"><span></span><span>Offense</span><span></span><span>Defense</span><span></span></div>` +
      profile.map(([lab, ko, io, kd, id]) => `<div class="profile-row"><span class="lab">${lab}</span>${bar(ko, io)}${bar(kd, id)}</div>`).join("");

    const sched = d.schedule.map((g) => {
      const opp = T[g.opp];
      const res = g.played ? `<span class="${g.pts > g.opp_pts ? "pos" : "neg"}"><b>${g.pts > g.opp_pts ? "W" : "L"}</b> ${g.pts}-${g.opp_pts}</span>` : `<span class="muted">${isNum(g.p) ? pct(g.p) : ""}</span>`;
      const pre = g.played && isNum(g.p) ? `<span class="faint small">pre-game ${pct(g.p)}</span>` : isNum(g.margin) && !g.played ? `<span class="faint small">${sgn(g.margin)}</span>` : "";
      return `<tr><td class="l faint">${g.date.slice(5)}</td><td class="l faint">${locWord(g.loc)}</td><td class="l">${opp ? teamLink(g.opp, opp) : esc(g.opp)}</td><td>${res}</td><td class="hide-sm">${pre}</td></tr>`;
    }).join("");
    const roster = d.roster.map((p) => `<tr><td class="l wrap">${playerLink({ name: p.player, pid: p.pid, team: name })}${p.status ? `<div class="small"><span class="pill bad">✚ ${esc(p.status)}</span></div>` : ""}</td>
      <td class="faint">${esc(p.cls || "")}</td><td class="faint hide-sm">${p.inches ? `${Math.floor(p.inches / 12)}'${Math.round(p.inches % 12)}"` : ""}</td>
      <td class="num">${fmt(p.mpg)}</td><td class="num"><b>${sgn(p.value)}</b></td><td class="num hide-sm">${sgn(p.o)}</td><td class="num hide-sm">${sgn(p.d)}</td>
      <td class="num">${isNum(p.avail) ? pct(p.avail) : ""}</td></tr>`).join("");

    let identityHtml = "", styleHtml = "";
    if (card && STYLE) {
      const strong = new Set((card.traits || []).map((x) => x.block + ":" + x.idx));
      const seasonSel = seasons ? `<select id="idseason" aria-label="Identity season">${seasons.seasons.slice().reverse().map((s) => `<option value="${s}" ${s === idSeason ? "selected" : ""}>${seasonLabel(s)}</option>`).join("")}</select>` : "";
      identityHtml = `<div class="section card"><div class="section-head"><h2 style="margin:0">Identity <span class="conf-badge conf-${card.confidence.level}" title="${esc(card.confidence.notes.join(" · "))}">${card.confidence.level} confidence</span></h2>${seasonSel}</div>
        <p class="headline" style="margin-top:0">${esc(card.headline)}</p>
        <div class="identity">${BLOCKS.map(([b, lab]) => idCard(card, b, lab)).join("")}</div></div>
        <div class="section grid g2">
          <div class="card"><h2>Strengths</h2><ul class="clean plus">${card.strengths.map((s) => `<li>${esc(s)}</li>`).join("") || "<li class='empty muted'>Nothing stands out yet.</li>"}</ul></div>
          <div class="card"><h2>Vulnerabilities</h2><ul class="clean minus">${card.weaknesses.map((s) => `<li>${esc(s)}</li>`).join("") || "<li class='empty muted'>No clear weak spots yet.</li>"}</ul></div></div>
        <div class="section grid g2">
          <div class="card"><h2>Matchup notes</h2><h3 style="margin-top:6px">Likely to like facing</h3><ul class="clean arrow">${card.matchups.favorable.map((s) => `<li>${esc(s)}</li>`).join("") || "<li class='empty muted'>—</li>"}</ul>
            <h3 style="margin-top:14px">Watch out for</h3><ul class="clean arrow">${card.matchups.risky.map((s) => `<li>${esc(s)}</li>`).join("") || "<li class='empty muted'>—</li>"}</ul>
            <p class="note">Descriptive notes from the style profile. The Matchup Lab shows what the game model actually prices in.</p></div>
          <div class="card"><h2>Recent changes &amp; context</h2><ul class="clean warn">${card.changes.map((s) => `<li>${esc(s)}</li>`).join("") || "<li class='empty muted'>No significant style shift in recent games.</li>"}</ul>
            <ul class="clean arrow" style="margin-top:10px">${card.context.map((s) => `<li>${esc(s)}</li>`).join("")}</ul></div></div>`;
      styleHtml = `<div class="section grid g3">${BLOCKS.map(([b, lab]) => `<div class="card"><h2>${lab} dimensions</h2><p class="note" style="margin-top:-4px">Percentile vs all team-seasons.</p>${sliderKey(card.recent)}<div class="sliders">${STYLE.blocks[b].factors.map((f, i) => slider(f, card.blocks[b].scores[i], card.blocks[b].sd[i], card.blocks[b].pct[i], strong.has(b + ":" + i), card.recent ? card.recent[b][i] : null, "r")).join("")}</div></div>`).join("")}</div>
        <div class="section card"><h2>Supporting evidence</h2><p class="note" style="margin-top:-4px">Opponent- and venue-adjusted play-by-play stats behind the identity, vs the ${LG} average that season.</p>
          <div class="tbl-wrap"><table class="evidence"><thead><tr><th class="l nosort">Stat</th><th class="nosort">${esc(t.abbr || name)}</th><th class="nosort">${LG} avg</th><th class="l nosort">Percentile</th></tr></thead><tbody>${(card.evidence || []).map((e) => `<tr><td class="l">${esc(e.label)}</td><td class="num">${esc(e.value)}</td><td class="num faint">${esc(e.avg)}</td><td class="l"><span class="minibar"><i style="width:${e.pct}%"></i></span> <span class="small faint num">${ord(e.pct)}</span></td></tr>`).join("")}</tbody></table></div></div>`;
    } else if (ids) {
      identityHtml = `<div class="section card"><h2>Identity</h2><p class="muted">No style profile for this season (needs enough play-by-play games).</p></div>`;
    }

    app.innerHTML = `
      <div class="card" style="background:linear-gradient(120deg, color-mix(in srgb, ${color} 22%, var(--surface)), var(--surface) 55%)">
        <div class="team-hero">${logo(t, "lg")}<div class="meta"><h1>${esc(name)}</h1>
          <div class="badges"><span class="pill acc">#${t.rank} Power</span><span class="pill">${esc(t.conf || "")}</span><span class="pill">${t.wins}-${t.losses}</span>${injChip}
          ${card ? BLOCKS.map(([b, , ]) => `<span class="tag ${b === "offense" ? "o" : b === "defense" ? "d" : ""}">${esc(arch(b, card.blocks[b].primary).name)}</span>`).join("") : ""}</div></div>
          <a href="#/matchup?a=${encodeURIComponent(name)}">Open in Matchup Lab →</a></div>
      </div>
      <div class="section tiles">${tiles}</div>
      ${identityHtml}
      <div class="section grid g2">
        <div class="card"><h2>Rating through the season</h2><p class="note" style="margin-top:-4px">Adjusted efficiency margin (AdjEM) before each game day.</p><div class="chart" id="hist"></div></div>
        <div class="card"><h2>Four factors &amp; style, percentile</h2>${profileHtml}<p class="note">Bars are percentiles among ${LG} teams (longer = better; gray = no better/worse direction).</p></div>
      </div>
      ${styleHtml}
      <div class="section grid g2 top">
        <div class="card flush"><div style="padding:16px 20px 6px"><h2>Roster &amp; availability <a class="small" style="font-weight:500" href="#/players?team=${encodeURIComponent(name)}">all stats →</a></h2><p class="note" style="margin-top:-4px">Value = points per 100 possessions above an average ${LG} player (box score + prior). Avail = chance ${he} plays next game (${IS_W ? "recent missed games, overrides" : "injury reports, recent missed games"}).</p></div>
          <div class="tbl-wrap"><table><thead><tr><th class="l nosort">Player</th><th class="nosort">Yr</th><th class="nosort hide-sm">Ht</th><th class="nosort">MPG</th><th class="nosort">Value</th><th class="nosort hide-sm">Off</th><th class="nosort hide-sm">Def</th><th class="nosort">Avail</th></tr></thead><tbody>${roster}</tbody></table></div></div>
        <div class="card flush"><div style="padding:16px 20px 6px"><h2>Schedule</h2></div>
          <div class="tbl-wrap"><table><tbody>${sched}</tbody></table></div></div>
      </div>`;
    const pts = (d.history || []).filter((h) => isNum(h.adjem)).map((h) => ({ x: h.date, y: h.adjem, r: h.rank }));
    lineChart($("#hist"), pts, color === "var(--accent)" ? "var(--accent)" : color, "AdjEM");
    const sel = $("#idseason");
    if (sel) sel.addEventListener("change", () => { location.hash = `#/team/${slug}?season=${sel.value}`; });
  }

  // ================================================================ BRACKET
  let bracketMode = "pool";
  async function viewBracket() {
    const b = await get("bracket.json");
    const T = await teams();
    const tree = b[bracketMode];
    const regionsByName = {};
    (function collect(n) { if (!n) return; if (n.round === 4 && n.region) regionsByName[n.region] = n; (n.children || []).forEach(collect); })(tree);
    const ff = tree.children;
    const champ = tree.pick ? T[tree.pick] : null;
    const adv = b.advancement;
    const opening = [];
    (function findOpen(n) { if (!n) return; if (n.round === 0 && n.children) opening.push(n); (n.children || []).forEach(findOpen); })(tree);
    const ep = b.expected_points || {};
    app.innerHTML = `
      <div class="page-head"><div><h1>${b.projected ? "Projected " : ""}${b.season} NCAA Tournament</h1>
        <p class="sub">${b.field_size}-team field. Every game is priced by the final game model with each team's travel to the actual venue, and advancement odds are exact: win probabilities propagated through the bracket, no simulation noise.</p></div>
        ${seg("mode", [["pool", "Pool-optimal"], ["likely", "Most likely"]], bracketMode)}</div>
      ${b.projected ? `<div class="notice">${esc(b.note)} The official bracket replaces this automatically after Selection Sunday.</div>` : ""}
      <p class="note" style="margin-top:-6px">Pool-optimal picks the likeliest champion, then the likeliest team to reach each game given those picks${isNum(ep.pool) ? ` (expected ${fmt(ep.pool, 1)} points in a 1-2-4-8-16-32 pool` : ""}${isNum(ep.likely) ? `; most likely: ${fmt(ep.likely, 1)})` : isNum(ep.pool) ? ")" : ""}. Most likely takes the favorite in every game.</p>
      <div class="grid g3" style="margin:14px 0 22px">
        <div class="card champ-card"><h3>Champion pick</h3>${champ ? logo(champ, "lg") : ""}<div style="font-size:22px;font-weight:800;margin-top:6px">${esc(tree.pick || "—")}</div>
          <div class="muted">${tree.pick ? pct((adv.find((a) => a.team === tree.pick) || {}).champ, 1) + " to win it all" : ""}</div></div>
        <div class="card" style="grid-column: span 2"><h3>Title odds</h3>${adv.slice(0, 8).map((a) => `
          <div class="odds-row">${teamLink(a.team, T[a.team])}<div class="pbar"><span style="width:${(100 * a.champ / adv[0].champ).toFixed(1)}%"></span></div><div class="num" style="text-align:right;font-weight:600">${pct(a.champ, 1)}</div></div>`).join("")}</div>
      </div>
      <h2>Final Four · ${esc((ff[0] && ff[0].site) || "")}</h2>
      <div class="ff" style="margin-bottom:24px">${matchCard(ff[0], T, "National semifinal")}${matchCard(tree, T, "Championship")}${matchCard(ff[1], T, "National semifinal")}</div>
      ${opening.length ? `<h2>Opening Round</h2><div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:10px;margin-bottom:24px">${opening.map((n) => matchCard(n, T)).join("")}</div>` : ""}
      ${b.regions.map((r) => regionHtml(regionsByName[r], r, T)).join("")}
      <div class="section-head" style="margin-top:8px"><h2 style="margin:0">Advancement odds</h2><input type="search" id="bq" placeholder="Filter…" aria-label="Filter teams"></div>
      <div class="card flush tbl-wrap"><table id="advt"></table></div>`;
    const drawAdv = () => {
      const q = ($("#bq").value || "").toLowerCase();
      const rows = adv.filter((a) => !q || a.team.toLowerCase().includes(q));
      const cell = (a, k) => `<td><span class="heat num" style="background:color-mix(in srgb, var(--accent) ${Math.round(Math.min(1, a[k] * (k === "champ" ? 4 : k === "final" ? 3 : k === "f4" ? 2 : 1)) * 30)}%, transparent)">${a[k] < 0.001 ? "<span class='faint'>&lt;0.1%</span>" : pct(a[k], 1)}</span></td>`;
      $("#advt").innerHTML = `<thead><tr><th class="l nosort">Team</th><th class="nosort">Seed</th><th class="l hide-sm nosort">Region</th>${["R32", "S16", "E8", "F4", "Final", "Champ"].map((h) => `<th class="nosort">${h}</th>`).join("")}</tr></thead>
        <tbody>${rows.map((a) => `<tr><td class="l">${teamLink(a.team, T[a.team])}${a.main < 0.999 ? ` <span class="pill">opening rd ${pct(a.main)}</span>` : ""}</td><td class="faint">${a.seed ?? ""}</td><td class="l faint hide-sm">${esc(a.region || "")}</td>${["r32", "s16", "e8", "f4", "final", "champ"].map((k) => cell(a, k)).join("")}</tr>`).join("")}</tbody>`;
    };
    $("#bq").addEventListener("input", drawAdv);
    drawAdv();
    onSeg("mode", (v) => { bracketMode = v; viewBracket(); });
  }
  function matchCard(n, T, title = "") {
    if (!n) return "";
    const ents = (n.children || []).map((c) => c.pick || c.team);
    const top = Object.fromEntries(n.top || []);
    const seedOf = (c) => { let s = null; (function f(x) { if (!x || s) return; if (x.team === c) s = x.seed; (x.children || []).forEach(f); })(n); return s; };
    const row = (t) => t ? `<div class="row ${n.pick === t ? "w" : ""}"><span class="seed">${seedOf(t) ?? ""}</span>${logo(T[t])}<span class="ellip">${esc(t)}</span><span class="p">${top[t] != null ? pct(top[t]) : ""}</span></div>` : `<div class="row"><span></span><span></span><span class="faint">TBD</span><span></span></div>`;
    return `<div class="mu">${title ? `<div class="site" style="border-top:0;border-bottom:1px solid var(--border)">${title}</div>` : ""}${row(ents[0])}${row(ents[1])}${n.site ? `<div class="site">${esc(n.site)}${n.pick_prob ? ` · pick ${pct(n.pick_prob)}` : ""}</div>` : ""}</div>`;
  }
  function regionHtml(rootNode, name, T) {
    if (!rootNode) return "";
    const ordered = { 1: [], 2: [], 3: [], 4: [] };
    (function walk(n) { if (!n || !n.children) return; walk(n.children[0]); if (n.round >= 1) ordered[n.round].push(n); walk(n.children[1]); })(rootNode);
    const winner = rootNode.pick;
    return `<div class="region"><div class="region-title"><h2 style="margin:0">${esc(name)}</h2><span class="faint small">${esc((ordered[3][0] && ordered[3][0].site) || "")}</span></div>
      <div class="bracket-scroll"><div class="rounds rounds-head">${[1, 2, 3, 4].map((r) => `<h4>${ROUND_NAMES[r]}</h4>`).join("")}<h4>Regional champion</h4></div>
      <div class="rounds">${[1, 2, 3, 4].map((r) => `<div class="round-col">${ordered[r].map((n) => matchCard(n, T)).join("")}</div>`).join("")}
        <div class="round-col"><div class="mu"><div class="row w"><span></span>${logo(T[winner])}<span class="ellip">${esc(winner || "")}</span><span></span></div></div></div></div></div></div>`;
  }

  // ================================================================ GAME MODEL (in the browser)
  let MODEL = null, VEC = null;
  const gelu = (x) => 0.5 * x * (1 + Math.tanh(0.7978845608028654 * (x + 0.044715 * x * x * x)));
  const softplus = (x) => (x > 20 ? x : Math.log1p(Math.exp(x)));
  function dense(W, b, x, act) {
    const out = new Array(W.length);
    for (let i = 0; i < W.length; i++) { let s = b[i]; const w = W[i]; for (let j = 0; j < x.length; j++) s += w[j] * x[j]; out[i] = act ? act(s) : s; }
    return out;
  }
  function netForward(net, xa, xb, ctx) {
    const enc = (x) => dense(net.enc1[0], net.enc1[1], dense(net.enc0[0], net.enc0[1], x, gelu), gelu);
    const ea = enc(xa), eb = enc(xb);
    const prod = ea.map((v, i) => v * eb[i]);
    const ctxS = ctx.slice(); ctxS[0] = -ctxS[0];
    const pair = (u, v, c) => dense(net.pair1[0], net.pair1[1], dense(net.pair0[0], net.pair0[1], [...u, ...v, ...prod, ...c], gelu))[0];
    let mu = pair(ea, eb, ctx) - pair(eb, ea, ctxS) + net.hca * ctx[0];
    for (let j = 0; j < xa.length; j++) mu += net.skip[j] * (xa[j] - xb[j]);
    const sym = [...ea.map((v, i) => v + eb[i]), ...prod, ...ctx.slice(1)];
    const s = dense(net.sig1[0], net.sig1[1], dense(net.sig0[0], net.sig0[1], sym, gelu))[0];
    return [mu, 6 + softplus(s + 4)];
  }
  function km(a, b) {
    if (!a || !b || a[0] == null || b[0] == null) return null;
    const R = 6371, r = Math.PI / 180, dl = (b[0] - a[0]) * r, dn = (b[1] - a[1]) * r;
    const h = Math.sin(dl / 2) ** 2 + Math.cos(a[0] * r) * Math.cos(b[0] * r) * Math.sin(dn / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(h));
  }
  // opts: {loc: 1 (A home) | -1 (B home) | 0, venue: {lat, lon} | null, type: "reg" | "conf" | "ncaa"}
  function predict(A, B, opts) {
    const F = MODEL.features, iRest = F.indexOf("rest"), iDist = F.indexOf("dist");
    const z = (i, v) => Math.max(-5, Math.min(5, (v - MODEL.norm.mean[i]) / MODEL.norm.std[i]));
    const dist = (name) => {
      if (opts.loc !== 0) { const home = opts.loc > 0 ? A : B; return name === home ? 0 : (km(VEC.loc[home], VEC.loc[name]) ?? 800); }
      return opts.venue ? (km([opts.venue.lat, opts.venue.lon], VEC.loc[name]) ?? 800) : 800;
    };
    const build = (name) => { const x = VEC.teams[name].slice(); x[iRest] = z(iRest, 4); x[iDist] = z(iDist, Math.log1p(dist(name))); return x; };
    const xa = build(A), xb = build(B);
    const same = VEC.conf[A] && VEC.conf[A] === VEC.conf[B] ? 1 : 0;
    const ncaa = opts.type === "ncaa" ? 1 : 0;
    const ctx = [opts.loc, opts.loc === 0 ? 1 : 0, ncaa, opts.type === "conf" ? 1 : 0, ncaa, same, (ncaa ? (VEC.post_day ?? 138) : VEC.day) / 100];
    const mus = [], sg2 = [];
    for (const net of MODEL.nets) { const [m, s] = netForward(net, xa, xb, ctx); mus.push(m); sg2.push(s * s); }
    const muN = mus.reduce((a, b) => a + b, 0) / mus.length;
    const sgN = Math.sqrt(sg2.reduce((a, b) => a + b, 0) / sg2.length + mus.reduce((a, b) => a + (b - muN) ** 2, 0) / mus.length);
    // linear game model (dynamic + snapshot ratings, site, travel, availability)
    const ra = VEC.raw[A], rb = VEC.raw[B], L = MODEL.linear;
    const [pLo, pHi] = MODEL.poss_range || [55, 85];
    const poss = Math.max(pLo, Math.min(pHi, MODEL.mu_t + ra.k_t + rb.k_t));
    const x = {
      k_margin: ((ra.k_o - ra.k_d) - (rb.k_o - rb.k_d)) * poss / 100, r_margin: ((ra.adjem ?? 0) - (rb.adjem ?? 0)) * poss / 100,
      loc: opts.loc, travel: Math.log1p(dist(B)) - Math.log1p(dist(A)), rest: 0, avail: ((ra.avail ?? 0) - (rb.avail ?? 0)) * poss / 100,
    };
    const parts = { ratings: 0, site: 0, injuries: 0 };
    let muL = L.icpt || 0;
    L.cols.forEach((c, i) => {
      const v = L.coef[i] * (x[c] ?? 0); muL += v;
      if (c === "k_margin" || c === "r_margin") parts.ratings += v; else if (c === "avail") parts.injuries += v; else parts.site += v;
    });
    const sgL = L.s0 * Math.sqrt(poss / 68);
    const w = MODEL.w_net;
    const mu = w * muN + (1 - w) * muL;
    const sigma = Math.sqrt(w * sgN ** 2 + (1 - w) * sgL ** 2 + w * (1 - w) * (muN - muL) ** 2);
    // projected score from adjusted efficiencies and tempo
    const ta = TEAMS[A], tb = TEAMS[B];
    const lg = RATINGS.league_eff || 105;
    const tempo = ((ta.adjt || 68) + (tb.adjt || 68)) / 2;
    const total = ((ta.eff_o + tb.eff_d - lg) + (tb.eff_o + ta.eff_d - lg)) * tempo / 100;
    return { mu, sigma, p: Phi(mu / sigma), muN, muL, parts, poss, ptsA: total / 2 + mu / 2, ptsB: total / 2 - mu / 2 };
  }

  // ================================================================ MATCHUP LAB
  function combo(el, names, value, onPick) {
    el.innerHTML = `<div class="combo"><input type="text" placeholder="Type a team…" value="${esc(value)}" aria-label="Team"><div class="list" role="listbox"></div></div>`;
    const inp = $("input", el), list = $(".list", el);
    let act = -1;
    const render = () => {
      const q = inp.value.trim().toLowerCase();
      const ts = names.filter((n) => !q || n.toLowerCase().includes(q) || (TEAMS[n].location || "").toLowerCase().includes(q)).slice(0, 40);
      list.innerHTML = ts.map((n, i) => `<div class="opt ${i === act ? "act" : ""}" data-n="${esc(n)}" role="option">${logo(TEAMS[n])}${esc(n)} <span class="faint small">#${TEAMS[n].rank}</span></div>`).join("");
      list.classList.add("open");
    };
    inp.addEventListener("focus", () => { inp.select(); act = -1; render(); });
    inp.addEventListener("input", () => { act = 0; render(); });
    inp.addEventListener("keydown", (e) => {
      const opts = $$(".opt", list);
      if (e.key === "ArrowDown") { act = Math.min(opts.length - 1, act + 1); render(); e.preventDefault(); }
      else if (e.key === "ArrowUp") { act = Math.max(0, act - 1); render(); e.preventDefault(); }
      else if (e.key === "Enter" && opts[Math.max(0, act)]) { onPick(opts[Math.max(0, act)].dataset.n); list.classList.remove("open"); inp.blur(); }
    });
    inp.addEventListener("blur", () => setTimeout(() => list.classList.remove("open"), 150));
    list.addEventListener("mousedown", (e) => { const o = e.target.closest(".opt"); if (o) { onPick(o.dataset.n); list.classList.remove("open"); } });
  }

  async function viewMatchup(_, params) {
    [MODEL, VEC] = await Promise.all([get("model.json"), get("vectors.json")]);
    const T = await teams();
    await style();
    const ids = await identities();
    const names = Object.keys(VEC.teams).filter((n) => T[n]).sort((a, b) => T[a].rank - T[b].rank);
    const st = { a: params.get("a") && T[params.get("a")] ? params.get("a") : names[0], b: params.get("b") && T[params.get("b")] ? params.get("b") : names[1],
      site: params.get("site") || (IS_PRO ? "a" : "neutral"), type: params.get("type") || (IS_PRO ? "reg" : "ncaa") };
    if (st.a === st.b) st.b = names.find((n) => n !== st.a);
    app.innerHTML = `
      <div class="page-head"><div><h1>Matchup Lab</h1><p class="sub">Any two teams, any site. The full game model runs in your browser: the matchup network, the dynamic-rating linear model, travel to the venue, injuries and style.</p></div>${art("neural")}</div>
      <div class="card">
        <div class="lab-pick"><div><label>Team A</label><div id="ca"></div></div><div class="vs-mid">vs</div><div><label>Team B</label><div id="cb"></div></div></div>
        <div class="controls" style="margin:12px 0 0">
          <select id="site" aria-label="Site"><option value="neutral">Neutral site (no travel)</option><option value="a">Team A home</option><option value="b">Team B home</option>
            ${VEC.venues.map((v, i) => `<option value="v${i}">${esc(v.group)}: ${esc(v.city)}</option>`).join("")}</select>
          <select id="gtype" aria-label="Game type">${IS_PRO ? `<option value="reg">Regular season</option><option value="ncaa">Playoffs</option>${LEAGUE === "nba" ? `<option value="conf">Play-in</option>` : ""}`
              : `<option value="ncaa">NCAA tournament</option><option value="reg">Regular season</option><option value="conf">Conference tournament</option>`}</select>
          <button class="icon-btn" id="swap" title="Swap teams" style="width:auto;padding:0 12px">⇄ swap</button>
        </div>
      </div>
      <div id="mres" class="section"></div>`;
    $("#site").value = st.site; $("#gtype").value = st.type;
    const go = () => {
      st.site = $("#site").value; st.type = $("#gtype").value;
      history.replaceState(null, "", `#/matchup?a=${encodeURIComponent(st.a)}&b=${encodeURIComponent(st.b)}&site=${st.site}&type=${st.type}`);
      renderMatchup(st, T, ids);
    };
    combo($("#ca"), names, st.a, (n) => { if (n !== st.b) { st.a = n; go(); } });
    combo($("#cb"), names, st.b, (n) => { if (n !== st.a) { st.b = n; go(); } });
    $("#site").addEventListener("change", go); $("#gtype").addEventListener("change", go);
    $("#swap").addEventListener("click", () => { [st.a, st.b] = [st.b, st.a]; const s = $("#site").value; if (s === "a" || s === "b") $("#site").value = s === "a" ? "b" : "a"; viewMatchupKeep(st); });
    go();
  }
  function viewMatchupKeep(st) { location.hash = `#/matchup?a=${encodeURIComponent(st.a)}&b=${encodeURIComponent(st.b)}&site=${$("#site").value}&type=${$("#gtype").value}`; }

  function renderMatchup(st, T, ids) {
    const A = st.a, B = st.b, ta = T[A], tb = T[B];
    const opts = { loc: st.site === "a" ? 1 : st.site === "b" ? -1 : 0, type: st.type, venue: st.site.startsWith("v") ? VEC.venues[+st.site.slice(1)] : null };
    const r = predict(A, B, opts);
    const neutral = predict(A, B, { loc: 0, type: st.type, venue: null });
    const reg = predict(A, B, { loc: 0, type: "reg", venue: null });
    const gap = ((ta.power ?? 0) - (tb.power ?? 0)) * r.poss / 100;
    const edge = reg.mu - gap;
    const who = (v) => (Math.abs(v) < 0.05 ? "even" : `favors ${esc(v > 0 ? A : B)}`);
    const all = Object.values(T);
    const P = (k, v) => pctRank(all.map((t) => t[k]).filter(isNum), v);
    const duels = [["Shooting", "efg_o", 1, "efg_d", 1], ["Ball security", "tov_o", -1, "tov_d", -1], ["Off. rebounding", "orb_o", 1, "orb_d", 1],
      ["Getting to the line", "ftr_o", 1, "ftr_d", 1], ["3-pt shooting", "tp_o", 1, "tp_d", 1], ["Inside scoring", "twop_o", 1, "twop_d", 1]];
    const duel = (off, def) => duels.map(([lab, ko, so, kd, sd]) => {
      const po = so > 0 ? P(ko, off[ko]) : 1 - P(ko, off[ko]);
      const pd = sd > 0 ? 1 - P(kd, def[kd]) : P(kd, def[kd]);
      return { lab, po, pd, adv: po - pd };
    }).sort((x, y) => Math.abs(y.adv) - Math.abs(x.adv)).slice(0, 4);
    const duelHtml = (off, def, on, dn) => duel(off, def).map((d) => `<div class="duel"><div><b>${esc(on)}</b> ${esc(d.lab.toLowerCase())} <span class="faint">${ord(d.po * 100)}</span></div>
      <div class="cmp" title="offense percentile vs defense percentile"><span style="width:${d.po * 50}%;background:var(--accent)"></span><span style="width:${(1 - d.po) * 50 + (1 - d.pd) * 50}%"></span><span style="width:${d.pd * 50}%;background:var(--hot)"></span></div>
      <div class="r"><b>${esc(dn)}</b> D <span class="faint">${ord(d.pd * 100)}</span> ${d.adv > 0.25 ? `<span class="pill good">O edge</span>` : d.adv < -0.25 ? `<span class="pill bad">D edge</span>` : ""}</div></div>`).join("");
    // decomposition of the blended margin: (1-w) x linear parts + w x network margin
    const w = MODEL.w_net;
    const comps = [["Team strength (ratings)", r.parts.ratings], ["Site, travel & rest", r.parts.site],
      ["Injuries & availability", r.parts.injuries], ["Matchup network adjustment", w * (r.muN - r.muL)]];
    const mx = Math.max(4, ...comps.map((c) => Math.abs(c[1])), Math.abs(r.mu));
    const bdRow = (lab, v, bold) => `<div class="bd-row"><span class="${bold ? "" : "muted"}">${bold ? `<b>${esc(lab)}</b>` : esc(lab)}</span><div class="bd-bar"><span class="zero"></span><i style="${v >= 0 ? `left:50%;width:${(Math.abs(v) / mx) * 50}%` : `right:50%;width:${(Math.abs(v) / mx) * 50}%`};background:${v >= 0 ? "var(--accent)" : "var(--hot)"}"></i></div><span class="num" style="text-align:right">${bold ? `<b>${sgn(v)}</b>` : sgn(v)}</span></div>`;
    const inj = availChip;
    const ca = ids && ta.espn_id ? ids.byId[String(ta.espn_id)] : null, cb = ids && tb.espn_id ? ids.byId[String(tb.espn_id)] : null;
    let styleCmp = "";
    if (STYLE && ca && cb) {
      styleCmp = `<div class="section grid g3">${BLOCKS.map(([bk, lab]) => `<div class="card"><h2>${lab}</h2>
        <p class="note" style="margin-top:-4px"><span style="color:var(--accent)">●</span> ${esc(A)}: <b>${esc(arch(bk, ca.blocks[bk].primary).name)}</b><br><span style="color:var(--hot)">●</span> ${esc(B)}: <b>${esc(arch(bk, cb.blocks[bk].primary).name)}</b></p>
        <div class="sliders">${STYLE.blocks[bk].factors.map((f, i) => slider(f, ca.blocks[bk].scores[i], 0, ca.blocks[bk].pct[i], false, cb.blocks[bk].scores[i], "b")).join("")}</div></div>`).join("")}</div>
        <p class="note">Style identities describe how teams play, with strength removed. Matchup effects are learned by the network from game results; in a decade of data they are real but small (see Method).</p>`;
    }
    $("#mres").innerHTML = `
      <div class="card">
        <div class="versus"><a href="#/team/${slugify(A)}" class="side teamcell">${logo(ta, "md")}<div><h2>${esc(A)}</h2><div class="small muted">#${ta.rank} · ${esc(ta.conf || "")} · ${inj(ta)}</div></div></a>
          <div class="score"><b class="num">${Math.round(r.ptsA)}</b><span class="faint small">projected</span><b class="num">${Math.round(r.ptsB)}</b></div>
          <a href="#/team/${slugify(B)}" class="side right teamcell"><div><h2>${esc(B)}</h2><div class="small muted">#${tb.rank} · ${esc(tb.conf || "")} · ${inj(tb)}</div></div>${logo(tb, "md")}</a></div>
        <div class="split" style="margin-top:14px" role="img" aria-label="Win probability ${esc(A)} ${pct(r.p, 1)}, ${esc(B)} ${pct(1 - r.p, 1)}"><div style="width:${(r.p * 100).toFixed(1)}%">${pct(r.p, 1)}</div><div style="width:${(100 - r.p * 100).toFixed(1)}%">${pct(1 - r.p, 1)}</div></div>
        <div class="tiles" style="margin-top:14px">
          <div class="tile"><span class="k">Predicted margin</span><span class="v num">${sgn(r.mu)}</span><span class="r">${esc(A)} view · ±${fmt(r.sigma)} (1σ)</span></div>
          <div class="tile"><span class="k">Power gap</span><span class="v num">${sgn(gap)}</span><span class="r">at ~${Math.round(r.poss)} possessions</span></div>
          <div class="tile"><span class="k">Matchup edge</span><span class="v num">${sgn(edge)}</span><span class="r">${Math.abs(edge) < 0.3 ? "styles neutral" : who(edge)}</span></div>
          <div class="tile"><span class="k">Site effect</span><span class="v num">${sgn(r.mu - neutral.mu)}</span><span class="r">home court / travel</span></div>
          <div class="tile"><span class="k">Network vs linear</span><span class="v num">${sgn(r.muN - r.muL)}</span><span class="r">model disagreement</span></div>
        </div>
      </div>
      <div class="section grid g2">
        <div class="card"><h2>How the forecast is built</h2><p class="note" style="margin-top:-4px">The linear game model's terms, plus the matchup network's adjustment (its ${Math.round(w * 100)}% share of the gap between the two models). Positive = helps ${esc(A)}.</p>
          <div class="breakdown">${comps.map(([l, v]) => bdRow(l, v)).join("")}${bdRow("Predicted margin", r.mu, true)}</div>
          <p class="note">Matchup edge = the neutral-site regular-season margin minus the gap in Power ratings: what these two teams' specific interaction adds beyond overall strength.</p></div>
        <div class="card"><h2>${esc(A)} offense vs ${esc(B)} defense</h2>${duelHtml(ta, tb, A, B)}<h2 style="margin-top:16px">${esc(B)} offense vs ${esc(A)} defense</h2>${duelHtml(tb, ta, B, A)}</div>
      </div>
      ${IS_PRO ? seriesHtml(A, B, st.type) : ""}
      ${styleCmp}`;
  }

  // best-of-N series from the two home/away game probabilities and the league's home pattern
  function seriesProb(ph, pa, n, sites, wa = 0, wb = 0) {
    const need = Math.floor(n / 2) + 1, memo = {};
    const f = (a, b) => {
      if (a >= need) return 1;
      if (b >= need) return 0;
      const k = a + "," + b;
      if (!(k in memo)) { const p = sites[a + b] ? ph : pa; memo[k] = p * f(a + 1, b) + (1 - p) * f(a, b + 1); }
      return memo[k];
    };
    return f(wa, wb);
  }
  function seriesHtml(A, B, type) {
    const fmtc = (VEC.playoff_format || {}), lens = [...new Set(fmtc.rounds || [7])].sort((x, y) => x - y);
    const pat = (n) => ((fmtc.patterns || {})[n] || [1, 1, 0, 0, 1, 0, 1].slice(0, n));
    const t = type === "reg" ? "ncaa" : type;
    const aHome = predict(A, B, { loc: 1, type: t, venue: null }).p, aAway = predict(A, B, { loc: -1, type: t, venue: null }).p;
    const rows = lens.map((n) => {
      const pA = seriesProb(aHome, aAway, n, pat(n)), pB = 1 - seriesProb(1 - aAway, 1 - aHome, n, pat(n));
      return `<tr><td class="l">Best of ${n}</td><td class="num"><b>${pct(pA, 1)}</b></td><td class="num">${pct(pB, 1)}</td></tr>`;
    }).join("");
    return `<div class="section card"><h2>Playoff series</h2><p class="note" style="margin-top:-4px">P(${esc(A)} wins the series), priced game by game at each arena with playoff context. Game ${esc(A)} hosts: ${pct(aHome, 1)} · game ${esc(B)} hosts: ${pct(aAway, 1)} for ${esc(A)}.</p>
      <div class="tbl-wrap"><table><thead><tr><th class="l nosort">Series</th><th class="nosort">${esc(A)} has home court</th><th class="nosort">${esc(B)} has home court</th></tr></thead><tbody>${rows}</tbody></table></div>
      <p class="note">Home pattern ${lens.map((n) => `best-of-${n}: ${pat(n).map((x) => (x ? "H" : "A")).join("")}`).join(" · ")} (H = the team with home court hosts).</p></div>`;
  }

  // ================================================================ GAMES
  async function viewGames() {
    const g = await get("games.json");
    const T = await teams();
    // finals from today stay on the board (with the score) until the day rolls over; they also go to the results list
    const today = boardDay();
    const gkey = (x) => `${x.date}|${x.team_a}|${x.team_b}`;
    const finals = g.recent.filter((x) => gameDay(x) === today).map((x) => ({ ...x, final: true }));
    const done = new Set(finals.map(gkey));
    const byDate = {};
    [...g.upcoming.filter((x) => !done.has(gkey(x))), ...finals].forEach((x) => (byDate[gameDay(x)] = byDate[gameDay(x)] || []).push(x));
    const acc = g.season_accuracy;
    // Featured: both teams in the league's top tier (top 25 in college, the top third in the pros).
    const nTeams = Object.keys(T).length;
    const topN = nTeams > 100 ? 25 : Math.round(nTeams / 3);
    const rk = (n) => (T[n] || {}).rank || 999;
    const featured = (x) => rk(x.team_a) <= topN && rk(x.team_b) <= topN;
    const tip = (x) => { const t = x.start ? Date.parse(x.start) : NaN; return isNaN(t) ? Infinity : t; };  // earliest first; TBD last
    const card = (x) => {
      const ta = T[x.team_a], tb = T[x.team_b];
      const feat = featured(x);
      const favA = x.p_a >= 0.5;
      const fin = !!x.final, wonA = fin && x.pts_a > x.pts_b, ok = fin && favA === wonA;
      const edgeT = x.edge > 0 ? x.team_a : x.team_b;
      const site = x.loc === 0 ? `neutral${x.city ? " · " + esc(x.city) : ""}` : `at ${esc(x.loc > 0 ? x.team_a : x.team_b)}`;
      const when = timeFmt(x.start);
      const row = (t, team, isA) => {
        const won = fin && (isA ? wonA : !wonA);
        const cls = fin ? (won ? "won" : "lost") : (isA === favA ? "fav" : "");
        return `<div class="g-team ${cls}">${logo(team)}<a href="#/team/${slugify(t)}">${esc(t)}</a><span class="faint small">${won ? `<span class="win-check" title="Winner">✓</span> ` : ""}#${team ? team.rank : "–"}</span><b class="num">${fin ? (isA ? x.pts_a : x.pts_b) : fmt(isA ? x.pts_a : x.pts_b, 0)}</b></div>`;
      };
      const pill = fin ? `<span class="pill final-pill">Final</span>` : feat ? `<span class="pill acc" title="Both teams are in the top ${topN} by Power">Top ${topN} matchup</span>` : "";
      const foot = fin
        ? `<span>Pre-game: <b>${esc(favA ? x.team_a : x.team_b)}</b> ${pct(Math.max(x.p_a, 1 - x.p_a))} · <span class="${ok ? "pos" : "neg"}">${ok ? "✓ called it" : "✗ missed"}</span></span>`
        : `<span><b>${esc(favA ? x.team_a : x.team_b)}</b> ${pct(Math.max(x.p_a, 1 - x.p_a))} · by ${fmt(Math.abs(x.margin))}</span>
          ${isNum(x.edge) && Math.abs(x.edge) >= 0.75 ? `<span class="pill edge" title="How much this specific matchup moves the game vs the Power gap">edge ${esc(edgeT)} ${fmt(Math.abs(x.edge))}</span>` : ""}`;
      return `<div class="game${feat ? " featured" : ""}${fin ? " final" : ""}"><div class="g-meta small faint"><span class="ellip">${when ? `<span class="g-time">${esc(when)}</span> · ` : ""}${site}</span>${pill}</div>
        ${row(x.team_a, ta, true)}${row(x.team_b, tb, false)}
        <div class="prob" role="img" aria-label="${fin ? "Pre-game: " : ""}${esc(x.team_a)} ${pct(x.p_a)}"><i style="width:${x.p_a * 100}%"></i><i style="width:${(1 - x.p_a) * 100}%"></i></div>
        <div class="g-foot small">${foot}
          <a href="#/matchup?a=${encodeURIComponent(x.team_a)}&b=${encodeURIComponent(x.team_b)}&site=${x.loc > 0 ? "a" : x.loc < 0 ? "b" : "neutral"}&type=reg">Lab →</a></div></div>`;
    };
    const recent = g.recent.slice(-200).reverse();
    const resRow = (x) => {
      const favA = x.p_a >= 0.5, wonA = x.pts_a > x.pts_b, ok = favA === wonA;
      return `<div class="res-row"><span class="faint">${x.date.slice(5)}</span><span class="ellip ${wonA ? "" : "faint"}">${wonA ? "<b>" : ""}${esc(x.team_a)}${wonA ? "</b>" : ""}</span>
        <span class="sc num">${x.pts_a}-${x.pts_b}</span><span class="ellip r ${wonA ? "faint" : ""}">${!wonA ? "<b>" : ""}${x.loc === 0 ? "" : x.loc < 0 ? "@ " : "vs "}${esc(x.team_b)}${!wonA ? "</b>" : ""}</span>
        <span class="r pre small ${ok ? "pos" : "neg"}">${ok ? "✓" : "✗"} pre ${pct(Math.max(x.p_a, 1 - x.p_a))}</span></div>`;
    };
    const tzName = (inTZ(new Date(), { timeZoneName: "short" }).split(" ").pop() || "").replace(/[^A-Za-z0-9+\-:]/g, "");
    app.innerHTML = `
      <div class="page-head"><div><h1>Games</h1><p class="sub">Forecasts for the next week of ${LG} games, and how this season's pre-game forecasts have done. The current season is never used to train the model, so these numbers are out-of-sample.</p></div>${art("arc")}</div>
      ${acc ? `<div class="tiles">
        <div class="tile"><span class="k">Games this season</span><span class="v num">${acc.n.toLocaleString()}</span></div>
        <div class="tile"><span class="k">Winners called</span><span class="v num">${pct(acc.accuracy, 1)}</span></div>
        <div class="tile"><span class="k">Avg margin error</span><span class="v num">${fmt(acc.mae)}</span><span class="r">points</span></div>
        <div class="tile"><span class="k">Log loss</span><span class="v num">${fmt(acc.log_loss, 3)}</span>${isNum(acc.market_log_loss) ? `<span class="r">betting line ${fmt(acc.market_log_loss, 3)} on ${acc.market_n} games (model ${fmt(acc.model_log_loss_on_market_games, 3)})</span>` : ""}</div></div>` : ""}
      <div class="section-head" style="margin-top:20px"><h2 style="margin:0">Schedule</h2><input type="search" id="gq" placeholder="Filter by team…" aria-label="Filter by team"></div>
      <p class="note" style="margin-top:-4px">Tip-off times are in your time zone${tzName ? ` (${esc(tzName)})` : ""}. Today's finals stay here with the score until the day is over.</p>
      <div id="up"></div>
      <h2 class="day" style="margin-top:28px">Recent results</h2>
      <div class="card flush">${recent.map(resRow).join("") || `<p class="faint" style="padding:16px">No games yet.</p>`}</div>`;
    const draw = () => {
      const q = ($("#gq").value || "").toLowerCase();
      $("#up").innerHTML = Object.entries(byDate).sort(([a], [b]) => a.localeCompare(b)).map(([d, xs]) => {
        const ys = xs.filter((x) => !q || x.team_a.toLowerCase().includes(q) || x.team_b.toLowerCase().includes(q))
          .sort((a, b) => (tip(a) - tip(b)) || (featured(b) - featured(a)) || (rk(a.team_a) + rk(a.team_b) - rk(b.team_a) - rk(b.team_b)));
        const nf = ys.filter(featured).length, nd = ys.filter((x) => x.final).length;
        return ys.length ? `<h2 class="day">${new Date(d + "T12:00:00").toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric" })} <span class="faint small">${ys.length} games${nd ? ` · ${nd} final` : ""}${nf ? ` · ${nf} featured` : ""}</span></h2><div class="games">${ys.map(card).join("")}</div>` : "";
      }).join("") || `<p class="faint">No ${LG} games scheduled in the next week${q ? " for that filter" : ""}.</p>`;
    };
    $("#gq").addEventListener("input", draw);
    draw();
  }

  // ================================================================ STYLES (map) & GUIDE
  async function viewStyles(_, params) {
    await style();
    await teams();
    if (!STYLE) { app.innerHTML = `<div class="card">Style identities are not available yet.</div>`; return; }
    const seasons = await get("seasons.json");
    const s = Number(params.get("season")) || seasons.current;
    const d = await identities(s);
    const M = (b) => STYLE.blocks[b];
    app.innerHTML = `
      <section class="hero"><div>${art("latent", "hero-spot")}<h1>How every team plays, separate from how good it is</h1>
          <p>Each ${LG} team gets an offensive, defensive and personnel identity from play-by-play: shot locations, ball movement, pace, pressure, rebounding and roster build. Team strength is regressed out first, so these are styles, not rankings. <a href="#/guide">Style guide →</a></p></div>
        <div class="kpis"><div class="tile"><span class="v">${d.teams.length}</span><span class="k">teams profiled</span></div><div class="tile"><span class="v">${M("offense").factors.length + M("defense").factors.length + M("roster").factors.length}</span><span class="k">style dimensions</span></div><div class="tile"><span class="v">${M("offense").archetypes.length + M("defense").archetypes.length + M("roster").archetypes.length}</span><span class="k">archetypes</span></div></div></section>
      <section class="section card"><div class="section-head"><h2 style="margin:0">Style map</h2>
          <select id="season" aria-label="Season">${seasons.seasons.slice().reverse().map((x) => `<option value="${x}" ${x === s ? "selected" : ""}>${seasonLabel(x)}</option>`).join("")}</select></div>
        <div class="controls"><label class="small faint" for="mx">X</label><select id="mx"></select><label class="small faint" for="my">Y</label><select id="my"></select><input type="search" id="mq" placeholder="Find a team…" style="flex:1;min-width:140px" aria-label="Find a team"></div>
        <div class="controls"><span class="small faint">Highlight an archetype:</span><div class="chips" id="mchips"></div></div>
        <div class="map-wrap" id="map"></div>
        <p class="note">Each dot is a team. Hover for details; click to open its page.</p></section>
      <section class="section grid g3" id="archsum"></section>`;
    const opts = [];
    BLOCKS.forEach(([b, lab]) => M(b).factors.forEach((f, i) => opts.push([`${b}:${i}`, `${lab}: ${f.name}`])));
    const optHtml = opts.map(([v, l]) => `<option value="${v}">${esc(l)}</option>`).join("");
    $("#mx").innerHTML = optHtml; $("#my").innerHTML = optHtml; $("#mx").value = "offense:0"; $("#my").value = "defense:0";
    let hl = null;
    const chips = [];
    BLOCKS.forEach(([b, lab]) => M(b).archetypes.forEach((a, i) => chips.push([`${b}:${i}`, a.name, lab])));
    $("#mchips").innerHTML = chips.map(([k, n, l]) => `<button class="chip" data-k="${k}" title="${l}">${esc(n)}</button>`).join("");
    $("#mchips").addEventListener("click", (e) => { const c = e.target.closest(".chip"); if (!c) return; hl = hl === c.dataset.k ? null : c.dataset.k; $$("#mchips .chip").forEach((x) => x.classList.toggle("on", x.dataset.k === hl)); draw(); });
    const draw = () => styleMap($("#map"), d, $("#mx").value, $("#my").value, hl, $("#mq").value.trim().toLowerCase(), s);
    ["mx", "my"].forEach((id) => $("#" + id).addEventListener("change", draw)); $("#mq").addEventListener("input", draw);
    $("#season").addEventListener("change", (e) => { location.hash = `#/styles?season=${e.target.value}`; });
    draw();
    const ro = new ResizeObserver(() => { if (!document.body.contains(ro.el)) return ro.disconnect(); draw(); });
    ro.el = $("#map"); ro.observe(ro.el);
    $("#archsum").innerHTML = BLOCKS.map(([b, lab]) => {
      const counts = M(b).archetypes.map((a, i) => [a, d.teams.filter((t) => t.blocks[b].primary === i)]);
      return `<div class="card"><h2>${lab} archetypes</h2><ul class="clean arrow">${counts.map(([a, ts]) => {
        const top = ts.sort((x, y) => y.blocks[b].members[a.idx] - x.blocks[b].members[a.idx]).slice(0, 3);
        return `<li><b>${esc(a.name)}</b> <span class="faint small">${ts.length} teams</span><br><span class="small muted">${top.map((t) => idLink(t, s)).join(", ")}</span></li>`;
      }).join("")}</ul></div>`;
    }).join("");
  }
  function idLink(card, season) {
    const t = BY_ESPN[String(card.id)];
    const nm = card.short || card.name;
    if (!t) return esc(nm);
    return `<a href="#/team/${slugify(t.team)}${season ? `?season=${season}` : ""}">${esc(nm)}</a>`;
  }
  function styleMap(el, d, xk, yk, hl, q, season) {
    const [xb, xi] = xk.split(":"), [yb, yi] = yk.split(":");
    const W = el.clientWidth || 800, H = Math.max(320, Math.min(560, W * 0.62)), m = { l: 18, r: 18, t: 22, b: 30 };
    const ts = d.teams;
    const xs = ts.map((t) => t.blocks[xb].scores[xi]), ys = ts.map((t) => t.blocks[yb].scores[yi]);
    const lim = Math.max(2.5, ...xs.map(Math.abs), ...ys.map(Math.abs)) * 1.05;
    const sx = (v) => m.l + ((v + lim) / (2 * lim)) * (W - m.l - m.r), sy = (v) => H - m.b - ((v + lim) / (2 * lim)) * (H - m.t - m.b);
    const fx = STYLE.blocks[xb].factors[xi], fy = STYLE.blocks[yb].factors[yi];
    const [hb, hi] = hl ? hl.split(":") : [null, null];
    let dots = "", labels = "";
    const order = ts.map((t, i) => i).sort((a, b) => (hb && ts[a].blocks[hb].primary == hi) - (hb && ts[b].blocks[hb].primary == hi));
    for (const i of order) {
      const t = ts[i], isH = hb && t.blocks[hb].primary == hi, isQ = q && ((t.name || "").toLowerCase().includes(q) || (t.abbr || "").toLowerCase() === q);
      dots += `<circle class="dotm${isH ? " hl" : ""}${isQ ? " sel" : ""}" data-i="${i}" cx="${sx(xs[i]).toFixed(1)}" cy="${sy(ys[i]).toFixed(1)}" r="${isQ ? 7 : isH ? 5.5 : 4.5}"/>`;
      if (isQ) labels += `<text x="${sx(xs[i]) + 9}" y="${sy(ys[i]) + 4}" style="font-size:12px;font-weight:600;fill:var(--text)">${esc(t.short || t.name)}</text>`;
    }
    el.innerHTML = `<svg width="${W}" height="${H}" role="img" aria-label="Teams by ${esc(fx.name)} and ${esc(fy.name)}">
      <g class="axis"><line x1="${sx(0)}" x2="${sx(0)}" y1="${m.t}" y2="${H - m.b}"/><line x1="${m.l}" x2="${W - m.r}" y1="${sy(0)}" y2="${sy(0)}"/></g>
      <text x="${m.l}" y="${H - 8}" style="font-size:12px">← ${esc(fx.low)}</text><text x="${W - m.r}" y="${H - 8}" text-anchor="end" style="font-size:12px">${esc(fx.high)} →</text>
      <text x="${sx(0) + 6}" y="${m.t - 6}" style="font-size:12px">↑ ${esc(fy.high)}</text><text x="${sx(0) + 6}" y="${H - m.b - 6}" style="font-size:12px">↓ ${esc(fy.low)}</text>${dots}${labels}</svg>`;
    const svg = $("svg", el);
    svg.addEventListener("mousemove", (e) => {
      const c = e.target.closest("circle"); if (!c) return hideTip();
      const t = ts[c.dataset.i];
      showTip(`<b>${esc(t.name)}</b> · ${esc(t.conf || "")}<br>O: ${esc(arch("offense", t.blocks.offense.primary).name)}<br>D: ${esc(arch("defense", t.blocks.defense.primary).name)}<br>${esc(fx.name)}: ${ord(t.blocks[xb].pct[xi] * 100)} pct<br>${esc(fy.name)}: ${ord(t.blocks[yb].pct[yi] * 100)} pct`, e);
    });
    svg.addEventListener("mouseleave", hideTip);
    svg.addEventListener("click", (e) => { const c = e.target.closest("circle"); if (!c) return; const t = BY_ESPN[String(ts[c.dataset.i].id)]; if (t) location.hash = `#/team/${slugify(t.team)}?season=${season}`; });
  }
  async function viewGuide() {
    await style();
    await teams();
    if (!STYLE) { app.innerHTML = `<div class="card">Style identities are not available yet.</div>`; return; }
    const d = await identities();
    const lead = (b, i, dir) => d.teams.slice().sort((x, y) => dir * (y.blocks[b].scores[i] - x.blocks[b].scores[i])).slice(0, 4).map((t) => idLink(t)).join(", ");
    app.innerHTML = `<div class="page-head"><div><h1>Style guide</h1><p class="sub">Every dimension was found by factor analysis on ${STYLE.n_train.toLocaleString()} team-seasons (${STYLE.train_seasons[0]}–${STYLE.train_seasons[STYLE.train_seasons.length - 1]}) after removing team strength, then named by hand. Archetypes are soft clusters (Gaussian mixture) in that space.</p></div>${art("latent")}</div>
      ${BLOCKS.map(([b, lab]) => `<section class="section"><h2>${lab} dimensions</h2><div class="grid g3">${STYLE.blocks[b].factors.map((f, i) => `<div class="card factor-card">
          <h2 style="font-size:16px">${esc(f.name)}</h2><p class="small muted" style="margin:2px 0 6px">${esc(f.desc)}</p><div class="poles2"><span>← ${esc(f.low)}</span><span>${esc(f.high)} →</span></div>
          <div class="loads">${f.loadings.slice(0, 6).map((l) => `<div class="load"><span>${esc(l.label)}</span><span class="bar"><i style="${l.w >= 0 ? `left:50%;width:${Math.min(50, Math.abs(l.w) * 50)}%;background:var(--accent)` : `right:50%;width:${Math.min(50, Math.abs(l.w) * 50)}%;background:var(--hot)`}"></i></span></div>`).join("")}</div>
          <div class="examples"><b>Most:</b> ${lead(b, i, 1)}<br><b>Least:</b> ${lead(b, i, -1)}</div></div>`).join("")}</div>
        <h2 style="margin-top:20px">${lab} archetypes</h2><div class="grid g3">${STYLE.blocks[b].archetypes.map((a, i) => {
          const mem = d.teams.filter((t) => t.blocks[b].primary === i).sort((x, y) => y.blocks[b].members[i] - x.blocks[b].members[i]);
          return `<div class="card"><h2 style="font-size:16px">${esc(a.name)}</h2><p class="small muted" style="margin:2px 0 8px">${esc(a.desc)}</p><div class="chips">${a.signature.map((s) => `<span class="tag">${esc(s)}</span>`).join("")}</div>
            <div class="examples"><b>${mem.length} teams this season</b>${mem.length ? ": " + mem.slice(0, 6).map((t) => idLink(t)).join(", ") : ""}</div>${a.history ? `<div class="examples"><b>Historical examples:</b> ${esc(a.history)}</div>` : ""}</div>`;
        }).join("")}</div></section>`).join("")}`;
  }

  // ================================================================ PLAYOFFS (NBA / WNBA)
  async function viewPlayoffs() {
    const [d] = await Promise.all([get("playoffs.json"), teams()]);
    const R = d.rounds, nR = R.length, done = d.phase === "complete", inPost = d.phase === "postseason";
    const byConf = !!d.format.by_conference, playIn = !!d.format.play_in;
    const usePre = done && d.teams.some((t) => t.pre_title != null);
    const P = (t) => (usePre ? { title: t.pre_title, round: t.pre_round } : { title: t.p_title, round: t.p_round });
    const heat = (v) => `<span class="heat num" style="background:color-mix(in srgb, var(--accent) ${Math.round(Math.min(1, v || 0) * 45)}%, transparent)">${v == null ? "–" : v < 0.005 ? (v > 0 ? "<1%" : "–") : v > 0.995 && v < 1 ? ">99%" : pct(v)}</span>`;
    const short = (n) => (/^W?NBA Finals$/.test(n) ? "Finals" : n.replace("Conference ", "Conf. "));
    const head = `<thead><tr><th class="l nosort">Team</th><th class="nosort">Record</th>${done || inPost ? "" : `<th class="nosort" title="Average final record in the simulations (10th-90th percentile wins)">Projected</th>`}
      ${playIn && !(done || inPost) ? `<th class="nosort" title="Seeds 1-6: straight into the playoffs">Top 6</th><th class="nosort" title="Seeds 7-10">Play-in</th>` : ""}
      <th class="nosort">${usePre ? "Playoffs" : "Playoffs"}</th>${R.slice(1).map((r) => `<th class="nosort" title="${esc(r)}">${esc(short(r))}</th>`).join("")}<th class="nosort">Title</th></tr></thead>`;
    const row = (t) => {
      const x = P(t), ch = d.champion === t.team;
      const proj = `${Math.round(t.proj_w)}–${Math.round(t.g_total - t.proj_w)}<div class="small faint">${Math.round(t.w_lo)}–${Math.round(t.w_hi)} W</div>`;
      return `<tr><td class="l">${teamLink(t.team)}${ch ? ` <span class="pill good">champion</span>` : ""}</td><td class="num">${t.w}–${t.l}</td>
        ${done || inPost ? "" : `<td class="num">${proj}</td>`}
        ${playIn && !(done || inPost) ? `<td>${heat(t.p_top6)}</td><td>${heat(t.p_playin)}</td>` : ""}
        ${x.round.map((v) => `<td>${heat(v)}</td>`).join("")}<td>${heat(x.title)}</td></tr>`;
    };
    const groups = byConf ? [...new Set(d.teams.map((t) => t.conf))].sort() : [null];
    const tables = groups.map((g) => {
      const ts = d.teams.filter((t) => g == null || t.conf === g)
        .sort((a, b) => (P(b).round[0] - P(a).round[0]) || (P(b).title - P(a).title) || (b.proj_w - a.proj_w));
      return `<div class="card flush tbl-wrap section">${g ? `<h2 style="padding:14px 16px 0">${esc(g)}</h2>` : ""}<table class="ratings-table">${head}<tbody>${ts.map(row).join("")}</tbody></table></div>`;
    }).join("");
    const fav = d.teams.slice().sort((a, b) => P(b).title - P(a).title).slice(0, 8);
    const mx = Math.max(...fav.map((t) => P(t).title), 0.01);
    const odds = `<div class="card"><h2>${usePre ? "Title odds before the playoffs" : "Title odds"}</h2>${fav.map((t, i) => `
      <div class="odds-row lb-row"><span class="faint num">${i + 1}</span><span class="teamcell" style="font-weight:500">${logo(TEAMS[t.team])}<span class="nm">${esc(t.team)}${d.champion === t.team ? " 🏆" : ""}</span></span>
        <div class="pbar lb-bar"><span style="width:${(100 * P(t).title / mx).toFixed(1)}%"></span></div><div class="num" style="text-align:right;font-weight:600">${pct(P(t).title, 1)}</div></div>`).join("")}</div>`;
    const champCard = done ? `<div class="card champ-card">${logo(TEAMS[d.champion], "lg")}<div><div class="kind">${esc(seasonLabel(d.season))} champion</div><h2 style="font-size:28px;margin:2px 0">${esc(d.champion)}</h2>
      <p class="muted small">Before the playoffs the model gave them ${pct((d.teams.find((t) => t.team === d.champion) || {}).pre_title, 1)} to win the title${(() => { const o = d.teams.slice().sort((a, b) => (b.pre_title || 0) - (a.pre_title || 0)); const r = o.findIndex((t) => t.team === d.champion) + 1; return r ? `, ${ord(r)} best in the league` : ""; })()}.</p></div></div>` : "";
    const ser = d.series || [];
    const serRounds = [...new Set(ser.map((x) => x.round))].sort((a, b) => a - b);
    const serHtml = ser.length ? `<div class="section"><h2>Series</h2>${serRounds.map((r) => `<h3 style="margin:14px 0 8px">${esc(R[r - 1] || "Round " + r)}</h3><div class="grid g3">${ser.filter((x) => x.round === r).map((x) => {
        const win = x.done ? (x.w_hi > x.w_lo ? x.hi : x.lo) : null;
        const line = (t, w, p) => `<div class="row ${win === t ? "w" : ""}" style="display:flex;align-items:center;gap:8px;padding:4px 0">${logo(TEAMS[t])}<span class="ellip" style="flex:1">${teamLink(t)}</span><b class="num">${w}</b><span class="num small faint" style="width:52px;text-align:right">${p == null ? "" : pct(p)}</span></div>`;
        return `<div class="card">${x.conf ? `<div class="small faint">${esc(x.conf)}</div>` : ""}${line(x.hi, x.w_hi, x.done ? null : x.p_hi)}${line(x.lo, x.w_lo, x.done ? null : 1 - x.p_hi)}
          <div class="small muted" style="margin-top:6px">Best of ${x.n}${x.done ? ` · ${esc(win)} won ${Math.max(x.w_hi, x.w_lo)}–${Math.min(x.w_hi, x.w_lo)}` : " · live odds at right"}${x.p_hi_pre != null ? ` · before game 1: ${esc(x.hi)} ${pct(x.p_hi_pre)}` : ""}</div></div>`;
      }).join("")}</div>`).join("")}</div>` : "";
    const bt = d.backtest;
    const lab = { home_court: "Home-court team wins (base rate from earlier seasons)", blend: "hoop2vec blend (live model)", net: "Matchup network", net_mmai: "Matchup network (core features)",
      linear: "Linear game model", kalman: "Dynamic (Kalman) rating", efficiency: "Adjusted efficiency", elo: "Elo" };
    const btHtml = bt && bt.models ? (() => {
      const ks = Object.keys(bt.models), best = Math.min(...ks.map((k) => bt.models[k].log_loss));
      return `<div class="section card"><h2>Backtest: ${bt.n} playoff series, ${bt.seasons[0]}–${bt.seasons[1]}</h2>
        <p class="note" style="margin-top:-4px">Each series predicted before game 1 by models trained only on earlier seasons, with each game priced at its arena. The team with home court won ${pct(bt.home_court_won)} of these series.</p>
        <div class="tbl-wrap"><table><thead><tr><th class="l nosort">Model</th><th class="nosort">Log loss ↓</th><th class="nosort">Brier ↓</th><th class="nosort">Accuracy ↑</th></tr></thead><tbody>
        ${ks.sort((a, b) => bt.models[a].log_loss - bt.models[b].log_loss).map((k) => `<tr><td class="l">${esc(lab[k] || k)}</td><td class="num ${bt.models[k].log_loss === best ? "best" : ""}">${fmt(bt.models[k].log_loss, 3)}</td><td class="num">${fmt(bt.models[k].brier, 3)}</td><td class="num">${pct(bt.models[k].accuracy, 1)}</td></tr>`).join("")}</tbody></table></div></div>`;
    })() : "";
    const phaseTxt = done ? `The ${esc(seasonLabel(d.season))} season is over. Odds below are what the model expected before the first round, with the same models the site uses now (trained on earlier seasons).`
      : inPost ? "The playoffs are under way. Every game played is locked in, and each series is priced from its current score."
      : `${d.remaining_games.toLocaleString()} regular-season games left. Each one is priced by the game model at its arena, and the rest of the season is played out ${d.n_sims.toLocaleString()} times.`;
    app.innerHTML = `<div class="page-head"><div><h1>${esc(seasonLabel(d.season))} ${LEAGUE.toUpperCase()} Playoffs</h1>
      <p class="sub">${phaseTxt} ${byConf ? "Seeds come from the standings in each conference, with the play-in for seeds 7 to 10." : "The top eight teams overall make the playoffs, regardless of conference."} Series are best-of-${[...new Set(d.format.rounds)].join("/")} with home court to the better seed, and each series is solved exactly from its two home and away game probabilities. Updated ${esc(d.updated || "")}.</p></div></div>
      ${done ? `<div class="grid g2">${champCard}${odds}</div>` : odds}
      ${tables}
      ${serHtml}
      ${btHtml}
      <div class="section prose" style="max-width:none"><h2>How the playoff odds work</h2>
        <p>Each simulation plays out every remaining regular-season game with the game model's win probability. Team strength isn't fixed: each simulated season shifts every team up or down by a random amount, sized from history (how much ratings moved between this point of a season and its end). Early in the season that spread is wide; by April it's small.</p>
        <p>Ties in the standings go to head-to-head record among the tied teams, then ${byConf ? "conference record, then" : ""} a coin flip. The official rules have a few more steps, which rarely decide anything. Once the regular season ends, the official seeds replace the simulated ones.</p>
        <p>${byConf ? "The play-in is three single games: 7 hosts 8 (winner is the 7 seed), 9 hosts 10, and the loser of 7-8 hosts the winner of 9-10 for the 8 seed. " : ""}A series is priced from two numbers: the chance the home-court team wins a game it hosts, and a game on the road. Those go through the series home pattern (${Object.entries(d.format.patterns || {}).map(([n, pt]) => `best-of-${n}: ${pt.map((x) => (x ? "H" : "A")).join("")}`).join(", ")}) to give the exact chance of winning the series from any score.</p></div>`;
  }

  // ================================================================ METHOD
  async function viewMethod() {
    if (IS_PRO) return viewMethodPro();
    if (IS_W) return viewMethodW();
    const bt = await get("backtest.json", true);
    let btHtml = `<p class="muted">Backtest not run yet.</p>`;
    if (bt) {
      const keys = bt.models.map((m) => m.key);
      const label = Object.fromEntries(bt.models.map((m) => [m.key, m.label]));
      const short = Object.fromEntries(bt.models.map((m) => [m.key, m.short || m.label]));
      const bench = { market: "<i>Betting line (benchmark only)</i>", torvik: "<i>T-Rank pre-game probability (benchmark only)</i>" };
      const table = (sub, title, withMarket) => {
        const r = bt.subsets[sub]; if (!r) return "";
        const ks = keys.filter((k) => r[k]).concat(withMarket ? ["market", "torvik"].filter((k) => r[k]) : []);
        const best = Math.min(...ks.filter((k) => !bench[k]).map((k) => r[k].log_loss));
        return `<h3 style="margin-top:18px">${title}</h3><div class="card flush tbl-wrap"><table><thead><tr><th class="l nosort">Model</th><th class="nosort">Games</th><th class="nosort">Log loss ↓</th><th class="nosort">Brier ↓</th><th class="nosort">Accuracy ↑</th><th class="nosort">RMSE ↓</th></tr></thead><tbody>
          ${ks.map((k) => `<tr><td class="l">${bench[k] || esc(label[k])}${k === bt.final ? ` <span class="pill acc">live model</span>` : ""}</td><td class="num">${r[k].n.toLocaleString()}</td><td class="num ${r[k].log_loss === best ? "best" : ""}">${fmt(r[k].log_loss, 4)}</td><td class="num">${fmt(r[k].brier, 4)}</td><td class="num">${pct(r[k].accuracy, 1)}</td><td class="num">${isNum(r[k].rmse) ? fmt(r[k].rmse, 2) : "–"}</td></tr>`).join("")}
          </tbody></table></div>`;
      };
      const ps = bt.per_season || [];
      const psKeys = keys.filter((k) => ps.some((s) => s[k] != null));
      btHtml = `<p>Season-ahead backtest, ${bt.seasons[0]}–${bt.seasons[1]}: for every test season, each model is trained only on earlier seasons, then predicts every game from ratings as of the day before and availability knowable before tip-off. The same games are scored for every model.</p>
        ${table("all", "All D-I games", false)}
        ${table("ncaa", "NCAA tournament games", false)}
        <h3 style="margin-top:22px">Against the betting market</h3>
        <p>Lines are a benchmark only, never a model input. ESPN stores two kinds. Real sportsbook lines (DraftKings, 2025-26) are sharp: the market beats the model by about 0.003 log loss over a season and is level with it in March. That's the honest result, since no public-data model should expect to beat a closing book. Older "consensus" lines (2015–2023) are soft, especially in November, so the model beats them. Any model that beats <i>sportsbook</i> lines by a wide margin should be checked for leakage first. That check is how the original roster leak was found.</p>
        ${table("book_line_games", "Games with a sportsbook line (DraftKings)", true)}
        ${table("consensus_line_games", "Games with an ESPN consensus line", true)}
        ${bt.subsets.torvik_games ? `<h3 style="margin-top:22px">Against T-Rank</h3>
        <p>barttorvik.com keeps T-Rank's own win probability for every game it has played since 2015. It's written before tip-off and left alone afterward: this season's stored numbers were checked against a later download and none had changed. Like the lines, it's a benchmark only, never an input. The stored numbers are rounded to whole percents, so they're scored as no closer than 0.5 percent to a sure thing.</p>
        ${table("torvik_games", "Games with a T-Rank pre-game probability", true)}` : ""}
        ${ps.length ? `<h3 style="margin-top:18px">NCAA tournament log loss by season</h3><div class="card flush tbl-wrap"><table><thead><tr><th class="l nosort">Season</th><th class="nosort">Games</th>${psKeys.map((k) => `<th class="nosort" title="${esc(label[k])}">${esc(short[k])}</th>`).join("")}</tr></thead><tbody>
          ${ps.map((s) => { const m = Math.min(...psKeys.map((k) => s[k]).filter(isNum)); return `<tr><td class="l">${s.season}</td><td class="num">${s.n}</td>${psKeys.map((k) => `<td class="num ${s[k] === m ? "best" : ""}">${fmt(s[k], 3)}</td>`).join("")}</tr>`; }).join("")}</tbody></table></div>` : ""}`;
    }
    app.innerHTML = methodHtml(bt, btHtml);
  }

  function backtestHtml(bt, lineText) {
    if (!bt) return `<p class="muted">Backtest not run yet.</p>`;
    const keys = bt.models.map((m) => m.key);
    const label = Object.fromEntries(bt.models.map((m) => [m.key, m.label]));
    const table = (sub, title, withMarket) => {
      const r = bt.subsets[sub]; if (!r) return "";
      const ks = keys.filter((k) => r[k]).concat(withMarket && r.market ? ["market"] : []);
      const best = Math.min(...ks.filter((k) => k !== "market").map((k) => r[k].log_loss));
      return `<h3 style="margin-top:18px">${title}</h3><div class="card flush tbl-wrap"><table><thead><tr><th class="l nosort">Model</th><th class="nosort">Games</th><th class="nosort">Log loss ↓</th><th class="nosort">Brier ↓</th><th class="nosort">Accuracy ↑</th><th class="nosort">RMSE ↓</th></tr></thead><tbody>
        ${ks.map((k) => `<tr><td class="l">${k === "market" ? "<i>Betting line (benchmark only)</i>" : esc(label[k])}${k === bt.final ? ` <span class="pill acc">live model</span>` : ""}</td><td class="num">${r[k].n.toLocaleString()}</td><td class="num ${r[k].log_loss === best ? "best" : ""}">${fmt(r[k].log_loss, 4)}</td><td class="num">${fmt(r[k].brier, 4)}</td><td class="num">${pct(r[k].accuracy, 1)}</td><td class="num">${fmt(r[k].rmse, 2)}</td></tr>`).join("")}
        </tbody></table></div>`;
    };
    const lineSubs = Object.keys(bt.subsets).filter((k) => k.endsWith("line_games"));
    return `<p>Season-ahead backtest, ${bt.seasons[0]}–${bt.seasons[1]}: for every test season, each model is trained only on earlier seasons, then predicts every game from ratings as of the day before. The same games are scored for every model.</p>
      ${table("all", IS_PRO ? "All games" : "All D-I games", false)}${table("ncaa", IS_PRO ? "Playoff games" : "NCAA tournament games", false)}
      ${lineSubs.length ? `<h3 style="margin-top:22px">Against the betting market</h3><p>${lineText}</p>${lineSubs.map((k) => table(k, k.startsWith("book") ? "Games with a sportsbook line" : "Games with an ESPN consensus line", true)).join("")}` : ""}`;
  }

  async function viewMethodPro() {
    const bt = await get("backtest.json", true);
    const L = LEAGUE.toUpperCase();
    const btHtml = backtestHtml(bt, `Lines are a benchmark only, never a model input. ESPN has carried DraftKings lines for ${L} games in recent seasons. A sportsbook should beat any public-data model over a full season; the gap is the honest yardstick.`);
    app.innerHTML = `<div class="prose">
      <div class="page-head"><div><h1>How it works</h1><p class="sub">The ${L} site runs the same pipeline as the college sites (point-in-time ratings, player values, a blended game model, team identities), with the bracket replaced by a playoff model built for best-of-N series.</p></div>${art("mark")}</div>
      <h2>Backtest</h2>${btHtml}
      <h2>1 · Data (free and public)</h2>
      <ul>
        <li><b>ESPN</b> public API: every ${L} game since 2008 (regular season, ${L === "NBA" ? "play-in, " : ""}playoffs), with full box scores, play-by-play, venues, standings and official playoff seeds. Exhibitions and All-Star games are dropped. Pre-game lines, where ESPN has them, are a benchmark only.</li>
        <li><b>ESPN's injury report</b> for availability, plus recent missed games and a hand-edited <code>overrides/${LEAGUE}/injuries.yaml</code>.</li>
        <li>Player heights and years in the league come from ESPN athlete records. Every player rate (usage, TS%, rebound/assist/block/steal rates, box plus/minus) is computed here from the box scores.</li>
      </ul>
      <h2>2 · Ratings</h2>
      <p>A <b>dynamic Kalman rating</b> tracks each team's offense, defense and tempo after every game and carries over between seasons by returning minutes (trades and free agency move a lot of minutes in the pros). <b>Adjusted efficiency</b> is a recency-weighted ridge regression re-solved before every game day. Elo is kept as a baseline. The Kalman settings were re-tuned on ${L} seasons and checked on the three most recent ones; the college settings held up better there, so they stayed.</p>
      <h2>3 · Players</h2>
      <p>Player value starts from lineups. Every ${L} lineup stint since 2008 records possessions and points for and against, so impact can be measured directly: <b>RAPM</b> (regularized adjusted plus/minus) regresses each stint's opponent-adjusted offense and defense on the five players on the floor, with a ridge penalty, over rolling three-season windows. A box-score model is then fitted to RAPM, the way Basketball-Reference's BPM is built, so it credits scoring volume and efficiency, threes, playmaking and defense the way lineup data says they matter, instead of crediting a team's rebounds and blocks to whoever recorded them (the old PER bias toward centers). Each game's box line goes through that model, plus a team adjustment so the team's minute-weighted total matches its opponent-adjusted result that night. Values are shrunk toward a prior (last season, years in the league) until minutes pile up. Roster strength is Σ minutes share × value, and the injury report moves it before tip-off.</p>
      <h2>4 · Team identity</h2>
      <p>Play-by-play style rates (pace, shot locations, assisted shares, turnover types, rebounding, roster build) are opponent- and venue-adjusted, team strength is regressed out, and factor analysis plus a Gaussian mixture give named dimensions and soft archetypes, fitted on ${L} seasons.</p>
      <h2>5 · The game model</h2>
      <p>An antisymmetric <b>matchup network</b> (team embeddings, a pair head and a σ head, 5-seed ensemble) blended with a regularized <b>linear game model</b> on the Kalman margin, the AdjEM margin, home court, travel, rest and availability. Back-to-backs and travel matter a lot more here than in college. The blend weight comes from the backtest, and P(win) = Φ(margin/σ).</p>
      <h2>6 · Power ratings</h2>
      <p>Power is the blended model's round-robin on a neutral floor: average predicted margin per 100 possessions against every team, re-centered so 0 = an average ${L} team. Strength of schedule is the average Power of the opponents faced.</p>
      <h2>7 · Playoffs</h2>
      <p>College basketball ends in a bracket; the pros play long seasons into seeded best-of-N series, so the forecast plays out the rest of the regular season ${L === "NBA" ? "and the play-in " : ""}${(10000).toLocaleString()} times, applies the tiebreakers, and solves each series exactly from its home and away game probabilities. Every past playoff series was predicted before game 1 by models trained only on earlier seasons and scored against the result. Details are on the <a href="#/playoffs">Playoffs</a> page.</p>
      <h2>Refresh</h2>
      <p>The same GitHub Action runs <code>python -m hoop2vec daily --${LEAGUE}</code> every morning and retrains monthly. In between, a lighter job checks ESPN for finished games every 10 minutes. When there are new ones, <code>python -m hoop2vec live --${LEAGUE}</code> fetches just those games and re-publishes the ratings, forecasts, standings, and playoff odds. Past seasons come from a cache, so nothing is re-scraped. The site is static JSON.</p>
    </div>`;
  }

  async function viewMethodW() {
    const bt = await get("backtest.json", true);
    const btHtml = backtestHtml(bt, "Lines are a benchmark only, never a model input. ESPN carries sportsbook lines for women's games only from 2025-26, and mostly for bigger games, so this is a small, selected sample. The model edges the line there. Women's betting markets are thinner than men's, so that's plausible. As a sanity check, a one-term model on the roster rating alone scores 0.404 log loss on those games, worse than the line (0.396), and the Kalman rating alone is level with it.");
    app.innerHTML = `<div class="prose">
      <div class="page-head"><div><h1>How it works</h1><p class="sub">The women's site runs the same pipeline as the men's (point-in-time ratings, player values, a blended game model, exact bracket odds, team identities and WNBA prospects), rebuilt on data that is free for the women's game.</p></div>${art("mark")}</div>
      <h2>Backtest</h2>${btHtml}
      <h2>1 · Data (free and public)</h2>
      <ul>
        <li><b>ESPN</b> public API: every D-I game since 2019 (schedules, venues and neutral sites, NCAA seeds and rounds, conferences, logos), full box scores, and <b>play-by-play</b> (four 10-minute quarters) with full shot detail since 2020. Player heights and classes come from ESPN athlete records. Pre-game lines, where ESPN has them, are used only as a benchmark.</li>
        <li><b>basketball-reference.com</b>: WNBA seasons since 2018 and WNBA drafts since 2019 (requests spaced 6 seconds apart).</li>
        <li>No women's Torvik feed is used, so every player rate (usage, TS%, assist/rebound/block/steal rates, box plus/minus) is computed here from ESPN box scores. There's no free injury feed either: availability comes from recent missed games plus a hand-edited <code>overrides/womens/injuries.yaml</code>.</li>
      </ul>
      <h2>2 · Ratings</h2>
      <p>The same two engines as the men's site. A <b>dynamic Kalman rating</b> tracks each team's offense, defense and tempo after every game. Between seasons it starts from last season's rating pulled toward the roster-based preseason prior, harder when fewer minutes return, and it ignores part of each opponent's 3-point luck. <b>Adjusted efficiency</b> is a recency-weighted ridge regression re-solved before every game day. Elo is kept as a baseline. Possessions use the same 0.475·FTA estimate, and tempo is per 40 minutes.</p>
      <h2>3 · Players</h2>
      <p>Player value is a box plus/minus fitted for the women's game: each game's opponent-adjusted team offensive and defensive efficiency (vs the opponent's <i>pre-game</i> Kalman rating, so it uses only what was known before tip-off) is regressed on the team's minute-weighted per-40 box-score rates. A player's value is her share of that fit, plus the team residual, in the same points-per-100 units as the men's site. Values are shrunk toward a prior (last season, class) until minutes pile up, and roster strength is Σ minutes share × value.</p>
      <h2>4 · Team identity</h2>
      <p>Play-by-play style rates (transition, shot zones, assisted shares, turnover types, putbacks and more) are opponent- and venue-adjusted, strength is regressed out, and factor analysis plus a Gaussian mixture give named dimensions and soft archetypes. The women's style model is fitted on women's play-by-play (2020–21 onward, about 2,000 team-seasons). Where its dimensions match the men's (most do, closely), the men's names carry over; the archetypes were named by hand from their own profiles.</p>
      <h2>5 · The game model</h2>
      <p>The same blend: an antisymmetric <b>matchup network</b> (team embeddings plus a pair head and a σ head, 5-seed ensemble) and a regularized <b>linear game model</b> on the Kalman margin, the AdjEM margin, home court, travel, rest and availability. The weight is chosen by the backtest. P(win) = Φ(margin/σ).</p>
      <h2>6 · Power ratings</h2>
      <p>Power is the blended model's neutral-floor round-robin: average predicted margin per 100 possessions against the top-100 teams, re-centered so 0 = an average D-I team. Strength of schedule is the average Power of the opponents faced.</p>
      <h2>7 · Bracket</h2>
      <p>The 2027 women's tournament also has <b>76 teams</b>, with a 12-game opening round. First and second rounds are played on the campuses of the top-16 seeds, regionals are in Philadelphia and Las Vegas (two regions each), and the Final Four is in Columbus, Ohio. Before Selection Sunday the field is projected (conference champions plus best at-larges, S-curve seeding). Afterwards it's read from ESPN or <code>overrides/womens/bracket_2027.yaml</code>. Advancement odds are exact, with venue-specific travel.</p>
      <h2>8 · WNBA prospects</h2>
      <p>A draft model (P picked in the top 4, 12 or 24, or at all, in the three-round April draft) and a WNBA success model (P of 2.5+ Win Shares in the first three WNBA seasons, starting the summer of the draft), with college comps and "plays like" WNBA comps from a translation model. Trained on every D-I player-season since 2019 and checked against real drafts and careers. Details are on the <a href="#/prospects">Prospects</a> page.</p>
      <h2>Refresh</h2>
      <p>The same GitHub Action runs <code>python -m hoop2vec daily --womens</code> every morning in season and retrains monthly. In between, a lighter job checks ESPN for finished games every 10 minutes. When there are new ones, <code>python -m hoop2vec live --womens</code> fetches just those games and re-publishes the ratings, forecasts, bracket, and team and player pages (prospect boards wait for the morning run). Past seasons come from a cache, so nothing is re-scraped. The site is static JSON.</p>
    </div>`;
  }

  function methodHtml(bt, btHtml) {
    return `<div class="prose">
      <div class="page-head"><div><h1>How it works</h1><p class="sub">Public data → point-in-time ratings, players and styles → a blended game model → ratings, forecasts and the bracket.</p></div>${art("mark")}</div>
      <h2>Backtest</h2>${btHtml}
      <h2>1 · Data (free and public)</h2>
      <ul>
        <li><b>barttorvik.com</b>: every D-I game since 2008 with box scores, plus player-game logs. Requests are spaced 10 seconds apart, as its robots.txt asks. If Torvik is unreachable, the current season falls back to ESPN box scores.</li>
        <li><b>ESPN</b> public API: venues and neutral sites, NCAA seeds and rounds, logos, and full <b>play-by-play</b> since 2015. That covers possessions, shot zones and types, assists, turnover types and on-court lineups. It also gives pre-game spreads, used only as a benchmark.</li>
        <li><b>Covers.com and Rotowire</b> injury reports, plus a hand-edited <code>overrides/injuries.yaml</code>.</li>
      </ul>
      <h2>2 · Ratings</h2>
      <p>Two complementary engines, both strictly point-in-time. A <b>dynamic Kalman rating</b> tracks each team's offense, defense and tempo with an uncertainty. It updates after every game, caps blowouts, and ignores a third of each opponent's 3-point luck (threes made above or below the league rate on its attempts), which says little about the defense. Between seasons it starts from last season's rating pulled toward the same roster-based preseason prior the efficiency ratings use, and the fewer minutes return, the harder the pull. <b>Adjusted efficiency</b> is a recency-weighted ridge regression re-solved before every game day. It gives AdjO/AdjD/AdjEM/tempo and opponent-adjusted four factors, anchored by a preseason prior from last season and the incoming roster. Elo is kept as a baseline.</p>
      <h2>3 · Players and availability</h2>
      <p>Player values blend a prior (last season, class progression, recruiting rank) with this season's minute-weighted game impact. Each game's value is measured against pre-game team ratings, so it uses only what was known at tip-off. Roster strength = Σ minutes share × value. Ratings already reflect the roster that played, so the injury adjustment is <code>Σ (availability now − usual participation) × share × (value − replacement)</code>. Live availability comes from the injury feeds, recent missed games and the overrides file.</p>
      <h2>4 · Team identity</h2>
      <p>About 32 play-by-play style rates (transition after steals, rebounds and makes, shot zones, pull-up and isolation proxies, assisted shares, late-clock shots, turnover types, blocks, fouls drawn, putbacks) are opponent- and venue-adjusted with empirical-Bayes shrinkage. Team strength is regressed out, then varimax factor analysis gives named offensive, defensive and personnel dimensions. A Gaussian mixture gives soft archetypes. Cards add evidence, strengths, vulnerabilities and matchup notes.</p>
      <h2>5 · The game model</h2>
      <p>Two models, blended on the margin scale with a weight chosen by the backtest:</p>
      <ul><li>the <b>matchup network</b>: an antisymmetric PyTorch model. Each team's ~40 features (both ratings, four factors O/D, roster, Elo, form, availability, rest, travel) are encoded into an embedding, and a pair head scores <code>[A, B, A⊙B, context]</code> so style interactions can matter. A second head predicts game-specific σ. It's a 5-seed ensemble.</li>
      <li>the <b>linear game model</b>: a regularized antisymmetric regression on the Kalman margin, the snapshot AdjEM margin, home court, travel (log-miles difference), rest and availability, with σ scaling with expected possessions.</li></ul>
      <p>Margins blend as <code>w·network + (1−w)·linear</code> with mixture variance, and P(win) = Φ(margin/σ). The Matchup Lab shows the decomposition. Play-by-play style factors add only ~0.001 log loss on all games and nothing measurable in March. So identities stay descriptive, and the network's box-score style inputs carry the matchup effects.</p>
      <h2>6 · Power ratings</h2>
      <p>Power is the blended model's round-robin on a neutral floor: each team's average predicted margin <b>per 100 possessions against the top-100 teams</b>, re-centered so 0 = an average D-I team. It's tempo-neutral and measured against tournament-calibre opposition, not by how badly a team beats the weakest D-I teams. Re-centering shifts every team equally, so ranks, gaps between teams and forecasts don't change. Strength of schedule is the average Power of the opponents faced, so 0 is an average D-I schedule.</p>
      <h2>7 · Bracket</h2>
      <p>2027 uses the <b>76-team</b> format with a 12-game Opening Round. Before Selection Sunday the field is projected: conference champions (or the best team in each conference), the best at-larges, S-curve seeding, protected seeds at the nearest first-round site. Afterwards it is read from ESPN automatically, or from <code>overrides/bracket_2027.yaml</code>. Advancement odds are exact, from propagating each game's win probability (with venue-specific travel) through the tree. Picks: pool-optimal and most likely.</p>
      <h2>8 · NBA prospects</h2>
      <p>A draft model (P picked in the top 5/14/30/60 this June) and an NBA success model (P of 8+ Win Shares in the first four NBA seasons), trained on every D-I player-season since 2008, with NBA careers from basketball-reference.com. Both are checked against real drafts and careers they never saw. Details and backtests are on the <a href="#/prospects">Prospects</a> page.</p>
      <h2>Refresh</h2>
      <p>A GitHub Action runs <code>python -m hoop2vec daily</code> every morning in season and retrains monthly (<code>python -m hoop2vec all</code>). In between, a lighter job checks ESPN for finished games every 10 minutes. When there are new ones, <code>python -m hoop2vec live</code> fetches just those games and re-publishes the ratings, forecasts, bracket, and team and player pages (prospect boards wait for the morning run). Past seasons come from a cache, so nothing is re-scraped. The site is static JSON plus this page.</p>
    </div>`;
  }

  route();
})();
