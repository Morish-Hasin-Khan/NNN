/* NNN — live front end
   Reads data/feed.json (written by fetch_news.py) and renders every section.
   Falls back to data/feed.js (same payload as a script tag) so the page still
   works when opened straight from disk, where fetch() is blocked. */

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

const FEED_URL = "data/feed.json";
const REFRESH_MS = 5 * 60 * 1000;

let FEED = null;
let ALL_ITEMS = [];
const ITEM_INDEX = new Map();

/* ------------------------------------------------------------ small helpers */
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function hash(str) {
  let h = 0;
  for (let i = 0; i < String(str).length; i++) h = (h * 31 + String(str).charCodeAt(i)) | 0;
  return Math.abs(h);
}

function timeAgo(iso) {
  if (!iso) return "Just in";
  const then = new Date(iso).getTime();
  if (!then) return "Just in";
  const mins = Math.round((Date.now() - then) / 60000);
  if (mins < 1) return "Just now";
  if (mins < 60) return `${mins} min ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs} hr${hrs > 1 ? "s" : ""} ago`;
  const days = Math.round(hrs / 24);
  return days === 1 ? "Yesterday" : `${days} days ago`;
}

function readMins(item) {
  const words = ((item.title || "") + " " + (item.summary || "")).split(/\s+/).length;
  return Math.max(2, Math.min(9, Math.round(words / 28) + 2));
}

/* ------------------------------------------------ generated cover fallback */
const ART_PALETTES = [
  ["#6032ad", "#f33d79", "#ff9d4b"], ["#2045a9", "#5b8ae8", "#a9c8f5"],
  ["#b8282d", "#f0803a", "#f7c56b"], ["#2c7154", "#77b58e", "#d6e9c9"],
  ["#7f356b", "#c46ba3", "#f0c3dc"], ["#8b6821", "#d8b455", "#f2e2a2"],
  ["#1f3d63", "#3f7fa6", "#8fd3d0"], ["#5a2472", "#a24bb0", "#f0a6d2"],
];

