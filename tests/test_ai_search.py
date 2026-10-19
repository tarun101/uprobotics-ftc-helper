"""AI Search retries and delete pacing. YouTube 429 handling is not covered here and must stay as it is.

Run: .venv/bin/python -m unittest tests.test_ai_search
"""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import requests
import ftc_index as fi


class Resp:
    def __init__(self, status, body, headers=None):
        self.status_code = status
        self.text = body
        self.headers = headers or {}
        self.ok = 200 <= status < 300
        self.content = body.encode()

    def json(self):
        import json
        return json.loads(self.text)


def client():
    return fi.AISearch("acct", "ftc-2026", "token")


class RetryTest(unittest.TestCase):
    def test_overloaded_honors_retry_after(self):
        cf = client()
        calls = {"n": 0}

        def request(method, url, timeout=None, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                body = '{"success":false,"errors":[{"message":"ai_search_instance_overloaded"}]}'
                return Resp(429, body, {"Retry-After": "7"})
            return Resp(200, '{"success":true,"result":{"ok":true}}')

        cf.s.request = request
        with patch("ftc_index.time.sleep") as sleep:
            data = cf._req("DELETE", "/items/x")
        self.assertEqual(data["result"]["ok"], True)
        self.assertEqual(calls["n"], 2)
        sleep.assert_called_once_with(7.0)

    def test_overloaded_without_retry_after_waits_at_least_the_floor(self):
        cf = client()
        body = '{"success":false,"errors":[{"message":"ai_search_instance_overloaded"}]}'
        cf.s.request = lambda *a, **k: Resp(429, body)
        with patch("ftc_index.time.sleep") as sleep:
            with self.assertRaises(RuntimeError):
                cf._req("GET", "/items")
        delays = [c.args[0] for c in sleep.call_args_list]
        self.assertEqual(len(delays), fi._CF_MAX_ATTEMPTS - 1)
        self.assertGreaterEqual(delays[0], fi._CF_OVERLOAD_FLOOR_S)
        self.assertGreaterEqual(delays[1], fi._CF_OVERLOAD_FLOOR_S)

    def test_retry_after_http_date_and_cap(self):
        when = datetime.now(timezone.utc) + timedelta(seconds=30)
        resp = Resp(429, "overloaded", {"Retry-After": format_datetime(when, usegmt=True)})
        delay = fi._retry_after_seconds(resp)
        self.assertAlmostEqual(delay, 30, delta=2)
        capped = Resp(429, "overloaded", {"Retry-After": "9999"})
        self.assertEqual(fi._cf_retry_delay(0, capped), fi._CF_RETRY_AFTER_CAP_S)
        plain = Resp(503, "unavailable")
        self.assertEqual(fi._cf_retry_delay(0, plain), 2.0)

    def test_read_timeout_and_connection_reset_are_retried_for_get(self):
        cf = client()
        seq = [
            requests.exceptions.ReadTimeout("read timed out (read timeout=180)"),
            requests.exceptions.ConnectionError("Connection reset by peer"),
            Resp(200, '{"success":true,"result":{"n":1}}'),
        ]

        def request(method, url, timeout=None, **kw):
            item = seq.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        cf.s.request = request
        with patch("ftc_index.time.sleep") as sleep:
            data = cf._req("GET", "/items")
        self.assertEqual(data["result"]["n"], 1)
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(seq, [])

    def test_post_timeout_is_not_retried(self):
        cf = client()
        calls = {"n": 0}

        def request(method, url, timeout=None, **kw):
            calls["n"] += 1
            raise requests.exceptions.ReadTimeout("read timed out")

        cf.s.request = request
        with patch("ftc_index.time.sleep") as sleep:
            with self.assertRaises(RuntimeError):
                cf._req("POST", "/items", data={"metadata": "{}"})
        self.assertEqual(calls["n"], 1)
        sleep.assert_not_called()

    def test_delete_404_is_success_without_sleep(self):
        cf = client()
        cf.s.request = lambda *a, **k: Resp(404, "missing")
        with patch("ftc_index.time.sleep") as sleep:
            data = cf._req("DELETE", "/items/gone")
        self.assertTrue(data["not_found"])
        sleep.assert_not_called()

    def test_client_error_is_not_retried(self):
        cf = client()
        calls = {"n": 0}

        def request(method, url, timeout=None, **kw):
            calls["n"] += 1
            return Resp(400, '{"success":false,"errors":[{"message":"bad"}]}')

        cf.s.request = request
        with patch("ftc_index.time.sleep") as sleep:
            with self.assertRaises(RuntimeError):
                cf._req("GET", "/items")
        self.assertEqual(calls["n"], 1)
        sleep.assert_not_called()

    def test_deletes_are_paced(self):
        cf = client()
        cf.s.request = lambda *a, **k: Resp(200, '{"success":true,"result":null}')
        with patch("ftc_index.time.sleep") as sleep:
            cf.delete("a")
            cf.delete("b")
        self.assertEqual(sleep.call_count, 1)
        self.assertAlmostEqual(sleep.call_args[0][0], fi._CF_DELETE_PACE_S, delta=0.2)


if __name__ == "__main__":
    unittest.main()
