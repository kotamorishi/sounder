"""今日のことわざのテスト。"""

import json
import sys
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sounder import config, proverbs, scheduler  # noqa: E402

ON = {"ai": {"enabled": True}}


class FakeAI:
    def __init__(self, answer=None, error=None):
        self.answer, self.error, self.prompts = answer, error, []

    def complete(self, settings, prompt, **kw):
        self.prompts.append(prompt)
        if self.error:
            raise self.error
        return self.answer


def wait():
    for th in list(threading.enumerate()):
        if th.name == "proverb":
            th.join(5)


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "proverbs.json"


class TestList(unittest.TestCase):
    def test_300_without_duplicates(self):
        self.assertEqual(len(proverbs.PROVERBS), 300)
        self.assertEqual(len({p[0] for p in proverbs.PROVERBS}), 300)
        for text, reading, meaning in proverbs.PROVERBS:
            self.assertTrue(text and meaning, text)
            # 読みはひらがな（と読点）だけ。読み上げで読み間違えないように
            self.assertRegex(reading, r"^[ぁ-んー、]+$", text)

    def test_starts_easy(self):
        self.assertEqual(proverbs.PROVERBS[0][0], "猿も木から落ちる")


class TestRotation(Base):
    def test_each_occurrence_gets_the_next_one_and_keeps_it(self):
        p = proverbs.Proverbs(None, self.path)
        self.assertEqual(p.index_for("a|08:00"), 0)
        self.assertEqual(p.index_for("a|08:00"), 0)      # 同じ回（予告の前の下ごしらえと本番）は同じもの
        self.assertEqual(p.index_for("b|12:00"), 1)
        self.assertEqual(p.index_for(None), 2)           # 試聴は次のものを見るだけで進めない
        self.assertEqual(p.index_for(None), 2)
        # 覚えておいて、起動し直しても続きから
        p2 = proverbs.Proverbs(None, self.path)
        self.assertEqual(p2.index_for("a|08:00"), 0)
        self.assertEqual(p2.index_for("c|18:00"), 2)

    def test_wraps_around(self):
        self.path.write_text(json.dumps({"next": len(proverbs.PROVERBS) - 1}))
        p = proverbs.Proverbs(None, self.path)
        self.assertEqual(p.index_for("x"), len(proverbs.PROVERBS) - 1)
        self.assertEqual(p.index_for("y"), 0)


class TestUsage(Base):
    def test_without_ai_reads_the_meaning(self):
        parts = proverbs.Proverbs(None, self.path).get({}, "k", "qwen:x")
        self.assertEqual([p["voice"] for p in parts], ["qwen:x"] * 3)
        self.assertEqual(parts[0]["text"], "今日のことわざです。さるもきからおちる。")
        self.assertIn("上手な人でも失敗する", parts[1]["text"])
        self.assertEqual(parts[2]["text"], "もう一度。さるもきからおちる。")

    def test_ai_usage_once_ready(self):
        ai = FakeAI(json.dumps({"usage": "上手な人でも失敗するよ、という意味です。"}))
        p = proverbs.Proverbs(ai, self.path)
        first = p.get(ON, "k", "v")
        self.assertIn("意味は、", first[1]["text"])      # 間に合わなければ一覧の意味
        wait()
        self.assertEqual(p.get(ON, "k", "v")[1]["text"], "上手な人でも失敗するよ、という意味です。")
        self.assertEqual(len(ai.prompts), 1)
        self.assertIn("猿も木から落ちる", ai.prompts[0])

    def test_ai_failure_is_not_retried(self):
        ai = FakeAI(error=OSError("down"))
        p = proverbs.Proverbs(ai, self.path)
        p.get(ON, "k", "v")
        wait()
        self.assertIn("意味は、", p.get(ON, "k", "v")[1]["text"])
        self.assertEqual(len(ai.prompts), 1)

    def test_ai_off(self):
        ai = FakeAI("{}")
        proverbs.Proverbs(ai, self.path).get({"ai": {"enabled": False}}, "k", "v")
        self.assertEqual(ai.prompts, [])

    def test_parse(self):
        self.assertEqual(proverbs.Proverbs.parse('はい {"usage": " 例です。 "}'), "例です。")
        with self.assertRaises(ValueError):
            proverbs.Proverbs.parse('{"usage": ""}')


class TestInSchedules(Base):
    def setUp(self):
        super().setUp()
        self.p = proverbs.Proverbs(None, self.path)
        self.sch = scheduler.Scheduler(None, None, None, proverbs=self.p)
        self.sched = {"id": "s1", "name": "夕ごはん", "proverb": True, "lead_times": [10, 3],
                      "action": {"type": "speak", "text": "夕ごはんの時間です。", "voice": "qwen:x"},
                      "lead_action": {"type": "sound", "sound": "builtin:ding", "speak_remaining": True}}

    def test_only_the_first_notice_gets_one(self):
        first = self.sch.action_for(self.sched, "lead", 10, {"default_voice": "qwen:d"},
                                    when=datetime(2026, 11, 5, 17, 50))
        self.assertEqual(first["text"], "夕ごはんまで、あと10分です。")
        self.assertEqual(first["then"][0]["text"], "今日のことわざです。さるもきからおちる。")
        self.assertEqual(first["then"][0]["voice"], "qwen:d")     # お知らせと同じ声
        later = self.sch.action_for(self.sched, "lead", 3, {}, when=datetime(2026, 11, 5, 17, 57))
        self.assertNotIn("then", later)
        main = self.sch.action_for(self.sched, "main", 0, {}, when=datetime(2026, 11, 5, 18, 0))
        self.assertNotIn("then", main)
        # 同じ回をもう一度作っても同じことわざ。次の日の回は次のことわざ
        again = self.sch.action_for(self.sched, "lead", 10, {"default_voice": "qwen:d"},
                                    when=datetime(2026, 11, 5, 17, 50))
        self.assertEqual(again["then"], first["then"])
        nxt = self.sch.action_for(self.sched, "lead", 10, {}, when=datetime(2026, 11, 6, 17, 50))
        self.assertIn("いそがばまわれ", nxt["then"][0]["text"])

    def test_sound_only_becomes_speech(self):
        sched = {"id": "s2", "name": "チャイム", "proverb": True, "lead_times": [],
                 "action": {"type": "sound", "sound": "builtin:ding"}}
        a = self.sch.action_for(sched, "main", 0, {"default_voice": "v"}, when=datetime(2026, 11, 5, 15, 0))
        self.assertEqual(a["type"], "both")
        self.assertEqual(a["text"], "今日のことわざです。さるもきからおちる。")
        self.assertEqual(len(a["then"]), 2)

    def test_off(self):
        sched = {**self.sched, "proverb": False}
        a = self.sch.action_for(sched, "lead", 10, {}, when=datetime(2026, 11, 5, 17, 50))
        self.assertNotIn("then", a)

    def test_validate(self):
        raw = {"name": "x", "kind": "daily", "time": "18:00", "days": [0],
               "action": {"type": "speak", "text": "a"}, "proverb": True}
        self.assertTrue(config.validate_schedule(raw)["proverb"])
        self.assertFalse(config.validate_schedule({**raw, "proverb": False})["proverb"])


if __name__ == "__main__":
    unittest.main()
