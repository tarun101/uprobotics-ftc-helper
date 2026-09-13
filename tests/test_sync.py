"""Change detection: unchanged -> skip, changed -> upload new + delete old, stale -> delete. Run: .venv/bin/python -m unittest tests/test_sync.py"""
import sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ftc_index as fi
import first_sources as fs
import youtube_sources as yt


class FakeCF:
    def __init__(self):
        self.items = {}
        self.calls = []
        self.n = 0

    def upload(self, key, body, metadata):
        self.n += 1
        item = {"id": f"id{self.n}", "key": key, "status": "completed"}
        self.items[item["id"]] = item
        self.calls.append(("upload", key))
        return item

    def delete(self, item_id):
        self.items.pop(item_id, None)
        self.calls.append(("delete", item_id))

    def list_all(self):
        return list(self.items.values())


def unit(uid, body, st="manual"):
    return fs.Unit(unit_id=uid, source_type=st, title=uid, body=body, url="u", published=1)


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = fi.State(Path(self.tmp.name) / "s.sqlite")
        self.cf = FakeCF()
        self.cfg = {"youtube": {"defaults": {"delay_seconds": [0, 0]}}, "paths": {"state_dir": self.tmp.name}, "season": {"label": "2026-27", "starts": "2026-09-01"}}

    def ix(self, run_id):
        return fi.Indexer(self.cfg, self.state, self.cf, run_id)

    def test_unchanged_changed_stale(self):
        a, b = unit("manual:G1", "one"), unit("manual:G2", "two")
        ix = self.ix("r1"); ix.sync_units([a, b], {"manual"})
        self.assertEqual(ix.stats["uploaded"], 2)
        # nothing changed -> nothing uploaded
        ix = self.ix("r2"); ix.sync_units([a, b], {"manual"})
        self.assertEqual((ix.stats["uploaded"], ix.stats["unchanged"], ix.stats["deleted"]), (0, 2, 0))
        # G1 changed -> new key uploaded, old deleted; G2 gone -> deleted
        a2 = unit("manual:G1", "one changed")
        ix = self.ix("r3"); ix.sync_units([a2], {"manual"})
        self.assertEqual((ix.stats["uploaded"], ix.stats["deleted"]), (1, 2))
        keys = {it["key"] for it in self.cf.items.values()}
        self.assertEqual(keys, {a2.key})
        self.assertTrue(a2.key.startswith("manual--G1--") and a2.key != a.key)

    def test_video_units_not_deleted_by_first_sync(self):
        v = unit("video:abc-0000", "hello", "video")
        ix = self.ix("r1"); ix.sync_units([v])
        ix = self.ix("r2"); ix.sync_units([unit("manual:G1", "x")], {"manual"})
        self.assertEqual(ix.stats["deleted"], 0)
        self.assertIsNotNone(self.state.unit("video:abc-0000"))

    def test_windows_and_season(self):
        cues = [yt.Cue(t, f"w{t}") for t in range(0, 600, 5)]
        w = yt.make_windows(cues, 600)
        self.assertTrue(3 <= len(w) <= 5)
        self.assertTrue(all(win.end - win.start <= yt.WINDOW_MAX_S + 5 for win in w))
        import datetime as d
        self.assertEqual(yt.season_label("20260912", d.date(2026, 9, 1), "2026-27"), "2026-27")
        self.assertEqual(yt.season_label("20220427", d.date(2026, 9, 1), "2026-27"), "2021-22")
        self.assertEqual(yt.season_label("20251015", d.date(2026, 9, 1), "2026-27"), "2025-26")

    def test_key_format(self):
        self.assertRegex(unit("manual:sec-10.3.1", "x").key, r"^manual--sec-10\.3\.1--[0-9a-f]{8}\.md$")
        self.assertRegex(unit("video:AbC_-9-0300", "x", "video").key, r"^video--AbC_-9-0300--[0-9a-f]{8}\.md$")
        self.assertTrue(fi.KEY_RE.match(unit("team_update:00", "x", "team_update").key))


if __name__ == "__main__":
    unittest.main()
