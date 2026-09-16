# BIOBUZZ digest sources (2026-09-16)

Added from the FTC Current Knowledge Digest — BIOBUZZ 2026–27 **Stable** list, plus Tarun’s override to index **community watchlist** sources for discovery/strategy only. Item headers carry `Note:` labels. Retrieval should stay **rules-first** via existing `manual` / `team_update` / `qa` items — watchlist `source_type: community_watchlist` is never rule proof.

## Official / technical (`config.yaml` → `web_sources`)

| id | URL / seed | Kind | Notes |
|---|---|---|---|
| `first-field` | https://ftc-resources.firstinspires.org/ftc/field | hub + PDFs | Field guides; CAD ZIP excluded |
| `first-team` | https://ftc-resources.firstinspires.org/ftc/archive/2027/team | hub + PDFs | Team printables / webinars |
| `first-event` | https://ftc-resources.firstinspires.org/ftc/archive/2027/event | hub + PDFs | Event ops PDFs |
| `first-season-overview` | https://www.firstinspires.org/programs/ftc/game-and-season | pages | Season overview |
| `first-community-blog` | https://community.firstinspires.org/topic/ftc | hub | Public FTC blog posts |
| `ftc-community-forum` | https://ftc-community.firstinspires.org/ | sitemap `/t/` | Public forum topics only (no login) |
| `ftc-sdk` | FtcRobotController README (raw v12.0) + releases/tag/v12.0 | pages | SDK docs / release notes |
| `ftcsim` | https://ftcsim.org/ (+ educators, competition-fields, eula) | pages | Simulator product pages |

## Community watchlist (discovery/strategy only — never rule proof)

| id | URL / seed | Kind | Status |
|---|---|---|---|
| `reddit-ftc-new` | https://www.reddit.com/r/FTC/new/.rss | rss | **Works** — public Atom feed; entry summaries indexed (HTML/JSON often 403) |
| `reddit-ftc-biobuzz` | https://www.reddit.com/r/FTC/search.rss?q=BIOBUZZ&restrict_sr=1&sort=new | rss | **Configured** — same Atom approach; may HTTP 429 under rate limits |
| `chief-delphi-ftc` | https://www.chiefdelphi.com/c/other/first-tech-challenge/60.json | discourse_json | **Works** — public category + topic JSON (no login) |

Fetcher support: `pages`, `hub`, `raw`, `rss`, `discourse_json`; hubs may set `allow_pdf: true`.

## Already covered (no config change)

- **AprilTag Clusters tech tip** is in the existing `ftcdocs` Sphinx crawl (`tech_tips/.../tech-tip-apriltag-clusters/...` in `searchindex.js`).

## Explicitly skipped

| Source | Reason |
|---|---|
| https://ftc-events.firstinspires.org/2026 | Site asks not to scrape webpages for event data; points at Events API; landing HTML has no usable results payload without JS/API. |
| Unofficial FTC Discord (`discord.com/invite/ftc`) | Invite page only exposes OG title/description; channel messages require Discord login/client — not useful public text. |
| https://ftcscout.org/ | SvelteKit SPA shell (~2KB); public REST guesses 404; GraphQL blocked without browser CSRF/client. |
| Hive Vision | Still excluded until field-validated. |
| Reddit `.json` listings (`/new.json`, `search.json`) | Often 403/HTML interstitial without browser cookies; **RSS used instead** when available. |

## Ingest

```bash
python ftc_index.py web

python ftc_index.py web --source reddit-ftc-new
python ftc_index.py web --source chief-delphi-ftc --max-pages 10
python ftc_index.py web --source reddit-ftc-biobuzz --out /tmp/ftc-web-dry
```

Official game hub + Q&A remain `python ftc_index.py first` / `run` via `first_sources.py`.
