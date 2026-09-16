"""Discovery helpers for web_sources kinds. Run: .venv/bin/python -m unittest tests.test_web_sources"""
import unittest
from unittest.mock import MagicMock
import web_sources as ws


class FakeResp:
    def __init__(self, text="", status=200, ctype="text/html", content=None, json_data=None):
        self.text = text
        self.status_code = status
        self.headers = {"content-type": ctype}
        self.content = content if content is not None else text.encode()
        self.ok = status == 200
        self._json = json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)

    def json(self):
        return self._json


class DiscoverTest(unittest.TestCase):
    def test_pages_multi_host_seeds(self):
        src = ws.WebSource(
            id="sdk", name="SDK", source_type="sdk_docs", kind="pages",
            base="https://github.com/",
            seeds=[
                "https://raw.githubusercontent.com/org/repo/v1/README.md",
                "https://github.com/org/repo/releases/tag/v1",
            ],
            min_items=1,
        )
        session = MagicMock()
        pages = ws.discover(src, session)
        self.assertEqual(len(pages), 2)
        session.get.assert_not_called()

    def test_hub_follow_include_and_exclude(self):
        html = """
        <html><body>
          <a href="/ftc/field/guide">Guide</a>
          <a href="/ftc/field/field-cad-step">CAD</a>
          <a href="/ftc/archive/2027/team">Other hub</a>
          <a href="https://example.com/x">External</a>
        </body></html>
        """
        src = ws.WebSource(
            id="field", name="Field", source_type="field_resources", kind="hub",
            base="https://ftc-resources.firstinspires.org/",
            seeds=["https://ftc-resources.firstinspires.org/ftc/field"],
            include=["/ftc/field"],
            exclude=["field-cad"],
            allow_pdf=True, min_items=1, delay_seconds=0,
        )
        session = MagicMock()
        session.get.return_value = FakeResp(html)
        pages = ws.discover(src, session)
        urls = [p.url for p in pages]
        self.assertIn("https://ftc-resources.firstinspires.org/ftc/field", urls)
        self.assertIn("https://ftc-resources.firstinspires.org/ftc/field/guide", urls)
        self.assertTrue(all("field-cad" not in u for u in urls if u.rstrip("/") != src.seeds[0].rstrip("/")))
        self.assertTrue(all("/ftc/archive/" not in u for u in urls))
        self.assertTrue(all("example.com" not in u for u in urls))

    def test_path_included(self):
        self.assertTrue(ws._path_included("https://h/ftc/field/x", ["/ftc/field"]))
        self.assertTrue(ws._path_included("https://h/ftc/field", ["/ftc/field"]))
        self.assertFalse(ws._path_included("https://h/ftc/game", ["/ftc/field"]))

    def test_rss_prefills_body(self):
        atom = b"""<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
          <entry>
            <title>Hello FTC</title>
            <link href="https://www.reddit.com/r/FTC/comments/abc/hello/"/>
            <summary>Strategy discussion about intake design for BIOBUZZ season teams. Long enough community watchlist summary for indexing thresholds.</summary>
            <updated>2026-09-16T00:00:00Z</updated>
          </entry>
        </feed>"""
        src = ws.WebSource(
            id="reddit", name="r/FTC", source_type="community_watchlist", kind="rss",
            base="https://www.reddit.com/", seeds=["https://www.reddit.com/r/FTC/new/.rss"],
            delay_seconds=0, min_items=1,
        )
        session = MagicMock()
        session.get.return_value = FakeResp(ctype="application/atom+xml", content=atom)
        pages = ws.discover(src, session)
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0].title, "Hello FTC")
        self.assertIn("intake", pages[0].body)
        src.note = "COMMUNITY WATCHLIST — NEVER rule proof"
        items = ws.fetch_items(src, session, 1)
        self.assertEqual(len(items), 1)
        self.assertIn("NEVER rule proof", items[0].body)

    def test_discourse_json_lists_topics(self):
        cat = {
            "topic_list": {
                "topics": [{"id": 1, "slug": "hello-ftc", "title": "Hello FTC"}],
                "more_topics_url": None,
            }
        }
        topic = {
            "title": "Hello FTC",
            "post_stream": {
                "posts": [
                    {"username": "alice", "cooked": "<p>Build thread advice for BIOBUZZ drivetrain.</p>"},
                    {"username": "bob", "cooked": "<p>More strategy notes about auto routines and intake.</p>"},
                ]
            },
        }
        src = ws.WebSource(
            id="cd", name="CD", source_type="community_watchlist", kind="discourse_json",
            base="https://www.chiefdelphi.com/",
            seeds=["https://www.chiefdelphi.com/c/other/first-tech-challenge/60.json"],
            delay_seconds=0, min_items=1, max_pages=10,
            note="COMMUNITY WATCHLIST — NEVER rule proof",
        )
        session = MagicMock()
        def get(url, timeout=60):
            if url.endswith("60.json") or "60.json?" in url:
                return FakeResp(ctype="application/json", json_data=cat, text="{}")
            return FakeResp(ctype="application/json", json_data=topic, text="{}")
        session.get.side_effect = get
        pages = ws.discover(src, session)
        self.assertEqual(pages[0].url, "https://www.chiefdelphi.com/t/hello-ftc/1.json")
        items = ws.fetch_items(src, session, 1)
        self.assertEqual(len(items), 1)
        self.assertIn("@alice", items[0].body)
        self.assertIn("NEVER rule proof", items[0].body)
        self.assertTrue(items[0].url.endswith("/t/hello-ftc/1"))


if __name__ == "__main__":
    unittest.main()
