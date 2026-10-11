"""AI 連携（朝のお知らせ）のテスト。AI サーバはダミーの OpenAI 互換 API を立てる。"""

import json
import sys
import tempfile
import threading
import time
import unittest
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sounder import ai, calendarfeed, config  # noqa: E402


class FakeLLM(BaseHTTPRequestHandler):
    prompts: list = []
    answer = "おはようございます。今日は14時からレッスンです。"
    fail = False

    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._send(200, {"data": [{"id": "gemma-test"}]})

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeLLM.prompts.append(req)
        if FakeLLM.fail:
            self._send(500, {"error": "boom"})
        else:
            self._send(200, {"choices": [{"message": {"content": FakeLLM.answer}}]})


TODAY = date.today()


def iso(day, hhmm):
    return datetime.combine(day, datetime.strptime(hhmm, "%H:%M").time()).astimezone().isoformat()


class Base(unittest.TestCase):
    def setUp(self):
        FakeLLM.prompts = []
        FakeLLM.fail = False
        srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeLLM)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        path = Path(self.tmp.name) / "calendar.json"
        path.write_text(json.dumps({"status": "authorized", "calendars": [{"id": "fam", "title": "Family"}],
            "events": [
                {"id": "a", "calendar_id": "fam", "title": "Tandem lesson", "all_day": False,
                 "start": iso(TODAY, "14:00"), "end": iso(TODAY, "15:00"), "location": "Studio"},
                {"id": "b", "calendar_id": "fam", "title": "誕生日", "all_day": True,
                 "start": iso(TODAY, "00:00"), "end": iso(TODAY + timedelta(days=1), "00:00")},
                {"id": "c", "calendar_id": "other", "title": "Work", "all_day": False,
                 "start": iso(TODAY, "09:00"), "end": iso(TODAY, "10:00")},
                {"id": "d", "calendar_id": "fam", "title": "明日の予定", "all_day": False,
                 "start": iso(TODAY + timedelta(days=1), "09:00"), "end": iso(TODAY + timedelta(days=1), "10:00")},
            ]}, ensure_ascii=False))
        self.logs = []
        self.ai = ai.AI(calendarfeed.CalendarFeed(path), log=lambda lv, m, **k: self.logs.append((lv, m)))
        self.settings = {
            "default_volume": 0.5,
            "calendar": {"calendars": ["fam"]},
            "ai": {"enabled": True, "url": f"http://127.0.0.1:{srv.server_address[1]}/v1", "model": "",
                   "briefing": {"enabled": True, "time": "07:30", "days": [0, 1, 2, 3, 4, 5, 6],
                                "sound": "builtin:melody_morning", "voice": ""}},
        }


class TestBriefing(Base):
    def test_prompt_has_todays_selected_events_only(self):
        self.ai.briefing_text(self.settings, TODAY, wait=True)
        req = FakeLLM.prompts[0]
        self.assertEqual(req["model"], "gemma-test")          # 空なら /models の最初
        prompt = req["messages"][0]["content"]
        self.assertIn("14:00〜15:00: Tandem lesson（場所: Studio）", prompt)
        self.assertIn("終日: 誕生日", prompt)
        self.assertNotIn("Work", prompt)                       # 選んでいないカレンダー
        self.assertNotIn("明日の予定", prompt)                  # 今日以外
        if not self.ai._day(self.settings, TODAY)["notes"]:
            self.assertNotIn("祝日・休み:", prompt)            # 休みが無い日は書かない

    def test_text_is_cached_until_the_events_change(self):
        a = self.ai.briefing_text(self.settings, TODAY, wait=True)
        b = self.ai.briefing_text(self.settings, TODAY, wait=True)
        self.assertEqual(a, FakeLLM.answer)
        self.assertEqual(a, b)
        self.assertEqual(len(FakeLLM.prompts), 1)

    def test_falls_back_to_a_fixed_sentence_when_ai_fails(self):
        FakeLLM.fail = True
        text = self.ai.briefing_text(self.settings, TODAY, wait=True)
        self.assertIn("14時から、Tandem lesson。", text)
        self.assertIn("今日は誕生日です。", text)
        self.assertEqual(self.logs[-1][0], "error")

    def test_fallback_without_events(self):
        info = {"date": date(2026, 10, 12), "events": [], "notes": ["サンクスギビング"]}
        self.assertEqual(ai.AI.fallback(info),
                         "おはようございます。10月12日、月曜日です。今日はサンクスギビングです。今日のカレンダーの予定はありません。")

    def test_not_waiting_returns_fallback_then_the_ai_text(self):
        first = self.ai.briefing_text(self.settings, TODAY)
        self.assertIn("おはようございます", first)
        end = time.time() + 5
        while time.time() < end and self.ai.briefing_text(self.settings, TODAY) != FakeLLM.answer:
            time.sleep(0.05)
        self.assertEqual(self.ai.briefing_text(self.settings, TODAY), FakeLLM.answer)

    def test_clock(self):
        self.assertEqual(ai._clock("14:00"), "14時")
        self.assertEqual(ai._clock("09:30"), "9時30分")


