"""Qwen3-TTS への橋渡しのテスト。モデルの代わりにダミーの HTTP サーバを立てる。"""

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# 本物の LaunchAgent（Qwen3-TTS・AivisSpeech）を起こしたり止めたりしない
os.environ["SOUNDER_MANAGE_TTS"] = "0"

from sounder import config, neural, scheduler  # noqa: E402
from sounder import player as player_mod  # noqa: E402
from tests.test_player import Base as PlayerBase  # noqa: E402


class FakeTTS(BaseHTTPRequestHandler):
    requests: list = []
    fail = False

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        body = json.dumps({"speakers": ["ono_anna", "ryan", "vivian"]}).encode()
        self._send(200, body, "application/json")

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeTTS.requests.append(req)
        if FakeTTS.fail:
            self._send(500, b'{"error": "boom"}', "application/json")
        else:
            self._send(200, b"RIFF....WAVEfake", "audio/wav")


class ServerMixin:
    def start_fake(self):
        FakeTTS.requests = []
        FakeTTS.fail = False
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeTTS)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        return f"http://127.0.0.1:{self.srv.server_address[1]}"


class TestLanguage(unittest.TestCase):
    def test_detects_language_from_text(self):
        self.assertEqual(neural.language_of("あと10分です"), "japanese")
        self.assertEqual(neural.language_of("Your meeting starts soon."), "english")
        self.assertEqual(neural.language_of("会議"), "auto")

    def test_is_neural(self):
        self.assertTrue(neural.is_neural("qwen:ono_anna"))
        self.assertFalse(neural.is_neural("Kyoko"))
        self.assertFalse(neural.is_neural(""))


