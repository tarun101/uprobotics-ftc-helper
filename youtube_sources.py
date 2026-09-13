"""YouTube captions for the UP Robotics FTC Helper.

Discovery: channel RSS feeds daily, yt-dlp --flat-playlist for backfill.
Captions: yt-dlp --skip-download, English creator captions preferred over auto captions.
Output: one Markdown item per 2-3 minute caption window with a &t= link.

Politeness lives in ftc_index.py (one video at a time, random delays, daily cap,
stop on 429 / bot check). Nothing here downloads video or audio.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import subprocess
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import feedparser

log = logging.getLogger("ftc-index.youtube")

YT_DLP = "/opt/homebrew/bin/yt-dlp"
WINDOW_TARGET_S = 150      # close a window at the first cue after 2.5 minutes
WINDOW_MAX_S = 180         # never longer than 3 minutes
WINDOW_MAX_CHARS = 6000
BLOCK_PATTERNS = re.compile(r"HTTP Error 429|Sign in to confirm|not a bot|Too Many Requests", re.I)


class Blocked(RuntimeError):
    """YouTube answered with a rate limit or bot check. Stop for the day."""


class NotYetAvailable(RuntimeError):
    """Scheduled live event or premiere; captions can only exist later."""


NOT_YET_PATTERNS = re.compile(r"live event will begin|Premieres in|will begin in|is not yet available|not yet been made available", re.I)


@dataclass
class Channel:
    name: str
    id: str
    title_include: list[str] | None = None
    published_after: date | None = None
    include_shorts: bool = False
    include_streams: bool = True

    @property
    def feed_url(self) -> str:
        return f"https://www.youtube.com/feeds/videos.xml?channel_id={self.id}"

    def title_ok(self, title: str) -> bool:
        if not self.title_include:
            return True
        t = title.lower()
        return any(s.lower() in t for s in self.title_include)

    def date_ok(self, upload_date: str | None) -> bool:
        """upload_date is YYYYMMDD; unknown dates pass (checked again after extraction)."""
        if not self.published_after or not upload_date:
            return True
        try:
            return datetime.strptime(upload_date, "%Y%m%d").date() >= self.published_after
        except ValueError:
            return True


@dataclass
class Discovered:
    video_id: str
    channel_id: str
    title: str
    upload_date: str | None     # YYYYMMDD
    duration: int | None
    via: str                    # rss | flat


@dataclass
class Cue:
    start: float
    text: str


@dataclass
class Captions:
    video_id: str
    title: str
    channel: str
    channel_id: str
    upload_date: str            # YYYYMMDD
    timestamp: int              # epoch seconds
    duration: int
    live_status: str
    kind: str                   # creator | auto
    cues: list[Cue]


# --------------------------------------------------------------------------- season label

def season_label(upload_date: str, current_starts: date, current_label: str) -> str:
    d = datetime.strptime(upload_date, "%Y%m%d").date()
    if d >= current_starts:
        return current_label
    y = d.year if d.month >= 9 else d.year - 1
    return f"{y}-{(y + 1) % 100:02d}"


# --------------------------------------------------------------------------- discovery

def discover_rss(ch: Channel) -> list[Discovered]:
    feed = feedparser.parse(ch.feed_url, agent="UPRoboticsFTCHelper/1.0")
    if feed.bozo and not feed.entries:
        raise RuntimeError(f"RSS feed failed for {ch.name}: {getattr(feed, 'bozo_exception', '')}")
    out = []
    for e in feed.entries:
        vid = getattr(e, "yt_videoid", None) or e.get("id", "").rsplit(":", 1)[-1]
        if not vid:
            continue
        pub = e.get("published", "")
        upload = None
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})", pub)
        if m:
            upload = "".join(m.groups())
        out.append(Discovered(vid, ch.id, e.get("title", ""), upload, None, "rss"))
    return out


def discover_flat(ch: Channel, yt_dlp: str = YT_DLP) -> list[Discovered]:
    """List a channel's videos (and streams) newest-first without touching each video."""
    tabs = ["videos"] + (["streams"] if ch.include_streams else [])
    out: list[Discovered] = []
    for tab in tabs:
        cmd = [yt_dlp, "--flat-playlist", "--no-warnings", "--print", "%(id)s\t%(title)s\t%(duration)s\t%(live_status)s",
               f"https://www.youtube.com/channel/{ch.id}/{tab}"]
        log.info("flat-playlist %s/%s", ch.name, tab)
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        if p.returncode != 0:
            if BLOCK_PATTERNS.search(p.stderr):
                raise Blocked(p.stderr.strip()[-300:])
            if "does not have a" in p.stderr or "This channel does not" in p.stderr:
                continue  # channel has no streams tab
            raise RuntimeError(f"flat-playlist failed for {ch.name}/{tab}: {p.stderr.strip()[-300:]}")
        for line in p.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) < 2 or not parts[0]:
                continue
            vid, title = parts[0], parts[1]
            dur = int(float(parts[2])) if len(parts) > 2 and parts[2] not in ("NA", "", "None") else None
            status = parts[3] if len(parts) > 3 else ""
            if status in ("is_live", "is_upcoming"):
                continue
            out.append(Discovered(vid, ch.id, title, None, dur, "flat"))
    return out