function generatedArt(seedText, label) {
  const p = ART_PALETTES[hash(seedText) % ART_PALETTES.length];
  const rot = (hash(seedText + "r") % 60) - 30;
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 420" width="640" height="420">
<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
<stop offset="0" stop-color="${p[0]}"/><stop offset="0.58" stop-color="${p[1]}"/><stop offset="1" stop-color="${p[2]}"/>
</linearGradient></defs>
<rect width="640" height="420" fill="url(#g)"/>
<g fill="none" stroke="rgba(255,255,255,.42)" stroke-width="1.4">
<circle cx="512" cy="352" r="88"/><circle cx="512" cy="352" r="132"/><circle cx="512" cy="352" r="180"/></g>
<g transform="rotate(${rot} 170 190)" fill="none" stroke="rgba(255,255,255,.32)" stroke-width="1.4">
<rect x="70" y="92" width="200" height="200"/><rect x="102" y="124" width="136" height="136"/></g>
<text x="30" y="46" fill="rgba(255,255,255,.9)" font-family="monospace" font-size="17" letter-spacing="2">${esc((label || "NNN").toUpperCase()).slice(0, 26)}</text>
</svg>`;
  return "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
}

/* --------------------------------------------------------------- image card */
function imageBlock(item, { className = "", label = "", tall = false } = {}) {
  const fallback = generatedArt(item.title || item.id || "nnn", label || item.source);
  const src = item.image || fallback;
  return `<div class="image-block ${className} ${tall ? "image-lead" : ""}">
      <img class="story-img" src="${esc(src)}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer"
           onerror="this.onerror=null;this.src='${fallback}';this.dataset.generated='1';this.closest('.image-block')?.classList.add('generated')" />
      ${label ? `<b class="image-tag">${esc(label)}</b>` : ""}
      <i class="image-credit">${esc(item.image ? (item.source || "Source") : "NNN illustration")}</i>
    </div>`;
}

/* ------------------------------------------------------------ story links
   Cards no longer jump straight to the publisher. Clicking one opens the NNN
   reader; the href stays put so middle-click, ⌘-click and a JS-less browser
   still reach the source. */
function linkAttrs(item, extraClass = "") {
  return `class="story-link${extraClass ? " " + extraClass : ""}" href="${esc(item.url)}" `
       + `data-story="${esc(item.id)}" target="_blank" rel="noopener noreferrer nofollow"`;
}

function bylineOf(item) {
  const who = (item.byline || "").trim();
  const house = (item.source || "").trim();
  if (who && house) return `By ${who} · ${house}`;
  if (who) return `By ${who}`;
  return house || "Newswire";
}

/* ------------------------------------------------------- licence + reading
   An item is readable in full on NNN only when its licence says so. That flag
   is set by fetch_news.py, never guessed here. */
const FULL_TEXT = new Set(["gov-open", "cc-by", "nnn"]);
const LICENCE_LABEL = {
  "gov-open": "Government of India — free to reproduce",
  "cc-by": "Creative Commons BY — free to republish",
  "nnn": "NNN original",
};
const LICENCE_URL = {
  "gov-open": "https://www.pib.gov.in/content/102_2_Copyright-Policy.aspx",
  "cc-by": "https://creativecommons.org/licenses/by/3.0/",
};

const readsInFull = (item) => FULL_TEXT.has(item.licence) && (item.body || []).length > 0;

function licenceNote(item) {
  const source = esc(item.source || "the publisher");
  const who = esc(item.byline || "");
  const link = LICENCE_URL[item.licence]
    ? ` <a href="${LICENCE_URL[item.licence]}" target="_blank" rel="noopener noreferrer">Licence</a>.` : "";
  switch (item.licence) {
    case "gov-open":
      return `Reproduced from <b>${source}</b>, Government of India. Government material may be
        reproduced free of charge provided it is reproduced accurately, is not used in a derogatory
        or misleading way, and the source is prominently acknowledged.${link}`;
    case "cc-by":
      return `This article${who ? ` by <b>${who}</b>` : ""} originally appeared on <b>${source}</b>
        and is republished in full under a Creative Commons Attribution licence.${link}`;
    case "nnn":
      return `Written for NNN${who ? ` by <b>${who}</b>` : ""}.`;
    default:
      return `<b>${source}</b> holds the rights to this report. NNN shows the headline, the summary
        they syndicate and their preview image, and links back to the original.`;
  }
}

const trimTo = (text, n) => {
  const t = String(text || "");
  if (t.length <= n) return t;
  return t.slice(0, n).replace(/\s+\S*$/, "") + "…";
};

const alsoBadge = (item) =>
  (item.also || []).length ? ` · ${item.also.length + 1} outlets` : "";

/* ============================================================== the reader's
   own state: what they saved, and what they like.

   All of this lives in this browser. There are no accounts and no server, so
   nothing here identifies anybody — it is a preference file on one device. Real
   cross-device accounts need a backend; see the README. */
const SAVED_KEY = "nnn-saved";
const PREFS_KEY = "nnn-prefs";

function readStore(key, fallback) {
  try { return JSON.parse(localStorage.getItem(key)) ?? fallback; }
  catch { return fallback; }             // private mode, quota, corrupt value
}
function writeStore(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); return true; }
  catch { return false; }
}

/* ------------------------------------------------------------ saved / later */
const getSaved = () => readStore(SAVED_KEY, []);
const isSaved = (id) => getSaved().some((x) => x.id === id);

function toggleSave(item) {
  if (!item || !item.id) return false;
  const saved = getSaved();
  const at = saved.findIndex((x) => x.id === item.id);
  let nowSaved;
  if (at >= 0) { saved.splice(at, 1); nowSaved = false; }
  else {
    saved.unshift({
      id: item.id, title: item.title, url: item.url, source: item.source || "",
      kind: item.kind || "story", section: item.section || item.from_section || "",
      published: item.published || null, savedAt: new Date().toISOString(),
    });
    nowSaved = true;
    learn(item, 3);                      // saving is the strongest signal we get
  }
  if (!writeStore(SAVED_KEY, saved.slice(0, 200))) {
    showToast("This browser is blocking storage, so saving won't stick");
    return false;
  }
  paintSavedCount();
  renderSaved();
  return nowSaved;
}

const isWatchable = (item) => item && (item.kind === "podcast" || item.kind === "show" || item.kind === "stream");
const saveVerb = (item) => (isWatchable(item) ? "Watch later" : "Save story");

function paintSavedCount() {
  const n = getSaved().length;
  const badge = $("#savedCount");
  if (!badge) return;
  badge.textContent = n;
  badge.hidden = n === 0;
  $("#savedToggle")?.setAttribute("aria-label", `Saved items (${n})`);
}

function renderSaved() {
  const host = $("#savedList");
  if (!host) return;
  const saved = getSaved();
  if (!saved.length) {
    host.innerHTML = `<p class="empty-note">Nothing saved yet. Use ＋ on a story, or
      <b>Watch later</b> on a podcast, and it will wait for you here.</p>`;
    return;
  }
  host.innerHTML = saved.map((item) => `
    <article class="saved-row">
      <div>
        <p class="story-kicker">${esc(item.source || item.section || "NNN")}${
          isWatchable(item) ? ` <span>•</span> ${item.kind === "stream" ? "Live" : "Listen"}` : ""}</p>
        <h3>${item.kind === "story"
          ? `<a class="story-link" href="${esc(item.url)}" data-story="${esc(item.id)}"
                target="_blank" rel="noopener noreferrer nofollow">${esc(item.title)}</a>`
          : `<a href="${esc(item.url)}" target="_blank" rel="noopener noreferrer">${esc(item.title)}</a>`}</h3>
        <span>Saved ${esc(timeAgo(item.savedAt).toLowerCase())}</span>
      </div>
      <button class="saved-remove" data-remove="${esc(item.id)}" aria-label="Remove from saved">✕</button>
    </article>`).join("");
  $$("[data-remove]", host).forEach((button) => button.addEventListener("click", () => {
    const keep = getSaved().filter((x) => x.id !== button.dataset.remove);
    writeStore(SAVED_KEY, keep);
    paintSavedCount(); renderSaved(); syncSaveButtons();
    showToast("Removed from your list");
  }));
}

function syncSaveButtons() {
  $$(".save-button").forEach((button) => {
    const on = isSaved(button.dataset.id);
    button.classList.toggle("saved", on);
    button.textContent = on ? "✓" : "＋";
  });
}

/* ------------------------------------------------------- what they care about
   A tiny, transparent recommender. Opening a story is a weak signal, saving it
   a strong one, and the categories they pick at sign-up are the starting point.
   Weights are capped so one obsessive evening can't permanently define the feed. */
const emptyPrefs = () => ({ cats: {}, sources: {}, email: null, signedUpAt: null, forYou: false });
const getPrefs = () => ({ ...emptyPrefs(), ...readStore(PREFS_KEY, {}) });
const CAP = 24;

function learn(item, weight) {
  if (!item) return;
  const prefs = getPrefs();
  const cat = item.category;
  const src = item.source;
  if (cat) prefs.cats[cat] = Math.min(CAP, (prefs.cats[cat] || 0) + weight);
  if (src) prefs.sources[src] = Math.min(CAP, (prefs.sources[src] || 0) + weight);
  writeStore(PREFS_KEY, prefs);
}

function prefScore(item) {
  const prefs = getPrefs();
  return (prefs.cats[item.category] || 0) * 3 + (prefs.sources[item.source] || 0);
}

const hasPrefs = () => {
  const p = getPrefs();
  return Object.keys(p.cats).length > 0 || Object.keys(p.sources).length > 0;
};

function personalise(items) {
  // keep the editorial ranking as the base, then let taste nudge it
  const n = items.length;
  return items
    .map((item, i) => ({ item, weight: (n - i) + prefScore(item) * 4 }))
    .sort((a, b) => b.weight - a.weight)
    .map((x) => x.item);
}

/* ------------------------------------------------------------- renderers */
function renderLead(host, section) {
  const items = section.items || [];
  if (!items.length) { host.innerHTML = `<p class="empty-note">No stories in this section yet.</p>`; return; }
  const [lead, ...rest] = items;
  const side = rest.slice(0, 3);

  host.innerHTML = `
    <article class="lead-story story-card" data-topic="${esc((section.title + " " + lead.title).toLowerCase())}">
      <a ${linkAttrs(lead, "image-link")} tabindex="-1" aria-hidden="true">
        ${imageBlock(lead, { tall: true, label: section.title })}
      </a>
      <div class="story-copy">
        <p class="story-kicker">${esc(bylineOf(lead))} <span>•</span> ${readMins(lead)} min read</p>
        <h3><a ${linkAttrs(lead)}>${esc(lead.title)}</a></h3>
        <p>${esc(lead.summary || "Open the story for the publisher's summary and a link to their full report.")}</p>
        <div class="story-footer"><span>${esc(timeAgo(lead.published))}</span>
          <button class="save-button" data-id="${esc(lead.id)}" aria-label="Save story">＋</button></div>
      </div>
    </article>
    <div class="side-stories">
      ${side.map((item) => `
        <article class="compact-story" data-topic="${esc((section.title + " " + item.title).toLowerCase())}">
          <a ${linkAttrs(item, "image-link")} tabindex="-1" aria-hidden="true">${imageBlock(item)}</a>
          <div>
            <p class="story-kicker">${esc(bylineOf(item))}</p>
            <h3><a ${linkAttrs(item)}>${esc(item.title)}</a></h3>
            <span>${esc(timeAgo(item.published))}</span>
          </div>
        </article>`).join("")}
    </div>`;
}

function renderInvestigation(host, section) {
  const items = section.items || [];
  if (!items.length) { host.innerHTML = `<p class="empty-note">No open case files right now.</p>`; return; }
  const [lead, ...rest] = items;
  const states = [["inquiry", "Public inquiry"], ["active", "Under investigation"], ["resolved", "Latest ruling"]];
  host.innerHTML = `
    <article class="investigation-main" data-topic="${esc(("scandals " + lead.title).toLowerCase())}">
      <div class="investigation-number">Case file / ${String(hash(lead.id || lead.title) % 900 + 100)}</div>
      <p class="story-kicker"><i class="dot inquiry"></i> ${esc(bylineOf(lead))}</p>
      <h3><a ${linkAttrs(lead)}>${esc(lead.title)}</a></h3>
      <p>${esc(lead.summary || "We separate what is verified, what officials have said, and what remains under investigation.")}</p>
      <div class="case-tags"><span>${esc(timeAgo(lead.published))}</span><span>${esc(lead.source || "Source")}</span><span>${rest.length} related</span></div>
      <a ${linkAttrs(lead, "button button-dark")}>Open case file <span>→</span></a>
    </article>
    <div class="case-list">
      ${rest.slice(0, 3).map((item, i) => `
        <article class="case-row" data-topic="${esc(("scandals " + item.title).toLowerCase())}">
          <div>
            <p class="story-kicker"><i class="dot ${states[i % 3][0]}"></i> ${esc(states[i % 3][1])}</p>
            <h3><a ${linkAttrs(item)}>${esc(item.title)}</a></h3>
            <p>${esc(bylineOf(item))} · ${esc(timeAgo(item.published))}</p>
          </div>
          <button class="save-button" data-id="${esc(item.id)}" aria-label="Save story">＋</button>
        </article>`).join("")}
    </div>`;
}

function renderEditorial(host, section) {
  const items = section.items || [];
  if (!items.length) { host.innerHTML = `<p class="empty-note">No editorials loaded yet.</p>`; return; }
  const [lead, ...rest] = items;
  host.innerHTML = `
    <article class="editorial-lead" data-topic="${esc(("editorial " + lead.title).toLowerCase())}">
      <p class="story-kicker">${esc(bylineOf(lead))}</p>
      <h3><a ${linkAttrs(lead)}>${esc(lead.title)}</a></h3>
      <p>${esc(lead.summary || "")}</p>
      <div class="story-footer">
        <span>${esc(timeAgo(lead.published))} · ${readMins(lead)} min read</span>
        ${readsInFull(lead) ? `<b class="licence-tag">${esc(LICENCE_LABEL[lead.licence] || "Open licence")}</b>` : ""}
      </div>
    </article>
    ${rest.slice(0, 3).map((item) => `
      <article class="editorial-row" data-topic="${esc(("editorial " + item.title).toLowerCase())}">
        <p class="story-kicker">${esc(bylineOf(item))}</p>
        <h3><a ${linkAttrs(item)}>${esc(item.title)}</a></h3>
        <span>${esc(timeAgo(item.published))}${readsInFull(item) ? " · full text on NNN" : ""}</span>
      </article>`).join("")}`;
}

function renderFront(host, section) {
  const prefs = getPrefs();
  const on = prefs.forYou && hasPrefs();
  // section.items is a candidate pool (~30). Editorial order takes the top of
  // it; For-you re-ranks the whole pool first, so a category that never makes
  // the default cut can still be promoted.
  const pool = section.items || [];
  const items = (on ? personalise(pool) : pool).slice(0, 9);
  const toggle = $("#forYouToggle");
  if (toggle) {
    toggle.textContent = on ? "✦ For you" : "Top stories";
    toggle.classList.toggle("on", on);
    toggle.hidden = !hasPrefs();
  }
  if (!items.length) { host.innerHTML = `<p class="empty-note">Front page is still filling.</p>`; return; }
  const [lead, ...rest] = items;
  const card = (item, i) => `
    <article class="front-card" data-topic="${esc((item.category_label + " " + item.title).toLowerCase())}">
      <a ${linkAttrs(item, "image-link")} tabindex="-1" aria-hidden="true">${imageBlock(item)}</a>
      <div class="front-copy">
        <p class="story-kicker"><b class="cat-tag cat-${esc(item.category || "general")}">${esc(item.category_label || "News")}</b> ${esc(item.source || "")}</p>
        <h3><a ${linkAttrs(item)}>${esc(item.title)}</a></h3>
        <span>${esc(timeAgo(item.published))}${alsoBadge(item)}</span>
      </div>
    </article>`;
  host.innerHTML = `
    <article class="front-lead story-card" data-topic="${esc((lead.category_label + " " + lead.title).toLowerCase())}">
      <a ${linkAttrs(lead, "image-link")} tabindex="-1" aria-hidden="true">${imageBlock(lead, { tall: true, label: lead.category_label })}</a>
      <div class="story-copy">
        <p class="story-kicker"><b class="cat-tag cat-${esc(lead.category || "general")}">${esc(lead.category_label || "News")}</b> ${esc(lead.source || "")} <span>•</span> ${readMins(lead)} min read</p>
        <h3><a ${linkAttrs(lead)}>${esc(lead.title)}</a></h3>
        <p>${esc(trimTo(lead.summary, 260))}</p>
        <div class="story-footer"><span>${esc(timeAgo(lead.published))}${alsoBadge(lead)}</span>
          <button class="save-button" data-id="${esc(lead.id)}" aria-label="Save story">＋</button></div>
      </div>
    </article>
    <div class="front-rest">${rest.map(card).join("")}</div>`;
}

function renderLive(host, section) {
  const items = section.items || [];
  if (!items.length) { host.innerHTML = `<p class="empty-note">Nothing running right now.</p>`; return; }
  const streams = items.filter((i) => i.kind === "stream");
  const blogs = items.filter((i) => i.kind !== "stream");
  host.innerHTML = `
    <div class="stream-row">
      ${streams.map((item) => `
        <a class="stream-card" href="${esc(item.url)}" target="_blank" rel="noopener noreferrer">
          <span class="stream-live"><i class="live-dot"></i>Live</span>
          <b>${esc(item.source)}</b>
          <em>${esc(item.summary || "")}</em>
          <u>Open stream →</u>
        </a>`).join("")}
    </div>
    ${blogs.length ? `<div class="liveblog-list">
      ${blogs.slice(0, 6).map((item) => `
        <article class="liveblog-row" data-topic="${esc(("live " + item.title).toLowerCase())}">
          <span class="live-pip"><i class="live-dot"></i></span>
          <div>
            <p class="story-kicker">${esc(item.source || "")}</p>
            <h3><a ${linkAttrs(item)}>${esc(item.title)}</a></h3>
            <span>${esc(timeAgo(item.published))}</span>
          </div>
        </article>`).join("")}
    </div>` : ""}`;
}

function renderPodcasts(host, section) {
  const items = section.items || [];
  if (!items.length) { host.innerHTML = `<p class="empty-note">No episodes loaded yet.</p>`; return; }
  const episodes = items.filter((i) => i.kind !== "show").slice(0, 8);
  const shows = items.filter((i) => i.kind === "show");
  host.innerHTML = [...episodes, ...shows].map((item) => `
    <article class="podcast-card${item.kind === "show" ? " is-show" : ""}">
      <a class="podcast-open" href="${esc(item.url)}" target="_blank" rel="noopener noreferrer">
        <span class="podcast-play" aria-hidden="true">${item.kind === "show" ? "◉" : "▶"}</span>
        <div>
          <p class="story-kicker">${esc(item.show || item.source || "Podcast")}${
            item.kind === "show" ? ` <span>•</span> Show` : ""}</p>
          <h3>${esc(item.title)}</h3>
          <p>${esc(trimTo(item.summary, 150))}</p>
          <span>${item.kind === "show"
            ? `Open the channel`
            : `${esc(timeAgo(item.published))} · listen at ${esc(item.show || item.source || "the publisher")}`}</span>
        </div>
      </a>
      <button class="watch-later" data-watch="${esc(item.id)}" type="button">${
        isSaved(item.id) ? "✓ Saved" : "Watch later"}</button>
    </article>`).join("");

  $$("[data-watch]", host).forEach((button) => button.addEventListener("click", () => {
    const item = ITEM_INDEX.get(button.dataset.watch);
    const saved = toggleSave(item);
    button.classList.toggle("saved", saved);
    button.textContent = saved ? "✓ Saved" : "Watch later";
    showToast(saved ? "Added to Watch later" : "Removed from your list");
  }));
}

function renderColumns(host) {
  const wanted = ["business", "technology", "sports", "culture", "health"];
  const rows = [];
  wanted.forEach((id) => {
    const sec = (FEED.sections || []).find((s) => s.id === id);
    (sec?.items || []).slice(0, 2).forEach((item) => rows.push({ ...item, kicker: sec.title, anchor: id }));
  });
  if (!rows.length) { host.innerHTML = `<p class="empty-note">No stories loaded yet.</p>`; return; }
  host.innerHTML = rows.map((item, i) => `
    <article class="news-story" ${i < wanted.length ? `id="${esc(item.anchor)}"` : ""} data-topic="${esc((item.kicker + " " + item.title).toLowerCase())}">
      <span class="news-index">${String(i + 1).padStart(2, "0")}</span>
      <div>
        <p class="story-kicker">${esc(item.kicker)} <span>•</span> ${esc(bylineOf(item))}</p>
        <h3><a ${linkAttrs(item)}>${esc(item.title)}</a></h3>
        <p>${esc(item.summary || timeAgo(item.published))}</p>
      </div>
    </article>`).join("");
}

/* ---------------------------------------------------------- market strip */
function formatValue(q) {
  const d = q.decimals ?? 2;
  const v = Number(q.value).toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d });
  return q.unit && q.unit.startsWith("$") ? `$${v}` : v;
}

function renderMarkets() {
  const track = $("#marketTrack");
  const quotes = (FEED && FEED.markets) || [];
  if (!quotes.length) {
    track.innerHTML = `<span class="quote quote-note">Live prices unavailable — run <b>fetch_news.py</b> to refresh</span>`;
    return;
  }
  const cell = (q) => {
    const dir = q.change > 0 ? "up" : q.change < 0 ? "down" : "flat";
    const arrow = dir === "up" ? "▲" : dir === "down" ? "▼" : "■";
    const d = q.decimals ?? 2;
    const chg = Math.abs(q.change).toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d });
    return `<span class="quote quote-${esc(q.key)} ${dir}${q.stale ? " stale" : ""}">
        <b>${esc(q.label)}</b>
        <i>${formatValue(q)}</i>
        ${q.unit && !q.unit.startsWith("$") ? `<u>${esc(q.unit)}</u>` : ""}
        <em>${arrow} ${chg} (${q.changePct > 0 ? "+" : ""}${q.changePct}%)</em>
      </span>`;
  };
  const row = quotes.map(cell).join("");
  track.innerHTML = row + row; // duplicated for a seamless loop
  track.style.animationDuration = Math.max(28, quotes.length * 6) + "s";
}

function renderTopics() {
  const track = $("#tickerTrack");
  const topics = (FEED && FEED.topics && FEED.topics.length ? FEED.topics : ["Live desk", "Markets", "Policy", "Global"]);
  const row = topics.map((t) => `<span>${esc(t)}</span>`).join("");
  track.innerHTML = row + row;
}

/* ------------------------------------------------------------------ mount */
function renderAll() {
  const byId = Object.fromEntries((FEED.sections || []).map((s) => [s.id, s]));
  $$("[data-render]").forEach((host) => {
    const section = byId[host.dataset.section];
    try {
      if (host.dataset.render === "columns") renderColumns(host);
      else if (!section) host.innerHTML = `<p class="empty-note">Section not in feed.</p>`;
      else if (host.dataset.render === "lead") renderLead(host, section);
      else if (host.dataset.render === "investigation") renderInvestigation(host, section);
      else if (host.dataset.render === "editorial") renderEditorial(host, section);
      else if (host.dataset.render === "front") renderFront(host, section);
      else if (host.dataset.render === "live") renderLive(host, section);
      else if (host.dataset.render === "podcasts") renderPodcasts(host, section);
    } catch (err) {
      host.innerHTML = `<p class="empty-note">Could not render this section.</p>`;
      console.error(host.dataset.section, err);
    }
  });

  renderMarkets();
  renderTopics();

  ALL_ITEMS = (FEED.sections || []).flatMap((s) => (s.items || []).map((i) => ({ ...i, section: s.title })));
  ITEM_INDEX.clear();
  ALL_ITEMS.forEach((item) => { if (item.id && !ITEM_INDEX.has(item.id)) ITEM_INDEX.set(item.id, item); });

  const when = FEED.generated_at ? new Date(FEED.generated_at) : null;
  $("#feedStatus").textContent = when
    ? `Updated ${timeAgo(FEED.generated_at).toLowerCase()} · ${ALL_ITEMS.length} stories`
    : "Sample data — run fetch_news.py for live stories";
  $("#sourceStrip").textContent = "Headlines and preview images belong to their publishers: "
    + ((FEED.sources || []).join(" · ")) + ". NNN links every story back to its source.";

  bindCards();
  paintSavedCount();
  renderSaved();
}

function bindCards() {
  $$(".save-button").forEach((button) => button.addEventListener("click", (e) => {
    e.preventDefault(); e.stopPropagation();
    const item = ITEM_INDEX.get(button.dataset.id);
    const saved = toggleSave(item);
    button.classList.toggle("saved", saved);
    button.textContent = saved ? "✓" : "＋";
    showToast(saved ? `${saveVerb(item)} — it's in your list` : "Removed from your list");
  }));
  syncSaveButtons();
}

