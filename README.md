# UP Robotics FTC Helper

A community tool for the *FIRST*® Tech Challenge 2026-27 season (BIOBUZZ™). Students open
**ftc.uprobotics.tech**, ask a question, and get a short answer with links to the exact source: a rule in the
official Competition Manual, a Team Update, a Q&A entry, or a timestamped moment in a YouTube video.

The helper is free and community-run: no accounts, no ads, no paid tier. UP Robotics provides the hosting and
development support; the code is open source under the MIT license. Not affiliated with or endorsed by *FIRST*.
AI answers can be wrong; always check the linked source.

This repository is published for transparency. **Contributions are not accepted**; please open an issue
or use the "Report a problem" link on the page instead.

## How it works

```
Mac mini (home internet) — launchd at 23:00 and 05:00 ET
  ftc_index.py run
    ├─ FIRST: HTML manual → one Item per rule / section; Team Update PDFs → one Item each; hub → one Item
    ├─ Q&A: FIRST's public answers RSS feed → one Item per answered question (never logs in)
    ├─ YouTube: RSS + yt-dlp captions → one Item (a Window) per 2–3 minute stretch, with a &t= link
    ├─ docs sites (weekly): gm0, FTC Docs, REV, FIRST field/team/event hubs, season overview, community blog/forum, SDK, FTCSIM → one Item per page
    ├─ state.sqlite: what is uploaded, content hashes, video queue
    ├─ Cloudflare AI Search Items API: upload new/changed Items, delete old ones
    ├─ weekly: reconcile the instance with local state, then the 30-question retrieval check
    └─ on success: ping a dead-man's-switch Monitor

Cloudflare AI Search instance "ftc-2026" (built-in storage, hybrid search)
    ├─ API Worker at ftc.uprobotics.tech/api (page/src/index.js): runs the keyword and vector legs itself,
    │    waits for both, fuses them (RRF), generates with the same Workers AI model → page widgets
    └─ public endpoint: /mcp → Claude/ChatGPT skill
```

Only URLs under `https://ftc-resources.firstinspires.org/ftc/game/` and on `ftc-qa.firstinspires.org` (public,
read-only) are fetched. No video or audio is downloaded; captions only. No logins, cookies, or proxies.

Vocabulary (Source, Item, Window, Chunk, Run, Round, Season, Rollover) is defined in [CONTEXT.md](CONTEXT.md).
Decisions that would surprise a reader are in [docs/adr/](docs/adr/).

## Files

| Path | Purpose |
|---|---|
| `ftc_index.py` | CLI: `run`, `first`, `youtube`, `backfill`, `web`, `reconcile`, `test`, `status`; state, upload, politeness |
| `first_sources.py` | Manual splitter (rule anchors + headings), Team Update PDFs, hub, Q&A feed |
| `youtube_sources.py` | RSS / flat-playlist discovery, yt-dlp captions, 2–3 minute windows, season labels |
| `config.yaml` | Channels and filters, instance name, paths (no secrets) |
| `page/` | `ftc.uprobotics.tech`: `/` is the chat, `/about/` the overview (AI Search UI snippets; `/chat/` redirects to `/`) + `src/index.js`, the API Worker the widgets call (AI Search, Workers AI, and rate-limit bindings in `wrangler.jsonc`) |
| `launchd/` | launchd plist template for the 23:00 and 05:00 runs |
| `tests/` | 30-question test (`run_questions.py --grade` auto-checks retrieval weekly), bad-question list, Worker link tests, sync unit tests |

## Setup (Mac mini)

1. Tools: Homebrew Python 3.12+, `brew install yt-dlp` (pin with `brew pin yt-dlp`).
   The interpreter and yt-dlp must live on the boot volume; binaries on some external volumes are killed on exec.
2. Environment:
   ```bash
   uv venv --python /opt/homebrew/bin/python3 .venv && uv pip install --python .venv/bin/python -r requirements.txt
   ```
3. `config.yaml`: fill in `cloudflare.account_id`.
4. Secrets in the login Keychain (never in files):
   ```bash
   security add-generic-password -s ftc-index -a cf-api-token -w '<token with AI Search:Edit + AI Search:Run>'
   security add-generic-password -s ftc-index -a monitor-ping-url -w 'https://hc-ping.com/<uuid>'
   ```
5. Cloudflare AI Search instance `ftc-2026` (Workers Paid plan, default Workers AI model; no paid model during trials): hybrid search on; custom metadata `source_type` (text) and
   `published` (number); boost by `published` desc; public endpoint on with authorized host
   `https://ftc.uprobotics.tech` and a 60 requests/minute limit; system prompt from `PROMPT.md`.
6. Dry run to inspect output without uploading anything:
   ```bash
   .venv/bin/python ftc_index.py first --out /tmp/ftc-dry
   ```
7. First real runs:
   ```bash
   .venv/bin/python ftc_index.py first            # rules content
   .venv/bin/python ftc_index.py backfill          # discover every channel, index up to 150 videos (repeat daily until caught up)
   ```
8. Schedule (23:00 and 05:00 local time, so a Team Update posted in the evening is indexed before school):
   ```bash
   sed "s#__REPO__#$PWD#g; s#__HOME__#$HOME#g" launchd/me.uprobotics.ftc-index.plist.template > ~/Library/LaunchAgents/me.uprobotics.ftc-index.plist
   cp ~/Library/LaunchAgents/me.uprobotics.ftc-index.plist /Users/Shared/ftc-tools/
   launchctl bootstrap gui/$(id -u) /Users/Shared/ftc-tools/me.uprobotics.ftc-index.plist
   ```
   On the Mac mini `launchctl bootstrap` returns "Input/output error" for a plist under `/Volumes/home`, so it is
   loaded from the boot-volume copy; the `~/Library/LaunchAgents` copy is there for login-time loading. Check with
   `launchctl print gui/$(id -u)/me.uprobotics.ftc-index`.