# --------------------------------------------------------------------------- captions

def fetch_captions(video_id: str, workdir: Path, yt_dlp: str = YT_DLP) -> Captions | None:
    """Fetch English captions for one video. None when the video has no captions."""
    workdir.mkdir(parents=True, exist_ok=True)
    meta_path = workdir / f"{video_id}.meta.json"
    if meta_path.exists():
        meta_path.unlink()
    fields = "%(.{id,title,channel,channel_id,upload_date,timestamp,duration,live_status,subtitles,requested_subtitles})j"
    cmd = [yt_dlp, "--no-simulate", "--skip-download", "--no-playlist", "--no-warnings",
           "--write-subs", "--write-auto-subs", "--sub-langs", "en", "--sub-format", "json3/vtt/best",   # one file: creator "en" if it exists, else auto "en"
           "-o", str(workdir / "%(id)s.%(ext)s"), "--print-to-file", fields, str(meta_path),
           f"https://www.youtube.com/watch?v={video_id}"]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if BLOCK_PATTERNS.search(p.stderr or ""):
        raise Blocked(p.stderr.strip()[-300:])
    if NOT_YET_PATTERNS.search(p.stderr or ""):
        raise NotYetAvailable(p.stderr.strip()[-200:])
    if p.returncode != 0 or not meta_path.exists():
        raise RuntimeError(f"yt-dlp failed for {video_id}: {p.stderr.strip()[-300:]}")
    meta = json.loads(meta_path.read_text())
    requested = meta.get("requested_subtitles") or {}
    manual_langs = {k for k in (meta.get("subtitles") or {}) if k.startswith("en")}
    # --print-to-file runs before download, so requested_subtitles has no filepath yet: look on disk.
    files = {f.name[len(video_id) + 1:].rsplit(".", 1)[0]: f for f in workdir.glob(f"{video_id}.*")
             if f.suffix in (".json3", ".vtt") and f.name.startswith(video_id + ".")}
    path = None
    for lang in ("en", "en-US", "en-GB", "en-orig", *sorted(files)):
        if lang in files:
            path = files[lang]
            break
    if path is None:
        return None
    kind = "creator" if manual_langs else "auto"
    cues = parse_json3(path.read_text()) if path.suffix == ".json3" else parse_vtt(path.read_text())
    if not cues:
        return None
    duration = int(meta.get("duration") or 0)
    if duration and cues[-1].start > duration + 30 and cues[0].start > 30:
        offset = cues[0].start
        log.warning("%s: captions start at %.0fs on a %ds video; shifting by -%.0fs", video_id, offset, duration, offset)
        cues = [Cue(max(0.0, c.start - offset), c.text) for c in cues]
    upload = meta.get("upload_date") or datetime.fromtimestamp(meta.get("timestamp") or 0, timezone.utc).strftime("%Y%m%d")
    ts = meta.get("timestamp") or int(datetime.strptime(upload, "%Y%m%d").replace(tzinfo=timezone.utc).timestamp())
    return Captions(video_id=meta["id"], title=meta.get("title") or video_id, channel=meta.get("channel") or "",
                    channel_id=meta.get("channel_id") or "", upload_date=upload, timestamp=int(ts),
                    duration=int(meta.get("duration") or 0), live_status=meta.get("live_status") or "", kind=kind, cues=cues)


