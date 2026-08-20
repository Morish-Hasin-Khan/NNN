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
# Only allow genuinely recent news into the feed.
# 24 hours = today's news / very recent breaking news.
MAX_NEWS_AGE_HOURS = 24
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


def trim_words(text, limit):
    """Cut a summary to `limit` characters without slicing a word in half."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:-–—")
    return (cut or text[:limit]) + "…"


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

    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%d %b %Y",
        "%B %d, %Y",
        "%d/%m/%Y",
        "%b %d, %Y",
    ):
        try:
            dt = datetime.strptime(value, fmt)

            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)

            return dt.astimezone(timezone.utc).isoformat()

        except Exception:
            continue

    return None

def page_published_date(url):
    """Extract the publisher's actual publication date from an article page."""
    if not url:
        return None

    r = get(url)
    if r is None:
        return None

    try:
        soup = BeautifulSoup(r.text, "html.parser")
    except Exception:
        return None

    # Common metadata used by news websites.
    for selector, attr in (
        ('meta[property="article:published_time"]', "content"),
        ('meta[name="article:published_time"]', "content"),
        ('meta[property="datePublished"]', "content"),
        ('meta[name="datePublished"]', "content"),
        ('meta[itemprop="datePublished"]', "content"),
        ('time[datetime]', "datetime"),
    ):
        tag = soup.select_one(selector)
        if tag and tag.get(attr):
            date = to_iso(tag.get(attr))
            if date:
                return date

    # PIB: "Posted On: 25 APR 2026 11:56AM by PIB Delhi"
    text = clean(soup.get_text(" ", strip=True))
    match = re.search(
        r"Posted\s+On\s*:\s*(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})",
        text,
        re.I,
    )
    if match:
        date = to_iso(match.group(1))
        if date:
            return date

    # RBI: "Date : Feb 24, 2020"
    match = re.search(
        r"\bDate\s*:\s*([A-Za-z]{3,9}\s+\d{1,2},\s+\d{4})",
        text,
        re.I,
    )
    if match:
        date = to_iso(match.group(1))
        if date:
            return date

    # MEA and some other government pages:
    # "Published On: 30-Apr-2026"
    match = re.search(
        r"Published\s+On\s*:\s*(\d{1,2}-[A-Za-z]{3}-\d{4})",
        text,
        re.I,
    )
    if match:
        date = to_iso(match.group(1))
        if date:
            return date

    return None
def is_recent(published, max_hours=MAX_NEWS_AGE_HOURS):
    """Return True only for articles published within the freshness window."""
    if not published:
        return False

    try:
        dt = datetime.fromisoformat(published)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        age_hours = (
            datetime.now(timezone.utc) -
            dt.astimezone(timezone.utc)
        ).total_seconds() / 3600

        return 0 <= age_hours <= max_hours

    except Exception:
        return False

BYLINE_JUNK = re.compile(r"^(by|from)\s+", re.I)


def tidy_byline(name):
    """Normalise an RSS author field into a readable byline, or drop it."""
    name = clean(name)
    if not name:
        return ""
    name = BYLINE_JUNK.sub("", name).strip()
    # feeds often put an email address or a bare handle in dc:creator
    if "@" in name and " " not in name:
        return ""
    if len(name) > 60 or len(name) < 3:
        return ""
    return name


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
def parse_rss(url, source_name=None, limit=12, licence="linkout", keep_body=False):
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
    for node in items:
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
        if not is_recent(published):
            continue

        # byline: the journalist, where the publisher supplies one
        byline = clean(text("dc:creator") or text("author"))
        if not byline:
            el = node.find("atom:author/atom:name", NS)
            if el is not None and el.text:
                byline = clean(el.text)
        byline = tidy_byline(byline)

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

        item = {
            "title": title,
            "url": link.strip(),
            "summary": trim_words(summary, 1400),
            "published": published,
            "source": source_name or domain_of(link) or domain_of(url),
            "byline": byline,
            "image": image,
            "licence": licence,
        }
        # feeds that syndicate the whole piece (Global Voices does) save us a fetch
        if keep_body and licence in FULL_TEXT_LICENCES:
            body = html_to_paras(text("content:encoded"))
            if body:
                item["body"] = body
        out.append(item)
        if len(out) >=limit:
            break
    return out


# ------------------------------------------------------------------ licences
# What NNN may legally show inside its own reader, per source.
#   gov-open  Government of India material. The PIB copyright policy lets it be
#             "reproduced free of charge ... no need for any prior approval",
#             provided it is accurate, not misleading, and the source is
#             prominently acknowledged. Full text is fine.
#   cc-by     Creative Commons Attribution. Full text is fine with credit, a
#             link to the original and a link to the licence.
#   linkout   Everything else — BBC, NDTV, The Hindu, RBI, Google News results.
#             Headline, the summary the publisher syndicates, and a link. Their
#             article text is theirs; copying it would be infringement and no
#             byline changes that.
FULL_TEXT_LICENCES = {"gov-open", "cc-by"}

LICENCES = {
    "gov-open": {
        "label": "Government of India — free to reproduce",
        "note": ("Reproduced from {source}, Government of India. Government material may be "
                 "reproduced free of charge provided it is reproduced accurately, is not used "
                 "in a derogatory or misleading way, and the source is prominently acknowledged."),
        "url": "https://www.pib.gov.in/content/102_2_Copyright-Policy.aspx",
    },
    "cc-by": {
        "label": "Creative Commons BY — free to republish",
        "note": ("This article by {byline} originally appeared on {source} and is republished "
                 "in full under a Creative Commons Attribution licence."),
        "url": "https://creativecommons.org/licenses/by/3.0/",
    },
    "nnn": {
        "label": "NNN original",
        "note": "Written for NNN by {byline}.",
        "url": "",
    },
    "linkout": {
        "label": "",
        "note": ("{source} holds the rights to this report. NNN shows the headline, the summary "
                 "they syndicate and their preview image, and links back to the original."),
        "url": "",
    },
}

