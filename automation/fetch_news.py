#!/usr/bin/env python3
"""
NNN feed builder
================
Pulls publicly available headlines + market data and writes data/feed.json,
which the front end reads. Run it on a schedule (GitHub Action, cron, or any
host) and the site updates itself.

    python3 fetch_news.py            # normal run
    python3 fetch_news.py --quiet    # less logging
    python3 fetch_news.py --limit 4  # fewer stories per section

Only headline, timestamp, source name, link and the publisher's own preview
image are stored. Every card links back to the original article.
"""

import argparse, hashlib, html, json, os, re, sys, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree as ET

import requests
from bs4 import BeautifulSoup

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT,"..", "data")
FEED_PATH = os.path.join(DATA_DIR, "feed.json")
CACHE_PATH = os.path.join(DATA_DIR, ".image-cache.json")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
}
TIMEOUT = 20
NS = {"media": "http://search.yahoo.com/mrss/",
      "content": "http://purl.org/rss/1.0/modules/content/",
      "atom": "http://www.w3.org/2005/Atom",
      "dc": "http://purl.org/dc/elements/1.1/"}

session = requests.Session()
session.headers.update(HEADERS)
LOG = True


def log(*a):
    if LOG:
        print(*a, file=sys.stderr, flush=True)


def get(url, **kw):
    kw.setdefault("timeout", TIMEOUT)
    for attempt in (1, 2):
        try:
            r = session.get(url, **kw)
            if r.status_code == 200:
                return r
            log(f"  ! {r.status_code} {url}")
        except Exception as exc:
            log(f"  ! {type(exc).__name__} {url}")
        if attempt == 1:
            time.sleep(1.5)
    return None


# ---------------------------------------------------------------- utilities
def clean(text):
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def norm_key(title):
    return re.sub(r"[^a-z0-9]+", "", (title or "").lower())[:70]


def to_iso(value):
    if not value:
        return None
    value = value.strip()
    try:
        return parsedate_to_datetime(value).astimezone(timezone.utc).isoformat()
    except Exception:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d",
                "%d %b %Y", "%B %d, %Y", "%d/%m/%Y", "%b %d, %Y"):
        try:
            dt = datetime.strptime(value[:len(datetime.now().strftime(fmt)) + 6].strip(), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).isoformat()
        except Exception:
            continue
    return None


def domain_of(url):
    try:
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""


# ------------------------------------------------------------ image lookup
_image_cache = {}


def load_cache():
    global _image_cache
    try:
        with open(CACHE_PATH) as fh:
            _image_cache = json.load(fh)
    except Exception:
        _image_cache = {}


def save_cache():
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        trimmed = dict(list(_image_cache.items())[-1200:])
        with open(CACHE_PATH, "w") as fh:
            json.dump(trimmed, fh)
    except Exception:
        pass


BAD_IMAGE = re.compile(r"(logo|sprite|placeholder|blank|default|avatar|1x1|pixel|icon)", re.I)


def usable(src):
    if not src or not src.startswith("http"):
        return False
    if BAD_IMAGE.search(src):
        return False
    return re.search(r"\.(jpe?g|png|webp|avif)(\?|$)", src, re.I) is not None or "image" in src


def scrape_og_image(url):
    """Fetch an article page and read its own social preview image."""
    if not url:
        return None
    if url in _image_cache:
        return _image_cache[url] or None
    found = None
    r = get(url)
    if r is not None:
        soup = BeautifulSoup(r.text, "html.parser")
        for sel, attr in (('meta[property="og:image"]', "content"),
                          ('meta[name="og:image"]', "content"),
                          ('meta[name="twitter:image"]', "content"),
                          ('meta[property="twitter:image"]', "content"),
                          ('link[rel="image_src"]', "href")):
            tag = soup.select_one(sel)
            if tag and tag.get(attr):
                cand = urljoin(url, tag[attr].strip())
                if usable(cand):
                    found = cand
                    break
        if not found:
            for img in soup.select("article img, .article img, .story img, main img, img"):
                cand = img.get("data-src") or img.get("data-lazy-src") or img.get("src")
                if cand:
                    cand = urljoin(url, cand.strip())
                    if usable(cand):
                        found = cand
                        break
    _image_cache[url] = found or ""
    return found


