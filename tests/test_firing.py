"""Scheduler.tick が「いつ・何を」鳴らすかのテスト。音は出さない。"""

import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sounder import config, scheduler  # noqa: E402


class FakePlayer:
    def __init__(self):
        self.played = []
        self.queued = []

    def play(self, action, *, settings, label="", queue=False):
        self.played.append((label, action))
        self.queued.append(queue)

    def stop(self):
        pass


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = config.Store(Path(self.tmp.name) / "config.json")
        self.player = FakePlayer()
        self.msgs = []
        self.log = lambda level, msg, **kw: self.msgs.append((level, msg))
        self.sched = scheduler.Scheduler(self.store, self.player, self.log)

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, **kw):
        base = {"name": "テスト", "kind": "daily", "time": "08:30",
                "days": [0, 1, 2, 3, 4, 5, 6],
                "action": {"type": "sound", "sound": "builtin:ding", "volume": 0.5}}
        base.update(kw)
        return self.store.add(base)

    def levels(self):
        return [lv for lv, _m in self.msgs]


class TestFiring(Base):
    def test_scheduled_sounds_are_queued_not_cut(self):
        """同じ時刻に複数鳴るとき、後から来たものが前のものを消さないこと。"""
        self.add(name="A", time="08:30")
        self.add(name="B", time="08:30")
        t = datetime(2026, 9, 21, 8, 29, 59)
        self.sched._last_tick = t
        self.sched.tick(t + timedelta(seconds=1))
        self.assertEqual(len(self.player.played), 2)
        self.assertEqual(self.player.queued, [True, True])

    def test_fires_once_at_the_right_second(self):
        self.add(time="08:30")
        t = datetime(2026, 9, 21, 8, 29, 59)
        self.sched._last_tick = t
        self.sched.tick(t + timedelta(seconds=1))      # 08:30:00 -> 鳴る
        self.sched.tick(t + timedelta(seconds=2))      # 二重に鳴らない
        self.assertEqual(len(self.player.played), 1)
        self.assertIn("fired", self.levels())

    def test_lead_uses_lead_action(self):
        self.add(time="08:30", lead_times=[10],
                 lead_action={"sound": "builtin:melody_notice"})
        t = datetime(2026, 9, 21, 8, 19, 59)
        self.sched._last_tick = t
        self.sched.tick(t + timedelta(seconds=1))
        self.assertEqual(len(self.player.played), 1)
        label, action = self.player.played[0]
        self.assertIn("10分前", label)
        self.assertEqual(action["sound"], "builtin:melody_notice")

    def test_lead_speak_remaining_builds_sentence(self):
        self.add(name="お出かけ", time="08:30", lead_times=[5],
                 lead_action={"sound": "builtin:ding", "speak_remaining": True})
        t = datetime(2026, 9, 21, 8, 24, 59)
        self.sched._last_tick = t
        self.sched.tick(t + timedelta(seconds=1))
        _label, action = self.player.played[0]
        self.assertEqual(action["type"], "both")
        self.assertEqual(action["text"], "お出かけまで、あと5分です。")

    def test_disabled_schedule_is_silent(self):
        self.add(time="08:30", enabled=False)
        t = datetime(2026, 9, 21, 8, 29, 59)
        self.sched._last_tick = t
        self.sched.tick(t + timedelta(seconds=1))
        self.assertEqual(self.player.played, [])

    def test_missed_after_sleep_is_not_played(self):
        """スリープ復帰で何時間も前の予定が一斉に鳴らないこと。"""
        self.add(time="08:30")
        self.sched._last_tick = datetime(2026, 9, 21, 8, 0)
        self.sched.tick(datetime(2026, 9, 21, 12, 0))   # 4時間後に復帰
        self.assertEqual(self.player.played, [])
        self.assertIn("missed", self.levels())

    def test_slightly_late_still_plays(self):
        self.add(time="08:30")
        self.sched._last_tick = datetime(2026, 9, 21, 8, 29, 50)
        self.sched.tick(datetime(2026, 9, 21, 8, 30, 30))  # 30秒遅れ
        self.assertEqual(len(self.player.played), 1)

    def test_quiet_hours_skips(self):
        self.store.update_settings({"quiet_hours": {"enabled": True, "start": "23:00", "end": "07:00"}})
        self.add(time="02:00")
        t = datetime(2026, 9, 21, 1, 59, 59)
        self.sched._last_tick = t
        self.sched.tick(t + timedelta(seconds=1))
        self.assertEqual(self.player.played, [])
        self.assertIn("skipped", self.levels())

    def test_master_off_skips(self):
        self.store.update_settings({"master_enabled": False})
        self.add(time="08:30")
        t = datetime(2026, 9, 21, 8, 29, 59)
        self.sched._last_tick = t
        self.sched.tick(t + timedelta(seconds=1))
        self.assertEqual(self.player.played, [])
        self.assertIn("skipped", self.levels())

    def test_once_turns_itself_off_and_records_time(self):
        s = self.add(kind="once", date="2026-09-21", time="08:30", days=None)
        t = datetime(2026, 9, 21, 8, 29, 59)
        self.sched._last_tick = t
        self.sched.tick(t + timedelta(seconds=1))
        after = self.store.get(s["id"])
        self.assertFalse(after["enabled"])
        self.assertEqual(after["last_fired"], "2026-09-21T08:30:00")

    def test_interval_fires_each_step(self):
        self.add(kind="interval", every_minutes=30, time=None,
                 window={"start": "09:00", "end": "10:00"}, anchor="09:00")
        self.sched._last_tick = datetime(2026, 9, 21, 8, 59, 59)
        for minute in (0, 30, 60):
            at = datetime(2026, 9, 21, 9, 0) + timedelta(minutes=minute)
            self.sched._last_tick = at - timedelta(seconds=1)
            self.sched.tick(at)
        self.assertEqual(len(self.player.played), 3)

    def test_clock_going_backwards_does_not_crash(self):
        self.add(time="08:30")
        self.sched._last_tick = datetime(2026, 9, 21, 9, 0)
        self.sched.tick(datetime(2026, 9, 21, 8, 0))  # 時計が巻き戻った
        self.assertEqual(self.player.played, [])

    def test_store_survives_broken_config_file(self):
        path = Path(self.tmp.name) / "broken.json"
        path.write_text("{ これはJSONではない", "utf-8")
        store = config.Store(path)
        self.assertEqual(store.schedules(), [])


