"""Official FIRST content for the UP Robotics FTC Helper.

Fetches only URLs under https://ftc-resources.firstinspires.org/ftc/game and
turns them into small Markdown documents ("units"):

  * the HTML Competition Manual -> one item per rule, one per heading section
  * Team Update PDFs            -> one item per Team Update
  * the season hub page         -> one item
  * the official Q&A system      -> one item per answered question, from its public RSS feed (never logs in)

Nothing here talks to Cloudflare or touches state; see ftc_index.py.
"""
from __future__ import annotations

import hashlib
import io
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup, NavigableString, Tag

log = logging.getLogger("ftc-index.first")

HOST = "ftc-resources.firstinspires.org"
GAME_PATH = "/ftc/game"
HUB_URL = f"https://{HOST}{GAME_PATH}"
QA_HOST = "ftc-qa.firstinspires.org"                       # readable without login; asking needs a Lead Coach login we never use
QA_RSS_URL = f"https://{QA_HOST}/rss/answers.rss"
QA_ONEPAGE_URL = f"https://{QA_HOST}/onepage.html"
USER_AGENT = "UPRoboticsFTCHelper/1.0 (+https://ftc.uprobotics.tech; community tool, not affiliated with FIRST)"

RULE_ID_RE = re.compile(r"^[A-Z]{1,2}\d{3}$")
RULE_PARA_CLASSES = {
    "RuleNumber-Game", "RuleNumber-Robot", "RuleNumber-Event", "RuleNumber-Inspection",
    "TRules-Evergreen", "RulesNumbering-awards", "C-ChampsRules",
}
QUOTE_CLASSES = {"OrangeBox", "BlueBox", "Callout", "MsoIntenseQuote", "Quotes"}
CAPTION_CLASSES = {"MsoCaption", "OrangeBox-caption"}
SKIP_CLASS_PREFIXES = ("MsoToc",)
BULLET_GLYPHS = "·•o-–—*"
DIAGRAM_NOTE = "Diagram in the manual; open the link to see it."


class SourceError(RuntimeError):
    """A FIRST source could not be fetched or parsed."""


@dataclass
class Item:
    """One Markdown document to index."""
    item_id: str          # stable identity, e.g. "manual:G202", "tu:00", "hub", "qa:Q12"
    source_type: str      # manual | team_update | qa | hub
    title: str
    body: str             # full Markdown including header
    url: str
    published: int        # epoch seconds

    @property
    def hash8(self) -> str:
        return hashlib.sha256(self.body.encode("utf-8")).hexdigest()[:8]

    @property
    def key(self) -> str:
        kind, _, ident = self.item_id.partition(":")
        ident = ident or kind
        ident = re.sub(r"[^A-Za-z0-9._-]+", "-", ident).strip("-") or "x"
        return f"{kind}--{ident}--{self.hash8}.md"[:128]


@dataclass
class HubEntry:
    title: str
    url: str
    version: str | None = None
    updated: str | None = None   # "Sep 12, 2026" as printed on the hub
    external: bool = False       # listed on the hub but outside /ftc/game/: linked, never fetched


@dataclass
class HubInfo:
    season_title: str
    entries: list[HubEntry] = field(default_factory=list)

    def find(self, path_suffix: str) -> HubEntry | None:
        for e in self.entries:
            if not e.external and urlparse(e.url).path.rstrip("/").endswith(path_suffix):
                return e
        return None

    @property
    def team_updates(self) -> list[HubEntry]:
        out = []
        for e in self.entries:
            if not e.external and re.search(r"/tu-(\d+)$", urlparse(e.url).path):
                out.append(e)
        return out

    @property
    def qa_archive(self) -> HubEntry | None:
        for e in self.entries:
            if not e.external and re.search(r"q\s*&\s*a\s+archive", e.title, re.I):
                return e
        return None


# --------------------------------------------------------------------------- fetch

def allowed(url: str) -> bool:
    """Only the game hub tree and the public Q&A site are ever fetched."""
    p = urlparse(url)
    if p.scheme != "https":
        return False
    if p.netloc == QA_HOST:
        return True
    return p.netloc == HOST and (p.path == GAME_PATH or p.path.startswith(GAME_PATH + "/"))


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