class TestClient(ServerMixin, unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.msgs = []
        self.tts = neural.NeuralTTS(Path(self.tmp.name) / "cache", url=self.start_fake(),
                                    log=lambda lv, m, **k: self.msgs.append((lv, m)))

    def test_voices_japanese_first_with_labels(self):
        v = self.tts.voices()
        self.assertEqual([x["name"] for x in v], ["qwen:ono_anna", "qwen:ryan", "qwen:vivian"])
        self.assertEqual(v[0]["locale"], "ja_JP")
        self.assertEqual(v[0]["label"], "Ono Anna（Qwen3-TTS）")

    def test_render_sends_speaker_and_language_then_caches(self):
        p1 = self.tts.render("こんにちは", "qwen:ono_anna")
        p2 = self.tts.render("こんにちは", "qwen:ono_anna")
        self.assertEqual(p1, p2)
        self.assertTrue(p1.is_file())
        self.assertEqual(FakeTTS.requests, [
            {"text": "こんにちは", "speaker": "ono_anna", "language": "japanese"}])

    def test_different_voice_is_a_different_cache_entry(self):
        self.tts.render("Hello", "qwen:ryan")
        self.tts.render("Hello", "qwen:ono_anna")
        self.assertEqual(len(FakeTTS.requests), 2)

    def test_server_error_returns_none_and_logs(self):
        FakeTTS.fail = True
        self.assertIsNone(self.tts.render("はい", "qwen:ono_anna"))
        self.assertIn("boom", self.msgs[-1][1])

    def test_cache_hit_counts_as_use(self):
        p = self.tts.render("こんにちは", "qwen:ono_anna")
        os.utime(p, (0, 0))
        self.tts.render("こんにちは", "qwen:ono_anna")
        self.assertGreater(p.stat().st_mtime, 1e9)   # 使ったので更新時刻が今になる
        self.assertEqual(len(FakeTTS.requests), 1)


class TestServerDown(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.msgs = []
        # 誰も待ち受けていないポート
        self.tts = neural.NeuralTTS(Path(self.tmp.name), url="http://127.0.0.1:9",
                                    log=lambda lv, m, **k: self.msgs.append((lv, m)))

    def test_no_voices(self):
        self.assertEqual(self.tts.voices(), [])

    def test_render_returns_none(self):
        self.assertIsNone(self.tts.render("はい", "qwen:ono_anna"))
        self.assertIn("動いていません", self.msgs[-1][1])


class TestPlayerWithNeural(ServerMixin, PlayerBase):
    settings = {"default_volume": 0.6, "default_voice": "qwen:ono_anna", "speak_rate": 180}

    def setUp(self):
        super().setUp()
        self.p.tts = [neural.NeuralTTS(self.home / "cache", url=self.start_fake(),
                                       log=lambda lv, m, **k: self.msgs.append((lv, m)))]

    def test_neural_voice_skips_say(self):
        self.p.play_blocking({"type": "speak", "text": "はい"}, settings=self.settings)
        calls = self.recorded()
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].startswith("afplay"))
        self.assertIn(str(self.home / "cache"), calls[0])
        self.assertEqual(FakeTTS.requests[0]["speaker"], "ono_anna")

    def test_cached_speech_is_not_deleted_after_playing(self):
        self.p.play_blocking({"type": "speak", "text": "はい"}, settings=self.settings)
        self.assertEqual(len(list((self.home / "cache").glob("*.wav"))), 1)

    def test_falls_back_to_system_say_when_server_fails(self):
        FakeTTS.fail = True
        self.p.play_blocking({"type": "speak", "text": "はい"}, settings=self.settings)
        calls = self.recorded()
        self.assertEqual(len(calls), 2)
        self.assertTrue(calls[0].startswith("say "))
        self.assertNotIn("-v ", calls[0])  # "qwen:..." を say に渡さない

    def test_voices_lists_neural_first(self):
        names = [v["name"] for v in self.p.voices()]
        self.assertEqual(names[0], "qwen:ono_anna")
        self.assertIn("Kyoko", names)
        self.assertIn("qwen:ryan", names)
        self.assertNotIn("qwen:vivian", names)  # 中国語の声は出さない

    def test_prefetch_renders_in_background(self):
        self.p.prefetch({"type": "speak", "text": "もうすぐです"}, settings=self.settings)
        end = time.time() + 5
        while time.time() < end and not FakeTTS.requests:
            time.sleep(0.02)
        self.assertEqual(FakeTTS.requests[0]["text"], "もうすぐです")

    def test_prefetch_ignores_sounds_and_say_voices(self):
        self.p.prefetch({"type": "sound", "sound": "builtin:ding"}, settings=self.settings)
        self.p.prefetch({"type": "speak", "text": "はい", "voice": "Kyoko"}, settings=self.settings)
        time.sleep(0.2)
        self.assertEqual(FakeTTS.requests, [])


class FakeAivis(BaseHTTPRequestHandler):
    """AivisSpeech Engine（VOICEVOX 互換）のダミー。"""
    requests: list = []

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        body = json.dumps([{"name": "まお", "styles": [{"name": "ノーマル", "id": 888753760},
                                                     {"name": "あまあま", "id": 888753762}]}])
        self._send(200, body.encode(), "application/json")

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        FakeAivis.requests.append((self.path, json.loads(body) if body else None))
        if self.path.startswith("/audio_query"):
            self._send(200, b'{"speedScale": 1.0, "accent_phrases": []}', "application/json")
        else:
            self._send(200, b"RIFF....WAVEaivis", "audio/wav")