# --------------------------------------------------------------- RSS parser
def parse_rss(url, source_name=None, limit=12):
    r = get(url)
    if r is None:
        return []
    try:
        root = ET.fromstring(r.content)
    except Exception:
        log(f"  ! unparseable xml {url}")
        return []

    items = root.findall(".//item") or root.findall(".//atom:entry", NS)
    out = []
    for node in items[:limit]:
        def text(tag):
            el = node.find(tag, NS) if ":" in tag else node.find(tag)
            return (el.text or "").strip() if el is not None and el.text else ""

        title = clean(text("title"))
        if not title:
            continue
        link = text("link")
        if not link:
            el = node.find("atom:link", NS)
            if el is not None:
                link = el.get("href", "")
        summary = clean(text("description") or text("atom:summary"))
        published = to_iso(text("pubDate") or text("dc:date") or text("atom:published") or text("atom:updated"))

        image = None
        for path, attr in (("media:content", "url"), ("media:thumbnail", "url"), ("enclosure", "url")):
            el = node.find(path, NS) if ":" in path else node.find(path)
            if el is not None and usable(el.get(attr, "")):
                image = el.get(attr)
                break
        if not image:
            blob = (text("content:encoded") or text("description") or "")
            m = re.search(r'<img[^>]+src=["\']([^"\']+)', blob)
            if m and usable(m.group(1)):
                image = m.group(1)

        out.append({
            "title": title,
            "url": link.strip(),
            "summary": summary[:240],
            "published": published,
            "source": source_name or domain_of(link) or domain_of(url),
            "image": image,
        })
    return out


# ------------------------------------------------------- bespoke scrapers
def scrape_pib(url="https://www.pib.gov.in/Allrel.aspx?reg=48&lang=1", limit=12):
    """Press Information Bureau — Government of India releases (public domain)."""
    r = get(url)
    if r is None:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    out, seen = [], set()
    ministry = ""
    for node in soup.select("div.content-area li, div.content-area h3, .num, li a, h3"):
        if node.name == "h3":
            ministry = clean(node.get_text())
            continue
        a = node if node.name == "a" else node.find("a")
        if not a or not a.get("href"):
            continue
        href = urljoin("https://www.pib.gov.in/", a["href"])
        if "PressRelea" not in href and "PRID" not in href:
            continue
        title = clean(a.get_text())
        if len(title) < 25 or href in seen:
            continue
        seen.add(href)
        out.append({"title": title, "url": href, "summary": "",
                    "published": datetime.now(timezone.utc).isoformat(),
                    "source": ministry or "Press Information Bureau", "image": None})
        if len(out) >= limit:
            break
    return out


def scrape_mea(url="https://www.mea.gov.in/press-releases.htm", limit=10):
    """Ministry of External Affairs press releases."""
    r = get(url, headers={**HEADERS, "Referer": "https://www.mea.gov.in/"})
    if r is None:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    out, seen = [], set()
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        if "dtl" not in href and "press-releases.htm?" not in href:
            continue
        title = clean(a.get_text())
        if len(title) < 25:
            continue
        full = urljoin(url, href)
        if full in seen:
            continue
        seen.add(full)
        date = ""
        holder = a.find_parent(["li", "div", "tr"])
        if holder:
            m = re.search(r"(\w+ \d{1,2}, \d{4}|\d{1,2} \w+ \d{4})", holder.get_text())
            if m:
                date = m.group(1)
        out.append({"title": title, "url": full, "summary": "",
                    "published": to_iso(date) or datetime.now(timezone.utc).isoformat(),
                    "source": "Ministry of External Affairs", "image": None})
        if len(out) >= limit:
            break
    return out