# Paragraphs that are page furniture rather than article text.
JUNK_PARA = re.compile(
    r"^(follow us|share this|also read|read more|download|subscribe|click here|advertisement|"
    r"posted on|related (posts|articles)|tags:|previous post|next post|written by)", re.I)


def article_body(url, selectors, max_paras=60):
    """Pull the readable body of an article page as a list of paragraphs.

    Only ever called for sources whose licence allows full reproduction.
    """
    r = get(url)
    if r is None:
        return []
    try:
        soup = BeautifulSoup(r.text, "html.parser")
    except Exception:
        return []
    for tag in soup(["script", "style", "nav", "header", "footer", "form", "noscript"]):
        tag.decompose()

    node = None
    for sel in selectors:
        node = soup.select_one(sel)
        if node is not None:
            break
    if node is None:
        return []

    paras, seen = [], set()
    for el in node.find_all(["p", "li", "h2", "h3"]):
        txt = clean(el.get_text())
        heading = el.name in ("h2", "h3")
        # headings are short by nature — don't hold them to the prose threshold
        if len(txt) < (6 if heading else 30) or JUNK_PARA.match(txt):
            continue
        key = txt[:80].lower()
        if key in seen:
            continue
        seen.add(key)
        paras.append({"kind": "h" if heading else "p", "text": txt})
        if len(paras) >= max_paras:
            break
    # a couple of stray lines is a failed extraction, not an article
    words = sum(len(p["text"].split()) for p in paras if p["kind"] == "p")
    return paras if words >= 90 else []


def html_to_paras(blob, max_paras=60):
    """Turn a feed's content:encoded HTML into the same paragraph list."""
    if not blob:
        return []
    try:
        soup = BeautifulSoup(blob, "html.parser")
    except Exception:
        return []
    for tag in soup(["script", "style", "figure", "iframe"]):
        tag.decompose()
    paras = []
    for el in soup.find_all(["p", "li", "h2", "h3"]):
        txt = clean(el.get_text())
        heading = el.name in ("h2", "h3")
        if len(txt) < (6 if heading else 30) or JUNK_PARA.match(txt):
            continue
        paras.append({"kind": "h" if heading else "p", "text": txt})
        if len(paras) >= max_paras:
            break
    words = sum(len(p["text"].split()) for p in paras if p["kind"] == "p")
    return paras if words >= 90 else []


# ------------------------------------------------------- bespoke scrapers
# Where the readable text sits on each government page. Tried in order; the
# first selector that matches wins, and a thin result is treated as a failure.
PIB_BODY = ["div.innner-page-main-about-us-content-right-part", "#PdfDiv",
            "div.content-area", "div[id*='ContentPlaceHolder'] div.pib_cnt", "article"]
MEA_BODY = ["div.innerContent", "#ctl00_ContentPlaceHolder1_divContent",
            "div.pressRelease", "div.content", "article"]


def scrape_rbi(url="https://rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx", limit=8):
    """Reserve Bank of India press releases.

    RBI's site is 'All Rights Reserved' with no open-reproduction policy, so
    these stay headline + link. Do not add them to FULL_TEXT_LICENCES.
    """
    r = get(url)
    if r is None:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    out, seen = [], set()
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        if "PressReleaseDisplay" not in href and "BS_PressRelease" not in href:
            continue
        title = clean(a.get_text())
        if len(title) < 25:
            continue
        full = urljoin("https://rbi.org.in/", href)
        if full in seen:
            continue
        seen.add(full)
        published = page_published_date(full)

        if not published or not is_recent(published):
            continue

        out.append({
            "title": title,
            "url": full,
            "summary": "",
            "published": published,
            "source": "Reserve Bank of India",
            "image": None,
            "licence": "linkout",
        })
        if len(out) >= limit:
            break
    return out


def global_voices(path="feed", limit=8):
    """Global Voices — CC BY 3.0, republishable in full with attribution.

    Their robots.txt allows feeds (crawl-delay 10s, which one call a run
    respects comfortably). Images are NOT covered by the licence, so we drop
    them and let app.js draw its own cover.
    """
    items = parse_rss(f"https://globalvoices.org/{path}/", "Global Voices",
                      limit, licence="cc-by", keep_body=True)
    for it in items:
        it["image"] = None          # licence covers the words, not the pictures
        it["publisher"] = "Global Voices"
    return [it for it in items if it.get("body")]


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
        published = page_published_date(href)

        if not published or not is_recent(published):
            continue

        out.append({
            "title": title,
            "url": href,
            "summary": "",
            "published": published,
            "source": ministry or "Press Information Bureau",
            "image": None,
            "licence": "gov-open",
            "publisher": "Press Information Bureau",
            "body_selectors": PIB_BODY,
        })
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
        published = page_published_date(full) or to_iso(date)

        if not published or not is_recent(published):
            continue

        out.append({
            "title": title,
            "url": full,
            "summary": "",
            "published": published,
            "source": "Ministry of External Affairs",
            "image": None,
            "licence": "gov-open",
            "publisher": "Ministry of External Affairs",
            "body_selectors": MEA_BODY,
        })
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


