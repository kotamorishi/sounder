"""Qwen3-TTS への橋渡しのテスト。モデルの代わりにダミーの HTTP サーバを立てる。"""

import json
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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

    def test_cache_is_pruned(self):
        old = neural.CACHE_LIMIT
        neural.CACHE_LIMIT = 2
        self.addCleanup(setattr, neural, "CACHE_LIMIT", old)
        for i in range(4):
            self.tts.render(f"文 {i}", "qwen:ono_anna")
        self.assertEqual(len(list(self.tts.cache_dir.glob("*.wav"))), 2)


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
        self.assertIn("接続できません", self.msgs[-1][1])


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
            {"name": "aivis:888753760", "label": "まお・ノーマル（AivisSpeech）", "locale": "ja_JP"},
            {"name": "aivis:888753762", "label": "まお・あまあま（AivisSpeech）", "locale": "ja_JP"},
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


class RecordingPlayer:
    def __init__(self):
        self.prefetched = []

    def play(self, action, *, settings, label="", queue=False):
        pass

    def prefetch(self, action, *, settings):
        self.prefetched.append(action)


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

    def test_nothing_to_prefetch_far_ahead(self):
        t = datetime(2026, 9, 21, 7, 0, 0)
        self.sched._last_tick = t
        self.sched.tick(t)
        self.assertEqual(self.player.prefetched, [])


if __name__ == "__main__":
    unittest.main()
