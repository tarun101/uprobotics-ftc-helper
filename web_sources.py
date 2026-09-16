"""Documentation websites for the UP Robotics FTC Helper (added 2026-09-12 at Tarun's request).

Each configured site is fetched page by page (politely, 1 request/second), the main content is
converted to Markdown, and each page becomes one item. Discovery:
  * kind "sphinx":  read <base>/searchindex.js (Read the Docs / Sphinx sites) for the page list
  * kind "sitemap": read a sitemap index and the sub-sitemaps whose URL contains one of `include_sitemaps`
  * kind "pages":   fetch the explicit `seeds` URLs only (HTML)
  * kind "hub":     fetch `seeds`, then same-host links whose path starts with one of `include`
                    (HTML and optional PDFs when allow_pdf is true; binaries/ZIPs skipped)
  * kind "raw":     fetch `seeds` as plain text (e.g. raw.githubusercontent.com README)
Nothing here logs in or downloads video/audio. Non-rule sources must set `note` in config.
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

from first_sources import USER_AGENT, Item, norm

log = logging.getLogger("ftc-index.web")


@dataclass
class WebSource:
    id: str
    name: str
    source_type: str
    kind: str                       # sphinx | sitemap | pages | hub | raw
    base: str = ""
    selector: str = "main"
    note: str = ""
    license: str = ""
    include: list[str] = field(default_factory=list)          # sphinx docname prefixes, or hub path prefixes
    include_sitemaps: list[str] = field(default_factory=list) # sitemap: substrings of sub-sitemap URLs to keep
    exclude: list[str] = field(default_factory=list)          # URL substrings to skip
    seeds: list[str] = field(default_factory=list)            # pages/hub/raw: starting URLs
    allow_pdf: bool = False                                   # hub: also fetch linked PDFs as items
    min_items: int = 10                                       # run_web: below this, sync without deleting old
    delay_seconds: float = 1.0
    max_pages: int = 2000
    markdown_suffix: str = ""       # e.g. ".md" for GitBook sites that serve a Markdown copy of every page
    enabled: bool = True


@dataclass
class Page:
    url: str
    lastmod: int | None = None


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


# --------------------------------------------------------------------------- discovery

def _host_ok(url: str, base_host: str) -> bool:
    host = urlparse(url).netloc
    if not base_host:
        return bool(host)
    return host == base_host


def _path_included(url: str, include: list[str]) -> bool:
    if not include:
        return True
    path = urlparse(url).path
    return any(path == p.rstrip("/") or path.startswith(p) for p in include)


def discover(src: WebSource, session: requests.Session) -> list[Page]:
    base_host = urlparse(src.base).netloc if src.base else ""
    pages: list[Page] = []
    if src.kind == "sphinx":
        if not src.base:
            raise RuntimeError(f"{src.id}: sphinx kind requires base")
        base_host = urlparse(src.base).netloc
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
        if not src.base:
            raise RuntimeError(f"{src.id}: sitemap kind requires base")
        base_host = urlparse(src.base).netloc
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
    elif src.kind == "pages":
        if not src.seeds:
            raise RuntimeError(f"{src.id}: pages kind requires seeds")
        if not base_host:
            base_host = urlparse(src.seeds[0]).netloc
        for u in src.seeds:
            pages.append(Page(u))
    elif src.kind == "raw":
        if not src.seeds:
            raise RuntimeError(f"{src.id}: raw kind requires seeds")
        if not base_host:
            base_host = urlparse(src.seeds[0]).netloc
        for u in src.seeds:
            pages.append(Page(u))
    elif src.kind == "hub":
        if not src.seeds:
            raise RuntimeError(f"{src.id}: hub kind requires seeds")
        if not base_host:
            base_host = urlparse(src.seeds[0]).netloc
        seen_seed = set()
        for seed in src.seeds:
            if seed in seen_seed:
                continue
            seen_seed.add(seed)
            pages.append(Page(seed))
            time.sleep(src.delay_seconds)
            try:
                r = session.get(seed, timeout=60)
            except requests.RequestException as e:
                log.warning("%s: hub seed failed %s: %s", src.id, seed, e)
                continue
            if not r.ok or "html" not in r.headers.get("content-type", "").lower():
                continue
            soup = BeautifulSoup(r.text, "lxml")
            for a in soup.select("a[href]"):
                href = urljoin(seed, a.get("href") or "")
                p = urlparse(href)
                if p.scheme not in ("http", "https"):
                    continue
                # drop fragments/query for stable ids
                href = p._replace(fragment="", query="").geturl()
                if not _host_ok(href, base_host):
                    continue
                if src.include and not _path_included(href, src.include) and href.rstrip("/") not in {s.rstrip("/") for s in src.seeds}:
                    continue
                pages.append(Page(href))
    else:
        raise RuntimeError(f"{src.id}: unknown kind {src.kind}")
    seed_hosts = {urlparse(u).netloc for u in src.seeds} if src.seeds else set()
    seed_urls = {s.rstrip("/") for s in src.seeds}
    seen, out = set(), []
    for p in pages:
        host = urlparse(p.url).netloc
        is_seed = p.url.rstrip("/") in seed_urls
        if src.kind in ("pages", "raw"):
            allowed_host = host in seed_hosts or (base_host and host == base_host)
        else:
            allowed_host = _host_ok(p.url, base_host)
        if not allowed_host or p.url in seen:
            continue
        if (not is_seed) and any(x in p.url for x in src.exclude):
            continue
        if urlparse(p.url).path in ("", "/") and src.kind == "sitemap":
            continue
        if (not is_seed) and src.kind in ("hub", "sitemap") and src.include and not _path_included(p.url, src.include):
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


def clean_gitbook_markdown(text: str) -> str:
    """Strip GitBook template tags and the llms.txt preface; flatten HTML card tables to lines."""
    text = re.sub(r"^> For the complete documentation index.*?\n", "", text.strip(), flags=re.M)
    text = re.sub(r"\{%-?\s*/?(hint|endhint|tabs|endtabs|tab|endtab|embed|endembed|content-ref|endcontent-ref|file|stepper|endstepper|step|endstep|columns|endcolumns|column|endcolumn|code|endcode)\b[^%]*%\}", "", text)
    text = re.sub(r"<table[^>]*>(.*?)</table>", lambda m: "\n".join("- " + norm(re.sub(r"<[^>]+>", " ", row)) for row in re.findall(r"<tr>(.*?)</tr>", m.group(1), re.S) if norm(re.sub(r"<[^>]+>", " ", row))), text, flags=re.S)
    text = re.sub(r"<figure>.*?</figure>", "", text, flags=re.S)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"^# .*\n", "", text.strip(), count=1)     # page title is already in the item header
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# --------------------------------------------------------------------------- items

def slug(url: str, base: str) -> str:
    path = url[len(base):] if url.startswith(base) else urlparse(url).path
    path = re.sub(r"\.html?$", "", path).strip("/")
    path = re.sub(r"/(index)$", "", path) or "index"
    return re.sub(r"[^A-Za-z0-9._-]+", "-", path).strip("-")[:80]


def fetch_items(src: WebSource, session: requests.Session, default_published: int) -> list[Item]:
    from first_sources import pdf_text  # local import avoids a circular import at module load for non-PDF sources

    pages = discover(src, session)
    items: list[Item] = []
    failures = 0
    base_for_slug = src.base or (src.seeds[0] if src.seeds else "")
    for i, page in enumerate(pages):
        title, md = "", ""
        try:
            r = session.get(page.url, timeout=90)
            ctype = (r.headers.get("content-type") or "").lower()
            if r.status_code != 200:
                log.info("%s: skip %s (HTTP %s)", src.id, page.url, r.status_code)
                continue
            if src.kind == "raw" or ctype.startswith("text/plain") or page.url.endswith(".md"):
                md = r.text.strip()
                title = slug(page.url, base_for_slug).replace("-", " ") or src.name
            elif "pdf" in ctype or r.content.startswith(b"%PDF"):
                if not src.allow_pdf:
                    log.info("%s: skip PDF %s (allow_pdf false)", src.id, page.url)
                    continue
                text = pdf_text(r.content)
                if len(text) < 200:
                    log.info("%s: skip thin PDF %s", src.id, page.url)
                    continue
                title = slug(page.url, base_for_slug).replace("-", " ") or "PDF"
                md = text
            elif "html" in ctype:
                title, md = html_to_markdown(r.text, src.selector)
                if src.markdown_suffix:
                    soup_link = re.search(r'<link[^>]+rel="canonical"[^>]+href="([^"]+)"', r.text)
                    md_url = (soup_link.group(1) if soup_link else page.url).rstrip("/") + src.markdown_suffix
                    time.sleep(src.delay_seconds)
                    rm = session.get(md_url, timeout=60)
                    if rm.ok and "markdown" in rm.headers.get("content-type", "").lower() and not rm.text.lstrip().startswith("# Page Not Found"):
                        md = clean_gitbook_markdown(rm.text)
            else:
                log.info("%s: skip %s (%s)", src.id, page.url, ctype or "no content-type")
                continue
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
        link = page.url.replace("_", "%5F")   # the chat widget italicizes _text_, which would break URLs with underscores
        head = [f"# {title or slug(page.url, base_for_slug)}", f"Source: {src.name}" + (f" ({src.license})" if src.license else ""),
                f"Link: {link}", f"Type: {src.source_type}"]
        if src.note:
            head.append(f"Note: {src.note}")
        body = "\n".join(head) + "\n---\n\n" + md + "\n"
        if len(body.encode("utf-8")) > 3_500_000:
            body = body.encode("utf-8")[:3_500_000].decode("utf-8", errors="ignore")
        items.append(Item(item_id=f"web:{src.id}:{slug(page.url, base_for_slug)}", source_type=src.source_type, title=title,
                          body=body, url=link, published=page.lastmod or default_published))
    log.info("%s: %d items from %d pages (%d fetch failures)", src.id, len(items), len(pages), failures)
    return items
