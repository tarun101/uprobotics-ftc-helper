# BIOBUZZ digest sources (2026-09-16)

Added from the FTC Current Knowledge Digest — BIOBUZZ 2026–27 **Stable** list. These are official or high-quality technical sources. They are labeled in item headers as advice/docs/resources — **not** Competition Manual rules. Retrieval should stay rules-first via existing `manual` / `team_update` / `qa` items.

## Added (`config.yaml` → `web_sources`)

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

Fetcher support: `web_sources.py` kinds `pages`, `hub`, and `raw` (raw handled via pages/plain-text path); hubs may set `allow_pdf: true`.

## Already covered (no config change)

- **AprilTag Clusters tech tip** is in the existing `ftcdocs` Sphinx crawl (`tech_tips/tech-tips/tech-tip-apriltag-clusters/...` appears in `searchindex.js`). `ftcdocs` has no `include` filter, so the page is already discovered.

## Explicitly skipped

| Source | Reason |
|---|---|
| https://ftc-events.firstinspires.org/2026 | Site asks not to scrape webpages for event data; points developers at the Events API. Season landing HTML has no usable results payload without JS/API. |
| Reddit / Discord / Chief Delphi / FTCScout / Hive Vision | Digest watchlist only — never indexed as rule proof. |

## Ingest

```bash
# all web sources (weekly on reconcile weekday, or on demand)
python ftc_index.py web

# one source (smoke / backfill)
python ftc_index.py web --source first-field
python ftc_index.py web --source ftc-sdk --max-pages 5

# dry run to a folder
python ftc_index.py web --source first-team --out /tmp/ftc-web-dry
```

Official game hub + Q&A remain `python ftc_index.py first` / `run` via `first_sources.py` (unchanged path allowlist for `/ftc/game` + Q&A).
