#!/usr/bin/env python3
"""UP Robotics FTC Helper — daily ingestion into Cloudflare AI Search.

Commands
  run          daily job: FIRST content, YouTube (RSS + pending backfill), weekly reconcile, monitor ping
  first        FIRST content only (manual, Team Updates, hub, Q&A)
  youtube      RSS discovery + process pending videos
  backfill     flat-playlist discovery for every channel, then process pending videos
  reconcile    compare the instance's items with local state and repair
  status       print counts

Options
  --out DIR    dry run: write the Markdown units to DIR, no upload, no state change
  --limit N    process at most N videos this run
  --no-ping    skip the monitor ping
  --config F   config file (default: config.yaml next to this script)
"""
from __future__ import annotations

import argparse
import fcntl
import json
import logging
import logging.handlers
import os
import random
import re
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import requests
import yaml

import first_sources as fs
import youtube_sources as yt

log = logging.getLogger("ftc-index")
HERE = Path(__file__).resolve().parent
KEY_RE = re.compile(r"^(manual|team_update|hub|qa|video)--.+--[0-9a-f]{8}\.md$")


# --------------------------------------------------------------------------- config & secrets

def load_config(path: Path) -> dict:
    cfg = yaml.safe_load(path.read_text())
    for k in ("state_dir", "log_dir"):
        cfg["paths"][k] = str(Path(cfg["paths"][k]).expanduser())
    return cfg


