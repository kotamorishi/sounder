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
