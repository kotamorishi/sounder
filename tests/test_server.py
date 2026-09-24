"""HTTP サーバのテスト。実際にポートを開いて叩く。音は出さない。"""

import base64
import json
import os
import shutil
import signal
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import date, timedelta
from http.client import HTTPConnection
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sounder import server as server_mod  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FAKE_AFPLAY = "#!/bin/sh\nprintf 'afplay %s\\n' \"$*\" >> \"{log}\"\nexit 0\n"
FAKE_SAY = (
    "#!/bin/sh\n"
    "printf 'say %s\\n' \"$*\" >> \"{log}\"\n"
    "prev=\"\"\n"
    "for a in \"$@\"; do\n"
    "  if [ \"$prev\" = \"-o\" ]; then printf 'dummy' > \"$a\"; fi\n"
    "  prev=\"$a\"\n"
    "done\n"
    "if [ \"$1\" = \"-v\" ] && [ \"$2\" = \"?\" ]; then\n"
    "  printf 'Kyoko (Japanese (Japan)) ja_JP    # hi\\n'\n"
    "  printf 'Alex                en_US    # hello\\n'\n"
    "fi\n"
    "exit 0\n"
)


def make_app(home: Path, token=None, *, voice="Kyoko (Japanese (Japan))"):
    """内蔵サウンドは本物を symlink して使い回す（毎回合成すると遅いため）。"""
    (home / "sounds").mkdir(parents=True, exist_ok=True)
    (home / "data").mkdir(parents=True, exist_ok=True)
    os.symlink(ROOT / "sounds" / "builtin", home / "sounds" / "builtin")
    (home / "data" / "config.json").write_text(json.dumps({
        "version": 1, "settings": {"default_voice": voice}, "schedules": [],
    }), "utf-8")
    app = server_mod.App(home, token)
    calls = home / "calls.log"
    for name, body in (("afplay", FAKE_AFPLAY), ("say", FAKE_SAY)):
        path = home / name
        path.write_text(body.format(log=calls))
        path.chmod(0o755)
        setattr(app.player, name, str(path))
    return app, calls


class ServerCase(unittest.TestCase):
    """1 ケースごとにサーバを立てる（状態が混ざらないように）。"""

    token = None

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.app, self.calls = make_app(self.home, self.token)
        self.httpd = server_mod.make_server(self.app, "127.0.0.1", 0)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.port}"

    def tearDown(self):
        self.httpd.shutdown()
        self.thread.join(timeout=3)
        self.httpd.server_close()
        self.app.player.stop()
        self.tmp.cleanup()

    # --- HTTP の道具 ------------------------------------------------------

    def req(self, method, path, body=None, headers=None, raw=None):
        url = self.base + path
        data = raw if raw is not None else (
            json.dumps(body).encode() if body is not None else None)
        h = {"Content-Type": "application/json"}
        h.update(headers or {})
        if self.token:
            h.setdefault("X-Sounder-Token", self.token)
        r = urllib.request.Request(url, data=data, headers=h, method=method)
        try:
            with urllib.request.urlopen(r, timeout=10) as res:
                raw_body = res.read()
                return res.status, (json.loads(raw_body) if raw_body and
                                    "json" in res.headers.get("Content-Type", "") else raw_body), res
        except urllib.error.HTTPError as e:
            raw_body = e.read()
            try:
                return e.code, json.loads(raw_body), e
            except (json.JSONDecodeError, UnicodeDecodeError):
                return e.code, raw_body, e

    def ok(self, method, path, body=None, **kw):
        code, data, _res = self.req(method, path, body, **kw)
        self.assertIn(code, (200, 201), data)
        return data

    def err(self, method, path, body=None, expect=400, **kw):
        code, data, _res = self.req(method, path, body, **kw)
        self.assertEqual(code, expect, data)
        return data

    def wait_calls(self, n, timeout=5.0):
        end = time.time() + timeout
        while time.time() < end:
            if self.calls.exists() and len(self.calls.read_text().splitlines()) >= n:
                break
            time.sleep(0.02)
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def sample(self, **kw):
        base = {
            "name": "お出かけ", "kind": "weekly", "time": "08:15", "days": [0, 1, 2, 3, 4],
            "lead_times": [10], "action": {"type": "sound", "sound": "builtin:doorbell"},
            "lead_action": {"sound": "builtin:ding"},
        }
        base.update(kw)
        return base