def scrape_avn(path="/news", limit=10):
    """AVN — adult industry trade press. Headline + publisher preview only."""
    url = urljoin("https://avn.com/", path)
    r = get(url)
    if r is None:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    out, seen = [], set()
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        if not re.search(r"/(news|business|gay|pleasure)/[a-z0-9\-/]+-\d{5,}$", href):
            continue
        full = urljoin("https://avn.com/", href)
        title = clean(a.get_text())
        if len(title) < 20 or full in seen:
            continue
        seen.add(full)
        image = None
        card = a.find_parent(["article", "div", "li"])
        if card:
            img = card.find("img")
            if img:
                cand = img.get("data-src") or img.get("src") or ""
                cand = urljoin("https://avn.com/", cand)
                if usable(cand):
                    image = cand
        out.append({"title": title, "url": full, "summary": "",
                    "published": datetime.now(timezone.utc).isoformat(),
                    "source": "AVN", "image": image, "sensitive": True})
        if len(out) >= limit:
            break
    return out


def google_news(query, limit=10, label=None):
    """Google News RSS — aggregates publicly indexed headlines."""
    q = requests.utils.quote(query)
    url = f"https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"
    items = parse_rss(url, limit=limit)
    for it in items:
        # Google appends " - Publisher" to titles; split it back out.
        m = re.match(r"^(.*) - ([^-]{2,40})$", it["title"])
        if m:
            it["title"], it["source"] = m.group(1).strip(), m.group(2).strip()
        elif label:
            it["source"] = label
    return items


# ------------------------------------------------------------------ sections
SECTIONS = [
    {
        "id": "adult", "eyebrow": "01 — Leading today", "title": "Adult Industry",
        "sensitive": True,
        "fetch": lambda: (scrape_avn("/news", 8) + scrape_avn("/business", 6)
                          + google_news("adult industry OR creator platform policy age verification", 6, "Google News")),
    },
    {
        "id": "scandals", "eyebrow": "02 — Public interest", "title": "Scandals & Investigations",
        "fetch": lambda: google_news("inquiry OR probe OR investigation OR audit government India", 8, "Google News"),
    },
    {
        "id": "hot-news", "eyebrow": "03 — Breaking now", "title": "Hot News",
        "fetch": lambda: (parse_rss("https://feeds.feedburner.com/ndtvnews-top-stories", "NDTV", 8)
                          + google_news("breaking news India", 6)),
    },
    {
        "id": "global-news", "eyebrow": "04 — Beyond borders", "title": "Global News",
        "fetch": lambda: (parse_rss("https://feeds.bbci.co.uk/news/world/rss.xml", "BBC News", 8)
                          + parse_rss("https://feeds.feedburner.com/ndtvnews-world-news", "NDTV", 6)),
    },
    {
        "id": "global-hotline", "eyebrow": "05 — Essential global read", "title": "Global Hotline",
        "fetch": lambda: (scrape_mea(limit=8)
                          + parse_rss("https://feeds.bbci.co.uk/news/world/asia/rss.xml", "BBC News", 6)),
    },
    {
        "id": "national-news", "eyebrow": "06 — Across the country", "title": "National News",
        "fetch": lambda: (scrape_pib(limit=10)
                          + parse_rss("https://feeds.feedburner.com/ndtvnews-india-news", "NDTV", 6)),
    },
    {
        "id": "national-hotlines", "eyebrow": "07 — Essential national read", "title": "National Hotlines",
        "fetch": lambda: (parse_rss("https://www.thehindu.com/news/national/feeder/default.rss", "The Hindu", 8)
                          + google_news("India top headlines today", 6)),
    },
    {
        "id": "upsc", "eyebrow": "08 — Study-ready every day", "title": "UPSC Current Affairs",
        "fetch": lambda: (scrape_pib("https://www.pib.gov.in/Allrel.aspx?reg=3&lang=1", 8)
                          + google_news("UPSC current affairs policy scheme economy India explained", 6, "Google News")),
    },
    {
        "id": "sex-ed", "eyebrow": "09 — Clear, caring information", "title": "Sex Ed & Relationships",
        "fetch": lambda: google_news("sexual health education consent relationships research", 8, "Google News"),
    },
    {
        "id": "business", "eyebrow": "Business", "title": "Business",
        "fetch": lambda: parse_rss("https://feeds.bbci.co.uk/news/business/rss.xml", "BBC News", 6),
    },
    {
        "id": "technology", "eyebrow": "Technology", "title": "Technology",
        "fetch": lambda: parse_rss("https://feeds.bbci.co.uk/news/technology/rss.xml", "BBC News", 6),
    },
    {
        "id": "sports", "eyebrow": "Sports", "title": "Sports",
        "fetch": lambda: parse_rss("https://feeds.feedburner.com/ndtvsports-latest", "NDTV Sports", 6),
    },
    {
        "id": "culture", "eyebrow": "Culture", "title": "Culture",
        "fetch": lambda: parse_rss("https://feeds.bbci.co.uk/news/entertainment_and_arts/rss.xml", "BBC News", 6),
    },
    {
        "id": "health", "eyebrow": "Health & Science", "title": "Health & Science",
        "fetch": lambda: parse_rss("https://feeds.bbci.co.uk/news/health/rss.xml", "BBC News", 6),
    },
]