/* ------------------------------------------------------------------ feed IO */
async function loadFeed({ silent = false } = {}) {
  if (!silent) $("#feedStatus").textContent = "Loading live feed…";
  try {
    const res = await fetch(`${FEED_URL}?t=${Date.now()}`, { cache: "no-store" });
    if (!res.ok) throw new Error(res.status);
    FEED = await res.json();
  } catch (err) {
    if (window.NNN_FEED) {
      FEED = window.NNN_FEED;          // bundled copy — works from disk
    } else if (!FEED) {
      FEED = { sections: [], markets: [], topics: [], sources: [] };
      $("#feedStatus").textContent = "Feed not found — run fetch_news.py, then reload";
    } else return;
  }
  renderAll();

  openFromHash();
}

/* ------------------------------------------------------------------- chrome */
const toast = $("#toast");
let toastTimer;
function showToast(message) {
  toast.textContent = message;
  toast.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("show"), 2600);
}

const menuToggle = $("#menuToggle");
const mobileNav = $("#mobileNav");
menuToggle.addEventListener("click", () => {
  const open = mobileNav.classList.toggle("open");
  menuToggle.setAttribute("aria-expanded", open);
  menuToggle.textContent = open ? "Close" : "Menu";
});
$$(".mobile-nav a").forEach((link) => link.addEventListener("click", () => {
  mobileNav.classList.remove("open");
  menuToggle.setAttribute("aria-expanded", "false");
  menuToggle.textContent = "Menu";
}));