if __name__ == "__main__":
    unittest.main()


class TestLoopRobustness(Base):
    def test_tick_error_is_logged_and_the_loop_survives(self):
        def boom():
            raise RuntimeError("こわれた")
        self.store.schedules = boom
        self.sched.start()
        try:
            for _ in range(100):
                if any(lv == "error" for lv, _m in self.msgs):
                    break
                time.sleep(0.05)
        finally:
            self.sched.stop()
        self.assertTrue(any("スケジューラでエラー" in m for _lv, m in self.msgs))

    def test_start_and_stop(self):
        self.add(time="08:30")
        self.sched.start()
        time.sleep(0.05)
        self.sched.stop()
        self.assertEqual(self.player.played, [])

    def test_already_fired_keys_are_skipped(self):
        s = self.add(time="08:30")
        when = datetime(2026, 9, 21, 8, 30)
        self.sched._fired.add(f"{s['id']}|{when.isoformat()}|main0")
        self.sched._last_tick = when - timedelta(seconds=1)
        self.sched.tick(when)
        self.assertEqual(self.player.played, [])

    def test_fired_keys_are_pruned(self):
        self.sched._fired = {f"k{i}" for i in range(4001)}
        self.sched._last_tick = datetime(2026, 9, 21, 8, 0)
        self.sched.tick(datetime(2026, 9, 21, 8, 0, 1))
        self.assertEqual(len(self.sched._fired), 1000)

    def test_once_that_vanishes_before_being_disabled(self):
        """鳴らした直後に消された単発予定でも落ちないこと。"""
        s = self.add(kind="once", date="2026-09-21", time="08:30", days=None)

        def gone(_sid, _changes):
            raise KeyError("もういない")
        self.store.patch = gone
        t = datetime(2026, 9, 21, 8, 29, 59)
        self.sched._last_tick = t
        self.sched.tick(t + timedelta(seconds=1))
        self.assertEqual(len(self.player.played), 1)


class TestLeadActionFallback(Base):
    def test_lead_without_a_lead_action_uses_a_default_sound(self):
        s = self.add(time="08:30", lead_times=[5])
        self.store._data["schedules"][0]["lead_action"] = None  # 古い設定ファイル相当
        t = datetime(2026, 9, 21, 8, 24, 59)
        self.sched._last_tick = t
        self.sched.tick(t + timedelta(seconds=1))
        _label, action = self.player.played[0]
        self.assertEqual(action["sound"], "builtin:melody_notice")