def fetch(session: requests.Session, url: str) -> requests.Response:
    """GET an allowed URL (see allowed()), following redirects that stay allowed."""
    if not allowed(url):
        raise SourceError(f"refusing to fetch outside {HUB_URL} / {QA_HOST}: {url}")
    log.debug("GET %s", url)
    r = session.get(url, timeout=90, allow_redirects=True)
    if not allowed(r.url):
        raise SourceError(f"redirect left the allowed hosts: {url} -> {r.url}")
    if r.status_code != 200:
        raise SourceError(f"HTTP {r.status_code} for {url}")
    return r


def html_text(r: requests.Response) -> str:
    """Decode an HTML response: honour a declared charset, otherwise UTF-8 (requests would guess latin-1)."""
    ctype = r.headers.get("content-type", "")
    m = re.search(r"charset=([\w-]+)", ctype, re.I)
    return r.content.decode(m.group(1) if m else "utf-8", errors="replace")


def parse_hub_date(text: str | None) -> int:
    """'Sep 12, 2026' -> epoch seconds (UTC midnight). Unknown -> now."""
    if text:
        for fmt in ("%b %d, %Y", "%B %d, %Y", "%Y-%m-%d"):
            try:
                return int(datetime.strptime(text.strip(), fmt).replace(tzinfo=timezone.utc).timestamp())
            except ValueError:
                pass
    return int(datetime.now(timezone.utc).timestamp())


# --------------------------------------------------------------------------- text helpers

def norm(text: str) -> str:
    text = text.replace("\xa0", " ").replace("‑", "-")
    return re.sub(r"\s+", " ", text).strip()


def el_text(el: Tag) -> str:
    return norm(el.get_text(" "))


def classes(el: Tag) -> set[str]:
    c = el.get("class") or []
    return set(c if isinstance(c, list) else [c])


# --------------------------------------------------------------------------- hub

VERSION_RE = re.compile(r"Version\s+([^\s(]+)\s*\(updated\s+([A-Za-z]+\s+\d{1,2},\s+\d{4})\)", re.I)


def parse_hub(html: str, base_url: str = HUB_URL) -> HubInfo:
    soup = BeautifulSoup(html, "lxml")
    page_text = norm(soup.get_text(" "))
    m = re.search(r"(FIRST\s+Tech Challenge\s+\d{4}-\d{4}\s+Game\s*&\s*Season Materials for\s+.+?presented by\s+\w+)", page_text, re.I)
    season_title = m.group(1) if m else "FIRST Tech Challenge season materials"
    info = HubInfo(season_title=season_title)
    seen = set()
    for a in soup.select("a[href]"):
        href = urljoin(base_url + "/", a["href"])
        external = not allowed(href)
        if href.rstrip("/") == HUB_URL or (external and urlparse(href).netloc.endswith("firstinspires.org") is False and "youtu" not in href):
            continue  # skip nav/footer links to unrelated sites; keep FIRST pages and the game animation
        title = el_text(a)
        if not title or href in seen or title in ("Login", "About", "Help Center", "Donate", "Report a Concern", "Legal Notices", "Privacy Policy", "FIRST Resources", "FIRST Tech Challenge"):
            continue
        seen.add(href)
        container = a.find_parent(["li", "div", "p", "td"])
        ctx = el_text(container) if container else ""
        m = VERSION_RE.search(ctx)
        info.entries.append(HubEntry(title=title, url=href, external=external,
                                     version=m.group(1) if m else None,
                                     updated=m.group(2) if m else None))
    return info