/* ------------------------------------------------------------------ reader
   Stories open inside NNN. We show the publisher's own preview image and the
   summary they syndicate, credited to them, and send the reader on to their
   site for the full report — the article text stays theirs. */
const reader = $("#reader");
const readerBody = $("#readerBody");
const readerSection = $("#readerSection");
let lastFocus = null;

function readerCard(item) {
  const full = readsInFull(item);
  const fallback = generatedArt(item.title || item.id || "nnn", item.source);
  // CC BY covers Global Voices' words, not their photographs — draw our own
  const src = (item.licence === "cc-by" ? null : item.image) || fallback;
  const usingOwnArt = src === fallback;

  const norm = (t) => String(t || "").toLowerCase().replace(/[^a-z0-9]+/g, "");
  const echoed = item.summary && norm(item.summary).startsWith(norm(item.title).slice(0, 60));

  // For a linkout story the publisher's own syndicated summary is shown in
  // full — NNN no longer trims it — followed by the facts and by what other
  // newsrooms filed on the same event. That is a substantial read without
  // reproducing anybody's article.
  const body = full
    ? (item.body || []).map((block) => block.kind === "h"
        ? `<h3>${esc(block.text)}</h3>`
        : `<p>${esc(block.text)}</p>`).join("")
    : `<p class="reader-summary">${esc((!echoed && item.summary)
        || `${item.source || "This publisher"} syndicates this one as a headline only.`)}</p>`;

  const figures = (item.figures || []).length ? `
    <aside class="fact-box">
      <p class="fact-head">What we know</p>
      <ul>${item.figures.map((f) => `<li>${esc(f)}</li>`).join("")}</ul>
    </aside>` : "";

  const also = (item.also || []).length ? `
    <section class="also-box">
      <p class="also-head">The same story, ${item.also.length + 1} newsrooms</p>
      <ol class="also-list">
        <li class="also-lead">
          <b>${esc(item.source || "This report")}</b>
          <span>${esc(trimTo(item.summary, 200))}</span>
        </li>
        ${item.also.map((a) => `
          <li>
            <b>${esc(a.source)}</b>
            <a href="${esc(a.url)}" target="_blank" rel="noopener noreferrer nofollow">${esc(a.title)}</a>
            ${a.summary ? `<span>${esc(trimTo(a.summary, 200))}</span>` : ""}
          </li>`).join("")}
      </ol>
      <p class="also-note">Each line is that newsroom's own headline and summary, linking to their report.</p>
    </section>` : "";

  const upsc = item.upsc ? `
    <aside class="upsc-note">
      <p class="upsc-head">Why this matters for UPSC</p>
      <ul class="upsc-papers">${(item.upsc.papers || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
      ${(item.upsc.terms || []).length
        ? `<p class="upsc-terms"><b>Look up:</b> ${(item.upsc.terms).map((t) => esc(t)).join(" · ")}</p>` : ""}
      <p class="upsc-prompt">${esc(item.upsc.prompt || "")}</p>
    </aside>` : "";

  return `
    <figure class="reader-figure">
      <img src="${esc(src)}" alt="" referrerpolicy="no-referrer" decoding="async"
           onerror="this.onerror=null;this.src='${fallback}';const c=this.closest('figure')?.querySelector('figcaption');if(c)c.textContent='NNN illustration'" />
      <figcaption>${esc(usingOwnArt ? "NNN illustration" : `Picture: ${item.source || "Source"}`)}</figcaption>
    </figure>
    <h2 id="readerTitle">${esc(item.title)}</h2>
    <p class="reader-byline">${esc(bylineOf(item))}<span>•</span>${esc(timeAgo(item.published))}<span>•</span>${readMins(item)} min read</p>
    ${full ? `<b class="licence-tag">${esc(LICENCE_LABEL[item.licence] || "Open licence")}</b>` : ""}
    <div class="reader-text${full ? " is-full" : ""}">${body}</div>
    ${figures}
    ${also}
    ${upsc}
    <div class="reader-actions">
      ${full
        ? `<button class="button button-dark reader-save" data-id="${esc(item.id)}" type="button">Save story <span>+</span></button>
           ${item.licence === "nnn" ? "" :
             `<a class="button button-quiet" href="${esc(item.url)}" target="_blank" rel="noopener noreferrer">View the original</a>`}`
        : `<a class="button button-dark" href="${esc(item.url)}" target="_blank" rel="noopener noreferrer nofollow">
             Read the full report at ${esc(item.source || "the source")} <span>→</span></a>
           <button class="button button-quiet reader-save" data-id="${esc(item.id)}" type="button">Save story</button>`}
    </div>
    <p class="reader-credit">${licenceNote(item)}</p>`;
}