class TestStatic(ServerCase):
    def test_serves_the_app(self):
        code, body, res = self.req("GET", "/")
        self.assertEqual(code, 200)
        self.assertIn(b"sounder", body)
        self.assertTrue(res.headers["Content-Type"].startswith("text/html"))
        self.assertEqual(res.headers["Cache-Control"], "no-store")
        self.assertEqual(res.headers["X-Content-Type-Options"], "nosniff")

    def test_serves_css_and_js(self):
        for path, ctype in (("/style.css", "text/css"), ("/app.js", "javascript"),
                            ("/lib.js", "javascript")):
            code, _b, res = self.req("GET", path)
            self.assertEqual(code, 200, path)
            self.assertIn(ctype, res.headers["Content-Type"])

    def test_head_request(self):
        code, body, _res = self.req("HEAD", "/")
        self.assertEqual(code, 200)
        self.assertEqual(body, b"")

    def test_missing_page(self):
        self.err("GET", "/nope.html", expect=404)

    def test_directory_traversal_is_blocked(self):
        self.err("GET", "/%2e%2e/sounder/server.py", expect=403)

    def test_post_to_static_path_is_not_found(self):
        self.err("POST", "/index.html", {}, expect=404)


class TestState(ServerCase):
    def test_state_has_everything_the_ui_needs(self):
        d = self.ok("GET", "/api/state")
        for key in ("now", "settings", "schedules", "sounds", "voices",
                    "next_events", "log", "playing", "started_at", "builtin_labels"):
            self.assertIn(key, d)
        self.assertEqual(d["schedules"], [])
        self.assertTrue(d["sounds"]["builtin"])
        self.assertEqual([v["name"] for v in d["voices"]][0], "Kyoko (Japanese (Japan))")

    def test_now_is_small_and_live(self):
        d = self.ok("GET", "/api/now")
        self.assertEqual(sorted(d), ["log", "next_events", "now", "playing"])

    def test_log_endpoint_respects_limit(self):
        for i in range(5):
            self.app.log("info", f"m{i}")
        self.assertEqual(len(self.ok("GET", "/api/log?limit=2")["log"]), 2)
        self.assertGreaterEqual(len(self.ok("GET", "/api/log")["log"]), 5)

    def test_unknown_api(self):
        self.err("GET", "/api/nope", expect=404)
        self.err("POST", "/api/nope", {}, expect=404)