def hub_items(info: HubInfo, url: str = HUB_URL) -> list[Item]:
    """Two small items: current versions/Team Updates, and the hub's list of official resource links."""
    manual = info.find("/cm-html")
    published = parse_hub_date(manual.updated if manual else None)
    lines = [f"# {info.season_title} — current versions and Team Updates", "Source: official FIRST Tech Challenge game hub",
             f"Link: {url}", "Type: season hub", "---", ""]
    if manual:
        lines.append(f"Current Competition Manual version: {manual.version or 'unknown'} (updated {manual.updated or 'date not listed'}). HTML manual: {manual.url}")
    tus = info.team_updates
    if tus:
        latest = max(tus, key=lambda t: int(re.search(r"/tu-(\d+)$", urlparse(t.url).path).group(1)))
        num = re.search(r"/tu-(\d+)$", urlparse(latest.url).path).group(1)
        lines.append(f"Latest Team Update (newest, most recent): Team Update {num} ({latest.version or 'TU' + num}), "
                     f"published {latest.updated or 'date not listed'}: {latest.url}")
        lines.append("All Team Updates published so far: " + ", ".join(t.title for t in tus) + ".")
    qa = info.qa_archive
    lines.append("Public Q&A archive: " + (qa.url if qa else "not yet published on the hub."))
    lines += ["", "Official documents listed on the hub:", ""]
    for e in info.entries:
        if e.external:
            continue
        ver = f" — Version {e.version}" if e.version else ""
        upd = f" (updated {e.updated})" if e.updated else ""
        lines.append(f"- {e.title}{ver}{upd}: {e.url}")
    items = [Item(item_id="hub", source_type="hub", title=info.season_title, body="\n".join(lines) + "\n", url=url, published=published)]

    ext = [e for e in info.entries if e.external]
    if ext:
        r = ["# Where to find official FTC resources: programming, robot and team, playing field, volunteer, and event resources",
             "Source: official FIRST Tech Challenge game hub (\"Find More Resources\" and related links)", f"Link: {url}", "Type: season hub", "---", "",
             "The official FIRST Tech Challenge game hub links to these FIRST resource pages. Programming resources (SDK, "
             "Android Studio, Blocks, OnBot Java, control system documentation) are on the FIRST website's technology page. "
             "Field drawings, CAD, and the field setup guide are under Playing Field Resources.", ""]
        for e in ext:
            r.append(f"- {e.title}: {e.url}")
        items.append(Item(item_id="hub:resources", source_type="hub", title="Official FTC resources", body="\n".join(r) + "\n", url=url, published=published))
    return items


# --------------------------------------------------------------------------- manual

@dataclass
class Block:
    kind: str                   # heading | rule | para | table
    md: str
    level: int = 0
    anchors: list[str] = field(default_factory=list)
    rule_id: str | None = None
    number: str | None = None   # heading number "10.1"
    title: str | None = None    # heading title / rule headline


def _img_line(img: Tag) -> str:
    alt = norm(img.get("alt") or "")
    return f"*Image: {alt}* {DIAGRAM_NOTE}" if alt else f"*Image.* {DIAGRAM_NOTE}"


def _para_md(p: Tag) -> str | None:
    cls = classes(p)
    if any(c.startswith(pref) for c in cls for pref in SKIP_CLASS_PREFIXES):
        return None
    imgs = p.find_all("img")
    text = el_text(p)
    if imgs:
        parts = [_img_line(i) for i in imgs]
        if text:
            parts.append(text)
        return "\n".join(parts)
    if not text:
        return None
    if cls & CAPTION_CLASSES:
        return f"**{text}**"
    if "Violation" in cls:
        return f"**{text}**" if text.lower().startswith(("violation", "universal violation")) else text
    if "DiagramLabel" in cls:
        return f"*{text}*"
    if any(c in QUOTE_CLASSES or c.startswith(("OrangeBox", "BlueBox")) for c in cls):
        # side-box explanations: keep as blockquote; lists inside keep their letter/bullet
        return "> " + _listify(text)
    return _listify(text)


def _listify(text: str) -> str:
    """Normalise Word bullet glyphs to Markdown bullets; keep 'A.'/'1.' lists as-is."""
    if text and text[0] in BULLET_GLYPHS and (len(text) == 1 or text[1] == " "):
        return "- " + text[2:].lstrip()
    return text


