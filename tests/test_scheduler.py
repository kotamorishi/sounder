"""スケジュール計算のテスト: python3 -m unittest discover -s tests"""

import sys
import unittest
from datetime import date, datetime
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


class TestDescribeMore(unittest.TestCase):
    def test_once_and_interval(self):
        once = sched(kind="once", date="2026-09-25", time="07:00", days=None)
        self.assertEqual(scheduler.describe(once), "2026-09-25 07:00")
        iv = sched(kind="interval", every_minutes=30, days=[0, 1, 2, 3, 4, 5, 6],
                   window={"start": "09:00", "end": "18:00"})
        self.assertEqual(scheduler.describe(iv), "毎日 09:00〜18:00 の 30分ごと")

    def test_day_shorthands(self):
        self.assertEqual(scheduler.describe_days([5, 6]), "週末")
        self.assertEqual(scheduler.describe_days([0, 2, 4]), "月水金曜")
        self.assertEqual(scheduler.describe_days([0, 1, 2, 3, 4, 5, 6]), "毎日")

    def test_day_of_month_labels(self):
        self.assertEqual(scheduler.describe_day_of_month("last"), "毎月末")
        self.assertEqual(scheduler.describe_day_of_month("last", bare=True), "月末")
        self.assertEqual(scheduler.describe_day_of_month(5), "毎月5日")


class TestIntervalAnchor(unittest.TestCase):
    def test_anchor_before_the_window_is_stepped_forward(self):
        s = sched(kind="interval", every_minutes=45, anchor="08:00",
                  window={"start": "09:00", "end": "11:00"}, days=[0, 1, 2, 3, 4, 5, 6])
        got = [w.strftime("%H:%M") for w, _t, _l in scheduler.events_between(
            s, datetime(2026, 9, 21, 0, 0), datetime(2026, 9, 21, 23, 59))]
        self.assertEqual(got, ["09:30", "10:15", "11:00"])


class TestQuietEdge(unittest.TestCase):
    def test_same_start_and_end_never_silences(self):
        st = {"quiet_hours": {"enabled": True, "start": "08:00", "end": "08:00"}}
        self.assertFalse(scheduler.in_quiet_hours(st, datetime(2026, 9, 21, 8, 0)))

    def test_missing_settings_key(self):
        self.assertFalse(scheduler.in_quiet_hours({}, datetime(2026, 9, 21, 8, 0)))


class TestNextEventsEdge(unittest.TestCase):
    def test_skips_occurrences_already_past_today(self):
        s = sched(time="08:00", days=[0, 1, 2, 3, 4, 5, 6])
        got = scheduler.next_events([s], now=datetime(2026, 9, 21, 9, 0), limit=1)
        self.assertEqual(got[0]["at"], "2026-09-22T08:00:00")

    def test_per_schedule_cap(self):
        s = sched(time="08:00", days=[0, 1, 2, 3, 4, 5, 6])
        got = scheduler.next_events([s], now=datetime(2026, 9, 21, 0, 0), limit=99, per_schedule=2)
        self.assertEqual(len(got), 2)