def parse_json3(text: str) -> list[Cue]:
    data = json.loads(text)
    cues: list[Cue] = []
    for ev in data.get("events", []):
        segs = ev.get("segs")
        if not segs:
            continue
        t = "".join(s.get("utf8", "") for s in segs)
        t = re.sub(r"\s+", " ", t).strip()
        if t:
            cues.append(Cue(ev.get("tStartMs", 0) / 1000.0, t))
    return cues


VTT_TIME_RE = re.compile(r"(\d+):(\d{2}):(\d{2})\.(\d{3})\s*-->")


def parse_vtt(text: str) -> list[Cue]:
    cues: list[Cue] = []
    last = ""
    for block in re.split(r"\n\s*\n", text):
        m = VTT_TIME_RE.search(block)
        if not m:
            continue
        start = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)) + int(m.group(4)) / 1000
        lines = block.split("\n")[1:]
        clean = []
        for ln in lines:
            ln = re.sub(r"<[^>]+>", "", ln).replace("&nbsp;", " ").strip()
            if ln and ln != last and ln not in clean:
                clean.append(ln)
        if clean:
            last = clean[-1]
            cues.append(Cue(start, " ".join(clean)))
    return cues


# --------------------------------------------------------------------------- windows

@dataclass
class Window:
    start: float
    end: float
    text: str


def make_windows(cues: list[Cue], duration: int) -> list[Window]:
    windows: list[Window] = []
    buf: list[str] = []
    w_start = None
    for c in cues:
        if w_start is None:
            w_start = c.start
        elapsed = c.start - w_start
        if buf and (elapsed >= WINDOW_TARGET_S or sum(len(b) for b in buf) > WINDOW_MAX_CHARS) or (buf and elapsed >= WINDOW_MAX_S):
            windows.append(Window(w_start, c.start, " ".join(buf)))
            buf, w_start = [], c.start
        buf.append(c.text)
    if buf:
        windows.append(Window(w_start or 0.0, float(duration or (w_start or 0) + 1), " ".join(buf)))
    return windows


def fmt_ts(s: float) -> str:
    s = int(s)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def video_items(cap: Captions, season: str):
    """Yield (item_id, key_base, title, body, url, published) per window, as first_sources.Item."""
    from first_sources import Item  # same dataclass keeps one upload path
    pub_iso = datetime.strptime(cap.upload_date, "%Y%m%d").strftime("%Y-%m-%d")
    video_url = f"https://www.youtube.com/watch?v={cap.video_id}"
    items = []
    for w in make_windows(cap.cues, cap.duration):
        start = int(w.start)
        link = f"{video_url}&t={start}s"
        head = [f"# {cap.title} — {fmt_ts(w.start)} to {fmt_ts(w.end)}",
                f"Channel: {cap.channel}", f"Published: {pub_iso}", f"Season: {season}",
                f"Video: {video_url}", f"Link (this moment): {link}",
                f"Captions: {'creator captions' if cap.kind == 'creator' else 'auto-generated captions'}",
                "Type: video transcript window",
                "Note: videos are advice and examples, not rules." + (" This video is from an earlier season; rules may have changed." if season != "2026-27" else "")]
        body = "\n".join(head) + "\n---\n\n" + w.text + "\n"
        items.append(Item(item_id=f"video:{cap.video_id}-{start:04d}", source_type="video",
                          title=f"{cap.title} @ {fmt_ts(w.start)}", body=body, url=link, published=cap.timestamp))
    return items