class TestSchedules(ServerCase):
    def test_create_read_update_delete(self):
        created = self.ok("POST", "/api/schedules", self.sample())["schedule"]
        sid = created["id"]
        self.assertEqual(created["summary"], "平日 08:15（10分前に予告）")
        self.assertTrue(created["next_at"])

        listed = self.ok("GET", "/api/schedules")["schedules"]
        self.assertEqual([s["id"] for s in listed], [sid])
        self.assertEqual(self.ok("GET", f"/api/schedules/{sid}")["schedule"]["name"], "お出かけ")

        updated = self.ok("PUT", f"/api/schedules/{sid}",
                          self.sample(name="名前を変えた", time="09:00"))["schedule"]
        self.assertEqual(updated["name"], "名前を変えた")
        self.assertEqual(updated["id"], sid)

        patched = self.ok("PATCH", f"/api/schedules/{sid}", {"enabled": False})["schedule"]
        self.assertFalse(patched["enabled"])
        self.assertIsNone(patched["next_at"])

        self.ok("DELETE", f"/api/schedules/{sid}")
        self.assertEqual(self.ok("GET", "/api/schedules")["schedules"], [])

    def test_monthly_and_yearly(self):
        m = self.ok("POST", "/api/schedules", self.sample(
            kind="monthly", day_of_month="last", days=None))["schedule"]
        self.assertEqual(m["summary"], "毎月末 08:15（10分前に予告）")
        y = self.ok("POST", "/api/schedules", self.sample(
            kind="yearly", month=1, day_of_month=1, days=None, lead_times=[]))["schedule"]
        self.assertEqual(y["summary"], "毎年1月1日 08:15")

    def test_missing_schedule(self):
        self.err("GET", "/api/schedules/nope", expect=404)
        self.err("PUT", "/api/schedules/nope", self.sample(), expect=404)
        self.err("PATCH", "/api/schedules/nope", {"enabled": False}, expect=404)
        self.err("DELETE", "/api/schedules/nope", expect=404)
        self.err("POST", "/api/schedules/nope/test", {}, expect=404)

    def test_validation_errors_come_back_as_messages(self):
        self.assertIn("時刻", self.err("POST", "/api/schedules", self.sample(time="99:99"))["error"])
        self.assertIn("名前", self.err("POST", "/api/schedules", self.sample(name=" "))["error"])
        self.assertIn("サウンド", self.err("POST", "/api/schedules",
                                       self.sample(action={"type": "sound", "sound": "builtin:nope"}))["error"])

    def test_bad_json_and_non_object(self):
        self.err("POST", "/api/schedules", raw=b"{not json")
        self.err("POST", "/api/schedules", raw=b"[1,2]")

    def test_empty_body_is_treated_as_empty_object(self):
        self.err("POST", "/api/schedules", raw=b"")

    def test_test_endpoint_plays_main_and_lead(self):
        sid = self.ok("POST", "/api/schedules", self.sample())["schedule"]["id"]
        self.ok("POST", f"/api/schedules/{sid}/test", {})
        self.assertIn("doorbell.wav", "\n".join(self.wait_calls(1)))
        self.calls.unlink()
        self.ok("POST", f"/api/schedules/{sid}/test", {"which": "lead"})
        self.assertIn("ding.wav", "\n".join(self.wait_calls(1)))

    def test_test_falls_back_to_main_without_lead(self):
        sid = self.ok("POST", "/api/schedules",
                      self.sample(lead_times=[]))["schedule"]["id"]
        self.ok("POST", f"/api/schedules/{sid}/test", {"which": "lead"})
        self.assertIn("doorbell.wav", "\n".join(self.wait_calls(1)))

    def test_too_many_schedules(self):
        for i in range(200):
            self.app.store.add(self.sample(name=f"s{i}"))
        self.assertIn("200", self.err("POST", "/api/schedules", self.sample())["error"])


class TestCalendar(ServerCase):
    def test_returns_seven_days_with_events(self):
        self.ok("POST", "/api/schedules", self.sample(days=[0, 1, 2, 3, 4, 5, 6]))
        d = self.ok("GET", "/api/calendar?start=2026-09-21&days=7")
        self.assertEqual(d["start"], "2026-09-21")
        self.assertEqual(len(d["days"]), 7)
        first = d["days"][0]
        self.assertEqual(first["weekday"], 0)
        self.assertEqual([e["time"] for e in first["events"]], ["08:05", "08:15"])
        self.assertEqual(first["events"][0]["tag"], "lead")
        self.assertEqual(first["events"][0]["sound"], "builtin:ding")
        self.assertEqual(first["events"][1]["sound"], "builtin:doorbell")

    def test_defaults_to_today_and_clamps_days(self):
        d = self.ok("GET", "/api/calendar")
        self.assertEqual(d["start"], date.today().isoformat())
        self.assertEqual(len(d["days"]), 7)
        self.assertEqual(len(self.ok("GET", "/api/calendar?days=99")["days"]), 31)
        self.assertEqual(len(self.ok("GET", "/api/calendar?days=0")["days"]), 1)
        self.assertEqual(len(self.ok("GET", "/api/calendar?days=")["days"]), 7)

    def test_bad_start(self):
        self.assertIn("YYYY-MM-DD", self.err("GET", "/api/calendar?start=9/21")["error"])

    def test_marks_quiet_and_disabled(self):
        self.ok("PUT", "/api/settings",
                {"quiet_hours": {"enabled": True, "start": "08:00", "end": "09:00"}})
        self.ok("POST", "/api/schedules", self.sample(enabled=False, lead_times=[]))
        d = self.ok("GET", "/api/calendar?start=2026-09-21&days=1")
        ev = d["days"][0]["events"][0]
        self.assertTrue(ev["quiet"])
        self.assertFalse(ev["enabled"])