# ------------------------------------------------------------- UPSC helper
# Rough syllabus routing. Deliberately shallow: it points an aspirant at the
# right paper and the terms worth looking up, it does not pretend to be a
# verified syllabus mapping.
UPSC_PAPERS = [
    ("GS-II — Polity & Governance", r"parliament|bill|act\b|supreme court|high court|constitution|"
                                    r"amendment|governance|panchayat|election commission|judiciary|"
                                    r"cabinet|ordinance|tribunal|rti|federal"),
    ("GS-II — International Relations", r"bilateral|treaty|summit|united nations|unsc|brics|g20|g7|"
                                        r"quad|asean|saarc|diplomat|ambassador|visit to|foreign minister|"
                                        r"external affairs|mou with"),
    ("GS-III — Economy", r"gdp|inflation|repo|monetary|fiscal|budget|tax|gst|export|import|trade|"
                         r"rbi|bank|investment|msme|subsid|tariff|disinvest|infrastructure"),
    ("GS-III — Environment & Ecology", r"climate|emission|forest|wildlife|biodiversity|pollution|"
                                       r"tiger|wetland|renewable|solar|carbon|ganga|conservation"),
    ("GS-III — Science & Technology", r"isro|satellite|space|semiconductor|artificial intelligence|"
                                      r"quantum|vaccine|biotech|nuclear|drone|5g|digital india"),
    ("GS-III — Security", r"terror|border|army|navy|air force|defence|cyber|naxal|insurgen|"
                          r"paramilitary|drdo|missile"),
    ("GS-I — Society & Culture", r"heritage|unesco|tribal|caste|women|census|migration|"
                                 r"urbanis|festival|archaeolog|monument|language"),
    ("GS-II — Social Justice", r"health|education|scheme|welfare|pension|nutrition|scholarship|"
                               r"disabilit|minorit|ayushman|poverty|employment guarantee"),
]

SCHEME_HINT = re.compile(
    r"\b((?:[A-Z][A-Za-z]+ ){1,4}(?:Yojana|Abhiyan|Mission|Scheme|Bill|Act|Policy|Programme|"
    r"Corridor|Award|Summit|Agreement|Initiative|Fund|Committee|Commission))\b")
ACRONYM = re.compile(r"\b([A-Z]{2,6})\b")
STOP_ACRONYMS = {"THE", "AND", "FOR", "WITH", "INDIA", "PIB", "MEA", "NNN", "GS", "PM", "MR", "DR"}


def upsc_notes(item):
    """Build the 'why this matters' block shown under a UPSC story."""
    blob = f"{item.get('title','')} {item.get('summary','')}"
    body = " ".join(p["text"] for p in (item.get("body") or [])[:6])
    haystack = f"{blob} {body}".lower()

    papers = [name for name, pattern in UPSC_PAPERS if re.search(pattern, haystack)][:3]
    if not papers:
        papers = ["GS-II — Governance"]

    terms, seen = [], set()
    for match in SCHEME_HINT.findall(f"{blob} {body}"):
        key = match.lower()
        if key not in seen and len(match) < 60:
            seen.add(key)
            terms.append(match.strip())
    for match in ACRONYM.findall(blob):
        if match not in STOP_ACRONYMS and match.lower() not in seen:
            seen.add(match.lower())
            terms.append(match)
    return {"papers": papers, "terms": terms[:6],
            "prompt": f"Note the what, the who-issues-it and the date — {papers[0].split(' — ')[0]} "
                      f"answers score on specifics, not adjectives."}


# ------------------------------------------------------------- NNN editorial
EDITORIAL_DIR = os.path.join(ROOT, "..", "editorial")


def load_editorials(limit=12):
    """NNN's own pieces: editorial/*.md with a simple key: value header.

        title: Why the audit matters
        author: Morish Hasin Khan
        date: 2026-08-20
        summary: One line for the card.
        ---
        First paragraph...
    """
    if not os.path.isdir(EDITORIAL_DIR):
        return []
    out = []
    for name in sorted(os.listdir(EDITORIAL_DIR), reverse=True):
        if not name.endswith((".md", ".markdown")):
            continue
        try:
            with open(os.path.join(EDITORIAL_DIR, name), encoding="utf-8") as fh:
                raw = fh.read()
        except Exception:
            continue
        head, _, body_text = raw.partition("\n---")
        meta = {}
        for line in head.splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                meta[k.strip().lower()] = v.strip()
        # markdown paragraphs are separated by blank lines, not by newlines —
        # join wrapped lines back up before turning each block into one para
        paras = []
        for block in re.split(r"\n\s*\n", body_text):
            block = block.strip()
            if not block:
                continue
            if block.startswith("#"):
                paras.append({"kind": "h", "text": clean(block.lstrip("# ").strip())})
            else:
                text = clean(" ".join(line.strip() for line in block.splitlines()))
                if len(text) > 20:
                    paras.append({"kind": "p", "text": text})
        if not meta.get("title") or not paras:
            continue
        out.append({
            "title": meta["title"],
            "url": f"#story-nnn-{re.sub(r'[^a-z0-9]+', '-', name.lower())[:40]}",
            "summary": meta.get("summary", "") or paras[0]["text"][:200],
            "published": to_iso(meta.get("date", "")) or datetime.now(timezone.utc).isoformat(),
            "source": "NNN",
            "byline": meta.get("author", "NNN Editorial"),
            "image": None,
            "licence": "nnn",
            "publisher": "NNN",
            "body": paras,
        })
        if len(out) >= limit:
            break
    return out


