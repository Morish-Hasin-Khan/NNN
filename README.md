# NNN — live news site

Static front end + a Python feed builder. Nothing renders from hard-coded copy any
more: `fetch_news.py` writes `data/feed.json`, and the page renders itself from it.

```
index.html        page shell — headings only, every card is rendered by JS
styles.css        original design + a "live layer" (market strip, photos, reader)
app.js            reads data/feed.json, renders all sections, refreshes every 5 min
automation/fetch_news.py   the aggregator: headlines, licences, full text, markets
data/feed.json    generated feed (also written as data/feed.js, see below)
editorial/        your own pieces, one markdown file each
serve.py          local preview server
.github/workflows/update-news.yml   refreshes the feed every 30 min once hosted
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
| Scandals & Investigations | Google News — inquiry / probe / audit queries |
| Hot News | NDTV top stories, Google News breaking |
| Global News | BBC World, NDTV World |
| Global Hotline | **Ministry of External Affairs** press releases, BBC Asia |
| National News | **Press Information Bureau** releases, NDTV India |
| National Hotlines | The Hindu national, Google News India |
| Front Page | everything above, re-ranked by attention category |
| Live | curated official streams + live blogs detected in the feeds |
| National News | PIB + The Hindu, NDTV, Indian Express, HT, TOI, Mint, New Indian Express |
| Global News | Al Jazeera, BBC, Guardian, NYT, Washington Post, NPR, DW, France 24, CBC, SCMP, Dawn |
| UPSC Current Affairs | PIB (two release streams), MEA, RBI, Google News explainers |
| Podcasts | BBC Global News, NPR News Now, Al Jazeera, Guardian, The Daily |
| Editorial | NNN originals (`editorial/*.md`) + Global Voices (CC BY) |
| Business / Tech / Sports / Culture / Health | BBC and NDTV section feeds |

Add or swap a source by editing the `SECTIONS` list at the bottom of
`fetch_news.py` — each entry is just a lambda returning a list of stories, and
`parse_rss()` handles any RSS or Atom URL.

## Images

Each card shows the publisher's own preview image (`og:image` / `media:content`),
credited in the corner and linked back to the source article. When a story has no
image, or the image fails to load, `app.js` draws a deterministic gradient cover
from the headline — so a card is never empty and nothing hotlinks a broken file.

## The reader — and what NNN may legally show in it

Clicking a card does **not** send you to the publisher. `app.js` opens the NNN
reader: a panel over the page carrying the NNN masthead, the headline, a byline
and the story itself. Each open story gets a shareable `#story-<id>` URL, and Esc,
the ✕, the backdrop and browser Back all close it.

**How much of the story appears depends on the licence, not on preference.**
`fetch_news.py` tags every item with a `licence`, and `app.js` only ever renders a
full body when that tag allows it:

| licence | Sources | Reader shows |
|---|---|---|
| `gov-open` | PIB, MEA | **The complete release.** Government of India material "may be reproduced free of charge ... no need for any prior approval", provided it is accurate, not misleading, and the source is **prominently acknowledged** — which the reader does, every time. |
| `cc-by` | Global Voices | **The complete article**, credited to the author with a link to the original and to the licence. Their *images* are not covered by the licence, so NNN drops them and draws its own cover. |
| `nnn` | `editorial/*.md` | **Everything.** It's yours. |
| `linkout` | BBC, NDTV, The Hindu, RBI, Google News | Headline, the summary the publisher syndicates, their preview image, and a link to the original. |

That last row is not a style choice and should not be "fixed". Copying a Hindu,
NDTV, BBC or RBI article into NNN is copyright infringement, and a byline is not
a licence — attribution and permission are different things. RBI in particular is
`All Rights Reserved` with no open-reproduction policy, so it links out even
though it is a public institution. If you ever add a source, set its licence
tier deliberately; anything unrecognised defaults to `linkout`, which is the safe
direction to fail.

## Running order

Sections run in the order Morish set: **what pulls attention first**, then the
desks.

`01 Front Page · 02 Live · 03 Scandals · 04 National · 05 Global · 06 Global
Hotline · 07 National Hotlines · 08 UPSC · 09 Editorial · 10 Podcasts ·
11 All Other News`

The **Front Page** is derived, not fetched. Every story from every desk is routed
by `classify()` into one of ten attention categories, in this ranking:

`crime → war & conflict → political controversy → money & scams → celebrity →
human interest → weather & disaster → AI & technology → sports → mystery`

Classification counts how many distinct patterns hit and breaks ties by rank, so
"airstrike kills dozens" lands in War & conflict rather than Crime just because
the word *killed* appears in both lists. No category may take more than three
front-page slots — one grim news day should not swallow the page.

## Making a story worth opening

A `linkout` story cannot show the publisher's article. It can show three things
that are ours to show, and together they read as a proper page:

1. **The publisher's full syndicated summary.** NNN used to trim this to 240
   characters, which was our own cap, not theirs. It now keeps what the feed
   gives — usually 60–120 words.
2. **What we know** — `key_figures()` pulls the numbers out (casualties, sums,
   percentages, counts) with just enough words to read them. Facts are not
   copyrightable; this is data, not prose.
3. **The same story, N newsrooms** — `cluster_coverage()` matches paraphrased
   headlines across outlets (stemmed word overlap, ≥3 shared words with at least
   one distinctive) and hangs the others off the lead. Every line is that
   newsroom's own headline and their own summary, linking to their report. This
   is the Google News behaviour, and it is the single biggest reason a story
   page feels like NNN's rather than a redirect.

## Live

Deliberately curated. YouTube's feeds are disallowed to generic crawlers, so NNN
does not crawl them — `LIVE_STREAMS` is a hand-kept list of broadcasters'
official live pages (Al Jazeera, DW, France 24, NDTV, Sansad TV, DD News) and
NNN links to them. Alongside it, any headline that reads like a running blog
("LIVE updates", "as it happened") is pulled from the feeds. If a channel moves,
edit the list.

## Podcasts

Thirteen shows in `PODCAST_SOURCES`, in two tiers:

- **With a feed** — BBC Global News, In Our Time, NPR News Now, The Intelligence
  (The Economist), Al Jazeera, Today in Focus. Episodes are listed and refresh
  themselves.
- **Without one** — ANI Podcast with Smita Prakash, The Cārvāka Podcast, The
  Bigger Picture, Ajeet Bharti, PBD Podcast, Intelligence Squared,
  Modern-Day Debate. These are YouTube-first and publish no public podcast RSS I
  could verify, so NNN shows a **show card** that links to the channel. Nothing
  is crawled — YouTube's robots.txt disallows it.

Two ways to upgrade a show card into a live episode list:

1. Find the show's podcast RSS and put it in that entry's `"feed"`.
2. Set `YOUTUBE_API_KEY` (env var locally, repo secret in Actions). The official
   YouTube Data API is fine to use; `youtube_uploads()` then lists recent videos
   for any entry with a `"youtube"` handle. Without a key it returns nothing and
   the show card is used, so the site works either way.

**The Bigger Picture Podcast** is a guess — several shows share that name. Set
the exact channel URL in `PODCAST_SOURCES` when you've picked the right one.

Podcasts refresh on a **two-day cadence** (`PODCAST_MAX_AGE_HOURS = 48`), not on
the half-hourly news run. `podcasts_fetched_at` in the feed is the clock. Force
one with `python3 automation/fetch_news.py --force-podcasts`.

## Saved and Watch later

The ＋ on a story and **Watch later** on a podcast both write to `localStorage`
and appear in the drawer behind the bookmark icon in the header. Podcasts and
streams open at the publisher rather than in the reader, because that is where
they play.

This is per-browser, not per-account. Clearing site data clears the list, and it
does not follow a reader to their phone. Real cross-device saving needs accounts
and a backend.

## Sign-ups and the welcome email

**A static site cannot send email.** There is no server here to send from, so
NNN needs a mail or form service. Until one is connected the form says
"Sign-up isn't connected yet" rather than pretending a message went out.

To connect one:

1. Create a form/list at a service that sends a confirmation or welcome email —
   Formspree, Buttondown, Mailchimp and Beehiiv all do this on a free tier.
2. Put the endpoint in `app.js`:
   ```js
   const NEWSLETTER_ENDPOINT = "https://formspree.io/f/xxxxxxxx";
   ```
3. Write the welcome email in that service. Suggested copy:

   > **Subject:** You're on the NNN list
   >
   > Thanks for signing up. NNN is news with the noise taken out — front page
   > ranked by what actually matters, government releases in full, and every
   > story credited to the newsroom that filed it.
   >
   > Read it here: https://your-domain
   >
   > You picked: {{topics}}. Your front page is already ordered around that.

NNN posts JSON: `{ email, site, topics, signed_up_at }`. `topics` is the reader's
chosen categories, so the service can merge them into the welcome email and
segment later sends.

## Personalisation — how the algorithm works

Per reader, in their browser, no backend:

- **On sign-up** they pick categories. A deliberate pick seeds a weight of 8.
- **Opening a story** adds 1 to that story's category and source.
- **Saving one** adds 3 — the strongest signal a reader gives without typing.
- Weights cap at 24, so one obsessive evening cannot permanently define the feed.

The front page ships a **candidate pool of ~30** stories (3 per category, so
every category is represented) and renders the top 9. In editorial order that is
the attention ranking. With **For you** on, `personalise()` re-ranks the whole
pool by `(position) + preference × 4` first — which is why a reader who picks
Sports gets sport on the front page even though sport never makes the default
cut. The toggle sits in the Front Page heading and flips back any time.

Since this is per-device, a reader on a new phone starts fresh. Moving it to an
account is the main thing a backend would buy you.

## Writing editorials

Drop a markdown file in `editorial/`:

```
title: What an audit report is actually for
author: Morish Hasin Khan
date: 2026-08-20
summary: One line for the card.
---
# A subheading

A paragraph. Blank lines separate paragraphs; lines starting with # become
subheadings in the reader.
```

`load_editorials()` picks it up on the next run — newest filename first, so date
prefixes sort correctly. Nothing to configure.

## UPSC section

Government releases lead the section because they are the ones that open in full.
Each story carries an auto-generated **"Why this matters for UPSC"** block: the
likely GS papers (keyword-routed by `UPSC_PAPERS`), scheme and acronym terms worth
looking up, and a one-line answer-writing prompt. It is a study aid built from
keywords, not a verified syllabus mapping — treat it as a pointer.

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
- PIB and MEA material is Government of India public information. BBC, NDTV
  and The Hindu content stays theirs — the attribution and link-back are what make
  this a legitimate aggregator, so don't remove them.
- If you monetise the site, check each publisher's terms; some ask you to use
  their feed rather than scrape the page, and hotlinked images can be blocked at
  any time (the generated cover catches that).
- Keep a human review step before promoting anything into the Scandals section.
  The status labels (Active / Inquiry / Resolved) are assigned by position right
  now, not by verified case state.
Deployment test