function openReader(id, { push = true } = {}) {
  const item = ITEM_INDEX.get(id);
  if (!item) return false;
  lastFocus = document.activeElement;
  readerSection.textContent = item.section || item.source || "Story";
  readerBody.innerHTML = readerCard(item);
  reader.hidden = false;
  document.body.classList.add("reader-open");
  requestAnimationFrame(() => {
    reader.classList.add("open");
    $(".reader-panel", reader).focus();
  });
  readerBody.scrollTop = 0;
  const saveButton = $(".reader-save", readerBody);
  if (saveButton) {
    const already = isSaved(item.id);
    saveButton.classList.toggle("saved", already);
    saveButton.textContent = already ? "✓ Saved" : saveVerb(item);
    saveButton.addEventListener("click", () => {
      const on = toggleSave(item);
      saveButton.classList.toggle("saved", on);
      saveButton.textContent = on ? "✓ Saved" : saveVerb(item);
      showToast(on ? `${saveVerb(item)} — it's in your list` : "Removed from your list");
      syncSaveButtons();
    });
  }
  learn(item, 1);          // opening it is a mild vote for more like it
  // a full read starts at the top of the article, not mid-scroll
  $(".reader-panel", reader).scrollTop = 0;
  if (push) history.pushState({ nnnStory: id }, "", `#story-${id}`);
  return true;
}

