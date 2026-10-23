"""お出かけの読み上げの言い回し（AI）と、今日のフランス語のテスト。"""

import json
import sys
import threading
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sounder import config, outingtalk, scheduler  # noqa: E402

DAY = date(2026, 10, 23)
FACTS = "外は寒いです。体感温度は7度です。雨の心配はありません。"


class FakeAI:
    def __init__(self, answer=None, error=None):
        self.answer, self.error, self.prompts = answer, error, []

    def complete(self, settings, prompt):
        self.prompts.append(prompt)
        if self.error:
            raise self.error
        return self.answer


def settings(**outing):
    return {"ai": {"enabled": True}, "outing": {**outingtalk.DEFAULTS, **outing}}


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "outing-talk.json"

    def talk(self, ai):
        return outingtalk.OutingTalk(ai, self.path)

    def run_now(self, t, s, key="k"):
        """1 回目は作り始めて決まった文を返す。作り終わったあとの 2 回目の答えを返す。"""
        first = t.get(s, key, FACTS, DAY, "qwen:x")
        for th in list(threading.enumerate()):
            if th.name == "outing-talk":
                th.join(5)
        return first, t.get(s, key, FACTS, DAY, "qwen:x")


class TestOutingTalk(Base):
    def test_rephrase_and_french_from_ai(self):
        ai = FakeAI(json.dumps({"talk": "おはよう。今朝は冷えるね。", "french": {
            "word": "le froid", "meaning": "the cold", "example": "Il fait froid.", "example_en": "It is cold."}}))
        t = self.talk(ai)
        s = settings(french=True, french_voice="Amélie", english_voice="")
        first, (text, parts) = self.run_now(t, s)
        self.assertEqual(first[0], FACTS)                       # 間に合わなければ決まった文
        self.assertEqual(text, "おはよう。今朝は冷えるね。")
        self.assertEqual([(p["text"], p["voice"]) for p in parts], [
            ("Today's French word is", "qwen:x"), ("le froid", "Amélie"), ("It means the cold.", "qwen:x"),
            ("Il fait froid.", "Amélie"), ("It is cold.", "qwen:x")])
        self.assertIn(FACTS, ai.prompts[0])
        # 使った単語と言い方を覚えて、次の日の頼みに入れる
        mem = json.loads(self.path.read_text())
        self.assertEqual(mem["words"], ["le froid"])
        t2 = self.talk(FakeAI("{}"))
        p = t2.prompt(s, FACTS, DAY)
        self.assertIn("le froid", p)
        self.assertIn("おはよう。今朝は冷えるね。", p)

    def test_ai_failure_falls_back(self):
        t = self.talk(FakeAI(error=OSError("down")))
        _first, (text, parts) = self.run_now(t, settings(french=True))
        self.assertEqual(text, FACTS)
        self.assertEqual(parts[1]["text"], outingtalk.word_of_the_day(DAY)["word"])

    def test_without_ai_uses_the_word_list(self):
        ai = FakeAI("{}")
        t = self.talk(ai)
        text, parts = t.get({"ai": {"enabled": False}, "outing": {"french": True}}, "k", FACTS, DAY, "Kyoko")
        self.assertEqual(text, FACTS)
        self.assertEqual(len(parts), 5)
        self.assertEqual(ai.prompts, [])
        text, parts = t.get({"ai": {"enabled": False}}, "k", FACTS, DAY, "Kyoko")
        self.assertEqual((text, parts), (FACTS, []))

    def test_parse(self):
        self.assertEqual(outingtalk.OutingTalk.parse('```json\n{"talk": "やあ"}\n```'), {"talk": "やあ"})
        self.assertEqual(outingtalk.OutingTalk.parse('{"french": {"word": "x"}}'), {})   # 意味が無い単語は使わない
        with self.assertRaises(ValueError):
            outingtalk.OutingTalk.parse("ごめんなさい")

    def test_words_differ_by_day(self):
        a = outingtalk.word_of_the_day(DAY)["word"]
        b = outingtalk.word_of_the_day(date(2026, 10, 24))["word"]
        self.assertNotEqual(a, b)

    def test_settings(self):
        cur = dict(config.DEFAULT_SETTINGS)
        out = config.validate_settings({"outing": {"french": True}}, cur)["outing"]
        self.assertEqual(out, {**outingtalk.DEFAULTS, "french": True})
        with self.assertRaises(config.ValidationError):
            config.validate_settings({"outing": "x"}, cur)


class FakeWeather:
    def outing(self, at, settings=None, full=True):
        return "外は寒いです。昨日より寒いです。" if full else "外は寒いです。"


class TestInSchedules(Base):
    def setUp(self):
        super().setUp()
        self.t = outingtalk.OutingTalk(None, self.path)
        self.sch = scheduler.Scheduler(None, None, None, weather=FakeWeather(), talk=self.t)
        self.sched = {"id": "s1", "name": "学校", "outing": True, "lead_times": [10, 3],
                      "action": {"type": "speak", "text": "学校の時間です。", "voice": "qwen:x"},
                      "lead_action": {"type": "sound", "sound": "builtin:ding", "speak_remaining": True}}
        self.when = datetime(2026, 10, 23, 8, 25)
        self.s = {"outing": {"french": True, "french_voice": "Amélie"}}

    def test_only_the_first_of_the_morning_gets_everything(self):
        first = self.sch.action_for(self.sched, "lead", 10, self.s, when=self.when)
        self.assertIn("昨日より", first["text"])
        self.assertEqual(first["then"][1]["voice"], "Amélie")
        later = self.sch.action_for(self.sched, "lead", 3, self.s, when=datetime(2026, 10, 23, 8, 32))
        self.assertNotIn("昨日より", later["text"])
        self.assertNotIn("then", later)
        main = self.sch.action_for(self.sched, "main", 0, self.s, when=datetime(2026, 10, 23, 8, 35))
        self.assertNotIn("then", main)

    def test_without_leads_the_main_is_first(self):
        self.assertTrue(outingtalk.first_of_the_morning({"lead_times": []}, "main", 0))
        self.assertFalse(outingtalk.first_of_the_morning({"lead_times": [5]}, "main", 0))
        self.assertFalse(outingtalk.first_of_the_morning({"lead_times": [5]}, "late", 3))


if __name__ == "__main__":
    unittest.main()
