"""Documentation websites for the UP Robotics FTC Helper (added 2026-09-12 at Tarun's request).

Each configured site is fetched page by page (politely, 1 request/second), the main content is
converted to Markdown, and each page becomes one unit. Discovery:
  * kind "sphinx":  read <base>/searchindex.js (Read the Docs / Sphinx sites) for the page list
  * kind "sitemap": read a sitemap index and the sub-sitemaps whose URL contains one of `include_sitemaps`
Nothing here follows links beyond the configured site; robots.txt allows crawling on all three sites.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, NavigableString, Tag

from first_sources import USER_AGENT, Unit, norm

log = logging.getLogger("ftc-index.web")


@dataclass
class WebSource:
    id: str
    name: str
    source_type: str
    kind: str                       # sphinx | sitemap
    base: str
    selector: str = "main"
    note: str = ""
    license: str = ""
    include: list[str] = field(default_factory=list)          # sphinx: docname prefixes to keep (empty = all)
    include_sitemaps: list[str] = field(default_factory=list) # sitemap: substrings of sub-sitemap URLs to keep
    exclude: list[str] = field(default_factory=list)          # URL substrings to skip
    delay_seconds: float = 1.0
    max_pages: int = 2000
    markdown_suffix: str = ""       # e.g. ".md" for GitBook sites that serve a Markdown copy of every page


@dataclass
class Page:
    url: str
    lastmod: int | None = None


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


# --------------------------------------------------------------------------- discovery

def discover(src: WebSource, session: requests.Session) -> list[Page]:
    host = urlparse(src.base).netloc
    pages: list[Page] = []
    if src.kind == "sphinx":
        r = session.get(urljoin(src.base, "searchindex.js"), timeout=60)
        r.raise_for_status()
        m = re.search(r"\"?docnames\"?\s*:\s*\[(.*?)\]", r.text, re.S)
        if not m:
            raise RuntimeError(f"{src.id}: no docnames in searchindex.js")
        for name in re.findall(r'"([^"]+)"', m.group(1)):
            last = name.rsplit("/", 1)[-1]
            if last in ("genindex", "search", "404") or name in ("index",):
                continue
            if src.include and not any(name.startswith(p) for p in src.include):
                continue
            pages.append(Page(urljoin(src.base, name + ".html")))
    elif src.kind == "sitemap":
        r = session.get(urljoin(src.base, "sitemap.xml"), timeout=60)
        r.raise_for_status()
        locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", r.text)
        subs = [u for u in locs if u.endswith(".xml")]
        leaf_xml = [r.text] if not subs else []
        for u in subs:
            if src.include_sitemaps and not any(s in u for s in src.include_sitemaps):
                continue
            time.sleep(src.delay_seconds)
            rr = session.get(u, timeout=60)
            if rr.ok:
                leaf_xml.append(rr.text)
        for xml in leaf_xml:
            for block in re.findall(r"<url>(.*?)</url>", xml, re.S):
                loc = re.search(r"<loc>\s*([^<\s]+)\s*</loc>", block)
                if not loc:
                    continue
                lm = re.search(r"<lastmod>\s*([^<\s]+)\s*</lastmod>", block)
                pages.append(Page(loc.group(1), _epoch(lm.group(1)) if lm else None))
    else:
        raise RuntimeError(f"{src.id}: unknown kind {src.kind}")
    seen, out = set(), []
    for p in pages:
        if urlparse(p.url).netloc != host or any(x in p.url for x in src.exclude) or p.url in seen:
            continue
        seen.add(p.url)
        out.append(p)
    if len(out) > src.max_pages:
        log.warning("%s: %d pages found, keeping the first %d", src.id, len(out), src.max_pages)
        out = out[: src.max_pages]
    log.info("%s: %d pages discovered", src.id, len(out))
    return out


def _epoch(iso: str) -> int | None:
    try:
        return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


# --------------------------------------------------------------------------- HTML -> Markdown

DROP_TAGS = {"script", "style", "nav", "aside", "footer", "header", "form", "button", "svg", "noscript", "iframe"}
DROP_CLASSES = ("headerlink", "toc", "sidebar", "breadcrumb", "edit-this-page", "prev-next", "related", "sphinxsidebar")


def _clean(root: Tag) -> None:
    for t in list(root.find_all(list(DROP_TAGS))):
        t.decompose()
    for t in list(root.find_all(True)):
        attrs = getattr(t, "attrs", None)
        if attrs is None:      # already decomposed with a parent
            continue
        cls = " ".join(attrs.get("class") or [])
        if any(c in cls for c in DROP_CLASSES) or attrs.get("role") in ("navigation", "contentinfo"):
            t.decompose()


def _inline(el) -> str:
    if isinstance(el, NavigableString):
        return str(el)
    if el.name == "br":
        return "\n"
    if el.name == "img":
        alt = norm(el.get("alt") or "")
        return f" [Image: {alt}] " if alt else ""
    if el.name == "code" and el.parent.name != "pre":
        return f"`{el.get_text()}`"
    if el.name == "a":
        return el.get_text()
    return "".join(_inline(c) for c in el.children)


def _block(el: Tag, out: list[str], depth: int = 0) -> None:
    name = el.name
    if name in ("h1", "h2", "h3", "h4", "h5", "h6"):
        out.append("#" * int(name[1]) + " " + norm(_inline(el)))
    elif name == "p":
        t = norm(_inline(el))
        if t:
            out.append(t)
    elif name == "pre":
        out.append("```\n" + el.get_text().rstrip("\n") + "\n```")
    elif name in ("ul", "ol"):
        for i, li in enumerate(el.find_all("li", recursive=False), 1):
            sub = [c for c in li.children if isinstance(c, Tag) and c.name in ("ul", "ol", "pre", "table")]
            for s in sub:
                s.extract()
            text = norm(_inline(li))
            bullet = f"{i}." if name == "ol" else "-"
            if text:
                out.append("  " * depth + f"{bullet} {text}")
            for s in sub:
                _block(s, out, depth + 1)
    elif name == "table":
        rows = []
        for tr in el.find_all("tr"):
            rows.append([norm(_inline(td)).replace("|", "\\|") for td in tr.find_all(["td", "th"])])
        rows = [r for r in rows if any(c for c in r)]
        if rows:
            w = max(len(r) for r in rows)
            rows = [r + [""] * (w - len(r)) for r in rows]
            out.append("| " + " | ".join(rows[0]) + " |")
            out.append("| " + " | ".join(["---"] * w) + " |")
            out += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    elif name in ("blockquote", "div", "section", "article", "main", "details", "dl", "dd", "dt", "figure", "figcaption", "span", "li", "body", "td"):
        # container: recurse into children; loose text becomes a paragraph
        loose = []
        for c in el.children:
            if isinstance(c, NavigableString):
                loose.append(str(c))
            elif isinstance(c, Tag):
                if c.name in ("a", "strong", "em", "b", "i", "code", "img", "br", "small", "sup", "sub", "kbd", "abbr", "mark", "u", "s", "time"):
                    loose.append(_inline(c))
                else:
                    t = norm("".join(loose))
                    if t:
                        out.append(("> " if name == "blockquote" else "") + t)
                    loose = []
                    _block(c, out, depth)
        t = norm("".join(loose))
        if t:
            out.append(("> " if name == "blockquote" else "") + t)
    else:
        t = norm(_inline(el))
        if t:
            out.append(t)


def html_to_markdown(html: str, selector: str) -> tuple[str, str]:
    """Returns (title, markdown) for the page's main content."""
    soup = BeautifulSoup(html, "lxml")
    title = norm(soup.title.get_text()) if soup.title else ""
    root = None
    for sel in (selector, "article", "main", "div[role=main]", "body"):
        found = soup.select_one(sel)
        if found is not None:
            root = found
            break
    if root is None:
        return title, ""
    _clean(root)
    out: list[str] = []
    _block(root, out)
    md = "\n\n".join(out)
    md = re.sub(r"\n{3,}", "\n\n", md).strip()
    h1 = root.find("h1")
    if h1:
        title = norm(h1.get_text()) or title
    title = re.sub(r"\s*[¶#]\s*$", "", title)
    return title, md