# ------------------------------------------------------------------ markets
MARKETS = [
    {"key": "sensex",  "label": "SENSEX",    "symbol": "^BSESN",  "stooq": "^spx", "decimals": 2},
    {"key": "nifty",   "label": "NIFTY 50",  "symbol": "^NSEI",   "decimals": 2},
    {"key": "banknifty", "label": "BANK NIFTY", "symbol": "^NSEBANK", "decimals": 2},
    {"key": "gold",    "label": "GOLD",      "symbol": "GC=F",    "convert": "inr_10g", "unit": "₹/10g", "decimals": 0},
    {"key": "silver",  "label": "SILVER",    "symbol": "SI=F",    "convert": "inr_kg", "unit": "₹/kg", "decimals": 0},
    {"key": "usdinr",  "label": "USD/INR",   "symbol": "USDINR=X", "decimals": 2},
    {"key": "brent",   "label": "BRENT",     "symbol": "BZ=F",    "unit": "$/bbl", "decimals": 2},
    {"key": "btc",     "label": "BITCOIN",   "symbol": "BTC-USD", "unit": "$", "decimals": 0},
]


def yahoo_quote(symbol):
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
           f"{requests.utils.quote(symbol)}?range=5d&interval=1d")
    r = get(url, headers={**HEADERS, "Accept": "application/json"})
    if r is None:
        return None
    try:
        meta = r.json()["chart"]["result"][0]["meta"]
        price = meta.get("regularMarketPrice")
        prev = meta.get("chartPreviousClose") or meta.get("previousClose")
        if price is None:
            return None
        return float(price), float(prev) if prev else float(price)
    except Exception:
        return None


def stooq_quote(symbol):
    url = f"https://stooq.com/q/l/?s={symbol}&f=sd2t2ohlc&h&e=csv"
    r = get(url)
    if r is None or "N/D" in r.text:
        return None
    try:
        row = r.text.strip().splitlines()[1].split(",")
        close, open_ = float(row[-1]), float(row[-4])
        return close, open_
    except Exception:
        return None


def build_markets(previous=None):
    quotes, usdinr = {}, None
    for m in MARKETS:
        q = yahoo_quote(m["symbol"]) or (stooq_quote(m["stooq"]) if m.get("stooq") else None)
        quotes[m["key"]] = q
        if m["key"] == "usdinr" and q:
            usdinr = q[0]
    if not usdinr:
        usdinr = 87.0  # fallback so metals still render sensibly

    prev_map = {t["key"]: t for t in (previous or [])}
    out = []
    for m in MARKETS:
        q = quotes.get(m["key"])
        if not q:
            old = prev_map.get(m["key"])
            if old:
                out.append({**old, "stale": True})
            continue
        price, prev = q
        if m.get("convert") == "inr_10g":
            price, prev = price / 31.1035 * 10 * usdinr, prev / 31.1035 * 10 * usdinr
        elif m.get("convert") == "inr_kg":
            price, prev = price / 31.1035 * 1000 * usdinr, prev / 31.1035 * 1000 * usdinr
        change = price - prev
        out.append({
            "key": m["key"], "label": m["label"], "unit": m.get("unit", ""),
            "value": round(price, m["decimals"]),
            "change": round(change, m["decimals"]),
            "changePct": round((change / prev * 100) if prev else 0, 2),
            "decimals": m["decimals"],
        })
    return out