class TestCleanPastOnce(Base):
    def test_removes_once_schedules_before_today_once_a_day(self):
        old = self.add(name="去年の健診", kind="once", date="2026-09-20", time="10:00")
        today = self.add(name="今日の歯医者", kind="once", date="2026-09-21", time="08:00")
        later = self.add(name="来週の遠足", kind="once", date="2026-09-28", time="08:20")
        weekly = self.add(name="毎日")
        t = datetime(2026, 9, 21, 0, 0, 5)
        self.sched._last_tick = t
        self.sched.tick(t)
        ids = [s["id"] for s in self.store.schedules()]
        self.assertNotIn(old["id"], ids)
        self.assertEqual(sorted(ids), sorted([today["id"], later["id"], weekly["id"]]))
        self.assertTrue(any("去年の健診" in m for _lv, m in self.msgs))
        # 同じ日のうちは見に行かない。翌日になると「今日の歯医者」も片付く
        self.sched.tick(t + timedelta(hours=1))
        self.assertEqual(len(self.store.schedules()), 3)
        self.sched.tick(datetime(2026, 9, 22, 0, 0, 1))
        self.assertNotIn(today["id"], [s["id"] for s in self.store.schedules()])


class TestSkipDates(Base):
    def test_skipped_day_does_not_fire_main_or_lead(self):
        s = self.add(name="お迎え", time="08:30", lead_times=[10],
                     lead_action={"sound": "builtin:ding"}, skip_dates=["2026-09-21"])
        self.assertEqual(s["skip_dates"], ["2026-09-21"])
        t = datetime(2026, 9, 21, 8, 0)
        self.sched._last_tick = t
        self.sched.tick(datetime(2026, 9, 21, 8, 31))
        self.assertEqual(self.player.played, [])
        # 翌日はいつもどおり
        self.sched._last_tick = datetime(2026, 9, 22, 8, 19)
        self.sched.tick(datetime(2026, 9, 22, 8, 21))
        self.sched.tick(datetime(2026, 9, 22, 8, 31))
        self.assertEqual(len(self.player.played), 2)

    def test_timeline_shows_skipped_day_as_off(self):
        s = self.add(name="お迎え", skip_dates=["2026-09-21"])
        days = scheduler.calendar_days([s], datetime(2026, 9, 21).date(), 2,
                                       now=datetime(2026, 9, 20))
        (a,), (b,) = days[0]["events"], days[1]["events"]
        self.assertEqual((a["off"], a["skipped"], a["base_date"]), ("この日はオフ", True, "2026-09-21"))
        self.assertEqual((b["off"], b["skipped"]), (None, False))

    def test_editor_save_keeps_skip_dates_and_once_has_none(self):
        s = self.add(name="お迎え", skip_dates=["2026-09-21"])
        raw = {k: v for k, v in s.items() if k != "skip_dates"}
        self.assertEqual(self.store.replace(s["id"], raw)["skip_dates"], ["2026-09-21"])
        o = self.add(name="単発", kind="once", date="2026-09-21", skip_dates=["2026-09-21"])
        self.assertEqual(o["skip_dates"], [])
        with self.assertRaises(config.ValidationError):
            self.add(skip_dates=["9/21"])

    def test_past_skip_dates_are_pruned_daily(self):
        s = self.add(name="お迎え", skip_dates=["2026-09-20", "2026-09-21", "2026-09-30"])
        self.store.update_settings({"calendar": {"muted": ["a|2026-09-20T10:00:00-04:00",
                                                           "b|2026-09-21T10:00:00-04:00"]}})
        self.sched.tick(datetime(2026, 9, 21, 0, 0, 5))
        self.assertEqual(self.store.get(s["id"])["skip_dates"], ["2026-09-21", "2026-09-30"])
        self.assertEqual(self.store.settings["calendar"]["muted"], ["b|2026-09-21T10:00:00-04:00"])