# --------------------------------------------------------------------------- units

def slug(url: str, base: str) -> str:
    path = url[len(base):] if url.startswith(base) else urlparse(url).path
    path = re.sub(r"\.html?$", "", path).strip("/")
    path = re.sub(r"/(index)$", "", path) or "index"
    return re.sub(r"[^A-Za-z0-9._-]+", "-", path).strip("-")[:80]


def fetch_units(src: WebSource, session: requests.Session, default_published: int) -> list[Unit]:
    pages = discover(src, session)
    units: list[Unit] = []
    failures = 0
    for i, page in enumerate(pages):
        try:
            r = session.get(page.url, timeout=60)
            if r.status_code != 200 or "html" not in r.headers.get("content-type", "").lower():
                log.info("%s: skip %s (HTTP %s, %s)", src.id, page.url, r.status_code, r.headers.get("content-type", ""))
                continue
            title, md = html_to_markdown(r.text, src.selector)
            if src.markdown_suffix:
                soup_link = re.search(r'<link[^>]+rel="canonical"[^>]+href="([^"]+)"', r.text)
                md_url = (soup_link.group(1) if soup_link else page.url).rstrip("/") + src.markdown_suffix
                time.sleep(src.delay_seconds)
                rm = session.get(md_url, timeout=60)
                if rm.ok and "markdown" in rm.headers.get("content-type", "").lower() and not rm.text.lstrip().startswith("# Page Not Found"):
                    md = re.sub(r"^# .*\n", "", rm.text.strip(), count=1).strip()
        except requests.RequestException as e:
            failures += 1
            log.warning("%s: fetch failed %s: %s", src.id, page.url, e)
            if failures > max(5, len(pages) // 10):
                raise RuntimeError(f"{src.id}: too many fetch failures ({failures}); keeping last index")
            continue
        finally:
            if i < len(pages) - 1:
                time.sleep(src.delay_seconds)
        if len(md) < 200:
            continue
        head = [f"# {title or slug(page.url, src.base)}", f"Source: {src.name}" + (f" ({src.license})" if src.license else ""),
                f"Link: {page.url}", f"Type: {src.source_type}"]
        if src.note:
            head.append(f"Note: {src.note}")
        body = "\n".join(head) + "\n---\n\n" + md + "\n"
        if len(body.encode("utf-8")) > 3_500_000:
            body = body.encode("utf-8")[:3_500_000].decode("utf-8", errors="ignore")
        units.append(Unit(unit_id=f"web:{src.id}:{slug(page.url, src.base)}", source_type=src.source_type, title=title,
                          body=body, url=page.url, published=page.lastmod or default_published))
    log.info("%s: %d units from %d pages (%d fetch failures)", src.id, len(units), len(pages), failures)
    return units
