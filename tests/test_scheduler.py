"""スケジュール計算のテスト: python3 -m unittest discover -s tests"""

import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sounder import config, scheduler  # noqa: E402


def sched(**kw):
    base = {"name": "テスト", "kind": "daily", "time": "08:30",
            "days": [0, 1, 2, 3, 4], "action": {"type": "sound", "sound": "builtin:ding"}}
    base.update(kw)
    return config.validate_schedule(base)


class TestOccurrences(unittest.TestCase):
    def test_daily_fires_on_selected_weekday_only(self):
        s = sched()
        mon = datetime(2026, 9, 21, 8, 29, 59)  # 月曜
        sat = datetime(2026, 9, 26, 8, 29, 59)  # 土曜
        self.assertEqual(len(scheduler.events_between(s, mon, mon.replace(second=59, minute=30))), 1)
        self.assertEqual(scheduler.events_between(s, sat, sat.replace(minute=30, second=59)), [])

    def test_lead_times_fire_before_main(self):
        s = sched(time="08:30", lead_times=[10, 5])
        lo = datetime(2026, 9, 21, 8, 0)
        hi = datetime(2026, 9, 21, 9, 0)
        got = [(w.strftime("%H:%M"), tag, lead) for w, tag, lead in scheduler.events_between(s, lo, hi)]
        self.assertEqual(got, [("08:20", "lead", 10), ("08:25", "lead", 5), ("08:30", "main", 0)])

    def test_lead_can_cross_into_previous_day(self):
        s = sched(time="00:10", days=[1], lead_times=[30])  # 火曜 0:10、30分前=月曜 23:40
        lo = datetime(2026, 9, 21, 23, 0)   # 月曜
        hi = datetime(2026, 9, 21, 23, 59)
        got = scheduler.events_between(s, lo, hi)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0][0], datetime(2026, 9, 21, 23, 40))

    def test_once_fires_only_on_its_date(self):
        s = sched(kind="once", date="2026-09-25", time="07:00", days=None)
        lo = datetime(2026, 9, 25, 6, 59)
        self.assertEqual(len(scheduler.events_between(s, lo, lo.replace(hour=7, minute=1))), 1)
        other = datetime(2026, 9, 26, 6, 59)
        self.assertEqual(scheduler.events_between(s, other, other.replace(hour=7, minute=1)), [])

    def test_interval_steps_within_window(self):
        s = sched(kind="interval", every_minutes=60, window={"start": "09:00", "end": "12:00"},
                  anchor="09:00", days=[0, 1, 2, 3, 4, 5, 6])
        lo = datetime(2026, 9, 21, 8, 0)
        hi = datetime(2026, 9, 21, 23, 0)
        got = [w.strftime("%H:%M") for w, _t, _l in scheduler.events_between(s, lo, hi)]
        self.assertEqual(got, ["09:00", "10:00", "11:00", "12:00"])

    def test_interval_window_across_midnight(self):
        s = sched(kind="interval", every_minutes=60, window={"start": "22:00", "end": "02:00"},
                  anchor="22:00", days=[0, 1, 2, 3, 4, 5, 6])
        lo = datetime(2026, 9, 21, 21, 0)
        hi = datetime(2026, 9, 22, 3, 0)
        got = [w.strftime("%d %H:%M") for w, _t, _l in scheduler.events_between(s, lo, hi)]
        self.assertEqual(got, ["21 22:00", "21 23:00", "22 00:00", "22 01:00", "22 02:00"])

    def test_events_are_exclusive_at_lower_bound(self):
        """同じ時刻が2回のティックで二重に拾われないこと。"""
        s = sched(time="08:30")
        exact = datetime(2026, 9, 21, 8, 30)
        self.assertEqual(len(scheduler.events_between(s, exact.replace(minute=29), exact)), 1)
        self.assertEqual(scheduler.events_between(s, exact, exact.replace(minute=31)), [])


class TestQuietHours(unittest.TestCase):
    def test_wrapping_range(self):
        st = {"quiet_hours": {"enabled": True, "start": "23:00", "end": "07:00"}}
        self.assertTrue(scheduler.in_quiet_hours(st, datetime(2026, 9, 21, 23, 30)))
        self.assertTrue(scheduler.in_quiet_hours(st, datetime(2026, 9, 21, 3, 0)))
        self.assertFalse(scheduler.in_quiet_hours(st, datetime(2026, 9, 21, 7, 0)))
        self.assertFalse(scheduler.in_quiet_hours(st, datetime(2026, 9, 21, 12, 0)))

    def test_disabled(self):
        st = {"quiet_hours": {"enabled": False, "start": "23:00", "end": "07:00"}}
        self.assertFalse(scheduler.in_quiet_hours(st, datetime(2026, 9, 21, 23, 30)))


class TestValidation(unittest.TestCase):
    def test_rejects_bad_time(self):
        with self.assertRaises(config.ValidationError):
            sched(time="25:00")

    def test_rejects_empty_days(self):
        with self.assertRaises(config.ValidationError):
            sched(days=[])

    def test_rejects_missing_text_for_speak(self):
        with self.assertRaises(config.ValidationError):
            sched(action={"type": "speak"})

    def test_volume_range(self):
        with self.assertRaises(config.ValidationError):
            sched(action={"type": "sound", "sound": "builtin:ding", "volume": 5})

    def test_lead_times_deduped_and_sorted(self):
        s = sched(lead_times=[5, 10, 5])
        self.assertEqual(s["lead_times"], [10, 5])


class TestNextEvents(unittest.TestCase):
    def test_orders_across_schedules_and_skips_disabled(self):
        a = sched(name="朝", time="07:00")
        b = sched(name="夜", time="19:00")
        off = sched(name="オフ", time="08:00", enabled=False)
        now = datetime(2026, 9, 21, 6, 0)  # 月曜
        got = scheduler.next_events([a, b, off], now=now, limit=5)
        self.assertEqual([e["name"] for e in got][:2], ["朝", "夜"])
        self.assertNotIn("オフ", [e["name"] for e in got])


class TestDescribe(unittest.TestCase):
    def test_weekday_shorthand(self):
        self.assertTrue(scheduler.describe(sched()).startswith("平日 08:30"))
        self.assertTrue(scheduler.describe(sched(days=[0, 1, 2, 3, 4, 5, 6])).startswith("毎日"))
        self.assertIn("10分前", scheduler.describe(sched(lead_times=[10])))


if __name__ == "__main__":
    unittest.main()