class TestAivis(unittest.TestCase):
    def setUp(self):
        FakeAivis.requests = []
        srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeAivis)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tts = neural.AivisTTS(Path(self.tmp.name),
                                   url=f"http://127.0.0.1:{srv.server_address[1]}")

    def test_each_style_is_a_japanese_voice(self):
        self.assertEqual(self.tts.voices(), [
            {"name": "aivis:888753760", "label": "まお・ノーマル（AivisSpeech）", "locale": "ja_JP",
             "asleep": False},
            {"name": "aivis:888753762", "label": "まお・あまあま（AivisSpeech）", "locale": "ja_JP",
             "asleep": False},
        ])

    def test_render_uses_query_then_synthesis_with_speed(self):
        path = self.tts.render("こんにちは", "aivis:888753760", 270)
        self.assertEqual(path.read_bytes(), b"RIFF....WAVEaivis")
        (q_path, _), (s_path, s_body) = FakeAivis.requests
        self.assertTrue(q_path.startswith("/audio_query?"))
        self.assertIn("speaker=888753760", q_path)
        self.assertEqual(s_path, "/synthesis?speaker=888753760")
        self.assertEqual(s_body["speedScale"], 1.5)   # 270 / 180

    def test_speed_is_clamped_and_part_of_the_cache_key(self):
        self.tts.render("はい", "aivis:888753760", 90)
        self.tts.render("はい", "aivis:888753760", 90)    # キャッシュから
        self.tts.render("はい", "aivis:888753760", 400)   # 速さが違えば作り直す
        speeds = [b["speedScale"] for p, b in FakeAivis.requests if p.startswith("/synthesis")]
        self.assertEqual(speeds, [0.5, 2.0])

    def test_is_neural(self):
        self.assertTrue(neural.is_neural("aivis:888753760"))
        self.assertTrue(self.tts.handles("aivis:1"))
        self.assertFalse(self.tts.handles("qwen:ono_anna"))

    def test_server_down(self):
        down = neural.AivisTTS(Path(self.tmp.name), url="http://127.0.0.1:9")
        self.assertEqual(down.voices(), [])
        self.assertIsNone(down.render("はい", "aivis:1", 180))


class TestPlayerWithAivis(PlayerBase):
    settings = {"default_volume": 0.6, "default_voice": "aivis:888753760", "speak_rate": 180}

    def setUp(self):
        super().setUp()
        FakeAivis.requests = []
        srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeAivis)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        self.p.tts = [neural.NeuralTTS(self.home / "cache", url="http://127.0.0.1:9"),
                      neural.AivisTTS(self.home / "cache",
                                      url=f"http://127.0.0.1:{srv.server_address[1]}")]

    def test_routes_to_aivis_with_the_schedule_rate(self):
        self.p.play_blocking({"type": "speak", "text": "はい", "rate": 360}, settings=self.settings)
        calls = self.recorded()
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].startswith("afplay"))
        self.assertEqual(FakeAivis.requests[-1][1]["speedScale"], 2.0)

    def test_voices_list_both_engines(self):
        names = [v["name"] for v in self.p.voices()]
        self.assertIn("aivis:888753762", names)
        self.assertIn("Kyoko", names)


FAKE_LAUNCHCTL = """#!/bin/sh
printf '%s\\n' "$*" >> "{log}"
exit {code}
"""


