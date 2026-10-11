"""Claude API を使う機能（言葉で予定・臨時休校・お知らせ）のテスト。Claude API はダミーを立てる。"""

import json
import os
import sys
import tempfile
import threading
import unittest
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["SOUNDER_MANAGE_TTS"] = "0"

from sounder import ai, claudetasks, config, daysoff, scheduler  # noqa: E402


class FakeClaude(BaseHTTPRequestHandler):
    bodies: list = []
    replies: list = []      # 順番に返す

    def log_message(self, *a):
        pass

    def do_POST(self):
        FakeClaude.bodies.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        reply = FakeClaude.replies.pop(0) if FakeClaude.replies else {"stop_reason": "end_turn", "content": []}
        body = json.dumps(reply, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def text_reply(obj):
    return {"stop_reason": "end_turn", "content": [{"type": "text", "text": json.dumps(obj, ensure_ascii=False)}]}


SETTINGS = {"default_volume": 0.5,
            "ai": {"enabled": True, "provider": "anthropic", "claude_api_key": "sk-ant-test"}}


class Base(unittest.TestCase):
    def setUp(self):
        FakeClaude.bodies, FakeClaude.replies = [], []
        srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeClaude)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        old = ai.CLAUDE_URL
        ai.CLAUDE_URL = f"http://127.0.0.1:{srv.server_address[1]}/v1"
        self.addCleanup(setattr, ai, "CLAUDE_URL", old)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.logs = []
        self.closures = claudetasks.Closures(Path(self.tmp.name) / "closures.json")
        self.ai = ai.AI(None, log=lambda lv, m, **k: self.logs.append((lv, m)), closures=self.closures)
        self.addCleanup(daysoff.EXTRA_CLOSURES.clear)


SCHEDULE = {"name": "ゴミ出し", "kind": "weekly", "time": "07:30", "days": [1, 4], "date": "", "day_of_month": "",
            "month": 0, "every_minutes": 0, "window_start": "", "window_end": "", "lead_times": [15],
            "speak_text": "ゴミ出しの時間です。", "sound": "builtin:doorbell", "lead_speak_remaining": True,
            "skip": ["on_holidays"], "outing": False, "note": "", "understood": True}


class TestParseSchedule(Base):
    def test_words_become_a_valid_draft(self):
        FakeClaude.replies = [text_reply(SCHEDULE)]
        raw = claudetasks.parse_schedule(self.ai, SETTINGS, "毎週火金の7時半にゴミ出し", today=date(2026, 10, 11))
        s = config.validate_schedule(raw)
        self.assertEqual((s["kind"], s["time"], s["days"], s["lead_times"], s["skip"]),
                         ("weekly", "07:30", [1, 4], [15], ["on_holidays"]))
        self.assertEqual(s["action"]["type"], "both")
        self.assertTrue(s["lead_action"]["speak_remaining"])
        body = FakeClaude.bodies[0]
        self.assertEqual(body["output_config"]["format"]["type"], "json_schema")
        self.assertIn("2026-10-11（日曜日）", body["messages"][0]["content"])

    def test_not_understood(self):
        FakeClaude.replies = [text_reply({**SCHEDULE, "understood": False})]
        with self.assertRaises(claudetasks.ClaudeUnavailable):
            claudetasks.parse_schedule(self.ai, SETTINGS, "あいうえお")

    def test_needs_claude(self):
        with self.assertRaises(claudetasks.ClaudeUnavailable):
            claudetasks.parse_schedule(self.ai, {"ai": {"enabled": True, "provider": "openai"}}, "x")
        with self.assertRaises(claudetasks.ClaudeUnavailable):
            claudetasks.parse_schedule(self.ai, {"ai": {"enabled": True, "provider": "anthropic"}}, "x")

    def test_refusal(self):
        FakeClaude.replies = [{"stop_reason": "refusal", "content": []}]
        with self.assertRaises(claudetasks.ClaudeUnavailable):
            claudetasks.parse_schedule(self.ai, SETTINGS, "x")


def report(closed, confidence="high", reason="大雪のため"):
    return {"stop_reason": "tool_use", "content": [
        {"type": "server_tool_use", "id": "s1", "name": "web_search", "input": {"query": "TDSB closed"}},
        {"type": "tool_use", "id": "t1", "name": "report_school_status",
         "input": {"closed": closed, "reason": reason, "source_url": "https://www.tdsb.on.ca/", "confidence": confidence}}]}


