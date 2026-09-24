"""カレンダー連携（calendar.json → 1 回きりの読み上げ予定）のテスト。"""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sounder import calendarfeed, config, scheduler  # noqa: E402

FEED = {
    "status": "authorized", "generated": "2026-09-24T18:31:40-04:00",
    "calendars": [{"id": "fam", "title": "Morishitas", "source": "iCloud"},
                  {"id": "work", "title": "Work", "source": "iCloud"}],
    "events": [
        {"id": "e1|x", "calendar_id": "fam", "title": "算数", "all_day": False,
         "start": "2026-09-24T19:00:00-04:00", "end": "2026-09-24T20:00:00-04:00"},
        {"id": "e2|x", "calendar_id": "work", "title": "会議", "all_day": False,
         "start": "2026-09-24T10:00:00-04:00", "end": "2026-09-24T11:00:00-04:00"},
        {"id": "e3|x", "calendar_id": "fam", "title": "誕生日", "all_day": True,
         "start": "2026-09-25T00:00:00-04:00", "end": "2026-09-26T00:00:00-04:00"},
    ],
}


def local(iso):
    return datetime.fromisoformat(iso).astimezone().replace(tzinfo=None)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "calendar.json"
        self.path.write_text(json.dumps(FEED, ensure_ascii=False))
        self.feed = calendarfeed.CalendarFeed(self.path)
        self.settings = {"default_volume": 0.5,
                         "calendar": {"enabled": True, "calendars": ["fam"], "lead": 10,
                                      "sound": "builtin:melody_notice", "voice": ""}}


class TestSchedules(Base):
    def test_selected_calendar_timed_events_become_once_schedules(self):
        (s,) = self.feed.schedules(self.settings)
        at = local("2026-09-24T19:00:00-04:00") - timedelta(minutes=10)
        self.assertEqual((s["kind"], s["date"], s["time"]), ("once", at.date().isoformat(), at.strftime("%H:%M")))
        self.assertEqual(s["name"], "算数")
        self.assertEqual(s["calendar"], "Morishitas")
        self.assertEqual(s["action"]["text"], "10分後に、算数があります。")
        self.assertEqual(s["action"]["type"], "both")
        self.assertEqual(s["source"], "calendar")

    def test_at_start_and_speak_only(self):
        self.settings["calendar"].update(lead=0, sound="")
        (s,) = self.feed.schedules(self.settings)
        self.assertEqual(s["action"]["text"], "算数の時間です。")
        self.assertEqual(s["action"]["type"], "speak")
        self.assertNotIn("sound", s["action"])

    def test_off_or_nothing_selected(self):
        self.settings["calendar"]["enabled"] = False
        self.assertEqual(self.feed.schedules(self.settings), [])
        self.settings["calendar"].update(enabled=True, calendars=[])
        self.assertEqual(self.feed.schedules(self.settings), [])
        self.assertEqual(self.feed.schedules({}), [])

    def test_ids_are_stable_and_change_with_lead(self):
        a = self.feed.schedules(self.settings)[0]["id"]
        self.assertEqual(a, self.feed.schedules(self.settings)[0]["id"])
        self.settings["calendar"]["lead"] = 5
        self.assertNotEqual(a, self.feed.schedules(self.settings)[0]["id"])

    def test_reloads_when_the_file_changes_and_survives_missing_file(self):
        self.settings["calendar"]["calendars"] = ["fam", "work"]
        self.assertEqual(len(self.feed.schedules(self.settings)), 2)
        self.path.unlink()
        self.assertEqual(self.feed.schedules(self.settings), [])
        self.assertEqual(self.feed.status()["installed"], False)

    def test_status(self):
        st = self.feed.status()
        self.assertTrue(st["installed"])
        self.assertEqual(st["status"], "authorized")
        self.assertEqual([c["title"] for c in st["calendars"]], ["Morishitas", "Work"])


class TestFiring(Base):
    def test_fires_through_the_scheduler_without_touching_the_store(self):
        played, logs = [], []

        class P:
            def play(self, action, *, settings, label="", queue=False):
                played.append((label, action["text"]))

        store = config.Store(Path(self.tmp.name) / "config.json")
        store.update_settings(self.settings)
        sch = scheduler.Scheduler(store, P(), lambda lv, m, **k: logs.append((lv, m)), feed=self.feed)
        at = local("2026-09-24T19:00:00-04:00") - timedelta(minutes=10)
        sch._last_tick = at - timedelta(seconds=1)
        sch.tick(at)
        self.assertEqual(played, [("カレンダー: 算数", "10分後に、算数があります。")])
        self.assertIn(("fired", "カレンダー: 算数 を再生しました"), logs)
        self.assertEqual(store.schedules(), [])

    def test_timeline_and_next_events_include_calendar_items(self):
        store = config.Store(Path(self.tmp.name) / "config.json")
        store.update_settings(self.settings)
        sch = scheduler.Scheduler(store, None, lambda *a, **k: None, feed=self.feed)
        at = local("2026-09-24T18:50:00-04:00")
        days = scheduler.calendar_days(sch.all_schedules(), at.date(), 1, now=at - timedelta(hours=1))
        ev = days[0]["events"][0]
        self.assertEqual((ev["source"], ev["calendar"], ev["name"]), ("calendar", "Morishitas", "算数"))
        nxt = scheduler.next_events(sch.all_schedules(), now=at - timedelta(hours=1), limit=1)
        self.assertEqual(nxt[0]["name"], "算数")


class TestSettings(unittest.TestCase):
    def test_calendar_setting_survives_a_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            config.Store(path).update_settings({"calendar": {"enabled": True, "calendars": ["x"]}})
            again = config.Store(path).settings["calendar"]
            self.assertEqual((again["enabled"], again["calendars"]), (True, ["x"]))

    def test_every_setting_key_survives_a_restart(self):
        """起動時に知らない項目を読み捨てるので、設定の項目は全部 DEFAULT_SETTINGS に並べておく"""
        import re
        src = (Path(config.__file__)).read_text()
        keys = set(re.findall(r'if "(\w+)" in raw:', src.split("def validate_settings")[1]))
        self.assertLessEqual(keys, set(config.DEFAULT_SETTINGS))

    def test_validate(self):
        cur = dict(config.DEFAULT_SETTINGS)
        out = config.validate_settings({"calendar": {"enabled": True, "calendars": ["a", "a", "b"],
                                                     "lead": 15}}, cur)
        self.assertEqual(out["calendar"], {"enabled": True, "calendars": ["a", "b"], "lead": 15,
                                           "sound": "builtin:melody_notice", "voice": ""})
        with self.assertRaises(config.ValidationError):
            config.validate_settings({"calendar": {"lead": 999}}, cur)
        with self.assertRaises(config.ValidationError):
            config.validate_settings({"calendar": {"calendars": "a"}}, cur)


if __name__ == "__main__":
    unittest.main()
