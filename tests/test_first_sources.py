"""Q&A allowlist and board parsing. Run: .venv/bin/python -m unittest tests.test_first_sources"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import first_sources as fs


class Resp:
    def __init__(self, status=200, url="", headers=None, json_data=None, content=b""):
        self.status_code = status
        self.url = url
        self.headers = headers or {}
        self._json = json_data
        self.content = content
        self.ok = 200 <= status < 300

    def json(self):
        if isinstance(self._json, Exception):
            raise self._json
        return self._json


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, timeout=None, allow_redirects=True, headers=None):
        self.calls.append({"url": url, "allow_redirects": allow_redirects, "headers": headers})
        if not self.responses:
            raise AssertionError(f"unexpected GET {url}")
        return self.responses.pop(0)


REDIRECT = "https://game-qa.firstinspires.org/boards/2027/FTC/QA/rss/answers.rss"
QUESTIONS = "https://game-qa.firstinspires.org/boards/2027/FTC/QA/questions"


class AllowedTest(unittest.TestCase):
    def test_ftc_board_and_legacy_host_are_allowed(self):
        self.assertTrue(fs.allowed(QUESTIONS))
        self.assertTrue(fs.allowed("https://game-qa.firstinspires.org:443/boards/2027/FTC/QA/qa/28"))
        self.assertTrue(fs.allowed("https://ftc-qa.firstinspires.org/rss/answers.rss"))
        self.assertTrue(fs.allowed("https://ftc-resources.firstinspires.org/ftc/game/tu-00"))

    def test_other_hosts_programs_and_tricks_are_refused(self):
        refused = [
            "http://game-qa.firstinspires.org/boards/2027/FTC/QA/questions",
            "https://game-qa.firstinspires.org/login",
            "https://game-qa.firstinspires.org/boards/2027/FRC/QA",
            "https://game-qa.firstinspires.org/boards/2027/FTC/other",
            "https://evil.example/boards/2027/FTC/QA",
            "https://user:pass@game-qa.firstinspires.org/boards/2027/FTC/QA/questions",
            "https://game-qa.firstinspires.org/boards/2027/FTC/QA/../../secret",
            "https://game-qa.firstinspires.org:8443/boards/2027/FTC/QA/questions",
            "https://ftc-resources.firstinspires.org/ftc/other",
        ]
        for url in refused:
            self.assertFalse(fs.allowed(url), url)

    def test_qa_id_reads_the_board_path(self):
        link = "https://game-qa.firstinspires.org:443/boards/2027/FTC/QA/qa/28"
        self.assertEqual(fs.qa_id(link, ""), "28")


class QaBoardTest(unittest.TestCase):
    def test_redirect_names_the_board_and_off_board_redirects_are_refused(self):
        session = FakeSession([Resp(302, fs.QA_LEGACY_RSS_URL, {"Location": REDIRECT})])
        self.assertEqual(fs.discover_qa_board(session), "/boards/2027/FTC/QA")
        self.assertFalse(session.calls[0]["allow_redirects"])

        session = FakeSession([Resp(302, fs.QA_LEGACY_RSS_URL, {"Location": "https://evil.example/rss/answers.rss"})])
        with self.assertRaises(fs.SourceError) as caught:
            fs.discover_qa_board(session)
        self.assertIn("redirect left the allowed hosts", str(caught.exception))

        session = FakeSession([Resp(302, fs.QA_LEGACY_RSS_URL, {
            "Location": "https://game-qa.firstinspires.org/boards/2027/FRC/QA/rss/answers.rss"})])
        with self.assertRaises(fs.SourceError):
            fs.discover_qa_board(session)

    def test_questions_json_becomes_answered_items(self):
        payload = [
            {
                "number": 28,
                "status": "ANSWERED",
                "title": "ROBOT move to qualify for leave",
                "content": "<p>Would a partner push count for LEAVE?</p>",
                "topic": "10.5",
                "submittedAt": 1791928090000,
                "answer": {
                    "content": "<p>Yes. There is no mandatory method.</p>",
                    "createdAt": 1792443294000,
                    "modifiedAt": 1792443294000,
                },
            },
            {"number": 15, "status": "ACCEPTED", "title": "still open", "content": "<p>not yet</p>"},
            {"number": 7, "status": "ANSWERED", "title": "Short answer", "content": "<p>May we?</p>",
             "answer": {"content": "<p>Yes.</p>", "modifiedAt": 1791608978000}},
        ]
        session = FakeSession([
            Resp(302, fs.QA_LEGACY_RSS_URL, {"Location": REDIRECT}),
            Resp(200, QUESTIONS, json_data=payload),
        ])
        items = fs.qa_items(session)
        self.assertEqual([it.item_id for it in items], ["qa:28", "qa:7"])
        item = items[0]
        self.assertEqual(item.item_id, "qa:28")
        self.assertEqual(item.url, "https://game-qa.firstinspires.org/boards/2027/FTC/QA/qa/28")
        self.assertIn("Question:", item.body)
        self.assertIn("Would a partner push count for LEAVE?", item.body)
        self.assertIn("Answer:", item.body)
        self.assertIn("Yes. There is no mandatory method.", item.body)
        self.assertIn("Topic: 10.5", item.body)
        self.assertEqual(item.published, 1792443294)
        self.assertIn("Answer:\n\nYes.", items[1].body)
        self.assertEqual(session.calls[1]["url"], QUESTIONS)
        self.assertEqual(session.calls[1]["headers"]["Accept"], "application/json")

    def test_non_json_payload_raises(self):
        session = FakeSession([
            Resp(302, fs.QA_LEGACY_RSS_URL, {"Location": REDIRECT}),
            Resp(200, QUESTIONS, json_data=ValueError("nope")),
        ])
        with self.assertRaises(fs.SourceError):
            fs.qa_items(session)


if __name__ == "__main__":
    unittest.main()