class TestOutput(Base):
    def test_outputs_go_with_main_and_lead(self):
        bed = {"id": "ABC", "name": "Bedroom", "address": "192.0.2.1"}
        mac = {"id": "mac", "name": "この Mac", "address": ""}
        s = self.add(name="起床", outputs=[bed, mac, bed], lead_times=[5])
        self.assertEqual(s["outputs"], [bed, mac])     # 同じ機器は 1 つに
        for tag, lead in (("main", 0), ("lead", 5)):
            self.assertEqual(self.sched.action_for(s, tag, lead, self.store.settings)["outputs"], [bed, mac])
        self.assertEqual(self.add(name="ふつう")["outputs"], [])
        self.assertEqual(self.add(name="Mac だけ", outputs=[mac])["outputs"], [])
        self.assertNotIn("outputs", self.sched.action_for(self.add(name="x"), "main", 0, {}))
        with self.assertRaises(config.ValidationError):
            self.add(outputs=[{"name": "id が無い"}])

    def test_volume_per_output(self):
        bed = {"id": "ABC", "name": "Bedroom", "address": ""}
        mac = {"id": "mac", "name": "この Mac", "address": ""}
        s = self.add(name="起床", outputs=[{**bed, "volume": 0.3}, {**mac, "volume": 1.2}])
        self.assertEqual(s["outputs"], [{**bed, "volume": 0.3}, {**mac, "volume": 1.2}])
        # AirPlay の機器は 100% まで。空は「予定と同じ」
        self.assertEqual(self.add(name="a", outputs=[{**bed, "volume": 1.5}])["outputs"], [{**bed, "volume": 1.0}])
        self.assertEqual(self.add(name="b", outputs=[{**bed, "volume": None}])["outputs"], [bed])
        # この Mac だけでも音量があれば残す
        self.assertEqual(self.add(name="c", outputs=[{**mac, "volume": 0.4}])["outputs"], [{**mac, "volume": 0.4}])
        with self.assertRaises(config.ValidationError):
            self.add(name="d", outputs=[{**bed, "volume": "大"}])

    def test_old_single_output_is_migrated(self):
        bed = {"id": "ABC", "name": "Bedroom", "address": ""}
        self.assertEqual(self.add(name="起床", output=bed)["outputs"], [bed])


class LoopPlayer(FakePlayer):
    def __init__(self):
        super().__init__()
        self.desired = []
        self.running = set()

    def sync_loops(self, desired):
        self.desired.append(desired)
        started = [k for k in desired if k not in self.running]
        self.running = set(desired)
        return started


class TestUntil(Base):
    def setUp(self):
        super().setUp()
        self.player = LoopPlayer()
        self.sched = scheduler.Scheduler(self.store, self.player, self.log)

    def run_at(self, t):
        self.sched._last_tick = t - timedelta(seconds=1)
        self.sched.tick(t)
        return self.player.desired[-1]

    def test_loops_during_the_window_only(self):
        s = self.add(name="ホワイトノイズ", time="22:00", until="23:00", lead_times=[5],
                     lead_action={"sound": "builtin:ding"})
        self.assertEqual(self.run_at(datetime(2026, 9, 21, 21, 59)), {})
        d = self.run_at(datetime(2026, 9, 21, 22, 0))
        (key, spec), = d.items()
        self.assertTrue(key.startswith(f"{s['id']}|2026-09-21T22:00:00|"))
        self.assertEqual(spec["until"], datetime(2026, 9, 21, 23, 0))
        self.assertEqual(spec["action"]["sound"], "builtin:ding")
        self.assertTrue(any("23:00 まで繰り返し" in m for _lv, m in self.msgs))
        # 途中で起動し直しても、残りの時間を鳴らす
        self.assertEqual(len(self.run_at(datetime(2026, 9, 21, 22, 40))), 1)
        self.assertEqual(self.run_at(datetime(2026, 9, 21, 23, 0)), {})
        # 本番はふつうの再生には並べない。予告はふつうに鳴る
        self.assertEqual([lbl for lbl, _a in self.player.played], [])

    def test_lead_still_fires(self):
        self.add(name="ノイズ", time="22:00", until="23:00", lead_times=[5],
                 lead_action={"sound": "builtin:ding"})
        self.run_at(datetime(2026, 9, 21, 21, 55))
        self.assertEqual(len(self.player.played), 1)

    def test_crosses_midnight_and_respects_master_switch(self):
        self.add(name="夜", time="23:30", until="01:00")
        self.assertEqual(len(self.run_at(datetime(2026, 9, 22, 0, 30))), 1)
        self.store.update_settings({"master_enabled": False})
        self.assertEqual(self.run_at(datetime(2026, 9, 22, 0, 31)), {})

    def test_validation(self):
        with self.assertRaises(config.ValidationError):
            self.add(until="22:00", time="22:00")
        with self.assertRaises(config.ValidationError):
            self.add(until="23:00", time="22:00", action={"type": "speak", "text": "x"})
        with self.assertRaises(config.ValidationError):
            self.add(kind="interval", every_minutes=30, until="23:00")
        s = self.add(until="23:00", time="22:00", outing=True)
        self.assertFalse(s["outing"])
        self.assertIsNone(self.add()["until"])

    def test_timeline_span(self):
        s = self.add(name="ノイズ", time="22:00", until="23:30")
        (e,) = scheduler.calendar_days([s], datetime(2026, 9, 21).date(), 1, now=datetime(2026, 9, 20))[0]["events"]
        self.assertEqual((e["starts_at"], e["ends_at"], e["loop"]),
                         ("2026-09-21T22:00:00", "2026-09-21T23:30:00", True))