# ------------------------------------------------------------- world desks
# Major papers and wires, by public RSS. Anything here is `linkout`: we show
# the headline, the summary THEY syndicate, and a link. Nothing is scraped.
WORLD_PAPERS = [
    ("https://www.aljazeera.com/xml/rss/all.xml", "Al Jazeera"),
    ("https://feeds.bbci.co.uk/news/world/rss.xml", "BBC News"),
    ("https://www.theguardian.com/world/rss", "The Guardian"),
    ("https://rss.nytimes.com/services/xml/rss/nyt/World.xml", "The New York Times"),
    ("https://feeds.washingtonpost.com/rss/world", "The Washington Post"),
    ("https://feeds.npr.org/1001/rss.xml", "NPR"),
    ("https://rss.dw.com/rdf/rss-en-all", "Deutsche Welle"),
    ("https://www.france24.com/en/rss", "France 24"),
    ("https://www.cbc.ca/webfeed/rss/rss-world", "CBC News"),
    ("https://www.scmp.com/rss/91/feed", "South China Morning Post"),
    ("https://www.dawn.com/feeds/home", "Dawn"),
]

INDIA_PAPERS = [
    ("https://www.thehindu.com/news/national/feeder/default.rss", "The Hindu"),
    ("https://feeds.feedburner.com/ndtvnews-india-news", "NDTV"),
    ("https://indianexpress.com/section/india/feed/", "The Indian Express"),
    ("https://www.hindustantimes.com/feeds/rss/india-news/rssfeed.xml", "Hindustan Times"),
    ("https://timesofindia.indiatimes.com/rssfeedstopstories.cms", "The Times of India"),
    ("https://www.livemint.com/rss/news", "Mint"),
    ("https://www.newindianexpress.com/Nation/rssfeed/?id=170&getXmlFeed=true", "The New Indian Express"),
]


def desks(feeds, per_feed=6):
    """Pull several papers in parallel. A dead feed costs nothing but itself."""
    out = []
    with ThreadPoolExecutor(max_workers=min(8, len(feeds) or 1)) as pool:
        for got in pool.map(lambda f: parse_rss(f[0], f[1], per_feed), feeds):
            out.extend(got or [])
    return out


# ------------------------------------------------------- attention ordering
# The running order Morish asked for: what pulls attention, in his ranking.
# Each story is routed to the first category it matches, and the front page is
# sorted by that rank, then by how fresh the story is.
FRONT_CATEGORIES = [
    ("crime", "Crime", r"murder|killed|stabb|shot dead|rape|assault|kidnap|abduct|arrest|"
                       r"police|crime|body found|accused|custody|molest|firing|encounter"),
    ("conflict", "War & conflict", r"\bwar\b|airstrike|missile|drone strike|troops|ceasefire|militant|"
                                  r"terror|hostage|offensive|shelling|invasion|gaza|ukraine|"
                                  r"insurgen|army|rebels|strike on"),
    ("politics", "Political controversy", r"minister|parliament|election|opposition|resign|protest|"
                                          r"\bbill\b|president|prime minister|\bpoll\b|allegation|"
                                          r"cabinet|assembly|boycott|summoned|impeach"),
    ("money", "Money & scams", r"scam|fraud|crore|billion|inflation|\btax\b|market|\bbank\b|price|"
                               r"\bgst\b|economy|layoff|tariff|ponzi|\bloan\b|bankrupt|rupee|shares"),
    ("celebrity", "Celebrity", r"actor|actress|singer|\bfilm\b|bollywood|hollywood|influencer|"
                               r"celebrity|album|web series|box office|award show"),
    ("human", "Human interest", r"rescue|survivor|community|donat|volunteer|reunit|adopt|"
                                r"miracle|kindness|struggle|tribute|obituar"),
    ("weather", "Weather & disaster", r"flood|cyclone|earthquake|storm|heatwave|landslide|wildfire|"
                                      r"drought|tsunami|evacuat|torrential|avalanche"),
    ("tech", "AI & technology", r"\bai\b|artificial intelligence|semiconductor|\bchip\b|robot|"
                                r"startup|satellite|quantum|software|cyber|openai|"
                                r"algorithm|data centre|spacecraft"),
    ("sports", "Sports", r"cricket|football|olympic|tournament|world cup|wicket|championship|"
                         r"league|\bcoach\b|doping|medal|innings|striker"),
    ("mystery", "Mystery & investigation", r"mystery|missing|investigation|probe|inquiry|unexplained|"
                                           r"leaked|whistleblow|cover-up|expose|unsolved|raid"),
]
CATEGORY_RANK = {cid: i for i, (cid, _, _) in enumerate(FRONT_CATEGORIES)}


def classify(item):
    """Route a story to the category that matches it best.

    Taking the first pattern that hits would put "airstrike kills dozens" in
    Crime, because 'killed' appears in the crime list. Counting distinct hits
    and breaking ties by Morish's ranking puts it in War & conflict, which is
    where a reader would look for it.
    """
    hay = f"{item.get('title','')} {item.get('summary','')}".lower()
    best = None
    for cid, label, pattern in FRONT_CATEGORIES:
        hits = len({m.group(0).lower() for m in re.finditer(pattern, hay)})
        if not hits:
            continue
        score = (hits, -CATEGORY_RANK[cid])      # more hits wins; then higher rank
        if best is None or score > best[0]:
            best = (score, cid, label)
    return (best[1], best[2]) if best else ("general", "General")


# ------------------------------------------------------------- key figures
# Numbers are facts, and facts are not anyone's copyright. Pulling the figures
# out gives the reader something solid without reproducing a publisher's prose.
FIGURE = re.compile(
    r"(?:(?:₹|Rs\.?|\$|€|£)\s?\d[\d,.]*\s?(?:crore|lakh|billion|million|trillion|bn|mn)?"
    r"|\b\d[\d,.]*\s?(?:crore|lakh|billion|million|trillion|per cent|percent|%|km|kg|tonnes?|"
    r"people|killed|injured|dead|wounded|arrested|rescued|missing|seats?|votes?|days?|years?|"
    r"months?|drones?|missiles?|homes?|houses?|schools?|hospitals?|flights?|troops?|soldiers?|"
    r"districts?|villages?|states?|countries|companies|firms?|jobs?|patients?|students?)\b"
    r"|\b\d[\d,.]*\s+[a-z]{4,}\b)", re.I)