class TestEngineLifecycle(unittest.TestCase):
    """休ませる／起こす。launchctl はダミーのスクリプトに差し替える。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.calls = self.home / "launchctl.log"
        self.msgs = []
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeAivis)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.tts = neural.AivisTTS(self.home / "cache",
                                   url=f"http://127.0.0.1:{self.srv.server_address[1]}",
                                   log=lambda lv, m, **k: self.msgs.append(m))
        self.tts.launchctl = self.fake(0)

    def fake(self, code):
        path = self.home / f"launchctl{code}"
        path.write_text(FAKE_LAUNCHCTL.format(log=self.calls, code=code))
        path.chmod(0o755)
        return str(path)

    def recorded(self):
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def stop_server(self):
        self.srv.shutdown()
        self.srv.server_close()

    def test_sleeping_engine_keeps_its_voices_marked_asleep(self):
        self.assertFalse(self.tts.voices()[0]["asleep"])
        self.stop_server()
        v = self.tts.voices()
        self.assertEqual(v[0]["name"], "aivis:888753760")
        self.assertTrue(v[0]["asleep"])

    def test_voices_survive_a_sounder_restart(self):
        self.tts.voices()
        self.stop_server()
        again = neural.AivisTTS(self.home / "cache", url="http://127.0.0.1:9")
        again.launchctl = self.fake(0)
        self.assertEqual([x["name"] for x in again.voices()],
                         ["aivis:888753760", "aivis:888753762"])

    def test_unregistered_engine_shows_no_voices(self):
        self.tts.voices()
        self.stop_server()
        self.tts.launchctl = self.fake(1)   # launchctl print が失敗 = 登録なし
        self.assertEqual(self.tts.voices(), [])

    def test_ensure_up_kickstarts_and_waits(self):
        answers = iter([False, False, False, True])
        self.tts.is_up = lambda: next(answers)
        self.assertTrue(self.tts.ensure_up())
        self.assertIn(f"kickstart gui/{os.getuid()}/com.local.sounder-aivis", self.recorded())
        self.assertIn("AivisSpeech を起こしています", self.msgs)

    def test_ensure_up_gives_up_when_not_registered(self):
        self.tts.is_up = lambda: False
        self.tts.launchctl = self.fake(1)
        self.assertFalse(self.tts.ensure_up())
        self.assertFalse(any("kickstart" in c for c in self.recorded()))

    def test_sleeps_only_after_the_idle_time(self):
        self.assertFalse(self.tts.sleep_if_idle(600))          # 使ったばかり
        self.tts.last_used -= 601
        self.assertFalse(self.tts.sleep_if_idle(0))            # 0 = 休ませない
        self.assertTrue(self.tts.sleep_if_idle(600))
        self.assertIn(f"kill SIGTERM gui/{os.getuid()}/com.local.sounder-aivis", self.recorded())
        # 止める前に声の一覧を覚えている
        self.assertTrue((self.home / "cache" / "voices-aivis.json").is_file())

    def test_rendering_counts_as_use(self):
        self.tts.last_used -= 3600
        self.tts.render("はい", "aivis:888753760", 180)
        self.assertFalse(self.tts.sleep_if_idle(600))

    def test_no_launchctl_means_no_management(self):
        self.tts.launchctl = None
        self.tts.last_used -= 3600
        self.assertFalse(self.tts.sleep_if_idle(60))
        self.assertFalse(self.tts.registered())


class RecordingPlayer:
    def __init__(self):
        self.prefetched = []
        self.idle_checks = []

    def play(self, action, *, settings, label="", queue=False):
        pass

    def prefetch(self, action, *, settings):
        self.prefetched.append(action)

    def sleep_idle_engines(self, idle_seconds):
        self.idle_checks.append(idle_seconds)


class TestSchedulerPrefetch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = config.Store(Path(self.tmp.name) / "config.json")
        self.player = RecordingPlayer()
        self.sched = scheduler.Scheduler(self.store, self.player, lambda *a, **k: None)
        self.store.add({"name": "出発", "kind": "daily", "time": "08:30",
                        "days": [0, 1, 2, 3, 4, 5, 6],
                        "action": {"type": "speak", "text": "出発の時間です"}})

    def test_prefetches_speech_a_few_minutes_ahead_once(self):
        t = datetime(2026, 9, 21, 8, 28, 0)
        self.sched._last_tick = t
        self.sched.tick(t)
        self.sched.tick(t + timedelta(seconds=31))
        self.assertEqual([a["text"] for a in self.player.prefetched], ["出発の時間です"])

    def test_idle_check_uses_the_setting_once_a_minute(self):
        self.store.update_settings({"tts_idle_minutes": 10})
        t = datetime(2026, 9, 21, 7, 0, 0)
        self.sched._last_tick = t
        self.sched.tick(t)
        self.sched.tick(t + timedelta(seconds=30))
        self.sched.tick(t + timedelta(seconds=61))
        self.assertEqual(self.player.idle_checks, [600.0, 600.0])

    def test_nothing_to_prefetch_far_ahead(self):
        t = datetime(2026, 9, 21, 7, 0, 0)
        self.sched._last_tick = t
        self.sched.tick(t)
        self.assertEqual(self.player.prefetched, [])


if __name__ == "__main__":
    unittest.main()
