"""読み上げの置き場（30 日使われなければ消す）のテスト。"""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sounder.speechcache import SpeechCache  # noqa: E402
from tests.test_player import Base as PlayerBase  # noqa: E402

DAY = 86400


class TestSpeechCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = SpeechCache(Path(self.tmp.name))

    def test_same_voice_rate_and_text_share_a_file(self):
        a = self.cache.path("qwen:ono_anna", None, "はい")
        self.assertEqual(a, self.cache.path("qwen:ono_anna", None, "はい"))
        self.assertNotEqual(a, self.cache.path("qwen:ono_anna", None, "いいえ"))
        self.assertNotEqual(a, self.cache.path("qwen:ryan", None, "はい"))
        self.assertNotEqual(a, self.cache.path("qwen:ono_anna", 200, "はい"))

    def test_get_touches_and_missing_is_none(self):
        p = self.cache.path("say:Kyoko", 180, "はい")
        self.assertIsNone(self.cache.get(p))
        self.cache.put(p, b"RIFF")
        os.utime(p, (time.time() - 40 * DAY,) * 2)
        self.assertEqual(self.cache.get(p), p)
        self.assertLess(time.time() - p.stat().st_mtime, 60)

    def test_prunes_files_unused_for_30_days(self):
        old = self.cache.put(self.cache.path("v", None, "古い"), b"RIFF")
        new = self.cache.put(self.cache.path("v", None, "新しい"), b"RIFF")
        os.utime(old, (time.time() - 31 * DAY,) * 2)
        os.utime(new, (time.time() - 29 * DAY,) * 2)
        self.assertEqual(self.cache.prune(force=True), 1)
        self.assertFalse(old.exists())
        self.assertTrue(new.exists())

    def test_prune_runs_at_most_once_a_day(self):
        self.cache.prune()
        old = self.cache.put(self.cache.path("v", None, "古い"), b"RIFF")
        os.utime(old, (time.time() - 31 * DAY,) * 2)
        self.assertEqual(self.cache.prune(), 0)       # 今日はもう見回った
        self.assertTrue(old.exists())


class TestSayIsCached(PlayerBase):
    settings = {"default_volume": 0.6, "default_voice": "Kyoko", "speak_rate": 180}

    def setUp(self):
        super().setUp()
        self.p.speech_cache = SpeechCache(self.home / "cache")

    def test_second_time_plays_the_cached_file_without_say(self):
        act = {"type": "speak", "text": "ゴミの日です"}
        self.p.play_blocking(act, settings=self.settings)
        self.p.play_blocking(act, settings=self.settings)
        calls = self.recorded()
        self.assertEqual([c.split()[0] for c in calls], ["say", "afplay", "afplay"])
        self.assertIn(str(self.home / "cache"), calls[2])
        self.assertEqual(len(list((self.home / "cache").glob("*.wav"))), 1)

    def test_different_rate_makes_a_new_file(self):
        self.p.play_blocking({"type": "speak", "text": "はい", "rate": 180}, settings=self.settings)
        self.p.play_blocking({"type": "speak", "text": "はい", "rate": 250}, settings=self.settings)
        self.assertEqual(sum(c.startswith("say") for c in self.recorded()), 2)


if __name__ == "__main__":
    unittest.main()