function closeReader({ pop = true } = {}) {
  if (reader.hidden) return;
  reader.classList.remove("open");
  document.body.classList.remove("reader-open");
  setTimeout(() => { reader.hidden = true; readerBody.innerHTML = ""; }, 220);
  lastFocus?.focus?.();
  if (pop && history.state && history.state.nnnStory) history.back();
}

// one delegated listener covers every card, now and after each refresh
document.addEventListener("click", (event) => {
  const link = event.target.closest?.(".story-link");
  if (!link) return;
  const item = ITEM_INDEX.get(link.dataset.story);
  // an episode or a stream belongs on the publisher's player, not in a panel
  if (item && (item.kind === "podcast" || item.kind === "stream")) return;
  // let people open the source in a new tab the usual ways
  if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) return;
  if (openReader(link.dataset.story)) event.preventDefault();
});

reader.addEventListener("click", (event) => { if (event.target.closest("[data-close]")) closeReader(); });
$("#readerClose").addEventListener("click", () => closeReader());
window.addEventListener("popstate", () => { if (!reader.hidden) closeReader({ pop: false }); });

// a #story-… link pasted into the address bar, or followed from elsewhere
function openFromHash({ push = false } = {}) {
  const id = (location.hash.match(/^#story-([a-z0-9]+)$/i) || [])[1];
  if (id && reader.hidden) return openReader(id, { push });
  return false;
}
window.addEventListener("hashchange", () => openFromHash());

const searchPanel = $("#searchPanel");
const searchInput = $("#searchInput");
const searchResults = $("#searchResults");
function closeSearch() { searchPanel.classList.remove("open"); searchInput.value = ""; searchResults.innerHTML = ""; }
$("#searchToggle").addEventListener("click", () => {
  const isOpen = searchPanel.classList.toggle("open");
  if (isOpen) setTimeout(() => searchInput.focus(), 150); else closeSearch();
});
searchInput.addEventListener("input", (event) => {
  const query = event.target.value.trim().toLowerCase();
  if (!query) { searchResults.innerHTML = ""; return; }
  const matches = ALL_ITEMS.filter((i) =>
    (i.title + " " + (i.summary || "") + " " + (i.source || "") + " " + i.section).toLowerCase().includes(query)
  ).slice(0, 6);
  searchResults.innerHTML = matches.length
    ? matches.map((i) => `<div class="search-result"><a ${linkAttrs(i)}>${esc(i.title)}</a> <b>${esc(i.section)}</b></div>`).join("")
    : `<div class="search-result">Nothing matching yet. Try <b>policy</b>, <b>gold</b> or <b>global</b>.</div>`;
});
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  if (prefsDialog && !prefsDialog.hidden) closePrefs();
  else if (savedPanel && !savedPanel.hidden) closeSaved();
  else if (!reader.hidden) closeReader();
  else closeSearch();
});