def _table_md(t: Tag) -> str:
    rows: list[list[str]] = []
    for tr in t.find_all("tr"):
        cells = []
        for td in tr.find_all(["td", "th"]):
            imgs = td.find_all("img")
            txt = el_text(td)
            if imgs:
                txt = " ".join(_img_line(i) for i in imgs) + (" " + txt if txt else "")
            cells.append(txt.replace("|", "\\|"))
        if any(c.strip() for c in cells):
            rows.append(cells)
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    if width == 1:
        return "\n".join(r[0] for r in rows)
    out = ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join(["---"] * width) + " |"]
    out += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(out)


HEADING_NUM_RE = re.compile(r"^(\d+(?:\.\d+)*)\s+(.+)$")
RULE_HEADLINE_RE = re.compile(r"^([A-Z]{1,2}\d{3})\s+(.*)$")


def _split_headline(rest: str) -> tuple[str, str]:
    """'*Follow the CIC. When playing…' -> ('*Follow the CIC.', 'When playing…')"""
    m = re.match(r"^(.+?[.!?])(?:\s+(?=[A-Z“\"(])|$)(.*)$", rest)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return rest.strip(), ""


def manual_blocks(soup: BeautifulSoup) -> list[Block]:
    body = soup.body or soup
    blocks: list[Block] = []
    for el in body.find_all(["h1", "h2", "h3", "h4", "p", "table"]):
        if el.name != "table" and el.find_parent("table") is not None:
            continue  # cell content is rendered with its table
        anchors = [a.get("name") for a in el.find_all("a", attrs={"name": True})]
        if el.name.startswith("h"):
            text = el_text(el)
            if not text:
                continue
            m = HEADING_NUM_RE.match(text)
            blocks.append(Block("heading", text, level=int(el.name[1]), anchors=anchors,
                                number=m.group(1) if m else None, title=(m.group(2) if m else text).strip()))
            continue
        if el.name == "table":
            md = _table_md(el)
            if md:
                blocks.append(Block("table", md))
            continue
        # paragraph
        rule_anchor = next((a for a in anchors if a and RULE_ID_RE.match(a)), None)
        text = el_text(el)
        if rule_anchor and (classes(el) & RULE_PARA_CLASSES or text.startswith(rule_anchor)):
            m = RULE_HEADLINE_RE.match(text)
            rest = m.group(2) if m else text
            headline, remainder = _split_headline(rest)
            md = f"**{rule_anchor} {headline}**" + (f" {remainder}" if remainder else "")
            blocks.append(Block("rule", md, anchors=anchors, rule_id=rule_anchor, title=headline))
            continue
        md = _para_md(el)
        if md:
            blocks.append(Block("para", md, anchors=anchors))
    return blocks


def _section_id(number: str | None, title: str, used: set[str]) -> str:
    base = number if number else re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40] or "section"
    sid, n = base, 2
    while sid in used:
        sid, n = f"{base}-{n}", n + 1
    used.add(sid)
    return sid


