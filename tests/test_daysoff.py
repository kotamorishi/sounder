"""お休みの日（オンタリオ州の祝日・TDSB の休校日）のテスト。"""

import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sounder import config, daysoff, scheduler  # noqa: E402

D = date.fromisoformat


class TestOntarioHolidays(unittest.TestCase):
    def test_2026_27_matches_the_official_tdsb_key_dates(self):
        # TDSB の Key Dates PDF に載っている祝日と一致すること
        h26, h27 = daysoff.ontario_holidays(2026), daysoff.ontario_holidays(2027)
        self.assertEqual(h26[D("2026-09-07")], "レイバー・デー")
        self.assertEqual(h26[D("2026-10-12")], "サンクスギビング")
        self.assertEqual(h27[D("2027-02-15")], "ファミリー・デー")
        self.assertEqual(h27[D("2027-03-26")], "グッドフライデー")
        self.assertEqual(h27[D("2027-05-24")], "ビクトリア・デー")

    def test_nine_statutory_holidays(self):
        h = daysoff.ontario_holidays(2025)  # 振替なしの年
        self.assertEqual(sorted(h), [D("2025-01-01"), D("2025-02-17"), D("2025-04-18"),
                                     D("2025-05-19"), D("2025-07-01"), D("2025-09-01"),
                                     D("2025-10-13"), D("2025-12-25"), D("2025-12-26")])

    def test_civic_holiday_and_easter_monday_are_not_provincial_holidays(self):
        h = daysoff.ontario_holidays(2026)
        self.assertNotIn(D("2026-08-03"), h)   # シビック・ホリデー
        self.assertNotIn(D("2026-04-06"), h)   # イースター・マンデー

    def test_weekend_holidays_get_a_substitute_weekday(self):
        h = daysoff.ontario_holidays(2021)     # 12/25 土・12/26 日
        self.assertEqual(h[D("2021-12-27")], "クリスマスの振替休日")
        self.assertEqual(h[D("2021-12-28")], "ボクシング・デーの振替休日")
        self.assertEqual(daysoff.ontario_holidays(2022)[D("2022-01-03")], "元日の振替休日")
        self.assertEqual(daysoff.ontario_holidays(2029)[D("2029-07-02")], "カナダ・デーの振替休日")

    def test_victoria_day_is_the_monday_before_may_25(self):
        self.assertIn(D("2027-05-24"), daysoff.ontario_holidays(2027))  # 5/24 が月曜
        self.assertIn(D("2026-05-18"), daysoff.ontario_holidays(2026))
        self.assertIn(D("2025-05-19"), daysoff.ontario_holidays(2025))

    def test_easter(self):
        self.assertEqual(daysoff.easter(2026), D("2026-04-05"))
        self.assertEqual(daysoff.easter(2027), D("2027-03-28"))
        self.assertEqual(daysoff.easter(2038), D("2038-04-25"))


class TestTdsb(unittest.TestCase):
    def r(self, panel, iso):
        return daysoff.reason(f"tdsb_{panel}", D(iso))

    def test_pa_days_differ_by_panel(self):
        self.assertEqual(self.r("elementary", "2027-01-15"), "PA デー")
        self.assertIsNone(self.r("secondary", "2027-01-15"))
        self.assertEqual(self.r("secondary", "2027-02-02"), "PA デー")
        self.assertIsNone(self.r("elementary", "2027-02-02"))
        for iso in ("2026-09-02", "2026-09-03", "2026-11-20", "2027-02-12", "2027-06-30"):
            self.assertEqual(self.r("elementary", iso), "PA デー", iso)
            self.assertEqual(self.r("secondary", iso), "PA デー", iso)

    def test_breaks(self):
        self.assertEqual(self.r("elementary", "2026-12-21"), "冬休み")
        self.assertEqual(self.r("elementary", "2027-01-01"), "冬休み")
        self.assertIsNone(self.r("elementary", "2027-01-04"))
        self.assertEqual(self.r("elementary", "2027-03-17"), "ミッドウィンター・ブレイク")
        self.assertEqual(self.r("elementary", "2027-03-29"), "イースター・マンデー")

    def test_holidays_inside_the_school_year(self):
        self.assertEqual(self.r("elementary", "2026-10-12"), "サンクスギビング")
        self.assertEqual(self.r("elementary", "2026-09-07"), "レイバー・デー")

    def test_first_and_last_days(self):
        self.assertEqual(self.r("elementary", "2026-09-04"), "新学期前")
        self.assertIsNone(self.r("elementary", "2026-09-08"))
        self.assertIsNone(self.r("elementary", "2027-06-29"))      # 小学校の最終日
        self.assertEqual(self.r("secondary", "2027-06-29"), "PA デー")
        self.assertIsNone(self.r("secondary", "2027-06-28"))       # 中高の最終日
        self.assertEqual(self.r("elementary", "2027-07-15"), "夏休み")
        self.assertEqual(self.r("elementary", "2027-08-31"), "夏休み")

    def test_ordinary_school_days_and_remembrance_day(self):
        self.assertIsNone(self.r("elementary", "2026-11-11"))
        self.assertIsNone(self.r("elementary", "2026-09-24"))

    def test_unknown_years_are_not_treated_as_days_off(self):
        # 予定表が無い年は鳴らす側に倒す
        self.assertIsNone(self.r("elementary", "2027-09-07"))
        self.assertIsNone(self.r("elementary", "2026-06-15"))
        self.assertEqual(daysoff.known_until("tdsb_elementary"), D("2027-08-31"))
        self.assertIsNone(daysoff.known_until("on_holidays"))

    def test_catalog(self):
        ids = [c["id"] for c in daysoff.catalog()]
        self.assertEqual(ids, ["on_holidays", "tdsb_elementary", "tdsb_secondary"])