$("#filterButton").addEventListener("click", () => showToast("Showing the latest across business, tech, sports, culture and health."));
$("#marketRefresh").addEventListener("click", () => { showToast("Refreshing live data…"); loadFeed({ silent: true }); });
/* ---------------------------------------------------------------- sign-up
   A static site cannot send email by itself. Point NEWSLETTER_ENDPOINT at a
   form/mail service (Formspree, Buttondown, Mailchimp, or your own handler) and
   configure the welcome email there — the README has the copy. Until it is set,
   the form says so plainly rather than pretending a message was sent. */
const NEWSLETTER_ENDPOINT = "";     // ← set this
const SITE_URL = location.origin && location.origin !== "null" ? location.origin : "https://nnn.example";

const form = $("#newsletterForm");
const formMessage = $("#formMessage");

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const email = $("#email").value.trim();
  if (!email) return;

  if (!NEWSLETTER_ENDPOINT) {
    formMessage.textContent = "Sign-up isn’t connected yet — please check back shortly.";
    console.warn("[NNN] NEWSLETTER_ENDPOINT is empty in app.js, so %s was not sent anywhere. "
      + "See 'Sign-ups and the welcome email' in the README.", email);
    return;
  }

  const prefs = getPrefs();
  formMessage.textContent = "Signing you up…";
  try {
    const res = await fetch(NEWSLETTER_ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({
        email,
        site: SITE_URL,
        topics: Object.keys(prefs.cats),
        signed_up_at: new Date().toISOString(),
      }),
    });
    if (!res.ok) throw new Error(res.status);
    formMessage.textContent = "You’re on the list — the welcome note is on its way.";
    const next = getPrefs();
    next.email = email;
    next.signedUpAt = new Date().toISOString();
    writeStore(PREFS_KEY, next);
    form.reset();
    openPrefs({ firstRun: true });        // now ask what they actually want
  } catch (err) {
    formMessage.textContent = "That didn’t go through. Please try again in a moment.";
    console.error("[NNN] sign-up failed", err);
  }
});