9. Page: `cd page && wrangler deploy` (the endpoint ID and report address are already in `public/index.html`).
   On the Mac mini, npm-downloaded native binaries (esbuild, workerd) are killed on launch, so wrangler lives in
   `/Users/Shared/ftc-tools` (installed with `--ignore-scripts`), runs with `ESBUILD_BINARY_PATH=/opt/homebrew/bin/esbuild`
   (`brew install esbuild`), and its nested esbuild `main.js` has the version constant patched to the brew version.

Logs: `~/Library/Logs/ftc-index/ftc-index.log`. State: `~/Library/Application Support/ftc-index/state.sqlite`.
A run exits non-zero on any failure and does not ping the Monitor. Set the Healthchecks.io check to a 1-day period with a
12-hour grace; the weekly question test sends `<ping URL>/fail` when retrieval drops below 27/30, which alerts at once.

## Runbook (when the Monitor emails)

| Symptom | What to do |
|---|---|
| "Down" with no run in the log | Mac mini off, asleep, or offline. Power it on; `launchctl print gui/$(id -u)/me.uprobotics.ftc-index` should show the job; `launchctl kickstart -k gui/$(id -u)/me.uprobotics.ftc-index` runs it now. |
| Run logged `ok=False` | Read the `errors` list in the last `run … finished` log line. FIRST errors usually mean the hub or manual layout changed (`first_sources.py`). YouTube `blocked` means a 429; the next run retries, and a video parked after three blocks is listed in `status`. |
| "/fail" ping from the question test | Open the newest `tests/results/*-30-questions.md`; each failed question names the missing source. Run `ftc_index.py first` if a rule Item is missing, or check the AI Search instance for `error` items. |
| Answers stale but runs green | AI Search may be re-indexing; `ftc_index.py status` shows counts, the dashboard shows the queue. |
| Total loss of the Mac mini | Nothing durable is lost. On any Mac: clone the repo, do Setup 1–4 and 6–8, then `ftc_index.py first` and `backfill` until `videos.pending` is 0 (about two days at 150 videos a run). |

Any source owner who asks for removal gets it within a day: disable the channel or web source in `config.yaml` (or drop the
source type) and run `ftc_index.py reconcile`; stale Items are deleted.

## Season rollover

The Helper answers for one Season. When FIRST publishes the next Competition Manual: update `season` and the prompt in
`PROMPT.md` and `page/src/index.js`; run `ftc_index.py first`, which replaces the manual, Team Update, hub, and Q&A Items
(old-season rules are archived out of the index); keep Videos and web pages, which carry a `Season:` label and rank below
current content by `published`. Adding a filterable `season` metadata field is planned for that moment, not before.

## Using the index from your own tools

The AI Search public endpoint's `/mcp` route is open (60 requests a minute, no auth):
`https://146c9951-a803-43ec-82a5-667148163702.search.ai.cloudflare.com/mcp`, tool `search`, argument `query`. It returns
source chunks with their `Link:` headers; it does not generate answers (only the page's API Worker does, with link
verification). Packaged Claude and ChatGPT skills are a future goal.

## Why the API Worker

AI Search's hybrid search gives its vector leg a short time budget and silently returns keyword-only results
when the Workers AI query embedding is slow (2–3 of 10 searches had vector results on 2026-09-13). The Worker
runs `retrieval_type: keyword` and `retrieval_type: vector` as two calls, waits for both, fuses them with
Reciprocal Rank Fusion, and answers with `@cf/meta/llama-3.3-70b-instruct-fp8-fast` using the prompt in
`PROMPT.md`. It serves the same `/search` and `/chat/completions` shapes the UI snippets expect, so the page
just points `api-url` at `https://ftc.uprobotics.tech/api/`. A rate-limit binding caps each IP at 60/min.

The Worker also enforces two properties after generation: any Markdown or bare link whose URL did not appear in the
retrieved chunks is reduced to plain text, and every answer that cites anything ends with a **Sources** list of up to five
retrieved Items (title and link from each Item's header). `node --test tests/worker_links.test.mjs` covers this.
Questions are not logged; the only record of a bad answer is what a student sends through "Report a problem".

## Item keys and metadata

`<type>--<id>--<hash8>.md`, e.g. `manual--G204--a1b2c3d4.md`, `team_update--00--5120eae8.md`,
`video--<videoId>-0300--9f8e7d6c.md`, `qa--<id>--…`, `web--gm0-…`. Metadata: `source_type` (`manual`, `team_update`, `qa`,
`hub`, `video`, `guide`, `docs`, `vendor_docs`) and `published` (epoch seconds). In code an indexed document is an `Item`;
the SQLite table is still called `units` from before the rename. Unchanged content is skipped; changed content uploads the new key, waits for indexing,
then deletes the old key. A weekly reconcile compares the instance's item list with local state.

## YouTube politeness

One video at a time, one caption file per video, 10–25 s random delay, at most 150 videos per run, stop for the day on
HTTP 429 or a bot check. Videos without English captions are logged and skipped. Shorts are excluded; streams are included once
they have ended.

## License

MIT. *FIRST*®, *FIRST*® Tech Challenge, and BIOBUZZ™ are trademarks of *FIRST*; manual text remains © FIRST and is
indexed only to link students back to the official source.