class TestPreviewAndStop(ServerCase):
    def test_preview_sound(self):
        self.ok("POST", "/api/preview", {"type": "sound", "sound": "builtin:ding", "volume": 0.3})
        self.assertIn("-v 0.300", "\n".join(self.wait_calls(1)))

    def test_preview_speak_and_both(self):
        self.ok("POST", "/api/preview", {"type": "speak", "text": "こんにちは"})
        self.assertIn("こんにちは", "\n".join(self.wait_calls(2)))
        self.calls.unlink()
        self.ok("POST", "/api/preview",
                {"type": "both", "sound": "builtin:ding", "text": "はい"})
        self.assertEqual(len(self.wait_calls(3)), 3)

    def test_preview_requires_its_inputs(self):
        self.assertIn("サウンド", self.err("POST", "/api/preview", {"type": "sound"})["error"])
        self.assertIn("文章", self.err("POST", "/api/preview", {"type": "speak", "text": " "})["error"])
        self.err("POST", "/api/preview", {"type": "sound", "sound": "builtin:nope"})

    def test_stop(self):
        self.ok("POST", "/api/stop")
        self.assertFalse(self.ok("GET", "/api/now")["playing"])


class TestSounds(ServerCase):
    def wav(self):
        return base64.b64encode(b"RIFF----WAVEfake").decode()

    def test_upload_list_and_delete(self):
        d = self.ok("POST", "/api/sounds", {"filename": "song.mp3", "data": self.wav()})
        self.assertEqual(d["sound"]["ref"], "user:song.mp3")
        self.assertIn("user:song.mp3", [s["ref"] for s in d["sounds"]["user"]])
        self.assertIn("user:song.mp3", [s["ref"] for s in self.ok("GET", "/api/sounds")["sounds"]["user"]])
        left = self.ok("DELETE", "/api/sounds/song.mp3")["sounds"]["user"]
        self.assertEqual(left, [])

    def test_upload_rejects_bad_input(self):
        self.assertIn("読めません", self.err("POST", "/api/sounds",
                                        {"filename": "a.mp3", "data": "!!!notbase64"})["error"])
        self.assertIn("空", self.err("POST", "/api/sounds", {"filename": "a.mp3", "data": ""})["error"])
        self.assertIn("拡張子", self.err("POST", "/api/sounds",
                                      {"filename": "a.txt", "data": self.wav()})["error"])

    def test_upload_too_large(self):
        """上限そのものを小さくして、巨大なファイルを送らずに分岐を確かめる。"""
        real = server_mod.MAX_UPLOAD_BYTES
        server_mod.MAX_UPLOAD_BYTES = 8
        try:
            msg = self.err("POST", "/api/sounds",
                           {"filename": "a.mp3", "data": self.wav()})["error"]
        finally:
            server_mod.MAX_UPLOAD_BYTES = real
        self.assertIn("以下のファイル", msg)

    def test_delete_missing(self):
        self.err("DELETE", "/api/sounds/nope.mp3")

    def test_uploaded_sound_can_be_used_by_a_schedule(self):
        self.ok("POST", "/api/sounds", {"filename": "song.mp3", "data": self.wav()})
        s = self.ok("POST", "/api/schedules",
                    self.sample(action={"type": "sound", "sound": "user:song.mp3"}))
        self.assertEqual(s["schedule"]["action"]["sound"], "user:song.mp3")