# 4-digit years and clock times are not "figures" worth pulling out
YEARISH = re.compile(r"^(1[89]\d\d|20\d\d|21\d\d)\b")
MONTHISH = re.compile(r"^\d{1,2}\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", re.I)

TRAILING_JUNK = re.compile(r"\s+(?:with|and|or|but|of|in|on|at|to|for|from|by|as|the|a|an|that|"
                           r"which|while|after|before|says?|said)$", re.I)


def key_figures(item, limit=6):
    """Short factual extracts: a figure plus just enough words to read it."""
    text = f"{item.get('title','')}. {item.get('summary','')}"
    for para in (item.get("body") or [])[:4]:
        text += " " + para["text"]
    out, seen = [], set()
    for m in FIGURE.finditer(text):
        raw = m.group(0).strip()
        if YEARISH.match(raw) or MONTHISH.match(raw):
            continue                                    # a date is not a figure
        start, end = m.span()
        tail = text[end:end + 42].split(".")[0]
        tail = " ".join(tail.split()[:4])
        phrase = (m.group(0) + (" " + tail if tail else "")).strip(" ,;:")
        prev = None
        while prev != phrase:                      # "…12 regions, with" → "…12 regions"
            prev = phrase
            phrase = TRAILING_JUNK.sub("", phrase).strip(" ,;:")
        key = re.sub(r"[^a-z0-9]+", "", phrase.lower())
        # drop a figure already contained in one we kept ("12 regions" under
        # "$2.4 billion across 12 regions")
        if key and key not in seen and len(phrase) < 60 \
                and not any(key in prior for prior in seen):
            seen.add(key)
            out.append(phrase)
        if len(out) >= limit:
            break
    return out


# -------------------------------------------------------- multi-agency view
STOPWORDS = set("""the a an and or of in on at to for from with by as is are was were be been
being that this these those it its his her their they he she we you i not no but if then than
after before over under about into out up down new says say said will would can could may might
after amid over ahead against across among between during while when where who whom what which
first last next more most other another such only own same so also just now today yesterday""".split())


def stem(word):
    """Crude suffix trim. Headlines paraphrase, so 'countries' must meet 'country'."""
    for suffix in ("ies", "es", "s", "ing", "ed"):
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            base = word[: -len(suffix)]
            return base + "y" if suffix == "ies" else base
    return word


def signature(title):
    words = [stem(w) for w in re.findall(r"[a-z]{4,}", (title or "").lower())
             if w not in STOPWORDS]
    return {w for w in words if len(w) >= 4}


def cluster_coverage(sections, min_shared=3, max_also=5):
    """Find the same story across outlets and hang the others off the lead.

    This is the Google-News behaviour: one event, several mastheads, each
    linking to its own report. Nobody's text is copied — every entry is that
    outlet's own headline and their own syndicated line.
    """
    pool = []
    for sec in sections:
        for it in sec["items"]:
            pool.append(it)

    sigs = [(it, signature(it.get("title"))) for it in pool]
    used = set()
    for i, (item, sig) in enumerate(sigs):
        if len(sig) < 4 or id(item) in used:
            continue
        also, seen_sources = [], {(item.get("source") or "").lower()}
        cluster_sig = set(sig)
        for j, (other, osig) in enumerate(sigs):
            if i == j or id(other) in used:
                continue
            src = (other.get("source") or "").lower()
            if src in seen_sources:
                continue
            shared = cluster_sig & osig
            # a real match shares several words, at least one of them distinctive
            if len(shared) >= min_shared and any(len(w) >= 5 for w in shared):
                seen_sources.add(src)
                cluster_sig |= osig
                used.add(id(other))
                also.append({"source": other.get("source") or "", "title": other.get("title") or "",
                             "url": other.get("url") or "",
                             "summary": trim_words(other.get("summary") or "", 220)})
            if len(also) >= max_also:
                break
        if also:
            item["also"] = also


# ------------------------------------------------------------------- live
# Curated, not crawled. YouTube's feeds are disallowed to generic crawlers, so
# NNN links to the broadcasters' official streams rather than scraping them.
# Check these once in a while — channels do move.
LIVE_STREAMS = [
    {"source": "Al Jazeera English", "title": "Al Jazeera English — live",
     "url": "https://www.aljazeera.com/live/", "note": "24/7 world news"},
    {"source": "DW News", "title": "DW News — live",
     "url": "https://www.dw.com/en/live-tv/channel-english", "note": "Europe desk"},
    {"source": "France 24", "title": "France 24 English — live",
     "url": "https://www.france24.com/en/live", "note": "World, from Paris"},
    {"source": "NDTV", "title": "NDTV 24x7 — live",
     "url": "https://www.ndtv.com/video/live/channel/ndtv24x7", "note": "India"},
    {"source": "Sansad TV", "title": "Sansad TV — Parliament live",
     "url": "https://sansadtv.nic.in/live-tv/", "note": "Lok Sabha & Rajya Sabha"},
    {"source": "Doordarshan", "title": "DD News — live",
     "url": "https://www.ddnews.gov.in/en/live-tv/", "note": "Public broadcaster"},
]

LIVE_HINT = re.compile(r"\blive\b|live updates|as it happened|highlights:|minute-by-minute", re.I)