class TestSchedules(Base):
    def test_off_means_nothing(self):
        for patch in ({"enabled": False}, {"briefing": {"enabled": False}}):
            s = json.loads(json.dumps(self.settings))
            s["ai"].update(patch)
            self.assertEqual(self.ai.schedules(s), [])

    def test_weekly_schedule_with_sound_and_speech(self):
        (s,) = self.ai.schedules(self.settings)
        self.assertEqual((s["kind"], s["time"], s["source"], s["id"]), ("weekly", "07:30", "ai", "ai-briefing"))
        self.assertEqual(s["action"]["type"], "both")
        self.assertEqual(s["action"]["sound"], "builtin:melody_morning")
        self.assertIn("おはようございます", s["action"]["text"])

    def test_prepare_asks_the_ai_shortly_before(self):
        now = datetime.combine(TODAY, datetime.strptime("07:25", "%H:%M").time())
        self.ai.prepare(now, self.settings)
        end = time.time() + 5
        while time.time() < end and not FakeLLM.prompts:
            time.sleep(0.05)
        self.assertEqual(len(FakeLLM.prompts), 1)

    def test_prepare_does_nothing_far_ahead(self):
        now = datetime.combine(TODAY, datetime.strptime("05:00", "%H:%M").time())
        self.ai.prepare(now, self.settings)
        time.sleep(0.2)
        self.assertEqual(FakeLLM.prompts, [])

    def test_check(self):
        self.assertEqual(self.ai.check(self.settings)["model"], "gemma-test")
        bad = json.loads(json.dumps(self.settings))
        bad["ai"]["url"] = "http://127.0.0.1:9/v1"
        self.assertFalse(self.ai.check(bad)["ok"])


class TestDayOffTime(Base):
    def setUp(self):
        super().setUp()
        self.settings["ai"]["briefing"]["holiday_time"] = "11:00"

    def test_is_day_off(self):
        self.assertTrue(ai.is_day_off(date(2026, 10, 12)))    # サンクスギビング（月）
        self.assertTrue(ai.is_day_off(date(2026, 9, 26)))     # 土曜
        self.assertFalse(ai.is_day_off(date(2026, 11, 20)))   # PA デー（学校だけの休み）は含めない
        self.assertFalse(ai.is_day_off(date(2026, 9, 29)))

    def test_briefing_time(self):
        b = ai.settings_of(self.settings)["briefing"]
        self.assertEqual(ai.briefing_time(b, date(2026, 10, 12)), "11:00")
        self.assertEqual(ai.briefing_time(b, date(2026, 9, 29)), "07:30")
        b["holiday_time"] = ""
        self.assertEqual(ai.briefing_time(b, date(2026, 10, 12)), "07:30")

    def test_schedules_split_weekdays_weekends_and_weekday_holidays(self):
        out = {s["id"]: s for s in self.ai.schedules(self.settings)}
        self.assertEqual((out["ai-briefing"]["time"], out["ai-briefing"]["days"], out["ai-briefing"]["skip"]),
                         ("07:30", [0, 1, 2, 3, 4], ["on_holidays"]))
        self.assertEqual((out["ai-briefing-weekend"]["time"], out["ai-briefing-weekend"]["days"]),
                         ("11:00", [5, 6]))
        for sid, s in out.items():
            if sid.startswith("ai-briefing-2"):
                d = date.fromisoformat(s["date"])
                self.assertTrue(d.weekday() < 5 and ai.is_day_off(d))
                self.assertEqual((s["kind"], s["time"]), ("once", "11:00"))

    def test_same_time_means_one_schedule(self):
        self.settings["ai"]["briefing"]["holiday_time"] = "07:30"
        self.assertEqual([s["id"] for s in self.ai.schedules(self.settings)], ["ai-briefing"])

    def test_prepare_uses_the_day_off_time(self):
        sat = TODAY + timedelta(days=(5 - TODAY.weekday()) % 7)
        self.ai.prepare(datetime.combine(sat, datetime.strptime("07:25", "%H:%M").time()), self.settings)
        time.sleep(0.2)
        self.assertEqual(FakeLLM.prompts, [])                  # 土曜の 7:25 には作らない
        self.ai.prepare(datetime.combine(sat, datetime.strptime("10:55", "%H:%M").time()), self.settings)
        end = time.time() + 5
        while time.time() < end and not FakeLLM.prompts:
            time.sleep(0.05)
        self.assertEqual(len(FakeLLM.prompts), 1)


class TestSettings(unittest.TestCase):
    def test_validate_and_survive_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            config.Store(path).update_settings({"ai": {"enabled": True, "briefing": {"enabled": True, "time": "06:45"}}})
            a = config.Store(path).settings["ai"]
            self.assertTrue(a["enabled"])
            self.assertEqual(a["briefing"]["time"], "06:45")
            self.assertEqual(a["url"], "http://spark-1:8000/v1")
            self.assertEqual(a["briefing"]["holiday_time"], "")
            config.Store(path).update_settings({"ai": {"briefing": {"holiday_time": "11:00"}}})
            self.assertEqual(config.Store(path).settings["ai"]["briefing"]["holiday_time"], "11:00")
            config.Store(path).update_settings({"ai": {"briefing": {"holiday_time": ""}}})
            self.assertEqual(config.Store(path).settings["ai"]["briefing"]["holiday_time"], "")

    def test_rejects_bad_values(self):
        cur = dict(config.DEFAULT_SETTINGS)
        for bad in ({"url": "ftp://x"}, {"briefing": {"time": "7:3"}}, {"briefing": {"days": []}}):
            with self.assertRaises(config.ValidationError):
                config.validate_settings({"ai": bad}, cur)