class TestSettings(ServerCase):
    def test_update_and_validate(self):
        d = self.ok("PUT", "/api/settings", {"default_volume": 0.25, "speak_rate": 200,
                                             "master_enabled": False, "default_voice": "Alex"})
        self.assertEqual(d["settings"]["default_volume"], 0.25)
        self.assertFalse(d["settings"]["master_enabled"])
        self.assertEqual(self.ok("PATCH", "/api/settings", {"master_enabled": True})
                         ["settings"]["master_enabled"], True)
        self.assertIn("音量", self.err("PUT", "/api/settings", {"default_volume": 9})["error"])
        self.assertIn("静音開始", self.err("PUT", "/api/settings",
                                       {"quiet_hours": {"start": "99:99"}})["error"])

    def test_settings_survive_a_restart(self):
        self.ok("PUT", "/api/settings", {"default_volume": 0.9})
        app2, _calls = None, None
        store = server_mod.Store(self.home / "data" / "config.json")
        self.assertEqual(store.settings["default_volume"], 0.9)


class TestSecurity(ServerCase):
    def test_cross_origin_writes_are_rejected(self):
        self.err("POST", "/api/schedules", self.sample(),
                 headers={"Origin": "http://evil.example"}, expect=403)
        self.err("DELETE", "/api/schedules/x",
                 headers={"Origin": "http://evil.example"}, expect=403)

    def test_same_origin_writes_pass(self):
        self.ok("POST", "/api/schedules", self.sample(),
                headers={"Origin": self.base})

    def test_malformed_origin_is_rejected(self):
        self.err("POST", "/api/schedules", self.sample(),
                 headers={"Origin": "http://[bad"}, expect=403)

    def test_reads_are_allowed_cross_origin(self):
        self.ok("GET", "/api/state", headers={"Origin": "http://evil.example"})

    def test_oversized_body_is_refused(self):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("POST", "/api/sounds", body=b"{}",
                     headers={"Content-Type": "application/json",
                              "Content-Length": str(server_mod.MAX_BODY + 1)})
        self.assertEqual(conn.getresponse().status, 400)
        conn.close()

    def test_bad_content_length_is_treated_as_empty(self):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.putrequest("POST", "/api/schedules")
        conn.putheader("Content-Length", "abc")
        conn.endheaders()
        res = conn.getresponse()
        self.assertEqual(res.status, 400)          # 空の本文として扱われ、種別が無いと言われる
        self.assertIn("繰り返し方", json.loads(res.read())["error"])
        conn.close()

    def test_internal_errors_become_500(self):
        def boom():
            raise RuntimeError("こわれた")
        self.app.player.library = boom
        code, data, _res = self.req("GET", "/api/state")
        self.assertEqual(code, 500)
        self.assertIn("こわれた", data["error"])
        self.assertTrue(any(e["level"] == "error" for e in self.app.log.recent()))