# --------------------------------------------------------------------- build
def dedupe(items):
    seen, out = set(), []
    for it in items:
        key = norm_key(it.get("title"))
        if not key or key in seen or not it.get("url"):
            continue
        seen.add(key)
        out.append(it)
    return out


def build_section(section, limit):
    try:
        raw = section["fetch"]() or []
    except Exception as exc:
        log(f"  ! {section['id']} failed: {type(exc).__name__}: {exc}")
        raw = []
    items = dedupe(raw)[: limit + 4]
    for it in items:
        it.setdefault("sensitive", bool(section.get("sensitive")))
        it["id"] = hashlib.sha1((it["url"] or it["title"]).encode()).hexdigest()[:12]
    log(f"  · {section['id']}: {len(items)} stories")
    return {"id": section["id"], "eyebrow": section["eyebrow"],
            "title": section["title"], "items": items}


def fill_images(sections, workers=8):
    """Fetch missing preview images from each article's own page."""
    targets = [it for sec in sections for it in sec["items"][:5] if not it.get("image")]
    if not targets:
        return
    log(f"· resolving {len(targets)} preview images")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for it, img in zip(targets, pool.map(lambda x: scrape_og_image(x["url"]), targets)):
            if img:
                it["image"] = img


def main():
    global LOG
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=6, help="stories kept per section")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--no-images", action="store_true", help="skip article-page image lookup")
    args = ap.parse_args()
    LOG = not args.quiet

    os.makedirs(DATA_DIR, exist_ok=True)
    load_cache()
    previous = {}
    try:
        with open(FEED_PATH) as fh:
            previous = json.load(fh)
    except Exception:
        pass

    log("· fetching sections")
    with ThreadPoolExecutor(max_workers=6) as pool:
        sections = list(pool.map(lambda s: build_section(s, args.limit), SECTIONS))

    if not args.no_images:
        fill_images(sections)
    save_cache()

    log("· fetching market data")
    markets = build_markets(previous.get("markets"))
    log(f"  · {len(markets)} instruments")

    # keep whatever still has stories; fall back to the last good copy
    old_sections = {s["id"]: s for s in previous.get("sections", [])}
    for sec in sections:
        if not sec["items"] and sec["id"] in old_sections:
            sec["items"] = old_sections[sec["id"]]["items"]
            sec["stale"] = True

    seen_global = set()
    for sec in sections:
        kept = []
        for item in sec["items"]:
            key = norm_key(item["title"])
            if key in seen_global and len(kept) >= 3:
                continue
            seen_global.add(key)
            kept.append(item)
        sec["items"] = kept

    topics, used = [], set()
    for sec in sections:
        for item in sec["items"][:2]:
            words = re.findall(r"[A-Z][a-zA-Z]{3,}", item["title"])
            topic = " ".join(words[:2]).strip() or sec["title"]
            if topic.lower() not in used and len(topic) > 3:
                used.add(topic.lower())
                topics.append(topic)
            if len(topics) >= 10:
                break
        if len(topics) >= 10:
            break

    feed = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "markets": markets,
        "topics": topics,
        "sections": sections,
        "sources": ["Press Information Bureau", "Ministry of External Affairs",
                    "AVN", "BBC News", "NDTV", "The Hindu", "Google News"],
    }
    with open(FEED_PATH, "w") as fh:
        json.dump(feed, fh, ensure_ascii=False, indent=1)
    # Same payload as a plain script, so the page also works from file:// where
    # fetch() is blocked by the browser.
    with open(os.path.join(DATA_DIR, "feed.js"), "w") as fh:
        fh.write("window.NNN_FEED = ")
        json.dump(feed, fh, ensure_ascii=False)
        fh.write(";\n")
    total = sum(len(s["items"]) for s in sections)
    log(f"✓ wrote {FEED_PATH} — {total} stories, {len(markets)} instruments")


if __name__ == "__main__":
    main()