def weekday_sched(**kw):
    s = {"id": "a1", "name": "お迎え", "enabled": True, "kind": "weekly", "time": "15:00",
         "days": [0, 1, 2, 3, 4], "lead_times": [10],
         "action": {"type": "sound", "sound": "builtin:ding"},
         "lead_action": {"type": "sound", "sound": "builtin:ding"}}
    s.update(kw)
    return s


class TestSchedulerSkip(unittest.TestCase):
    def test_holiday_skips_main_and_lead(self):
        s = weekday_sched(skip=["on_holidays"])
        self.assertEqual(scheduler.day_occurrences(s, D("2026-10-12")), [])
        self.assertEqual(len(scheduler.day_occurrences(s, D("2026-10-13"))), 2)

    def test_without_skip_holidays_still_ring(self):
        self.assertEqual(len(scheduler.day_occurrences(weekday_sched(), D("2026-10-12"))), 2)

    def test_next_events_jump_over_pa_day(self):
        s = weekday_sched(skip=["tdsb_elementary"], lead_times=[])
        ev = scheduler.next_events([s], now=datetime(2026, 11, 19, 16, 0), limit=1)
        self.assertEqual(ev[0]["at"], "2026-11-23T15:00:00")   # 11/20(金) は PA デー

    def test_calendar_marks_skipped_events_and_day_notes(self):
        s = weekday_sched(skip=["tdsb_elementary"], lead_times=[])
        days = scheduler.calendar_days([s], D("2026-11-19"), 2, now=datetime(2026, 11, 1))
        thu, fri = days
        self.assertIsNone(thu["events"][0]["off"])
        self.assertEqual(fri["events"][0]["off"], "PA デー")
        self.assertEqual(fri["notes"], ["PA デー"])
        self.assertEqual(thu["notes"], [])

    def test_holiday_note_is_shown_even_without_skip(self):
        days = scheduler.calendar_days([weekday_sched()], D("2026-10-12"), 1,
                                       now=datetime(2026, 10, 1))
        self.assertEqual(days[0]["notes"], ["サンクスギビング"])
        self.assertIsNone(days[0]["events"][0]["off"])

    def test_describe(self):
        s = weekday_sched(skip=["on_holidays", "tdsb_secondary"], lead_times=[])
        self.assertEqual(scheduler.describe(s), "平日 15:00（祝日・休校日は休み）")

    def test_tick_does_not_fire_on_a_holiday(self):
        played = []

        class P:
            def play(self, action, *, settings, label="", queue=False):
                played.append(label)

        with tempfile.TemporaryDirectory() as tmp:
            store = config.Store(Path(tmp) / "config.json")
            store.add(weekday_sched(id=None, skip=["on_holidays"], lead_times=[]))
            sch = scheduler.Scheduler(store, P(), lambda *a, **k: None)
            for day in ("2026-10-12", "2026-10-13"):
                t = datetime.fromisoformat(f"{day}T14:59:59")
                sch._last_tick = t
                sch.tick(t + timedelta(seconds=1))
        self.assertEqual(played, ["お迎え"])  # 祝日の 10/12 は鳴らず、10/13 だけ鳴る


class TestValidation(unittest.TestCase):
    def base(self, **kw):
        raw = {"name": "お迎え", "kind": "weekly", "time": "15:00", "days": [0, 1, 2, 3, 4],
               "action": {"type": "sound", "sound": "builtin:ding"}}
        raw.update(kw)
        return raw

    def test_skip_is_normalized(self):
        s = config.validate_schedule(self.base(skip=["tdsb_elementary", "on_holidays",
                                                     "on_holidays"]))
        self.assertEqual(s["skip"], ["on_holidays", "tdsb_elementary"])

    def test_default_is_empty(self):
        self.assertEqual(config.validate_schedule(self.base())["skip"], [])

    def test_unknown_calendar_is_rejected(self):
        with self.assertRaises(config.ValidationError):
            config.validate_schedule(self.base(skip=["japan"]))
        with self.assertRaises(config.ValidationError):
            config.validate_schedule(self.base(skip="on_holidays"))

    def test_once_ignores_skip(self):
        s = config.validate_schedule(self.base(kind="once", date="2026-10-12",
                                               skip=["on_holidays"]))
        self.assertEqual(s["skip"], [])


if __name__ == "__main__":
    unittest.main()