class TestClosure(Base):
    DAY = date(2027, 1, 27)   # 水曜（授業日）

    def test_closed_day_becomes_a_school_day_off(self):
        FakeClaude.replies = [report(True)]
        r = self.ai.check_closure(SETTINGS, self.DAY)
        self.assertTrue(r["closed"])
        body = FakeClaude.bodies[0]
        self.assertEqual(body["tools"][0]["type"], "web_search_20260209")
        self.assertIn("tdsb.on.ca", body["tools"][0]["allowed_domains"])
        self.assertTrue(body["tools"][1]["strict"])
        self.assertEqual(daysoff.reason("tdsb_elementary", self.DAY), "臨時休校（大雪のため）")
        # 学校の休みに鳴らさない予定は、この日は鳴らない
        s = {"kind": "weekly", "time": "08:35", "days": [2], "skip": ["tdsb_elementary"], "lead_times": []}
        self.assertEqual(scheduler.day_occurrences(s, self.DAY), [])
        # 朝のお知らせにも出る
        self.assertIn("TDSB の学校は臨時休校（大雪のため）", self.ai._day({}, self.DAY)["notes"])

    def test_unsure_is_not_treated_as_closed(self):
        FakeClaude.replies = [report(True, confidence="low")]
        self.ai.check_closure(SETTINGS, self.DAY)
        self.assertIsNone(daysoff.reason("tdsb_elementary", self.DAY))

    def test_pause_turn_is_resumed(self):
        FakeClaude.replies = [{"stop_reason": "pause_turn", "content": [
            {"type": "server_tool_use", "id": "s1", "name": "web_search", "input": {"query": "q"}}]}, report(False)]
        r = self.ai.check_closure(SETTINGS, self.DAY)
        self.assertFalse(r["closed"])
        self.assertEqual(len(FakeClaude.bodies), 2)
        self.assertEqual(FakeClaude.bodies[1]["messages"][-1]["role"], "assistant")

    def test_failure_is_recorded_not_raised(self):
        r = self.ai.check_closure({"ai": {"enabled": True, "provider": "openai"}}, self.DAY)
        self.assertIn("Claude API", r["error"])
        self.assertEqual(self.logs[-1][0], "error")

    def test_runs_once_in_the_window_on_winter_school_days(self):
        settings = {**SETTINGS, "ai": {**SETTINGS["ai"], "closure": {"enabled": True, "time": "06:30"}}}
        from datetime import datetime
        import time
        FakeClaude.replies = [report(False)]
        self.ai.prepare(datetime(2027, 1, 27, 6, 0), settings)       # まだ早い
        self.ai.prepare(datetime(2027, 1, 30, 6, 35), settings)      # 土曜
        self.ai.prepare(datetime(2027, 1, 27, 6, 35), settings)      # ここで確かめる
        end = time.time() + 5
        while time.time() < end and not self.closures.get(self.DAY):
            time.sleep(0.05)
        self.ai.prepare(datetime(2027, 1, 27, 6, 40), settings)      # 2 回目は確かめない
        time.sleep(0.2)
        self.assertEqual(len(FakeClaude.bodies), 1)

    def test_closures_file_survives(self):
        self.closures.put(self.DAY, {"closed": True, "reason": "大雪", "confidence": "high"})
        again = claudetasks.Closures(self.closures.path)
        self.assertEqual(again.closed_days(), {self.DAY: "大雪"})


class TestNotice(Base):
    def test_reads_events_from_a_pdf(self):
        today = date(2026, 10, 11)
        FakeClaude.replies = [text_reply({"events": [
            {"title": "写真撮影", "date": "2026-10-20", "time": "", "kind": "event", "note": "白いシャツ"},
            {"title": "PA デー", "date": "2026-11-20", "time": "", "kind": "no_school", "note": ""},
            {"title": "古い行事", "date": "2026-09-01", "time": "", "kind": "event", "note": ""},
            {"title": "壊れた日付", "date": "10/20", "time": "", "kind": "event", "note": ""}]})]
        events = claudetasks.read_notice(self.ai, SETTINGS, "news.pdf", "JVBERi0=", today=today)
        self.assertEqual([e["title"] for e in events], ["写真撮影", "PA デー"])
        block = FakeClaude.bodies[0]["messages"][0]["content"][0]
        self.assertEqual((block["type"], block["source"]["media_type"]), ("document", "application/pdf"))

    def test_images_and_bad_files(self):
        FakeClaude.replies = [text_reply({"events": []})]
        claudetasks.read_notice(self.ai, SETTINGS, "photo.JPG", "AAAA")
        self.assertEqual(FakeClaude.bodies[0]["messages"][0]["content"][0]["type"], "image")
        with self.assertRaises(claudetasks.ClaudeUnavailable):
            claudetasks.read_notice(self.ai, SETTINGS, "notes.docx", "AAAA")


if __name__ == "__main__":
    unittest.main()