def live_desk(pool, limit=8):
    """Official streams first, then any live blogs the feeds are running."""
    out = [{**s, "summary": s.pop("note", ""), "published": datetime.now(timezone.utc).isoformat(),
            "image": None, "licence": "linkout", "kind": "stream"} for s in
           [dict(x) for x in LIVE_STREAMS]]
    for it in pool:
        if LIVE_HINT.search(it.get("title", "")):
            out.append({**it, "kind": "liveblog"})
        if len(out) >= limit + len(LIVE_STREAMS):
            break
    return out


# --------------------------------------------------------------- podcasts
# Two tiers, because not every show publishes a public feed.
#   feed  → a real podcast RSS. Episodes are listed and update themselves.
#   None  → a YouTube-first show. NNN shows a card that links to the channel;
#           nothing is crawled, because YouTube disallows generic crawlers.
# To turn a show card into a live episode list, either find its podcast RSS and
# drop it into "feed", or set YOUTUBE_API_KEY (see youtube_uploads below).
PODCAST_SOURCES = [
    # --- verified feeds
    {"name": "BBC Global News Podcast", "url": "https://www.bbc.co.uk/programmes/p02nq0gn",
     "feed": "https://podcasts.files.bbci.co.uk/p02nq0gn.rss", "note": "World news, twice daily"},
    {"name": "In Our Time", "url": "https://www.bbc.co.uk/programmes/b006qykl",
     "feed": "https://podcasts.files.bbci.co.uk/b006qykl.rss", "note": "BBC — history and ideas"},
    {"name": "NPR News Now", "url": "https://www.npr.org/podcasts/500005/npr-news-now",
     "feed": "https://feeds.npr.org/500005/podcast.xml", "note": "Five minutes, every hour"},
    {"name": "The Intelligence", "url": "https://shows.acast.com/theintelligencepodcast",
     "feed": "https://access.acast.com/rss/d556eb54-6160-4c85-95f4-47d9f5216c49",
     "note": "The Economist, daily"},
    {"name": "Al Jazeera Podcasts", "url": "https://www.aljazeera.com/podcasts/",
     "feed": "https://www.aljazeera.com/xml/rss/podcast.xml", "note": "The Take and others"},
    {"name": "Today in Focus", "url": "https://www.theguardian.com/news/series/todayinfocus",
     "feed": "https://feeds.megaphone.fm/GLT1412515089", "note": "The Guardian, daily"},

    # --- YouTube-first shows: card + link, no crawling
    {"name": "ANI Podcast with Smita Prakash", "feed": None,
     "url": "https://www.youtube.com/@ANIPodcastwithSmitaPrakash",
     "note": "Long-form interviews — politics, policy, foreign affairs",
     "youtube": "@ANIPodcastwithSmitaPrakash"},
    {"name": "The Cārvāka Podcast", "feed": None,
     "url": "https://www.youtube.com/@CarvakaPodcast",
     "note": "Kushal Mehra — philosophy, Indic thought, debate",
     "youtube": "@CarvakaPodcast"},
    {"name": "The Bigger Picture Podcast", "feed": None,
     "url": "https://www.youtube.com/results?search_query=The+Bigger+Picture+Podcast",
     "note": "Set the exact channel URL in PODCAST_SOURCES"},
    {"name": "Ajeet Bharti", "feed": None,
     "url": "https://www.youtube.com/@AjeetBharti",
     "note": "Commentary and long-form discussion", "youtube": "@AjeetBharti"},
    {"name": "PBD Podcast", "feed": None,
     "url": "https://www.youtube.com/@PBDPodcast",
     "note": "Valuetainment — business and current affairs", "youtube": "@PBDPodcast"},
    {"name": "Intelligence Squared", "feed": None,
     "url": "https://www.intelligencesquared.com/podcasts/",
     "note": "IQ2 — formal debates"},
    {"name": "Modern-Day Debate", "feed": None,
     "url": "https://www.youtube.com/@ModernDayDebate",
     "note": "Live moderated debates", "youtube": "@ModernDayDebate"},
]

PODCAST_MAX_AGE_HOURS = 48        # Morish asked for a two-day cadence


def youtube_uploads(handle, limit=4):
    """Recent uploads via the official YouTube Data API — only if a key is set.

    Scraping YouTube is off the table (their robots.txt disallows it), but the
    documented API is fine. Set YOUTUBE_API_KEY in the environment, or as a
    GitHub Actions secret, and the YouTube-first shows list real episodes.
    Without a key this returns nothing and the show card is used instead.
    """
    key = os.environ.get("YOUTUBE_API_KEY")
    if not key or not handle:
        return []
    r = get("https://www.googleapis.com/youtube/v3/search", params={
        "key": key, "part": "snippet", "type": "video", "order": "date",
        "maxResults": limit, "channelId": handle} if handle.startswith("UC") else {
        "key": key, "part": "snippet", "type": "video", "order": "date",
        "maxResults": limit, "q": handle.lstrip("@")})
    if r is None:
        return []
    try:
        data = r.json()
    except Exception:
        return []
    out = []
    for entry in data.get("items", []):
        vid = (entry.get("id") or {}).get("videoId")
        snip = entry.get("snippet") or {}
        if not vid:
            continue
        out.append({
            "title": clean(snip.get("title")), "url": f"https://www.youtube.com/watch?v={vid}",
            "summary": trim_words(clean(snip.get("description")), 300),
            "published": to_iso(snip.get("publishedAt")) or datetime.now(timezone.utc).isoformat(),
            "image": ((snip.get("thumbnails") or {}).get("high") or {}).get("url"),
        })
    return out


