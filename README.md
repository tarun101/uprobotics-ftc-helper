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
Mac mini (home internet) — launchd at 23:00, 02:00 and 05:00 ET
  ftc_index.py run
    ├─ FIRST: HTML manual → one Markdown file per rule / section; Team Update PDFs → one file each; hub → one file
    ├─ YouTube: RSS + yt-dlp captions → one Markdown file per 2–3 minute window (with &t= link)
    ├─ state.sqlite: what is uploaded, content hashes, video queue
    ├─ Cloudflare AI Search Items API: upload new/changed files, delete old ones
    └─ on success: ping a dead-man's-switch monitor

Cloudflare AI Search instance "ftc-2026" (built-in storage, hybrid search)
    └─ public endpoint: /chat/completions, /search, /mcp → page snippets + Claude/ChatGPT skill
```

Only URLs under `https://ftc-resources.firstinspires.org/ftc/game/` are fetched. No video or audio is
downloaded; captions only. No logins, cookies, or proxies.

## Files

| Path | Purpose |
|---|---|
| `ftc_index.py` | CLI: `run`, `first`, `youtube`, `backfill`, `reconcile`, `status`; state, upload, politeness |
| `first_sources.py` | Manual splitter (rule anchors + headings), Team Update PDFs, hub, Q&A archive |
| `youtube_sources.py` | RSS / flat-playlist discovery, yt-dlp captions, 2–3 minute windows, season labels |
| `config.yaml` | Channels and filters, instance name, paths (no secrets) |
| `page/` | Static page for `ftc.uprobotics.tech` (Cloudflare AI Search UI snippets) + `wrangler.jsonc` |
| `launchd/` | launchd plist template for the 23:00 / 02:00 / 05:00 runs |
| `tests/` | 30-question test and bad-question list used for acceptance |

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
8. Schedule (23:00, 02:00 and 05:00 local time; three runs while the video backlog drains):
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
A run exits non-zero on any failure and does not ping the monitor, so the monitor emails after two missed days.

## Item keys and metadata

`<type>--<id>--<hash8>.md`, e.g. `manual--G204--a1b2c3d4.md`, `team_update--00--5120eae8.md`,
`video--<videoId>-0300--9f8e7d6c.md`. Metadata: `source_type` (`manual`, `team_update`, `qa`, `hub`, `video`) and
`published` (epoch seconds). Unchanged content is skipped; changed content uploads the new key, waits for indexing,
then deletes the old key. A weekly reconcile compares the instance's item list with local state.

## YouTube politeness

One video at a time, one caption file per video, 10–25 s random delay, at most 150 videos per run, stop for the day on
HTTP 429 or a bot check. Videos without English captions are logged and skipped. Shorts are excluded; streams are included once
they have ended.

## License

MIT. *FIRST*®, *FIRST*® Tech Challenge, and BIOBUZZ™ are trademarks of *FIRST*; manual text remains © FIRST and is
indexed only to link students back to the official source.