class TestCalendarDays(unittest.TestCase):
    def test_shape_and_flags(self):
        s = sched(name="朝", time="07:00", days=[0, 1, 2, 3, 4, 5, 6], lead_times=[10])
        days = scheduler.calendar_days(
            [s], date(2026, 9, 21), 2,
            settings={"quiet_hours": {"enabled": True, "start": "06:00", "end": "06:55"}},
            now=datetime(2026, 9, 21, 6, 58))
        self.assertEqual([d["date"] for d in days], ["2026-09-21", "2026-09-22"])
        self.assertTrue(days[0]["is_today"])
        self.assertFalse(days[1]["is_today"])
        first = days[0]["events"]
        self.assertEqual([e["time"] for e in first], ["06:50", "07:00"])
        self.assertTrue(first[0]["past"])      # 06:50 は 06:58 より前
        self.assertTrue(first[0]["quiet"])     # 禁止時間に入っている
        self.assertFalse(first[1]["past"])
        self.assertEqual(first[0]["tag"], "lead")
        self.assertEqual(first[0]["lead"], 10)

    def test_uses_the_lead_sound_for_lead_events(self):
        s = sched(lead_times=[5], action={"type": "both", "sound": "builtin:doorbell",
                                          "text": "はい"},
                  lead_action={"sound": "builtin:ding"})
        events = scheduler.calendar_days([s], date(2026, 9, 21), 1)[0]["events"]
        self.assertEqual(events[0]["sound"], "builtin:ding")
        self.assertEqual(events[0]["action_type"], "sound")
        self.assertEqual(events[1]["sound"], "builtin:doorbell")
        self.assertEqual(events[1]["action_type"], "both")

    def test_defaults_to_now(self):
        days = scheduler.calendar_days([], date.today(), 1)
        self.assertTrue(days[0]["is_today"])

    def test_lead_before_midnight_belongs_to_the_main_day(self):
        """0 時台の予定の予告は前日の時刻を持つ（画面側で時刻の日付に振り分ける）。"""
        s = sched(time="00:10", days=[1], lead_times=[30])  # 火曜 0:10 → 月曜 23:40 に予告
        days = scheduler.calendar_days([s], date(2026, 9, 21), 2, now=datetime(2026, 9, 20))
        self.assertEqual(days[0]["events"], [])
        self.assertEqual([e["at"] for e in days[1]["events"]],
                         ["2026-09-21T23:40:00", "2026-09-22T00:10:00"])


class TestOutcome(unittest.TestCase):
    """過ぎた予定がどうなったかを、実行ログから読み取る。"""

    def setUp(self):
        self.s = sched(name="朝", time="07:00", days=[0, 1, 2, 3, 4, 5, 6], lead_times=[5])

    def entry(self, ts, level, message, sid=None):
        return {"ts": ts, "level": level, "message": message,
                "schedule_id": self.s["id"] if sid is None else sid}

    def results(self, log, day=date(2026, 9, 21), now=datetime(2026, 9, 21, 8, 0)):
        events = scheduler.calendar_days([self.s], day, 1, now=now, log_entries=log)[0]["events"]
        return [e["result"] for e in events]

    def test_fired_and_skipped(self):
        log = [
            self.entry("2026-09-21T06:55:00", "fired", "朝（5分前の予告） を再生しました"),
            self.entry("2026-09-21T07:00:01", "skipped", "朝: 禁止時間のためスキップしました"),
        ]
        self.assertEqual(self.results(log), ["fired", "skipped"])

    def test_other_days_and_other_entries_are_ignored(self):
        log = [
            self.entry("2026-09-20T07:00:00", "fired", "朝 を再生しました"),      # 前日
            self.entry("2026-09-21T07:00:00", "info", "朝 を更新しました"),        # 種類が違う
            self.entry("2026-09-21T07:00:00", "fired", "朝 を再生しました", "x"),  # 別の予定
            self.entry("2026-09-21T07:00:00", "fired", "朝ごはん を再生しました"),  # 名前が違う
            self.entry("めちゃくちゃ", "fired", "朝 を再生しました"),               # 時刻が読めない
            {"level": "fired", "message": "朝 を再生しました", "schedule_id": self.s["id"]},
        ]
        self.assertEqual(self.results(log), [None, None])

    def test_missed_is_matched_by_the_stamp_in_the_message(self):
        log = [
            self.entry("2026-09-21T09:30:00", "missed",
                       "朝: 09/20 07:00 の予定を過ぎていたため鳴らしませんでした（150分遅れ）"),
            self.entry("2026-09-21T09:30:00", "missed",
                       "朝: 09/21 07:00 の予定を過ぎていたため鳴らしませんでした（150分遅れ）"),
        ]
        self.assertEqual(self.results(log, now=datetime(2026, 9, 21, 10, 0)), [None, "missed"])

    def test_future_events_have_no_result(self):
        log = [self.entry("2026-09-21T07:00:00", "fired", "朝 を再生しました")]
        self.assertEqual(self.results(log, now=datetime(2026, 9, 21, 6, 0)), [None, None])