def manual_items(html: str, html_url: str, version: str, updated: str | None) -> list[Item]:
    """Split the Word-exported HTML manual into rule items and section items."""
    soup = BeautifulSoup(html, "lxml")
    blocks = manual_blocks(soup)
    published = parse_hub_date(updated)
    updated_txt = updated or "date not listed"
    manual_label = f"FTC BIOBUZZ Competition Manual {version} (updated {updated_txt})"

    items: list[Item] = []
    used_ids: set[str] = set()
    path: list[tuple[int, str]] = []     # (level, "10.1 MATCH Overview")
    cur_kind: str | None = None          # "section" | "rule"
    cur_lines: list[str] = []
    cur_meta: dict = {}

    def crumbs() -> str:
        return " › ".join(t for _, t in path)

    def flush():
        nonlocal cur_kind, cur_lines, cur_meta
        if cur_kind is None:
            return
        content = "\n\n".join(l for l in cur_lines if l.strip())
        if cur_kind == "rule" or content.strip():
            if cur_kind == "rule":
                rid = cur_meta["rule_id"]
                head = [f"# {rid} {cur_meta['title']}", f"Source: {manual_label}", f"Section: {cur_meta['crumbs']}",
                        f"Link: {html_url}#{rid}", "Type: rule"]
                items.append(Item(item_id=f"manual:{rid}", source_type="manual", title=f"{rid} {cur_meta['title']}",
                                  body="\n".join(head) + "\n---\n\n" + content + "\n", url=f"{html_url}#{rid}", published=published))
            else:
                sid = cur_meta["sid"]
                anchor = cur_meta.get("anchor")
                link = f"{html_url}#{anchor}" if anchor else html_url
                head = [f"# {cur_meta['heading']}", f"Source: {manual_label}", f"Section: {cur_meta['crumbs']}",
                        f"Link: {link}", "Type: manual section"]
                items.append(Item(item_id=f"manual:sec-{sid}", source_type="manual", title=cur_meta["heading"],
                                  body="\n".join(head) + "\n---\n\n" + content + "\n", url=link, published=published))
        cur_kind, cur_lines, cur_meta = None, [], {}

    # front matter before the first heading
    cur_kind, cur_meta = "section", {"sid": _section_id(None, "front-matter", used_ids), "heading": "Front matter", "crumbs": "Front matter", "anchor": None}

    for b in blocks:
        if b.kind == "heading":
            flush()
            label = f"{b.number} {b.title}" if b.number else b.title
            while path and path[-1][0] >= b.level:
                path.pop()
            path.append((b.level, label))
            cur_kind = "section"
            cur_meta = {"sid": _section_id(b.number, b.title, used_ids), "heading": label, "crumbs": crumbs(),
                        "anchor": next((a for a in b.anchors if a), None)}
        elif b.kind == "rule":
            flush()
            cur_kind = "rule"
            cur_meta = {"rule_id": b.rule_id, "title": b.title, "crumbs": crumbs()}
            cur_lines.append(b.md)
        else:
            if cur_kind is None:
                cur_kind, cur_meta = "section", {"sid": _section_id(None, "untitled", used_ids), "heading": "Untitled", "crumbs": crumbs(), "anchor": None}
            cur_lines.append(b.md)
    flush()
    return items


def fetch_manual(session: requests.Session, hub: HubInfo) -> tuple[list[Item], str, str]:
    """Returns (items, html_url, version)."""
    entry = hub.find("/cm-html")
    r = fetch(session, f"{HUB_URL}/cm-html")
    html_url = r.url
    fname = unquote(urlparse(html_url).path.rsplit("/", 1)[-1])
    m = re.search(r"-\s*(V[\d.]+)\.htm", fname, re.I)
    version = (entry.version if entry and entry.version else None) or (m.group(1) if m else "unknown version")
    html = r.content.decode("windows-1252", errors="replace")
    items = manual_items(html, html_url, version, entry.updated if entry else None)
    rules = sum(1 for u in items if not u.item_id.startswith("manual:sec-"))
    log.info("manual %s: %d rule items, %d section items (%s)", version, rules, len(items) - rules, fname)
    if rules < 50:
        raise SourceError(f"manual parse found only {rules} rules; layout probably changed")
    return items, html_url, version


# --------------------------------------------------------------------------- PDFs (Team Updates, Q&A)