if __name__ == "__main__":
    unittest.main()


class FakeClaude(BaseHTTPRequestHandler):
    """Claude API（Messages API）のダミー。"""
    requests: list = []
    reply: dict = {}
    status = 200

    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        FakeClaude.requests.append(("GET", self.path, {k.lower(): v for k, v in self.headers.items()}, None))
        if FakeClaude.status != 200:
            self._send(FakeClaude.status, {"type": "error", "error": {"type": "authentication_error",
                                                                      "message": "invalid x-api-key"}})
        else:
            self._send(200, {"id": "claude-opus-5", "type": "model"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeClaude.requests.append(("POST", self.path, {k.lower(): v for k, v in self.headers.items()}, body))
        self._send(FakeClaude.status, FakeClaude.reply)


class TestClaude(Base):
    def setUp(self):
        super().setUp()
        FakeClaude.requests = []
        FakeClaude.status = 200
        FakeClaude.reply = {"stop_reason": "end_turn", "content": [
            {"type": "thinking", "thinking": ""},
            {"type": "text", "text": "おはようございます。"}, {"type": "text", "text": "今日は晴れです。"}]}
        srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeClaude)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        old = ai.CLAUDE_URL
        ai.CLAUDE_URL = f"http://127.0.0.1:{srv.server_address[1]}/v1"
        self.addCleanup(setattr, ai, "CLAUDE_URL", old)
        self.settings["ai"].update(provider="anthropic", claude_api_key="sk-ant-test", claude_model="")

    def test_request_shape(self):
        self.assertEqual(self.ai.briefing_text(self.settings, TODAY, wait=True),
                         "おはようございます。今日は晴れです。")
        method, path, headers, body = FakeClaude.requests[-1]
        self.assertEqual((method, path), ("POST", "/v1/messages"))
        self.assertEqual(headers["x-api-key"], "sk-ant-test")
        self.assertEqual(headers["anthropic-version"], "2023-06-01")
        self.assertEqual(headers["anthropic-beta"], "server-side-fallback-2026-07-01")
        self.assertEqual(body["model"], "claude-opus-5")
        self.assertEqual(body["fallbacks"], "default")
        self.assertEqual(body["output_config"], {"effort": "low"})
        self.assertNotIn("temperature", body)
        self.assertIn("Tandem lesson", body["messages"][0]["content"])

    def test_refusal_falls_back_to_the_fixed_sentence(self):
        FakeClaude.reply = {"stop_reason": "refusal", "content": [], "stop_details": None}
        text = self.ai.briefing_text(self.settings, TODAY, wait=True)
        self.assertIn("14時から、Tandem lesson。", text)
        self.assertIn("断りました", self.logs[-1][1])

    def test_without_a_key(self):
        self.settings["ai"]["claude_api_key"] = ""
        self.assertIn("おはようございます", self.ai.briefing_text(self.settings, TODAY, wait=True))
        self.assertEqual(FakeClaude.requests, [])
        self.assertFalse(self.ai.check(self.settings)["ok"])

    def test_check_uses_the_models_endpoint_and_shows_api_errors(self):
        r = self.ai.check(self.settings)
        self.assertEqual((r["ok"], r["model"]), (True, "claude-opus-5"))
        self.assertEqual(FakeClaude.requests[-1][1], "/v1/models/claude-opus-5")
        FakeClaude.status = 401
        r = self.ai.check(self.settings)
        self.assertFalse(r["ok"])
        self.assertIn("HTTP 401 invalid x-api-key", r["error"])

    def test_keys_are_never_returned(self):
        pub = ai.public(ai.settings_of(self.settings))
        self.assertNotIn("claude_api_key", pub)
        self.assertNotIn("api_key", pub)
        self.assertTrue(pub["claude_api_key_set"])
        self.assertFalse(pub["api_key_set"])


class TestKeySettings(unittest.TestCase):
    def test_omitted_key_is_kept_and_empty_clears(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            config.Store(path).update_settings({"ai": {"provider": "anthropic", "claude_api_key": "sk-ant-1"}})
            config.Store(path).update_settings({"ai": {"enabled": True}})       # キーを送らない
            self.assertEqual(config.Store(path).settings["ai"]["claude_api_key"], "sk-ant-1")
            config.Store(path).update_settings({"ai": {"claude_api_key": ""}})
            self.assertEqual(config.Store(path).settings["ai"]["claude_api_key"], "")
            with self.assertRaises(config.ValidationError):
                config.validate_settings({"ai": {"provider": "gpt"}}, config.Store(path).settings)
