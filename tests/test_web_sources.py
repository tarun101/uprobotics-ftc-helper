"""Discovery helpers for web_sources kinds pages/hub/raw. Run: .venv/bin/python -m unittest tests.test_web_sources"""
import unittest
from unittest.mock import MagicMock
import web_sources as ws


class FakeResp:
    def __init__(self, text, status=200, ctype="text/html"):
        self.text = text
        self.status_code = status
        self.headers = {"content-type": ctype}
        self.content = text.encode()
        self.ok = status == 200

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


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


if __name__ == "__main__":
    unittest.main()
