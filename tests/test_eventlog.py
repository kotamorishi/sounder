"""実行ログのテスト。"""

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sounder import eventlog  # noqa: E402


class TestEventLog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "sub" / "events.log"
        self.log = eventlog.EventLog(self.path, keep=5)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, *args, **kw):
        with redirect_stdout(io.StringIO()) as out:
            self.log(*args, **kw)
        return out.getvalue()

    def test_writes_to_file_and_memory(self):
        self.write("fired", "鳴らしました", schedule_id="abc")
        self.assertTrue(self.path.exists())
        self.assertIn("鳴らしました", self.path.read_text("utf-8"))
        entries = self.log.recent()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["level"], "fired")
        self.assertEqual(entries[0]["schedule_id"], "abc")
        self.assertIn("T", entries[0]["ts"])

    def test_also_prints_to_stdout(self):
        out = self.write("info", "起動しました")
        self.assertIn("起動しました", out)

    def test_recent_is_newest_first_and_capped(self):
        for i in range(8):
            self.write("info", f"m{i}")
        recent = self.log.recent()
        self.assertEqual([e["message"] for e in recent], ["m7", "m6", "m5", "m4", "m3"])
        self.assertEqual(len(self.log.recent(limit=2)), 2)
        self.assertEqual(self.log.recent(limit=2)[0]["message"], "m7")

    def test_rotates_when_large(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("x" * (eventlog.MAX_BYTES + 1), "utf-8")
        self.write("info", "新しい行")
        self.assertTrue(self.path.with_suffix(".1.log").exists())
        self.assertEqual(self.path.read_text("utf-8").count("\n"), 1)

    def test_survives_unwritable_path(self):
        log = eventlog.EventLog(Path("/dev/null/cannot/exist/events.log"))
        with redirect_stdout(io.StringIO()):
            log("error", "書けなくても落ちない")
        self.assertEqual(len(log.recent()), 1)


if __name__ == "__main__":
    unittest.main()