def podcast_desk(per_show=3, limit=24):
    """Episodes where a feed exists, a show card where one does not."""
    def one(show):
        items = []
        if show.get("feed"):
            items = parse_rss(show["feed"], show["name"], per_show) or []
        if not items and show.get("youtube"):
            items = youtube_uploads(show["youtube"], per_show)
            for it in items:
                it["source"] = show["name"]
        for it in items:
            it.update({"kind": "podcast", "licence": "linkout",
                       "show": show["name"], "show_url": show["url"]})
        if items:
            return items
        # nothing to list — offer the show itself
        return [{
            "title": show["name"], "url": show["url"],
            "summary": show.get("note", ""), "source": show["name"],
            "published": datetime.now(timezone.utc).isoformat(),
            "byline": "", "image": None, "licence": "linkout",
            "kind": "show", "show": show["name"], "show_url": show["url"],
        }]

    out = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for got in pool.map(one, PODCAST_SOURCES):
            out.extend(got or [])
    episodes = [x for x in out if x["kind"] == "podcast"]
    shows = [x for x in out if x["kind"] == "show"]
    episodes.sort(key=lambda x: x.get("published") or "", reverse=True)
    # trim episodes if we must, but every configured show keeps its card
    return episodes[: max(0, limit - len(shows))] + shows


def podcasts_are_fresh(previous):
    """True while the stored podcast section is younger than the cadence."""
    stamp = previous.get("podcasts_fetched_at")
    if not stamp:
        return False
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(stamp)
    except Exception:
        return False
    return age.total_seconds() < PODCAST_MAX_AGE_HOURS * 3600


