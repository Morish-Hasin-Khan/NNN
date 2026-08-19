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
  // a generated illustration has nothing to hide — only real photos get the blur
  const sensitive = !!item.sensitive && !!item.image && !sessionStorage.getItem("nnn-reveal-all");
  return `<div class="image-block ${className} ${tall ? "image-lead" : ""} ${sensitive ? "is-sensitive" : ""}">
      <img class="story-img" src="${esc(src)}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer"
           onerror="this.onerror=null;this.src='${fallback}';this.dataset.generated='1';this.closest('.image-block')?.classList.add('generated')" />
      ${label && !sensitive ? `<b class="image-tag">${esc(label)}</b>` : ""}
      <i class="image-credit">${esc(item.image ? (item.source || "Source") : "NNN illustration")}</i>
      ${sensitive ? `<button class="reveal" type="button">
          <i class="reveal-icon" aria-hidden="true">◉</i>
          <em class="reveal-text">Sensitive image — tap to view</em>
        </button>` : ""}
    </div>`;
}

/* ------------------------------------------------------------- renderers */
function renderLead(host, section) {
  const items = section.items || [];
  if (!items.length) { host.innerHTML = `<p class="empty-note">No stories in this section yet.</p>`; return; }
  const [lead, ...rest] = items;
  const side = rest.slice(0, 3);

  host.innerHTML = `
    <article class="lead-story story-card" data-topic="${esc((section.title + " " + lead.title).toLowerCase())}">
      <a class="image-link" href="${esc(lead.url)}" target="_blank" rel="noopener noreferrer nofollow">
        ${imageBlock(lead, { tall: true, label: section.title })}
      </a>
      <div class="story-copy">
        <p class="story-kicker">${esc(lead.source || section.title)} <span>•</span> ${readMins(lead)} min read</p>
        <h3><a href="${esc(lead.url)}" target="_blank" rel="noopener noreferrer nofollow">${esc(lead.title)}</a></h3>
        <p>${esc(lead.summary || "Follow the story at the source for the full report and the latest confirmed detail.")}</p>
        <div class="story-footer"><span>${esc(timeAgo(lead.published))}</span>
          <button class="save-button" data-id="${esc(lead.id)}" aria-label="Save story">＋</button></div>
      </div>
    </article>
    <div class="side-stories">
      ${side.map((item) => `
        <article class="compact-story" data-topic="${esc((section.title + " " + item.title).toLowerCase())}">
          <a class="image-link" href="${esc(item.url)}" target="_blank" rel="noopener noreferrer nofollow">${imageBlock(item)}</a>
          <div>
            <p class="story-kicker">${esc(item.source || section.title)}</p>
            <h3><a href="${esc(item.url)}" target="_blank" rel="noopener noreferrer nofollow">${esc(item.title)}</a></h3>
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
      <p class="story-kicker"><i class="dot inquiry"></i> ${esc(lead.source || "Public interest")}</p>
      <h3><a href="${esc(lead.url)}" target="_blank" rel="noopener noreferrer nofollow">${esc(lead.title)}</a></h3>
      <p>${esc(lead.summary || "We separate what is verified, what officials have said, and what remains under investigation.")}</p>
      <div class="case-tags"><span>${esc(timeAgo(lead.published))}</span><span>${esc(lead.source || "Source")}</span><span>${rest.length} related</span></div>
      <a class="button button-dark" href="${esc(lead.url)}" target="_blank" rel="noopener noreferrer nofollow">Open case file <span>→</span></a>
    </article>
    <div class="case-list">
      ${rest.slice(0, 3).map((item, i) => `
        <article class="case-row" data-topic="${esc(("scandals " + item.title).toLowerCase())}">
          <div>
            <p class="story-kicker"><i class="dot ${states[i % 3][0]}"></i> ${esc(states[i % 3][1])}</p>
            <h3><a href="${esc(item.url)}" target="_blank" rel="noopener noreferrer nofollow">${esc(item.title)}</a></h3>
            <p>${esc(item.source || "")} · ${esc(timeAgo(item.published))}</p>
          </div>
          <button class="save-button" data-id="${esc(item.id)}" aria-label="Save story">＋</button>
        </article>`).join("")}
    </div>`;
}

function renderSexEd(host, section) {
  const items = section.items || [];
  if (!items.length) { host.innerHTML = `<p class="empty-note">No explainers loaded yet.</p>`; return; }
  const [lead, ...rest] = items;
  host.innerHTML = `
    <article class="sex-lead" data-topic="${esc(("sex education " + lead.title).toLowerCase())}">
      <p class="story-kicker">${esc(lead.source || "Sexual health")}</p>
      <h3><a href="${esc(lead.url)}" target="_blank" rel="noopener noreferrer nofollow">${esc(lead.title)}</a></h3>
      <p>${esc(lead.summary || "Clear, respectful information from the reporting source.")}</p>
      <a href="${esc(lead.url)}" target="_blank" rel="noopener noreferrer nofollow">Read explainer <span>→</span></a>
    </article>
    ${rest.slice(0, 2).map((item) => `
      <article class="simple-story" data-topic="${esc(("sex education " + item.title).toLowerCase())}">
        <p class="story-kicker">${esc(item.source || "Explainer")}</p>
        <h3><a href="${esc(item.url)}" target="_blank" rel="noopener noreferrer nofollow">${esc(item.title)}</a></h3>
        <span>${esc(timeAgo(item.published))}</span>
      </article>`).join("")}`;
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
        <p class="story-kicker">${esc(item.kicker)} <span>•</span> ${esc(item.source || "")}</p>
        <h3><a href="${esc(item.url)}" target="_blank" rel="noopener noreferrer nofollow">${esc(item.title)}</a></h3>
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
      else if (host.dataset.render === "sexed") renderSexEd(host, section);
    } catch (err) {
      host.innerHTML = `<p class="empty-note">Could not render this section.</p>`;
      console.error(host.dataset.section, err);
    }
  });

  renderMarkets();
  renderTopics();

  ALL_ITEMS = (FEED.sections || []).flatMap((s) => (s.items || []).map((i) => ({ ...i, section: s.title })));

  const when = FEED.generated_at ? new Date(FEED.generated_at) : null;
  $("#feedStatus").textContent = when
    ? `Updated ${timeAgo(FEED.generated_at).toLowerCase()} · ${ALL_ITEMS.length} stories`
    : "Sample data — run fetch_news.py for live stories";
  $("#sourceStrip").textContent = "Headlines and preview images belong to their publishers: "
    + ((FEED.sources || []).join(" · ")) + ". NNN links every story back to its source.";

  bindCards();
}

function bindCards() {
  $$(".save-button").forEach((button) => button.addEventListener("click", (e) => {
    e.preventDefault(); e.stopPropagation();
    const saved = button.classList.toggle("saved");
    button.textContent = saved ? "✓" : "＋";
    showToast(saved ? "Saved to your reading list" : "Removed from your reading list");
  }));

  $$(".reveal").forEach((button) => button.addEventListener("click", (e) => {
    e.preventDefault(); e.stopPropagation();
    button.closest(".image-block").classList.remove("is-sensitive");
    button.remove();
  }));
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

const ageNotice = $("#ageNotice");
if (sessionStorage.getItem("nnn-age-confirmed")) ageNotice.classList.add("hidden");
$("#enterSite").addEventListener("click", () => {
  sessionStorage.setItem("nnn-age-confirmed", "true");
  ageNotice.classList.add("hidden");
});
$("#leaveSite").addEventListener("click", () => {
  ageNotice.querySelector(".age-card").innerHTML = `
    <p class="eyebrow">NNN</p><h1>Thanks for visiting.</h1>
    <p>Please come back when the site is appropriate for you.</p>
    <div class="age-actions"><button class="button button-primary" id="returnButton">Return to notice</button></div>`;
  $("#returnButton").addEventListener("click", () => location.reload());
});

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
    ? matches.map((i) => `<div class="search-result"><a href="${esc(i.url)}" target="_blank" rel="noopener noreferrer nofollow">${esc(i.title)}</a> <b>${esc(i.section)}</b></div>`).join("")
    : `<div class="search-result">Nothing matching yet. Try <b>policy</b>, <b>gold</b> or <b>global</b>.</div>`;
});
document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeSearch(); });

$$(".chip").forEach((chip) => chip.addEventListener("click", () => {
  $$(".chip").forEach((item) => item.classList.remove("active"));
  chip.classList.add("active");
  const term = chip.textContent.trim().toLowerCase();
  const host = $('[data-section="adult"]');
  if (term.startsWith("all")) { renderLead(host, (FEED.sections || []).find((s) => s.id === "adult") || { items: [] }); bindCards(); }
  else {
    const sec = (FEED.sections || []).find((s) => s.id === "adult");
    const filtered = (sec?.items || []).filter((i) => (i.title + " " + (i.summary || "")).toLowerCase().includes(term.split(" ")[0]));
    renderLead(host, { title: "Adult Industry", items: filtered.length ? filtered : sec?.items || [] });
    bindCards();
    if (!filtered.length) showToast(`No ${chip.textContent} stories in this refresh`);
  }
}));

$("#personaliseButton").addEventListener("click", () => showToast("Personalisation is ready to connect when accounts are added."));
$("#filterButton").addEventListener("click", () => showToast("Showing the latest across business, tech, sports, culture and health."));
$("#marketRefresh").addEventListener("click", () => { showToast("Refreshing live data…"); loadFeed({ silent: true }); });
$("#newsletterForm").addEventListener("submit", (event) => {
  event.preventDefault();
  $("#formMessage").textContent = "You’re on the list. Watch your inbox for the first NNN briefing.";
  event.currentTarget.reset();
});

$("#todayDate").textContent = new Date().toLocaleDateString("en-IN",
  { weekday: "long", month: "long", day: "numeric" });

loadFeed();
setInterval(() => loadFeed({ silent: true }), REFRESH_MS);