/* ------------------------------------------------------- preference chooser */
const prefsDialog = $("#prefsDialog");

function openPrefs({ firstRun = false } = {}) {
  if (!prefsDialog) return;
  const prefs = getPrefs();
  const cats = [...new Set(ALL_ITEMS.map((i) => i.category)
    .filter((c) => c && c !== "general"))];
  const labels = Object.fromEntries(ALL_ITEMS.filter((i) => i.category)
    .map((i) => [i.category, i.category_label || i.category]));
  $("#prefsIntro").textContent = firstRun
    ? "One quick thing — pick what you actually want to read. The front page reorders itself around it."
    : "Pick what you want more of. You can change this whenever you like.";
  $("#prefsChips").innerHTML = cats.map((c) => `
    <label class="pref-chip${prefs.cats[c] ? " on" : ""}">
      <input type="checkbox" value="${esc(c)}" ${prefs.cats[c] ? "checked" : ""} />
      ${esc(labels[c] || c)}
    </label>`).join("") || `<p class="empty-note">Categories appear once the feed loads.</p>`;
  $$("#prefsChips input").forEach((box) => box.addEventListener("change", () =>
    box.closest(".pref-chip").classList.toggle("on", box.checked)));
  prefsDialog.hidden = false;
  requestAnimationFrame(() => prefsDialog.classList.add("open"));
}

function closePrefs() {
  prefsDialog.classList.remove("open");
  setTimeout(() => { prefsDialog.hidden = true; }, 200);
}

$("#prefsSave")?.addEventListener("click", () => {
  const prefs = getPrefs();
  const chosen = $$("#prefsChips input:checked").map((b) => b.value);
  // a deliberate pick outranks anything we inferred, so seed it high
  chosen.forEach((c) => { prefs.cats[c] = Math.max(prefs.cats[c] || 0, 8); });
  Object.keys(prefs.cats).forEach((c) => { if (!chosen.includes(c)) delete prefs.cats[c]; });
  prefs.forYou = chosen.length > 0;
  writeStore(PREFS_KEY, prefs);
  closePrefs();
  renderAll();
  showToast(chosen.length ? "Your feed is reordered around that" : "Showing the standard front page");
});
$("#prefsSkip")?.addEventListener("click", closePrefs);
$("#prefsDialog")?.addEventListener("click", (e) => { if (e.target.closest("[data-close]")) closePrefs(); });
$("#personaliseButton").addEventListener("click", () => openPrefs());

$("#forYouToggle")?.addEventListener("click", () => {
  const prefs = getPrefs();
  if (!hasPrefs()) return openPrefs({ firstRun: true });
  prefs.forYou = !prefs.forYou;
  writeStore(PREFS_KEY, prefs);
  renderAll();
});

/* ------------------------------------------------------------ saved drawer */
const savedPanel = $("#savedPanel");
$("#savedToggle")?.addEventListener("click", () => {
  renderSaved();
  savedPanel.hidden = false;
  requestAnimationFrame(() => savedPanel.classList.add("open"));
});
function closeSaved() {
  savedPanel.classList.remove("open");
  setTimeout(() => { savedPanel.hidden = true; }, 200);
}
$("#savedClose")?.addEventListener("click", closeSaved);
savedPanel?.addEventListener("click", (e) => { if (e.target.closest("[data-close]")) closeSaved(); });
paintSavedCount();

$("#todayDate").textContent = new Date().toLocaleDateString("en-IN",
  { weekday: "long", month: "long", day: "numeric" });

loadFeed();
setInterval(() => loadFeed({ silent: true }), REFRESH_MS);