# ------------------------------------------------------------------ sections
SECTIONS = [
    {
        # Filled after every other section is built — see build_front_page().
        "id": "front", "eyebrow": "01 — What everyone is reading", "title": "Front Page",
        "fetch": lambda: [],
    },
    {
        "id": "live", "eyebrow": "02 — Happening now", "title": "Live",
        "fetch": lambda: [],          # also filled later, from the whole pool
    },
    {
        "id": "scandals", "eyebrow": "03 — Public interest", "title": "Scandals & Investigations",
        "fetch": lambda: (google_news("inquiry OR probe OR investigation OR audit government India", 8, "Google News")
                          + google_news("scam OR fraud OR corruption charges India", 6, "Google News")),
    },
    {
        "id": "national-news", "eyebrow": "04 — Across the country", "title": "National News",
        "fetch": lambda: (scrape_pib(limit=8) + desks(INDIA_PAPERS, per_feed=5)),
    },
    {
        "id": "global-news", "eyebrow": "05 — Beyond borders", "title": "Global News",
        "fetch": lambda: desks(WORLD_PAPERS, per_feed=5),
    },
    {
        "id": "global-hotline", "eyebrow": "06 — Essential global read", "title": "Global Hotline",
        "fetch": lambda: (scrape_mea(limit=8)
                          + parse_rss("https://feeds.bbci.co.uk/news/world/asia/rss.xml", "BBC News", 6)),
    },
    {
        "id": "national-hotlines", "eyebrow": "07 — Essential national read", "title": "National Hotlines",
        "fetch": lambda: (parse_rss("https://www.thehindu.com/news/national/feeder/default.rss", "The Hindu", 8)
                          + google_news("India top headlines today", 6)),
    },
    {
        "id": "upsc", "eyebrow": "08 — Study-ready every day", "title": "UPSC Current Affairs",
        "upsc": True,
        "fetch": lambda: (scrape_pib("https://www.pib.gov.in/Allrel.aspx?reg=3&lang=1", 8)
                          + scrape_pib("https://www.pib.gov.in/allRel.aspx", 8)
                          + scrape_mea(limit=5)
                          + scrape_rbi(limit=5)
                          + google_news("UPSC current affairs policy scheme economy India explained", 6, "Google News")),
    },
    {
        "id": "editorial", "eyebrow": "09 — Argument and analysis", "title": "Editorial",
        "fetch": lambda: (load_editorials(8) + global_voices(limit=8)),
    },
    {
        "id": "podcasts", "eyebrow": "10 — Listen instead", "title": "Podcasts",
        "cadence_hours": PODCAST_MAX_AGE_HOURS,
        "fetch": lambda: [] if SKIP_PODCASTS[0] else podcast_desk(),
    },
    {
        "id": "business", "eyebrow": "Business", "title": "Business",
        "fetch": lambda: (parse_rss("https://feeds.bbci.co.uk/news/business/rss.xml", "BBC News", 5)
                          + parse_rss("https://www.livemint.com/rss/money", "Mint", 5)),
    },
    {
        "id": "technology", "eyebrow": "Technology", "title": "Technology",
        "fetch": lambda: (parse_rss("https://feeds.bbci.co.uk/news/technology/rss.xml", "BBC News", 5)
                          + google_news("artificial intelligence OR semiconductor OR space technology", 5, "Google News")),
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

DERIVED_SECTIONS = {"front", "live"}

# flipped in main() when the stored podcast section is still inside its cadence
SKIP_PODCASTS = [False]


def build_front_page(sections, limit=30):
    """Rank everything by Morish's attention order, freshest first inside a tier."""
    pool = []
    seen = set()
    for sec in sections:
        if sec["id"] in DERIVED_SECTIONS or sec["id"] in ("podcasts", "editorial", "upsc"):
            continue
        for it in sec["items"]:
            key = norm_key(it["title"])
            if key in seen:
                continue
            seen.add(key)
            cid, label = classify(it)
            if cid == "general":
                continue
            pool.append({**it, "category": cid, "category_label": label,
                         "from_section": sec["title"]})
    # 30 candidates, 3 per category, so every category is represented and the
    # front end can re-rank for a reader who wants sport before crime.
    pool.sort(
        key=lambda x: (
            CATEGORY_RANK.get(x["category"], 99),
            -(len(x.get("also") or [])),
            -(datetime.fromisoformat(x["published"]).timestamp())
            if x.get("published") else 0
        )
    )
    # a front page that is nine crime stories is not a front page
    out, per_cat = [], {}
    for it in pool:
        c = it["category"]
        if per_cat.get(c, 0) >= 3:
            continue
        per_cat[c] = per_cat.get(c, 0) + 1
        out.append(it)
        if len(out) >= limit:
            break
    return out


# ------------------------------------------------------------------ markets
MARKETS = [
    {"key": "sensex",  "label": "SENSEX",    "symbol": "^BSESN",  "decimals": 2},
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
        it.setdefault("licence", "linkout")
        it["id"] = hashlib.sha1((it["url"] or it["title"]).encode()).hexdigest()[:12]
    log(f"  · {section['id']}: {len(items)} stories")
    return {"id": section["id"], "eyebrow": section["eyebrow"], "title": section["title"],
            "upsc": bool(section.get("upsc")), "items": items}


def fill_bodies(sections, workers=6, per_section=6):
    """Fetch full text for the stories whose licence allows us to show it."""
    targets = []
    for sec in sections:
        allowed = [it for it in sec["items"]
                   if it.get("licence") in FULL_TEXT_LICENCES
                   and not it.get("body") and it.get("body_selectors")]
        targets.extend(allowed[:per_section])
    if not targets:
        return
    log(f"· fetching {len(targets)} full texts (open-licence sources only)")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        bodies = pool.map(lambda it: article_body(it["url"], it["body_selectors"]), targets)
        for it, body in zip(targets, bodies):
            if body:
                it["body"] = body
                if not it.get("summary"):
                    it["summary"] = trim_words(body[0]["text"], 240)
    got = sum(1 for it in targets if it.get("body"))
    log(f"  · {got}/{len(targets)} resolved")
    # the selector list is build-time detail, it does not belong in the feed
    for sec in sections:
        for it in sec["items"]:
            it.pop("body_selectors", None)


def attach_upsc_notes(sections):
    for sec in sections:
        if not sec.get("upsc"):
            continue
        for it in sec["items"]:
            it["upsc"] = upsc_notes(it)


def fill_images(sections, workers=8):
    """Fetch missing publisher preview images for every story."""
    targets = [
        it
        for sec in sections
        for it in sec["items"]
        if not it.get("image") and it.get("url")
    ]

    if not targets:
        return

    log(f"· resolving {len(targets)} preview images")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = pool.map(
            lambda item: scrape_og_image(item["url"]),
            targets
        )

        for it, img in zip(targets, results):
            if img:
                it["image"] = img

def main():
    global LOG
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=6, help="stories kept per section")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--no-images", action="store_true", help="skip article-page image lookup")
    ap.add_argument("--force-podcasts", action="store_true",
                    help=f"refetch podcasts even inside the {PODCAST_MAX_AGE_HOURS}h cadence")
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
        SKIP_PODCASTS[0] = podcasts_are_fresh(previous) and not args.force_podcasts
        sections = list(pool.map(lambda s: build_section(s, args.limit), SECTIONS))

    fill_bodies(sections)
    attach_upsc_notes(sections)

    log("· matching the same story across agencies")
    cluster_coverage(sections)
    covered = sum(1 for sec in sections for it in sec["items"] if it.get("also"))
    log(f"  · {covered} stories carry other mastheads")

    by_id = {sec["id"]: sec for sec in sections}
    pool = [it for sec in sections if sec["id"] not in DERIVED_SECTIONS for it in sec["items"]]
    if "front" in by_id:
        by_id["front"]["items"] = build_front_page(sections)
        log(f"  · front page: {len(by_id['front']['items'])} stories")
    if "live" in by_id:
        by_id["live"]["items"] = live_desk(pool)
        log(f"  · live: {len(by_id['live']['items'])} entries")

    # Podcasts move slowly, so they get their own cadence instead of riding the
    # half-hourly news run.
    podcast_stamp = datetime.now(timezone.utc).isoformat()
    if "podcasts" in by_id and podcasts_are_fresh(previous) and not args.force_podcasts:
        stored = next((sec for sec in previous.get("sections", []) if sec["id"] == "podcasts"), None)
        if stored and stored.get("items"):
            by_id["podcasts"]["items"] = stored["items"]
            podcast_stamp = previous.get("podcasts_fetched_at") or podcast_stamp
            log(f"  · podcasts: reusing {len(stored['items'])} (next refresh in "
                f"{PODCAST_MAX_AGE_HOURS}h)")

    for sec in sections:
        for it in sec["items"]:
            if it.get("kind") in ("podcast", "show", "stream"):
                continue
            it.setdefault("category", classify(it)[0])
            it.setdefault("category_label", classify(it)[1])
            figures = key_figures(it)
            if figures:
                it["figures"] = figures

    if not args.no_images:
        fill_images(sections)
    save_cache()

    log("· fetching market data")
    markets = build_markets(previous.get("markets"))
    log(f"  · {len(markets)} instruments")

    # keep whatever still has stories; fall back to the last good copy
    old_sections = {s["id"]: s for s in previous.get("sections", [])}
    for sec in sections:
        if sec["id"] in DERIVED_SECTIONS:
            continue
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
        "podcasts_fetched_at": podcast_stamp,
        "markets": markets,
        "topics": topics,
        "sections": sections,
        "sources": (["Press Information Bureau", "Ministry of External Affairs",
                     "Reserve Bank of India", "Global Voices", "Google News"]
                    + [name for _, name in WORLD_PAPERS]
                    + [name for _, name in INDIA_PAPERS]),
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