def pdf_text(data: bytes) -> str:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    pages = [(p.extract_text() or "") for p in reader.pages]
    text = "\n".join(pages)
    # drop running page headers like "Team Update 00 September 12, 2026 1 of 3"
    text = re.sub(r"^\s*Team Update \d+\s+[A-Za-z]+ \d{1,2}, \d{4}\s+\d+ of \d+\s*$", "", text, flags=re.M)
    text = re.sub(r"[ \t]+\n", "\n", text)
    lines = text.split("\n")
    out: list[str] = []
    for ln in lines:
        ln = ln.rstrip()
        prev = out[-1] if out else ""
        heading_like = len(prev) < 70 and not re.search(r"[.,;:]$", prev)
        if prev and ln and not re.match(r"^\s*([•o\-–]\s|\d+[.)]\s|[A-Z]\.\s|Section\b)", ln) \
                and not re.search(r"[.:;!?]$", prev) and not heading_like \
                and (ln[0].islower() or prev[-1] in ",—-" or prev[-1].isalnum()):
            out[-1] = out[-1] + " " + ln.lstrip()
        else:
            out.append(ln)
    text = "\n".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def team_update_items(session: requests.Session, hub: HubInfo) -> list[Item]:
    items = []
    for e in hub.team_updates:
        num = re.search(r"/tu-(\d+)$", urlparse(e.url).path).group(1)
        r = fetch(session, e.url)
        if "pdf" not in r.headers.get("content-type", "").lower() and not r.content.startswith(b"%PDF"):
            raise SourceError(f"Team Update {num} is not a PDF ({r.headers.get('content-type')}): {e.url}")
        text = pdf_text(r.content)
        if len(text) < 200:
            raise SourceError(f"Team Update {num} PDF yielded no text: {e.url}")
        date = e.updated or "date not listed"
        head = [f"# Team Update {num} ({date})", f"Source: FIRST Tech Challenge Team Update {e.version or 'TU' + num}, {date}",
                f"Link: {e.url}", "Type: team update",
                "Note: Team Updates change or clarify the Competition Manual. The most recent Team Update wins over older manual text."]
        items.append(Item(item_id=f"team_update:{num}", source_type="team_update", title=f"Team Update {num}",
                          body="\n".join(head) + "\n---\n\n" + text + "\n", url=e.url, published=parse_hub_date(e.updated)))
    log.info("team updates: %d", len(items))
    return items


def qa_id(link: str, fallback: str) -> str:
    """'https://ftc-qa.firstinspires.org/qa/123' -> '123'; otherwise a slug of the guid/title."""
    path = urlparse(link or "").path.rstrip("/")
    m = re.search(r"/qa/([^/]+)$", path)
    raw = m.group(1) if m else (fallback or "")
    return re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-")[:40] or hashlib.sha256((link or fallback).encode()).hexdigest()[:10]


def html_to_text(html: str) -> str:
    """Feed entry HTML -> plain paragraphs (keeps line breaks between blocks)."""
    if not html:
        return ""
    if "<" not in html:
        return norm(html)
    soup = BeautifulSoup(html, "lxml")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    text = soup.get_text("\n")
    lines = [norm(l) for l in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(l for l in lines if l)).strip()


def qa_items(session: requests.Session) -> list[Item]:
    """One item per answered question from the official Q&A system's public RSS feed.

    The feed is empty until the season's Q&A opens (2026-09-28 for BIOBUZZ) and resets each season.
    An unreadable feed raises; an empty one returns [] (the caller decides whether that is suspicious)."""
    import calendar
    import feedparser
    r = fetch(session, QA_RSS_URL)
    feed = feedparser.parse(r.content)
    if feed.bozo and not feed.entries:
        raise SourceError(f"Q&A feed unreadable: {getattr(feed, 'bozo_exception', 'parse error')}")
    items: list[Item] = []
    for e in feed.entries:
        link = (e.get("link") or "").strip()
        qid = qa_id(link, e.get("id") or e.get("title") or "")
        title = norm(e.get("title") or "") or f"Q&A {qid}"
        html = ""
        if e.get("content"):
            html = e["content"][0].get("value") or ""
        html = html or e.get("summary") or e.get("description") or ""
        text = html_to_text(html)
        when = e.get("published_parsed") or e.get("updated_parsed")
        published = calendar.timegm(when) if when else int(datetime.now(timezone.utc).timestamp())
        date_txt = datetime.fromtimestamp(published, timezone.utc).strftime("%b %d, %Y") if when else "date not listed"
        url = link if allowed(link) else QA_ONEPAGE_URL
        head = [f"# Q&A {qid}: {title}", f"Source: FIRST Tech Challenge official Q&A, answered {date_txt}", f"Link: {url}", "Type: Q&A",
                "Note: official Q&A answers clarify the Competition Manual; the newest answer wins over older manual text."]
        if len(text) < 20:
            log.warning("Q&A %s has no answer text in the feed; indexing the title only", qid)
            text = title
        items.append(Item(item_id=f"qa:{qid}", source_type="qa", title=f"Q&A {qid}: {title}",
                          body="\n".join(head) + "\n---\n\n" + text + "\n", url=url, published=published))
    log.info("Q&A feed: %d answered questions", len(items))
    return items


