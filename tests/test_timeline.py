"""タイムライン（GET /api/timeline）のテスト。音は出さない。"""

import json
import sys
import tempfile
import threading
import unittest
from datetime import date, datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sounder import config, scheduler, server  # noqa: E402

MON = date(2026, 9, 21)  # 月曜
SETTINGS = dict(config.DEFAULT_SETTINGS)


def sched(**kw):
    base = {"name": "テスト", "kind": "daily", "time": "08:30",
            "days": [0, 1, 2, 3, 4, 5, 6], "action": {"type": "sound", "sound": "builtin:ding"}}
    base.update(kw)
    return config.validate_schedule(base)


def hm(events):
    return [(datetime.fromisoformat(e["at"]).strftime("%H:%M"), e["tag"], e["lead"]) for e in events]


class TestTimeline(unittest.TestCase):
    def test_includes_past_events_and_leads_in_order(self):
        s = sched(time="08:30", lead_times=[10, 5])
        now = datetime(2026, 9, 21, 12, 0)  # 全部過ぎている
        got = scheduler.timeline([s], MON, settings=SETTINGS, now=now)
        self.assertEqual(hm(got), [("08:20", "lead", 10), ("08:25", "lead", 5), ("08:30", "main", 0)])
        self.assertTrue(all(e["past"] for e in got))
        self.assertTrue(all(e["main_at"] == "2026-09-21T08:30:00" for e in got))
        self.assertFalse(any(e["will_ring"] for e in got))

    def test_matches_next_events(self):
        """next_events と同じ判定で、同じ時刻が並ぶ。"""
        s = sched(kind="interval", every_minutes=45, window={"start": "09:00", "end": "12:00"},
                  lead_times=[5])
        now = datetime(2026, 9, 21, 0, 0)
        tl = scheduler.timeline([s], MON, settings=SETTINGS, now=now)
        nx = scheduler.next_events([s], now=now, limit=3)
        self.assertEqual([e["at"] for e in tl[:3]], [e["at"] for e in nx])
        mains = [e for e in tl if e["tag"] == "main"]
        self.assertEqual([datetime.fromisoformat(e["at"]).strftime("%H:%M") for e in mains],
                         ["09:00", "09:45", "10:30", "11:15", "12:00"])  # 終了時刻も含む

    def test_weekday_filter(self):
        s = sched(days=[1])  # 火曜だけ
        self.assertEqual(scheduler.timeline([s], MON, settings=SETTINGS, now=datetime(2026, 9, 20)), [])
        got = scheduler.timeline([s], MON + timedelta(days=1), settings=SETTINGS,
                                 now=datetime(2026, 9, 20))
        self.assertEqual(hm(got), [("08:30", "main", 0)])

    def test_lead_for_next_days_main_shows_on_this_day(self):
        s = sched(time="00:10", days=[1], lead_times=[30])  # 火曜 0:10 の予告 = 月曜 23:40
        got = scheduler.timeline([s], MON, settings=SETTINGS, now=datetime(2026, 9, 20))
        self.assertEqual(hm(got), [("23:40", "lead", 30)])
        self.assertEqual(got[0]["main_at"], "2026-09-22T00:10:00")
        # 火曜のほうには本番だけ（前日の予告は出さない）
        got = scheduler.timeline([s], MON + timedelta(days=1), settings=SETTINGS,
                                 now=datetime(2026, 9, 20))
        self.assertEqual(hm(got), [("00:10", "main", 0)])

    def test_multiple_days(self):
        s = sched(days=[0, 2])
        got = scheduler.timeline([s], MON, settings=SETTINGS, now=datetime(2026, 9, 20), days=7)
        self.assertEqual([e["at"][:10] for e in got], ["2026-09-21", "2026-09-23"])

    def test_states_disabled_master_quiet(self):
        on = sched(name="オン", time="08:00")
        off = sched(name="オフ", time="09:00", enabled=False)
        night = sched(name="夜", time="23:30")
        now = datetime(2026, 9, 21, 0, 0)
        st = dict(SETTINGS, quiet_hours={"enabled": True, "start": "23:00", "end": "07:00"})
        got = {e["name"]: e for e in scheduler.timeline([on, off, night], MON, settings=st, now=now)}
        self.assertTrue(got["オン"]["will_ring"])
        self.assertFalse(got["オフ"]["enabled"])
        self.assertFalse(got["オフ"]["will_ring"])
        self.assertTrue(got["夜"]["quiet"])
        self.assertFalse(got["夜"]["will_ring"])

        st = dict(SETTINGS, master_enabled=False)
        got = scheduler.timeline([on], MON, settings=st, now=now)
        self.assertFalse(got[0]["master"])
        self.assertFalse(got[0]["will_ring"])

    def test_result_from_log(self):
        s = sched(name="朝", time="07:00", lead_times=[5])
        log = [
            {"ts": "2026-09-21T06:55:00", "level": "fired",
             "message": "朝（5分前の予告） を再生しました", "schedule_id": s["id"]},
            {"ts": "2026-09-21T07:00:01", "level": "skipped",
             "message": "朝: 静音時間帯のためスキップしました", "schedule_id": s["id"]},
            # 別の日のログは拾わない
            {"ts": "2026-09-20T07:00:00", "level": "fired",
             "message": "朝 を再生しました", "schedule_id": s["id"]},
        ]
        got = scheduler.timeline([s], MON, settings=SETTINGS, now=datetime(2026, 9, 21, 8, 0),
                                 log_entries=log)
        self.assertEqual([e["result"] for e in got], ["fired", "skipped"])
        got = scheduler.timeline([s], MON + timedelta(days=1), settings=SETTINGS,
                                 now=datetime(2026, 9, 22, 8, 0), log_entries=log)
        self.assertEqual([e["result"] for e in got], [None, None])

    def test_missed_matched_by_message_stamp(self):
        s = sched(name="朝", time="07:00")
        log = [{"ts": "2026-09-21T09:30:00", "level": "missed",
                "message": "朝: 09/21 07:00 の予定を過ぎていたため鳴らしませんでした（150分遅れ）",
                "schedule_id": s["id"]}]
        got = scheduler.timeline([s], MON, settings=SETTINGS, now=datetime(2026, 9, 21, 10, 0),
                                 log_entries=log)
        self.assertEqual(got[0]["result"], "missed")


class TestTimelineAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        home = Path(cls.tmp.name)
        app = server.App.__new__(server.App)  # 内蔵サウンドの生成を省く
        app.home = home
        app.web_dir = Path(server.__file__).resolve().parent.parent / "web"
        app.log = server.EventLog(home / "data" / "events.log")
        app.store = config.Store(home / "data" / "config.json")
        app.token = None
        app.started_at = datetime.now()
        cls.app = app
        handler = type("H", (server.Handler,), {"app": app})
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def get(self, path):
        with urlopen(self.base + path, timeout=5) as r:
            return json.loads(r.read())

    def test_returns_events_for_date(self):
        self.app.store.add({"name": "水曜の予定", "kind": "daily", "time": "10:00", "days": [2],
                            "lead_times": [15],
                            "action": {"type": "sound", "sound": "builtin:ding"}})
        data = self.get("/api/timeline?date=2026-09-23")
        self.assertEqual(data["date"], "2026-09-23")
        mine = [e for e in data["events"] if e["name"] == "水曜の予定"]
        self.assertEqual([(e["at"], e["tag"]) for e in mine],
                         [("2026-09-23T09:45:00", "lead"), ("2026-09-23T10:00:00", "main")])
        for key in ("schedule_id", "lead", "main_at", "enabled", "quiet", "master", "past", "result"):
            self.assertIn(key, mine[0])
        week = self.get("/api/timeline?date=2026-09-21&days=7")
        self.assertEqual(week["days"], 7)
        self.assertEqual(len([e for e in week["events"] if e["name"] == "水曜の予定"]), 2)

    def test_rejects_bad_date(self):
        for q in ("date=2026-13-01", "date=tomorrow", "date=2026-09-21&days=99"):
            with self.assertRaises(HTTPError) as cm:
                self.get("/api/timeline?" + q)
            self.assertEqual(cm.exception.code, 400)
            cm.exception.close()


if __name__ == "__main__":
    unittest.main()