class TestToken(ServerCase):
    token = "himitsu"

    def test_requires_the_token(self):
        r = urllib.request.Request(self.base + "/api/state")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(r, timeout=10)
        self.assertEqual(cm.exception.code, 401)

    def test_header_query_and_cookie_all_work(self):
        self.ok("GET", "/api/state")  # X-Sounder-Token（req が付ける）
        code, _b, _r = self.req("GET", "/api/state?t=himitsu", headers={"X-Sounder-Token": ""})
        self.assertEqual(code, 200)
        code, _b, _r = self.req("GET", "/api/state",
                                headers={"X-Sounder-Token": "", "Cookie": "sounder_token=himitsu"})
        self.assertEqual(code, 200)

    def test_wrong_token_is_rejected(self):
        code, _b, _r = self.req("GET", "/api/state", headers={"X-Sounder-Token": "chigau"})
        self.assertEqual(code, 401)

    def test_page_with_token_sets_a_cookie(self):
        code, _b, res = self.req("GET", "/?t=himitsu")
        self.assertEqual(code, 200)
        self.assertIn("sounder_token=himitsu", res.headers["Set-Cookie"])

    def test_page_with_wrong_token_sets_no_cookie(self):
        code, _b, res = self.req("GET", "/?t=chigau&", headers={"X-Sounder-Token": "himitsu"})
        self.assertEqual(code, 200)
        self.assertIsNone(res.headers.get("Set-Cookie"))


class TestHandlerUnits(unittest.TestCase):
    """ソケットを使わずにハンドラの細かい分岐を確かめる。"""

    def handler(self, headers=None, path="/"):
        h = object.__new__(server_mod.Handler)
        h.headers = headers or {}
        h.path = path
        h.command = "GET"
        h.requestline = "GET / HTTP/1.1"
        h.request_version = "HTTP/1.1"
        h.client_address = ("127.0.0.1", 1234)
        return h

    def test_cookie_parsing(self):
        h = self.handler({"Cookie": "a=1; sounder_token=abc%20d; b=2"})
        self.assertEqual(h._cookie("sounder_token"), "abc d")
        self.assertIsNone(h._cookie("nope"))
        self.assertIsNone(self.handler()._cookie("x"))

    def test_same_origin_checks(self):
        self.assertTrue(self.handler()._same_origin())
        self.assertTrue(self.handler({"Origin": "http://h:1", "Host": "h:1"})._same_origin())
        self.assertFalse(self.handler({"Origin": "http://other", "Host": "h:1"})._same_origin())

    def test_log_message_is_silent(self):
        self.assertIsNone(self.handler().log_message("%s", "x"))

    def test_broken_pipe_is_swallowed(self):
        """相手が先に切ったときに例外を外へ出さないこと。"""
        h = self.handler()
        h.app = type("Stub", (), {"token": None, "log": staticmethod(lambda *a, **k: None)})()

        def boom(_path, _query):
            raise BrokenPipeError()
        h._static = boom
        h._dispatch("GET")  # 例外が外に出なければ合格