def qa_archive_items(session: requests.Session, hub: HubInfo) -> list[Item]:
    """Fallback: index a Q&A archive the hub links under /ftc/game/, if FIRST ever publishes one there."""
    e = hub.qa_archive
    if not e:
        log.info("Q&A archive: not linked on the hub yet")
        return []
    r = fetch(session, e.url)
    ctype = r.headers.get("content-type", "").lower()
    published = parse_hub_date(e.updated)
    head = lambda title, link: [f"# {title}", f"Source: FIRST Tech Challenge official Q&A ({e.updated or 'date not listed'})", f"Link: {link}", "Type: Q&A",
                                "Note: official Q&A answers clarify the manual; the newest answer wins."]
    items: list[Item] = []
    if "html" in ctype:
        soup = BeautifulSoup(html_text(r), "lxml")
        heads = soup.find_all(["h2", "h3", "h4"])
        if len(heads) >= 3:
            for i, h in enumerate(heads):
                title = el_text(h)
                parts = []
                for sib in h.find_next_siblings():
                    if sib.name in ("h2", "h3", "h4"):
                        break
                    parts.append(el_text(sib))
                content = "\n\n".join(p for p in parts if p)
                if not content:
                    continue
                qid = re.sub(r"[^A-Za-z0-9]+", "-", title)[:40].strip("-") or f"q{i}"
                anchor = h.get("id") or next((a.get("name") for a in h.find_all("a", attrs={"name": True})), None)
                link = f"{e.url}#{anchor}" if anchor else e.url
                items.append(Item(item_id=f"qa:{qid}", source_type="qa", title=title,
                                  body="\n".join(head(title, link)) + "\n---\n\n" + content + "\n", url=link, published=published))
        if not items:
            text = norm(soup.get_text("\n"))
            items.append(Item(item_id="qa:archive", source_type="qa", title="Q&A archive",
                              body="\n".join(head("Q&A archive", e.url)) + "\n---\n\n" + text + "\n", url=e.url, published=published))
    else:
        text = pdf_text(r.content)
        chunks = re.split(r"\n(?=Q\d+\b)", text)
        for i, c in enumerate(chunks):
            c = c.strip()
            if len(c) < 40:
                continue
            m = re.match(r"(Q\d+)\b", c)
            qid = m.group(1) if m else f"part{i}"
            items.append(Item(item_id=f"qa:{qid}", source_type="qa", title=f"Q&A {qid}",
                              body="\n".join(head(f"Q&A {qid}", e.url)) + "\n---\n\n" + c + "\n", url=e.url, published=published))
    log.info("Q&A archive: %d items", len(items))
    return items


# --------------------------------------------------------------------------- all together

def collect_first(session: requests.Session | None = None) -> tuple[list[Item], HubInfo]:
    """Hub, manual, and Team Update items. Q&A is collected separately (collect_qa) so a feed outage
    cannot block a manual refresh or delete the Q&A items already indexed."""
    session = session or make_session()
    hub_html = html_text(fetch(session, HUB_URL))
    hub = parse_hub(hub_html)
    if not hub.find("/cm-html"):
        raise SourceError("hub page has no Competition Manual HTML link; layout changed?")
    items: list[Item] = hub_items(hub)
    manual, _, _ = fetch_manual(session, hub)
    items += manual
    items += team_update_items(session, hub)
    return items, hub


def collect_qa(session: requests.Session, hub: HubInfo) -> list[Item]:
    """Q&A items from the public RSS feed; if the feed has nothing and the hub links an archive, use that."""
    items = qa_items(session)
    if not items and hub.qa_archive:
        items = qa_archive_items(session, hub)
    return items