def keychain(account: str, env_var: str) -> str | None:
    if os.environ.get(env_var):
        return os.environ[env_var]
    p = subprocess.run(["security", "find-generic-password", "-s", "ftc-index", "-a", account, "-w"],
                       capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 and p.stdout.strip() else None


def channels_from(cfg: dict) -> list[yt.Channel]:
    d = cfg["youtube"]["defaults"]
    out = []
    for c in cfg["youtube"]["channels"]:
        pa = c.get("published_after")
        if isinstance(pa, str):
            pa = date.fromisoformat(pa)
        out.append(yt.Channel(name=c["name"], id=c["id"], title_include=c.get("title_include"), published_after=pa,
                              include_shorts=c.get("include_shorts", d["include_shorts"]),
                              include_streams=c.get("include_streams", d["include_streams"])))
    return out


# --------------------------------------------------------------------------- state

SCHEMA = """
CREATE TABLE IF NOT EXISTS units (
  unit_id TEXT PRIMARY KEY, source_type TEXT NOT NULL, key TEXT NOT NULL, hash8 TEXT NOT NULL,
  cf_item_id TEXT, published INTEGER, title TEXT, url TEXT, updated_at TEXT, last_seen_run TEXT);
CREATE INDEX IF NOT EXISTS units_key ON units(key);
CREATE TABLE IF NOT EXISTS videos (
  video_id TEXT PRIMARY KEY, channel_id TEXT, title TEXT, upload_date TEXT, duration INTEGER, via TEXT,
  status TEXT NOT NULL, caption_kind TEXT, windows INTEGER, note TEXT, discovered_at TEXT, attempted_at TEXT);
CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, started TEXT, finished TEXT, ok INTEGER, summary TEXT);
CREATE TABLE IF NOT EXISTS pending_deletes (old_item_id TEXT PRIMARY KEY, old_key TEXT, new_item_id TEXT, unit_id TEXT, queued_at TEXT);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""


class State:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def unit(self, unit_id: str):
        return self.db.execute("SELECT * FROM units WHERE unit_id=?", (unit_id,)).fetchone()

    def upsert_unit(self, u: fs.Unit, item_id: str | None, run_id: str):
        self.db.execute("""INSERT INTO units(unit_id,source_type,key,hash8,cf_item_id,published,title,url,updated_at,last_seen_run)
            VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(unit_id) DO UPDATE SET key=excluded.key,hash8=excluded.hash8,
            cf_item_id=excluded.cf_item_id,published=excluded.published,title=excluded.title,url=excluded.url,
            updated_at=excluded.updated_at,last_seen_run=excluded.last_seen_run""",
            (u.unit_id, u.source_type, u.key, u.hash8, item_id, u.published, u.title, u.url, now(), run_id))
        self.db.commit()

    def touch_unit(self, unit_id: str, run_id: str):
        self.db.execute("UPDATE units SET last_seen_run=? WHERE unit_id=?", (run_id, unit_id))
        self.db.commit()

    def stale_units(self, source_types: set[str], run_id: str):
        q = f"SELECT * FROM units WHERE source_type IN ({','.join('?' * len(source_types))}) AND last_seen_run IS NOT ?"
        return self.db.execute(q, (*source_types, run_id)).fetchall()

    def delete_unit(self, unit_id: str):
        self.db.execute("DELETE FROM units WHERE unit_id=?", (unit_id,))
        self.db.commit()

    def add_video(self, d: yt.Discovered) -> bool:
        cur = self.db.execute("""INSERT OR IGNORE INTO videos(video_id,channel_id,title,upload_date,duration,via,status,discovered_at)
                                 VALUES(?,?,?,?,?,?,'pending',?)""", (d.video_id, d.channel_id, d.title, d.upload_date, d.duration, d.via, now()))
        self.db.commit()
        return cur.rowcount > 0

    def pending_videos(self, limit: int):
        # newest first: RSS rows carry a date; flat rows keep channel listing order (already newest-first) via rowid
        return self.db.execute("""SELECT * FROM videos WHERE status='pending'
                                  ORDER BY COALESCE(upload_date,'99999999') DESC, rowid ASC LIMIT ?""", (limit,)).fetchall()

    def set_video(self, video_id: str, status: str, **kw):
        cols = ", ".join(f"{k}=?" for k in kw)
        self.db.execute(f"UPDATE videos SET status=?, attempted_at=?{', ' + cols if cols else ''} WHERE video_id=?",
                        (status, now(), *kw.values(), video_id))
        self.db.commit()

    def queue_delete(self, old_item_id: str, old_key: str, new_item_id: str, unit_id: str):
        self.db.execute("INSERT OR REPLACE INTO pending_deletes VALUES(?,?,?,?,?)", (old_item_id, old_key, new_item_id, unit_id, now()))
        self.db.commit()

    def pending_deletes(self):
        return self.db.execute("SELECT * FROM pending_deletes").fetchall()

    def clear_delete(self, old_item_id: str):
        self.db.execute("DELETE FROM pending_deletes WHERE old_item_id=?", (old_item_id,))
        self.db.commit()

    def get(self, k: str, default=None):
        r = self.db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return r["v"] if r else default

    def set(self, k: str, v: str):
        self.db.execute("INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))
        self.db.commit()

    def counts(self) -> dict:
        out = {}
        for r in self.db.execute("SELECT source_type, COUNT(*) n FROM units GROUP BY source_type"):
            out[f"units.{r['source_type']}"] = r["n"]
        for r in self.db.execute("SELECT status, COUNT(*) n FROM videos GROUP BY status"):
            out[f"videos.{r['status']}"] = r["n"]
        return out


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- Cloudflare AI Search items

class AISearch:
    def __init__(self, account_id: str, instance: str, token: str):
        if not account_id or not token:
            raise RuntimeError("Cloudflare account_id (config.yaml) and API token (Keychain ftc-index/cf-api-token) are required")
        self.base = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai-search/instances/{instance}"
        self.s = requests.Session()
        self.s.headers["Authorization"] = f"Bearer {token}"

    def _req(self, method: str, path: str, **kw) -> dict:
        last = None
        for attempt in range(4):
            r = self.s.request(method, self.base + path, timeout=180, **kw)
            if r.status_code in (429, 500, 502, 503, 504):
                last = f"HTTP {r.status_code}: {r.text[:200]}"
                time.sleep(2 ** attempt * 2)
                continue
            if r.status_code == 404 and method == "DELETE":
                return {"success": True, "result": None, "not_found": True}
            try:
                data = r.json()
            except ValueError:
                raise RuntimeError(f"{method} {path}: HTTP {r.status_code} non-JSON: {r.text[:200]}")
            if not r.ok or not data.get("success", True):
                raise RuntimeError(f"{method} {path}: HTTP {r.status_code}: {json.dumps(data.get('errors'))[:300]}")
            return data
        raise RuntimeError(f"{method} {path}: gave up after retries ({last})")

    def upload(self, key: str, body: str, metadata: dict) -> dict:
        """Submit a document for indexing and return immediately (status 'queued')."""
        data = self._req("POST", "/items",
                         files={"file": (key, body.encode("utf-8"), "text/markdown")},
                         data={"metadata": json.dumps(metadata)})
        return data["result"]

    def item(self, item_id: str) -> dict:
        return self._req("GET", f"/items/{item_id}")["result"]

    def count(self, status: str) -> int:
        info = self._req("GET", "/items", params={"status": status, "per_page": 1}).get("result_info") or {}
        return int(info.get("total_count", 0))

    def drain(self, timeout_s: int = 4 * 3600, poll_s: int = 30) -> bool:
        """Wait until nothing is queued or running. Returns False on timeout."""
        deadline = time.time() + timeout_s
        last = None
        while time.time() < deadline:
            pending = self.count("queued") + self.count("running")
            if pending == 0:
                return True
            if pending != last:
                log.info("indexing: %d items still queued/running", pending)
                last = pending
            time.sleep(poll_s)
        return False

    def errors(self) -> list[dict]:
        out, page = [], 1
        while True:
            data = self._req("GET", "/items", params={"status": "error", "page": page, "per_page": 50})
            res = data.get("result") or []
            out += res
            if len(res) < 50:
                return out
            page += 1

    def delete(self, item_id: str) -> None:
        self._req("DELETE", f"/items/{item_id}")

    def find_by_key(self, key: str) -> dict | None:
        data = self._req("GET", "/items", params={"key": key, "source": "builtin", "per_page": 5})
        res = data.get("result") or []
        return res[0] if res else None

    def list_all(self) -> list[dict]:
        out, page = [], 1
        while True:
            data = self._req("GET", "/items", params={"page": page, "per_page": 50})
            res = data.get("result") or []
            out += res
            info = data.get("result_info") or {}
            if not res or len(out) >= int(info.get("total_count", len(out))):
                break
            page += 1
        return out


class DryRun:
    """Stands in for AISearch when --out is given: writes files, uploads nothing."""
    def __init__(self, out: Path):
        self.out = out
        out.mkdir(parents=True, exist_ok=True)
        self.n = 0

    def upload(self, key, body, metadata):
        (self.out / key).write_text(body)
        self.n += 1
        return {"id": f"dry-{key}", "status": "completed"}

    def delete(self, item_id): pass
    def find_by_key(self, key): return None
    def list_all(self): return []
    def drain(self, timeout_s=0, poll_s=0): return True
    def errors(self): return []
    def item(self, item_id): return {"id": item_id, "status": "completed"}


# --------------------------------------------------------------------------- sync

class Indexer:
    def __init__(self, cfg: dict, state: State | None, cf, run_id: str):
        self.cfg, self.state, self.cf, self.run_id = cfg, state, cf, run_id
        self.stats = {"uploaded": 0, "unchanged": 0, "deleted": 0, "videos": 0, "no_captions": 0, "skipped": 0}
        self.errors: list[str] = []
        self.uploaded: list[tuple[str, str, str]] = []   # (unit_id, key, cf item id) submitted this run

    def sync_units(self, units: list[fs.Unit], complete_types: set[str] = frozenset()):
        """Upload new/changed units (non-blocking). Old keys of changed units are deleted by finalize()
        once the new item has indexed. Stale units of `complete_types` are deleted right away."""
        seen = set()
        for u in units:
            if u.unit_id in seen:
                log.warning("duplicate unit id %s; keeping first", u.unit_id)
                continue
            seen.add(u.unit_id)
            old = self.state.unit(u.unit_id) if self.state else None
            if old and old["hash8"] == u.hash8 and old["cf_item_id"]:
                self.state.touch_unit(u.unit_id, self.run_id)
                self.stats["unchanged"] += 1
                continue
            item = self.cf.upload(u.key, u.body, {"source_type": u.source_type, "published": str(u.published)})
            log.log(logging.DEBUG if isinstance(self.cf, DryRun) else logging.INFO, "uploaded %s (%s)", u.key, item.get("status"))
            self.stats["uploaded"] += 1
            self.uploaded.append((u.unit_id, u.key, item.get("id")))
            if self.state:
                if old and old["cf_item_id"] and old["key"] != u.key:
                    self.state.queue_delete(old["cf_item_id"], old["key"], item.get("id"), u.unit_id)
                self.state.upsert_unit(u, item.get("id"), self.run_id)
        if complete_types and self.state:
            for row in self.state.stale_units(set(complete_types), self.run_id):
                log.info("removing stale %s (%s)", row["key"], row["unit_id"])
                if row["cf_item_id"]:
                    self.cf.delete(row["cf_item_id"])
                self.state.delete_unit(row["unit_id"])
                self.stats["deleted"] += 1

    def finalize(self, timeout_s: int = 4 * 3600):
        """Wait for the indexing queue, surface errors, then delete superseded keys."""
        if not self.uploaded and not (self.state and self.state.pending_deletes()):
            return
        if not self.cf.drain(timeout_s=timeout_s):
            self.errors.append(f"indexing queue did not drain within {timeout_s}s")
            log.error("indexing queue did not drain within %ds", timeout_s)
        failed = {e.get("id"): e for e in self.cf.errors()}
        for unit_id, key, item_id in self.uploaded:
            if item_id in failed:
                msg = failed[item_id].get("error") or "indexing error"
                self.errors.append(f"index error {key}: {msg}")
                log.error("index error %s: %s", key, msg)
                try:
                    self.cf.delete(item_id)            # drop the failed copy so the retry does not duplicate the key
                except Exception as e:
                    log.warning("could not delete failed item %s: %s", key, e)
                if self.state:
                    self.state.delete_unit(unit_id)   # forces a re-upload next run
        if self.state:
            for row in self.state.pending_deletes():
                if row["new_item_id"] in failed:
                    continue  # keep the old copy until a good replacement exists
                try:
                    st = self.cf.item(row["new_item_id"]).get("status")
                except Exception as e:
                    log.warning("could not check %s: %s", row["new_item_id"], e)
                    continue
                if st == "completed":
                    self.cf.delete(row["old_item_id"])
                    self.state.clear_delete(row["old_item_id"])
                    self.stats["deleted"] += 1
                    log.info("deleted superseded %s", row["old_key"])

    # ---- FIRST
    def run_first(self):
        units = fs.collect_first()
        self.sync_units(units, complete_types={"manual", "team_update", "hub", "qa"})

    # ---- YouTube
    def discover(self, flat: bool):
        chans = channels_from(self.cfg)
        added = 0
        for ch in chans:
            try:
                found = yt.discover_flat(ch, self.cfg["paths"]["yt_dlp"]) if flat else yt.discover_rss(ch)
            except yt.Blocked as e:
                raise
            except Exception as e:  # one bad feed must not stop the others
                self.errors.append(f"discover {ch.name}: {e}")
                log.error("discover %s: %s", ch.name, e)
                continue
            for d in found:
                if not ch.title_ok(d.title):
                    continue
                if not ch.date_ok(d.upload_date):
                    continue
                if d.via == "rss" and not ch.include_shorts and d.duration is not None and d.duration <= 60:
                    continue
                if self.state and self.state.add_video(d):
                    added += 1
        log.info("discovery (%s): %d new videos", "flat" if flat else "rss", added)
        return added

    def process_pending(self, limit: int):
        if not self.state:
            log.info("dry run without state: nothing pending to process")
            return
        chans = {c.id: c for c in channels_from(self.cfg)}
        lo, hi = self.cfg["youtube"]["defaults"]["delay_seconds"]
        workdir = Path(self.cfg["paths"]["state_dir"]) / "captions"
        season_starts = self.cfg["season"]["starts"]
        if isinstance(season_starts, str):
            season_starts = date.fromisoformat(season_starts)
        rows = self.state.pending_videos(limit)
        log.info("processing up to %d pending videos (%d queued)", limit, len(rows))
        for i, row in enumerate(rows):
            vid = row["video_id"]
            ch = chans.get(row["channel_id"])
            try:
                cap = yt.fetch_captions(vid, workdir, self.cfg["paths"]["yt_dlp"])
            except yt.Blocked as e:
                self.errors.append(f"YouTube blocked at {vid}: {e}")
                log.error("YouTube rate limit / bot check at %s; stopping for today", vid)
                return
            except yt.NotYetAvailable as e:
                self.state.set_video(vid, "pending", note=f"not yet available: {str(e)[:120]}")
                log.info("video %s not yet available; will retry", vid)
                time.sleep(random.uniform(lo, hi))
                continue
            except Exception as e:
                self.state.set_video(vid, "error", note=str(e)[:300])
                self.errors.append(f"video {vid}: {e}")
                log.error("video %s: %s", vid, e)
                time.sleep(random.uniform(lo, hi))
                continue
            if cap is None:
                self.state.set_video(vid, "no_captions")
                self.stats["no_captions"] += 1
                log.info("no captions: %s", vid)
            elif ch and not ch.date_ok(cap.upload_date):
                self.state.set_video(vid, "skipped", note="published before channel cutoff", upload_date=cap.upload_date)
                self.stats["skipped"] += 1
            elif ch and not ch.title_ok(cap.title):
                self.state.set_video(vid, "skipped", note="title filter", upload_date=cap.upload_date, title=cap.title)
                self.stats["skipped"] += 1
            elif cap.live_status in ("is_live", "is_upcoming"):
                self.state.set_video(vid, "pending", note="live/upcoming; retry later")
            else:
                season = yt.season_label(cap.upload_date, season_starts, self.cfg["season"]["label"])
                units = yt.video_units(cap, season)
                self.sync_units(units)
                self.state.set_video(vid, "captioned", caption_kind=cap.kind, windows=len(units),
                                     upload_date=cap.upload_date, title=cap.title, duration=cap.duration)
                self.stats["videos"] += 1
                log.info("indexed %s: %d windows (%s captions, %s)", vid, len(units), cap.kind, season)
            for f in workdir.glob(f"{vid}.*"):
                f.unlink(missing_ok=True)
            if i < len(rows) - 1:
                time.sleep(random.uniform(lo, hi))

    # ---- reconcile
    def reconcile(self):
        if not self.state:
            return
        items = self.cf.list_all()
        remote = {it["key"]: it for it in items if it.get("key")}
        local = {r["key"]: r for r in self.state.db.execute("SELECT * FROM units")}
        for key, it in remote.items():
            if key not in local and KEY_RE.match(key):
                log.info("reconcile: deleting unknown item %s", key)
                self.cf.delete(it["id"])
                self.stats["deleted"] += 1
            elif key in local and local[key]["cf_item_id"] != it["id"]:
                self.state.db.execute("UPDATE units SET cf_item_id=? WHERE key=?", (it["id"], key))
        missing = [r for k, r in local.items() if k not in remote]
        for r in missing:
            log.info("reconcile: %s missing remotely; will re-upload", r["key"])
            if r["source_type"] == "video":
                vid = r["unit_id"].split(":", 1)[1].rsplit("-", 1)[0]
                self.state.set_video(vid, "pending", note="reconcile: re-index")
            self.state.delete_unit(r["unit_id"])
        self.state.db.commit()
        self.state.set("last_reconcile", now())
        log.info("reconcile: %d remote, %d local, %d missing", len(remote), len(local), len(missing))


# --------------------------------------------------------------------------- main

def setup_logging(log_dir: Path):
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fh = logging.handlers.RotatingFileHandler(log_dir / "ftc-index.log", maxBytes=5_000_000, backupCount=5)
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    root.handlers[:] = [fh, sh]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["run", "first", "youtube", "backfill", "reconcile", "status"])
    ap.add_argument("--config", default=str(HERE / "config.yaml"))
    ap.add_argument("--out", help="dry run: write units to this directory instead of uploading")
    ap.add_argument("--limit", type=int, help="max videos to process this run")
    ap.add_argument("--no-ping", action="store_true")
    args = ap.parse_args(argv)

    cfg = load_config(Path(args.config))
    setup_logging(Path(cfg["paths"]["log_dir"]))
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    dry = args.out is not None
    state_dir = Path(cfg["paths"]["state_dir"])
    state_dir.mkdir(parents=True, exist_ok=True)

    if args.command == "status":
        st = State(state_dir / "state.sqlite")
        for k, v in sorted(st.counts().items()):
            print(f"{k:24} {v}")
        print(f"{'last_reconcile':24} {st.get('last_reconcile', '-')}")
        return 0

    lock = open(state_dir / "run.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log.error("another run is in progress; exiting")
        return 3

    if dry:
        cf, state = DryRun(Path(args.out)), None
    else:
        token = keychain("cf-api-token", "FTC_CF_API_TOKEN")
        cf = AISearch(cfg["cloudflare"]["account_id"], cfg["cloudflare"]["instance"], token)
        state = State(state_dir / "state.sqlite")
        state.db.execute("INSERT INTO runs(run_id,started) VALUES(?,?)", (run_id, now()))
        state.db.commit()

    ix = Indexer(cfg, state, cf, run_id)
    limit = args.limit or cfg["youtube"]["defaults"]["backfill_per_day"]
    log.info("run %s: %s%s", run_id, args.command, " (dry run)" if dry else "")
    started = time.time()
    try:
        if args.command in ("run", "first"):
            try:
                ix.run_first()
            except Exception as e:
                ix.errors.append(f"FIRST: {e}")
                log.exception("FIRST content failed")
        if args.command in ("run", "youtube", "backfill"):
            try:
                weekly = args.command == "backfill" or (
                    args.command == "run" and datetime.now().weekday() == cfg["youtube"]["defaults"]["reconcile_weekday"])
                if args.command != "backfill":
                    ix.discover(flat=False)
                if weekly:
                    ix.discover(flat=True)
                ix.process_pending(limit)
            except yt.Blocked as e:
                ix.errors.append(f"YouTube blocked: {e}")
                log.error("YouTube blocked during discovery: %s", e)
            except Exception as e:
                ix.errors.append(f"YouTube: {e}")
                log.exception("YouTube stage failed")
        if args.command in ("run", "first", "youtube", "backfill"):
            try:
                ix.finalize()
            except Exception as e:
                ix.errors.append(f"finalize: {e}")
                log.exception("finalize failed")
        if args.command == "reconcile" or (args.command == "run" and not dry and
                                            datetime.now().weekday() == cfg["youtube"]["defaults"]["reconcile_weekday"]):
            try:
                ix.reconcile()
            except Exception as e:
                ix.errors.append(f"reconcile: {e}")
                log.exception("reconcile failed")
    finally:
        ok = not ix.errors
        summary = json.dumps({"stats": ix.stats, "errors": ix.errors[:20], "seconds": int(time.time() - started)})
        log.info("run %s finished ok=%s %s", run_id, ok, summary)
        if state:
            state.db.execute("UPDATE runs SET finished=?, ok=?, summary=? WHERE run_id=?", (now(), int(ok), summary, run_id))
            state.db.commit()
        if dry:
            log.info("dry run wrote %d files to %s", cf.n, args.out)

    if ok and not dry and not args.no_ping and args.command == "run":
        ping = keychain("monitor-ping-url", "FTC_MONITOR_PING_URL")
        if ping:
            try:
                requests.get(ping, timeout=20)
                log.info("monitor pinged")
            except Exception as e:
                log.error("monitor ping failed: %s", e)
        else:
            log.warning("no monitor ping URL configured (Keychain ftc-index/monitor-ping-url)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