class TestDefaultVoice(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_picks_kyoko_when_unset(self):
        app, _calls = make_app(Path(self.tmp.name) / "a", voice="")
        self.assertEqual(app.store.settings["default_voice"], "Kyoko (Japanese (Japan))")

    def test_leaves_it_empty_when_no_japanese_voice_exists(self):
        home = Path(self.tmp.name) / "b"
        app, _calls = make_app(home, voice="")
        # 日本語の声が無い環境を作って選び直させる
        (home / "say").write_text("#!/bin/sh\nprintf 'Alex   en_US    # hi\\n'\n")
        (home / "say").chmod(0o755)
        app.store.update_settings({"default_voice": ""})
        app._pick_default_voice()
        self.assertEqual(app.store.settings["default_voice"], "")

    def test_keeps_an_existing_choice(self):
        app, _calls = make_app(Path(self.tmp.name) / "c", voice="Alex")
        self.assertEqual(app.store.settings["default_voice"], "Alex")


class TestServeAndMain(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        (self.home / "sounds").mkdir()
        os.symlink(ROOT / "sounds" / "builtin", self.home / "sounds" / "builtin")

    def tearDown(self):
        self.tmp.cleanup()

    def test_serve_runs_until_a_signal(self):
        """SIGTERM を受けたら片付けて 0 で返ること。"""
        port = 8971
        done = {}

        def stopper():
            for _ in range(100):
                time.sleep(0.05)
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/api/now", timeout=1).read()
                    break
                except Exception:
                    continue
            os.kill(os.getpid(), signal.SIGTERM)

        old = signal.getsignal(signal.SIGTERM)
        threading.Thread(target=stopper, daemon=True).start()
        try:
            done["rc"] = server_mod.serve(self.home, "127.0.0.1", port, None)
        finally:
            signal.signal(signal.SIGTERM, old)
        self.assertEqual(done["rc"], 0)

    def test_serve_reports_a_busy_port(self):
        app, _calls = make_app(self.home / "a")
        httpd = server_mod.make_server(app, "127.0.0.1", 0)
        port = httpd.server_address[1]
        try:
            self.assertEqual(server_mod.serve(self.home / "b", "127.0.0.1", port, None), 1)
        finally:
            httpd.server_close()

    def test_main_parses_arguments(self):
        seen = {}

        def fake_serve(home, host, port, token):
            seen.update(home=home, host=host, port=port, token=token)
            return 0
        real, server_mod.serve = server_mod.serve, fake_serve
        try:
            self.assertEqual(server_mod.main(["--home", str(self.home), "--port", "9",
                                              "--host", "0.0.0.0"]), 0)
            self.assertEqual((seen["host"], seen["port"], seen["token"]), ("0.0.0.0", 9, None))
            server_mod.main(["--home", str(self.home), "--print-token"])
            self.assertTrue(seen["token"])
            server_mod.main(["--home", str(self.home), "--token", "abc", "--print-token"])
            self.assertEqual(seen["token"], "abc")
        finally:
            server_mod.serve = real

    def test_serve_with_a_token_shows_it_in_the_url(self):
        app, _calls = make_app(self.home / "t")
        httpd = server_mod.make_server(app, "127.0.0.1", 0)
        port = httpd.server_address[1]
        httpd.server_close()
        real = server_mod.ThreadingHTTPServer.serve_forever
        server_mod.ThreadingHTTPServer.serve_forever = lambda self, poll_interval=0.5: None
        home2 = self.home / "t2"
        (home2 / "sounds").mkdir(parents=True)
        os.symlink(ROOT / "sounds" / "builtin", home2 / "sounds" / "builtin")
        try:
            self.assertEqual(server_mod.serve(home2, "127.0.0.1", port, "aikotoba"), 0)
        finally:
            server_mod.ThreadingHTTPServer.serve_forever = real
        log = (home2 / "data" / "events.log").read_text("utf-8")
        self.assertIn("?t=aikotoba", log)

    def test_main_warns_when_exposed_without_a_token(self):
        """ローカル以外に公開するときは警告を出す（鳴らしっぱなし防止の注意喚起）。"""
        app, _calls = make_app(self.home / "c")
        httpd = server_mod.make_server(app, "127.0.0.1", 0)
        port = httpd.server_address[1]
        httpd.server_close()
        errs = []
        real_serve_forever = server_mod.ThreadingHTTPServer.serve_forever
        server_mod.ThreadingHTTPServer.serve_forever = lambda self, poll_interval=0.5: None
        real_stderr, sys.stderr = sys.stderr, type("W", (), {"write": errs.append, "flush": lambda s: None})()
        try:
            server_mod.serve(self.home / "d", "0.0.0.0", port, None)
        finally:
            server_mod.ThreadingHTTPServer.serve_forever = real_serve_forever
            sys.stderr = real_stderr
        self.assertTrue(any("警告" in e for e in errs))

    def test_entry_point_module(self):
        import runpy
        real, server_mod.main = server_mod.main, lambda: 7
        try:
            with self.assertRaises(SystemExit) as cm:
                runpy.run_module("sounder.__main__", run_name="__main__")
            self.assertEqual(cm.exception.code, 7)
        finally:
            server_mod.main = real


if __name__ == "__main__":
    unittest.main()
