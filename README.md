# NNN — live news site

Static front end + a Python feed builder. Nothing renders from hard-coded copy any
more: `fetch_news.py` writes `data/feed.json`, and the page renders itself from it.

```
index.html        page shell — headings only, every card is rendered by JS
styles.css        original design + a "live layer" (market strip, photos, blur)
app.js            reads data/feed.json, renders all sections, refreshes every 5 min
fetch_news.py     the aggregator: headlines, preview images, market prices
data/feed.json    generated feed (also written as data/feed.js, see below)
serve.py          local preview server
.github/workflows/update-feed.yml   refreshes the feed every 30 min once hosted
```

## Run it

```bash
pip3 install -r requirements.txt
python3 fetch_news.py          # writes data/feed.json + data/feed.js
python3 serve.py               # open http://localhost:8000
```

Useful flags: `--limit 8` (stories per section), `--no-images` (skip the
article-page image lookup — much faster), `--quiet`.

Opening `index.html` by double-clicking works too: browsers block `fetch()` on
`file://`, so the page falls back to `data/feed.js`, which holds the same payload
as a plain script. On a real domain it uses `feed.json` and refreshes live.

## The market strip

The bar under the masthead is live: **SENSEX, NIFTY 50, BANK NIFTY, GOLD (₹/10g),
SILVER (₹/kg), USD/INR, BRENT, BITCOIN** — value, absolute change and percent,
green up / red down, metals labelled in gold. Prices come from Yahoo Finance with
Stooq as a fallback; gold and silver are spot dollars converted to rupees at the
live USD/INR rate, so treat them as an indicative spot estimate, not a jeweller's
rate. If a quote can't be fetched the previous value is kept and dimmed rather
than dropped. Hovering pauses the scroll; the ⟳ button forces a refresh.

## Where the news comes from

| Section | Sources |
|---|---|
| Adult Industry | AVN (news + business) and matching Google News results |
| Scandals & Investigations | Google News — inquiry / probe / audit queries |
| Hot News | NDTV top stories, Google News breaking |
| Global News | BBC World, NDTV World |
| Global Hotline | **Ministry of External Affairs** press releases, BBC Asia |
| National News | **Press Information Bureau** releases, NDTV India |
| National Hotlines | The Hindu national, Google News India |
| UPSC Current Affairs | PIB releases, Google News policy/economy explainers |
| Sex Ed & Relationships | Google News — sexual health, consent, research |
| Business / Tech / Sports / Culture / Health | BBC and NDTV section feeds |

Add or swap a source by editing the `SECTIONS` list at the bottom of
`fetch_news.py` — each entry is just a lambda returning a list of stories, and
`parse_rss()` handles any RSS or Atom URL.

## Images

Each card shows the publisher's own preview image (`og:image` / `media:content`),
credited in the corner and linked back to the source article. When a story has no
image, or the image fails to load, `app.js` draws a deterministic gradient cover
from the headline — so a card is never empty and nothing hotlinks a broken file.

Adult-section photos load blurred behind a **"Sensitive image — tap to view"**
overlay; the reveal is per image, per session. Only AVN's own editorial thumbnails
are ever fetched, and generated covers are never blurred (there's nothing to hide).

## Keeping it fresh on a domain

**GitHub Pages / Netlify / Vercel (no server needed).** Push this folder to a repo
and the included workflow runs `fetch_news.py` every 30 minutes, commits the new
`data/feed.json`, and your host redeploys automatically. Turn it on under
*Actions*, and make sure *Settings → Actions → Workflow permissions* is set to
**Read and write**. Two GitHub caveats: scheduled runs are queued, so `*/30` means
"about every half hour", and GitHub pauses schedules on a repo with no commits for
60 days — the feed commits keep it alive on its own.

**Your own server / cPanel / VPS.** Point cron at the script:

```
*/30 * * * * cd /var/www/nnn && /usr/bin/python3 fetch_news.py --quiet
```

**Your Mac.** Same line via `crontab -e`, or a launchd plist if you want it to
survive reboots. The page itself re-reads `feed.json` every five minutes, so an
open tab keeps updating without a reload.

## Editorial and legal notes

- Only headline, timestamp, source name, link and the publisher's own preview
  image are stored. Full article text is never copied, and every card links out.
- PIB and MEA material is Government of India public information. AVN, BBC, NDTV
  and The Hindu content stays theirs — the attribution and link-back are what make
  this a legitimate aggregator, so don't remove them.
- If you monetise the site, check each publisher's terms; some ask you to use
  their feed rather than scrape the page, and hotlinked images can be blocked at
  any time (the generated cover catches that).
- Keep a human review step before promoting anything into the Scandals section.
  The status labels (Active / Inquiry / Resolved) are assigned by position right
  now, not by verified case state.