class TestBgm(Base):
    """設定の「時間帯で流す」。毎日、予定とは別に流し、予定の一覧・タイムラインには出さない。"""

    def setUp(self):
        super().setUp()
        self.player = LoopPlayer()
        self.sched = scheduler.Scheduler(self.store, self.player, self.log)

    run_at = TestUntil.run_at

    BED = {"id": "ABC", "name": "Bedroom", "address": "192.168.1.5", "volume": 0.3}

    def set_bgm(self, **kw):
        b = {"name": "BGM", "start": "09:00", "end": "11:30", "sound": "user:bgm.mp3",
             "volume": 0.5, "outputs": [self.BED], **kw}
        return self.store.update_settings({"bgm": [b]})["bgm"][0]

    def test_loops_every_day_on_chosen_outputs(self):
        b = self.set_bgm()
        self.assertEqual(self.run_at(datetime(2026, 9, 21, 8, 59)), {})
        for day in (21, 26):   # 平日も週末も
            (key, spec), = self.run_at(datetime(2026, 9, day, 10, 0)).items()
            self.assertTrue(key.startswith(f"bgm:{b['id']}|2026-09-{day}T09:00:00|"))
            self.assertEqual(spec["until"], datetime(2026, 9, day, 11, 30))
            self.assertEqual(spec["action"]["sound"], "user:bgm.mp3")
            self.assertEqual(spec["action"]["volume"], 0.5)
            self.assertEqual(spec["action"]["outputs"], [self.BED])
        self.assertEqual(self.run_at(datetime(2026, 9, 26, 11, 30)), {})
        # 予定・タイムラインには出ない
        self.assertEqual(self.sched.all_schedules(), [])
        self.assertEqual(self.store.schedules(), [])

    def test_off_and_master_stop_it_but_quiet_hours_do_not(self):
        self.set_bgm(enabled=False)
        self.assertEqual(self.run_at(datetime(2026, 9, 21, 10, 0)), {})
        self.set_bgm()
        self.store.update_settings({"quiet_hours": {"enabled": True, "start": "09:30", "end": "10:30"}})
        self.assertEqual(len(self.run_at(datetime(2026, 9, 21, 10, 0))), 1)
        self.store.update_settings({"master_enabled": False})
        self.assertEqual(self.run_at(datetime(2026, 9, 21, 10, 1)), {})

    def test_crosses_midnight(self):
        self.set_bgm(start="22:00", end="06:00")
        (key, spec), = self.run_at(datetime(2026, 9, 22, 5, 0)).items()
        self.assertIn("|2026-09-21T22:00:00|", key)
        self.assertEqual(spec["until"], datetime(2026, 9, 22, 6, 0))

    def test_validation(self):
        with self.assertRaises(config.ValidationError):
            self.set_bgm(end="09:00")
        with self.assertRaises(config.ValidationError):
            self.set_bgm(sound="")
        with self.assertRaises(config.ValidationError):
            self.set_bgm(start="9時")
        b = self.set_bgm(name="", volume=None, outputs=[])
        self.assertEqual((b["name"], b["volume"], b["outputs"]), ("時間帯で流す", 0.6, []))
        self.assertTrue(b["id"])
        # id は保存後も変わらない（画面から送り直しても同じ回として続ける）
        self.assertEqual(self.store.update_settings({"bgm": [b]})["bgm"][0]["id"], b["id"])

    def test_sound_must_exist(self):
        def check(ref):
            if ref != "user:bgm.mp3":
                raise FileNotFoundError(ref)
        self.store.sound_check = check
        self.set_bgm()
        with self.assertRaises(config.ValidationError):
            self.set_bgm(sound="user:nai.mp3")
