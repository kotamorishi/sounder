"""再生層のテスト。afplay / say はダミーのシェルスクリプトに差し替えて音は出さない。"""

import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sounder import player as player_mod  # noqa: E402
from sounder import tones  # noqa: E402

FAKE_AFPLAY = """#!/bin/sh
printf 'afplay %s\\n' "$*" >> "{log}"
exit 0
"""

# say は -o で指定されたファイルを作る必要がある（作らないと呼び出し側が失敗扱いにする）
FAKE_SAY = """#!/bin/sh
printf 'say %s\\n' "$*" >> "{log}"
prev=""
for a in "$@"; do
  if [ "$prev" = "-o" ]; then printf 'dummy' > "$a"; fi
  prev="$a"
done
if [ "$1" = "-v" ] && [ "$2" = "?" ]; then
  printf 'Kyoko               ja_JP    # こんにちは\\n'
  printf 'Alex                en_US    # Hello\\n'
  printf 'これは音声一覧ではない行\\n'
fi
exit 0
"""


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.builtin = self.home / "builtin"
        self.user = self.home / "user"
        self.user.mkdir()
        # 内蔵サウンドは 1 つだけ実体を置けば resolve の検査には足りる
        self.builtin.mkdir()
        for key in tones.BUILTINS:
            (self.builtin / f"{key}.wav").write_bytes(b"RIFF....WAVEfake")
        self.calls = self.home / "calls.log"
        self.msgs = []
        self.p = player_mod.Player(self.builtin, self.user,
                                   log=lambda lv, m, **k: self.msgs.append((lv, m)))
        self.p.afplay = self._script("afplay", FAKE_AFPLAY)
        self.p.say = self._script("say", FAKE_SAY)

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()

    def _script(self, name, body):
        path = self.home / name
        path.write_text(body.format(log=self.calls))
        path.chmod(0o755)
        return str(path)

    def recorded(self):
        return self.calls.read_text("utf-8").splitlines() if self.calls.exists() else []

    def wait_for_calls(self, n, timeout=5.0):
        end = time.time() + timeout
        while time.time() < end:
            if len(self.recorded()) >= n:
                return self.recorded()
            time.sleep(0.02)
        return self.recorded()


class TestResolve(Base):
    def test_builtin_with_and_without_scheme(self):
        self.assertEqual(self.p.resolve("builtin:ding"), self.builtin / "ding.wav")
        self.assertEqual(self.p.resolve("ding"), self.builtin / "ding.wav")

    def test_unknown_builtin(self):
        with self.assertRaises(player_mod.SoundNotFound):
            self.p.resolve("builtin:nope")

    def test_builtin_missing_file(self):
        (self.builtin / "ding.wav").unlink()
        with self.assertRaises(player_mod.SoundNotFound):
            self.p.resolve("builtin:ding")

    def test_user_file(self):
        (self.user / "song.mp3").write_bytes(b"x")
        self.assertEqual(self.p.resolve("user:song.mp3"), (self.user / "song.mp3").resolve())

    def test_user_missing(self):
        with self.assertRaises(player_mod.SoundNotFound):
            self.p.resolve("user:none.mp3")

    def test_user_rejects_path_traversal(self):
        for bad in ["user:../../etc/passwd", "user:/etc/passwd", "user:a/b.mp3"]:
            with self.subTest(bad), self.assertRaises(player_mod.SoundNotFound):
                self.p.resolve(bad)

    def test_system_sound(self):
        real = Path("/System/Library/Sounds/Ping.aiff")
        if not real.exists():
            self.skipTest("システムサウンドが無い環境")
        self.assertEqual(self.p.resolve("system:Ping"), real)

    def test_system_sound_from_the_sound_folders(self):
        """/System/Library/Sounds の無い環境（Linux など）でも、探し方を確かめる。"""
        folder = self.home / "sys"
        folder.mkdir()
        (folder / "Ping.aiff").write_bytes(b"FORM")
        real = player_mod.SYSTEM_SOUND_DIRS
        player_mod.SYSTEM_SOUND_DIRS = (self.home / "ない", folder)
        try:
            self.assertEqual(self.p.resolve("system:Ping"), folder / "Ping.aiff")
        finally:
            player_mod.SYSTEM_SOUND_DIRS = real

    def test_system_unknown_and_unsafe(self):
        with self.assertRaises(player_mod.SoundNotFound):
            self.p.resolve("system:NoSuchSound")
        with self.assertRaises(player_mod.SoundNotFound):
            self.p.resolve("system:../../etc/passwd")

    def test_absolute_file(self):
        f = self.home / "abs.wav"
        f.write_bytes(b"x")
        self.assertEqual(self.p.resolve(f"file:{f}"), f)
        with self.assertRaises(player_mod.SoundNotFound):
            self.p.resolve("file:relative.wav")
        with self.assertRaises(player_mod.SoundNotFound):
            self.p.resolve(f"file:{self.home}/missing.wav")

    def test_empty_and_unknown_scheme(self):
        for bad in ["", "   ", None, "http:x"]:
            with self.subTest(bad), self.assertRaises(player_mod.SoundNotFound):
                self.p.resolve(bad)


class TestLibrary(Base):
    def test_lists_three_groups(self):
        (self.user / "a.mp3").write_bytes(b"x")
        (self.user / "notes.txt").write_text("音声ではない")
        lib = self.p.library()
        self.assertEqual(len(lib["builtin"]), len(tones.BUILTINS))
        self.assertEqual([s["label"] for s in lib["user"]], ["a.mp3"])
        self.assertEqual(lib["user"][0]["size"], 1)
        self.assertTrue(all(s["ref"].startswith("system:") for s in lib["system"]))

    def test_missing_builtin_is_not_listed(self):
        (self.builtin / "ding.wav").unlink()
        refs = [s["ref"] for s in self.p.library()["builtin"]]
        self.assertNotIn("builtin:ding", refs)

    def test_handles_missing_user_dir(self):
        shutil.rmtree(self.user)
        self.assertEqual(self.p.library()["user"], [])


class TestVoices(Base):
    def test_parses_say_output_japanese_first(self):
        voices = self.p.voices()
        self.assertEqual([v["name"] for v in voices], ["Kyoko", "Alex"])
        self.assertEqual(voices[0]["locale"], "ja_JP")

    def test_duplicate_voice_names_are_listed_once(self):
        self.p.say = self._script("say2", "#!/bin/sh\n"
                                  "printf 'Kyoko   ja_JP    # a\\n'\n"
                                  "printf 'Kyoko   ja_JP    # b\\n'\n")
        self.assertEqual([v["name"] for v in self.p.voices()], ["Kyoko"])

    def test_no_say_command(self):
        self.p.say = None
        self.assertEqual(self.p.voices(), [])

    def test_say_failure_returns_empty(self):
        self.p.say = "/nonexistent/say"
        self.assertEqual(self.p.voices(), [])


class TestPlay(Base):
    settings = {"default_volume": 0.6, "default_voice": "Kyoko", "speak_rate": 180}

    def test_plays_sound_with_volume(self):
        self.p.play_blocking({"type": "sound", "sound": "builtin:ding", "volume": 0.25},
                             settings=self.settings)
        calls = self.recorded()
        self.assertEqual(len(calls), 1)
        self.assertIn("-v 0.250", calls[0])
        self.assertIn("ding.wav", calls[0])

    def test_repeat_plays_several_times(self):
        self.p.play_blocking({"type": "sound", "sound": "builtin:ding", "repeat": 3},
                             settings=self.settings)
        self.assertEqual(len(self.recorded()), 3)

    def test_speak_renders_then_plays(self):
        self.p.play_blocking({"type": "speak", "text": "こんにちは", "voice": "Kyoko",
                              "rate": 200, "volume": 0.5}, settings=self.settings)
        calls = self.recorded()
        self.assertEqual(len(calls), 2)
        self.assertIn("say ", calls[0])
        self.assertIn("-v Kyoko", calls[0])
        self.assertIn("-r 200", calls[0])
        self.assertIn("--file-format=WAVE", calls[0])
        self.assertIn("こんにちは", calls[0])
        self.assertTrue(calls[1].startswith("afplay"))

    def test_both_plays_sound_then_speech(self):
        self.p.play_blocking({"type": "both", "sound": "builtin:ding", "text": "はい"},
                             settings=self.settings)
        calls = self.recorded()
        self.assertEqual(len(calls), 3)  # say(生成) + afplay(音) + afplay(読み上げ)
        self.assertIn("ding.wav", calls[1])

    def test_speak_falls_back_to_default_voice(self):
        self.p.play_blocking({"type": "speak", "text": "はい"}, settings=self.settings)
        self.assertIn("-v Kyoko", self.recorded()[0])

    def test_missing_sound_is_logged_not_raised(self):
        self.p.play_blocking({"type": "sound", "sound": "builtin:nope"}, settings=self.settings)
        self.assertEqual(self.recorded(), [])
        self.assertTrue(any(lv == "error" for lv, _m in self.msgs))

    def test_no_afplay_logs_error(self):
        self.p.afplay = None
        self.p.play_blocking({"type": "sound", "sound": "builtin:ding"}, settings=self.settings)
        self.assertTrue(any("afplay" in m for _lv, m in self.msgs))

    def test_say_failure_is_logged(self):
        self.p.say = self._script("say-broken", "#!/bin/sh\nexit 1\n")
        self.p.play_blocking({"type": "speak", "text": "はい"}, settings=self.settings)
        self.assertTrue(any(lv == "error" for lv, _m in self.msgs))

    def test_play_is_async(self):
        self.p.play({"type": "sound", "sound": "builtin:ding"}, settings=self.settings)
        self.assertEqual(len(self.wait_for_calls(1)), 1)

    def test_temp_files_are_cleaned_up(self):
        self.p.play_blocking({"type": "speak", "text": "はい"}, settings=self.settings)
        self.assertEqual(list(self.p._tmpdir.glob("*.wav")), [])

    def test_stop_interrupts_a_long_sound(self):
        slow = self._script("afplay-slow", "#!/bin/sh\nsleep 30\n")
        self.p.afplay = slow
        self.p.play({"type": "sound", "sound": "builtin:ding"}, settings=self.settings)
        end = time.time() + 3
        while not self.p.is_playing() and time.time() < end:
            time.sleep(0.02)
        self.assertTrue(self.p.is_playing())
        self.p.stop()
        self.assertFalse(self.p.is_playing())

    def test_new_playback_replaces_the_old_one(self):
        self.p.afplay = self._script("afplay-slow2", "#!/bin/sh\nsleep 30\n")
        self.p.play({"type": "sound", "sound": "builtin:ding"}, settings=self.settings)
        end = time.time() + 3
        while not self.p.is_playing() and time.time() < end:
            time.sleep(0.02)
        self.p.afplay = self._script("afplay", FAKE_AFPLAY)
        self.p.play_blocking({"type": "sound", "sound": "builtin:ding"}, settings=self.settings)
        self.assertEqual(len([c for c in self.recorded() if "afplay" in c]), 1)

    def test_timeout_kills_playback(self):
        self.p.afplay = self._script("afplay-hang", "#!/bin/sh\nsleep 30\n")
        argv = [self.p.afplay, "x"]
        self.assertFalse(self.p._run(argv, self.p._job, timeout=0.3))
        self.assertTrue(any("長すぎる" in m for _lv, m in self.msgs))

    def test_nothing_to_play_is_a_no_op(self):
        self.p.play_blocking({"type": "sound"}, settings=self.settings)
        self.assertEqual(self.recorded(), [])


class TestUpload(Base):
    def test_saves_and_returns_ref(self):
        info = self.p.save_upload("melody.mp3", b"abc")
        self.assertEqual(info, {"ref": "user:melody.mp3", "label": "melody.mp3", "size": 3})
        self.assertEqual((self.user / "melody.mp3").read_bytes(), b"abc")

    def test_avoids_overwriting_by_renaming(self):
        self.p.save_upload("a.mp3", b"1")
        second = self.p.save_upload("a.mp3", b"2")
        self.assertEqual(second["label"], "a-1.mp3")
        self.assertEqual((self.user / "a.mp3").read_bytes(), b"1")

    def test_strips_directories_from_name(self):
        info = self.p.save_upload("/etc/../evil/x.wav", b"1")
        self.assertEqual(info["label"], "x.wav")

    def test_rejects_bad_names_and_extensions(self):
        for bad in ["", ".hidden.mp3", "x.txt", "x.exe", "x"]:
            with self.subTest(bad), self.assertRaises(player_mod.SoundNotFound):
                self.p.save_upload(bad, b"1")

    def test_delete(self):
        self.p.save_upload("a.mp3", b"1")
        self.p.delete_upload("a.mp3")
        self.assertFalse((self.user / "a.mp3").exists())

    def test_delete_rejects_traversal_and_missing(self):
        for bad in ["../../etc/passwd", "nope.mp3", ""]:
            with self.subTest(bad), self.assertRaises(player_mod.SoundNotFound):
                self.p.delete_upload(bad)


if __name__ == "__main__":
    unittest.main()


class TestEdges(Base):
    settings = {"default_volume": 0.6, "default_voice": "", "speak_rate": 180}

    def test_library_skips_duplicate_system_names(self):
        """同じ名前のシステムサウンドが複数の場所にあっても 1 つだけ載せる。"""
        a, b = self.home / "sysA", self.home / "sysB"
        for d in (a, b):
            d.mkdir()
            (d / "Ping.aiff").write_bytes(b"x")
        (b / "Only.aiff").write_bytes(b"x")
        real = player_mod.SYSTEM_SOUND_DIRS
        player_mod.SYSTEM_SOUND_DIRS = (a, b, self.home / "ない")
        try:
            names = [s["label"] for s in self.p.library()["system"]]
        finally:
            player_mod.SYSTEM_SOUND_DIRS = real
        self.assertEqual(names, ["Ping", "Only"])

    def test_signal_group_falls_back_for_a_dead_process(self):
        import subprocess
        proc = subprocess.Popen(["/bin/sh", "-c", "exit 0"])
        proc.wait()
        self.p._signal_group(proc, 15)  # 例外が出なければ合格

    def test_stop_kills_a_process_that_ignores_sigterm(self):
        # TERM を無視し、2 秒の猶予より長く生き延びるスクリプト（SIGKILL の経路を通す）
        self.p.afplay = self._script(
            "afplay-stubborn",
            "#!/bin/sh\ntrap '' TERM\ni=0\nwhile [ $i -lt 60 ]; do sleep 0.2; i=$((i+1)); done\n")
        self.p.play({"type": "sound", "sound": "builtin:ding"}, settings=self.settings)
        end = time.time() + 3
        while not self.p.is_playing() and time.time() < end:
            time.sleep(0.02)
        self.assertTrue(self.p.is_playing())
        time.sleep(0.4)  # trap が張られるまで待つ（張る前の TERM では素直に死ぬ）
        started = time.time()
        self.p.stop()
        self.assertGreater(time.time() - started, 1.5, "SIGTERM で止まらず SIGKILL まで進むこと")
        self.assertFalse(self.p.is_playing())

    def test_missing_afplay_binary_is_logged(self):
        self.p.afplay = str(self.home / "ない-afplay")
        self.p.play_blocking({"type": "sound", "sound": "builtin:ding"}, settings=self.settings)
        self.assertTrue(any("再生に失敗" in m for _lv, m in self.msgs))

    def test_run_gives_up_when_the_job_changed(self):
        argv = [self.p.afplay, "x"]
        self.assertFalse(self.p._run(argv, self.p._job - 1))

    def test_stderr_from_afplay_is_logged(self):
        self.p.afplay = self._script("afplay-err",
                                     "#!/bin/sh\necho 'こわれた音源' >&2\nexit 1\n")
        self.p.play_blocking({"type": "sound", "sound": "builtin:ding"}, settings=self.settings)
        self.assertTrue(any("再生エラー" in m for _lv, m in self.msgs))

    def test_speak_without_say_command(self):
        self.p.say = None
        self.p.play_blocking({"type": "speak", "text": "はい"}, settings=self.settings)
        self.assertEqual(self.recorded(), [])

    def test_speak_when_say_is_missing_binary(self):
        self.p.say = str(self.home / "ない-say")
        self.p.play_blocking({"type": "speak", "text": "はい"}, settings=self.settings)
        self.assertTrue(any("読み上げの生成に失敗" in m for _lv, m in self.msgs))

    def test_stop_between_repeats(self):
        self.p.afplay = self._script("afplay-slow3", "#!/bin/sh\nsleep 0.4\n")
        t = threading.Thread(target=self.p.play_blocking,
                             args=({"type": "sound", "sound": "builtin:ding", "repeat": 5},),
                             kwargs={"settings": self.settings})
        t.start()
        time.sleep(0.6)
        self.p.stop()
        t.join(timeout=5)
        self.assertFalse(t.is_alive())

    def test_cleanup_tolerates_a_vanished_temp_file(self):
        """一時ファイルが先に消えていても後始末で落ちないこと。"""
        self.p.afplay = self._script(
            "afplay-eat", "#!/bin/sh\nfor a in \"$@\"; do [ -f \"$a\" ] && rm -f \"$a\"; done\nexit 0\n")
        self.p.play_blocking({"type": "speak", "text": "はい"}, settings=self.settings)
        self.assertEqual(list(self.p._tmpdir.glob("*.wav")), [])


class TestSignalFallback(unittest.TestCase):
    def test_falls_back_to_send_signal_and_tolerates_failure(self):
        """プロセスグループに送れない場合の保険。"""
        p = player_mod.Player(Path("/tmp"), Path("/tmp"))

        class Stub:
            pid = -1

            def send_signal(self, _sig):
                raise OSError("送れない")

        p._signal_group(Stub(), 15)  # 例外が外に出なければ合格


class TestQueue(Base):
    settings = {"default_volume": 0.6, "default_voice": "", "speak_rate": 180}

    def sound(self, name="builtin:ding"):
        return {"type": "sound", "sound": name}

    def test_queued_playback_runs_one_after_another(self):
        self.p.afplay = self._script("afplay-slow", "#!/bin/sh\nsleep 0.25\n"
                                     "printf 'played %s\\n' \"$*\" >> \"" + str(self.calls) + "\"\n")
        self.p.play(self.sound(), settings=self.settings, queue=True)
        self.p.play(self.sound("builtin:doorbell"), settings=self.settings, queue=True)
        calls = self.wait_for_calls(2, timeout=8)
        self.assertEqual(len(calls), 2)
        self.assertIn("ding.wav", calls[0])
        self.assertIn("doorbell.wav", calls[1])

    def test_immediate_playback_drops_what_was_waiting(self):
        self.p.afplay = self._script("afplay-slow2", "#!/bin/sh\nsleep 0.6\n"
                                     "printf 'played %s\\n' \"$*\" >> \"" + str(self.calls) + "\"\n")
        self.p.play(self.sound(), settings=self.settings, queue=True)
        self.p.play(self.sound("builtin:doorbell"), settings=self.settings, queue=True)
        time.sleep(0.15)
        self.p.play(self.sound("builtin:cuckoo"), settings=self.settings)  # 試聴は割り込み
        calls = self.wait_for_calls(1, timeout=8)
        time.sleep(0.5)
        joined = "\n".join(self.recorded())
        self.assertIn("cuckoo.wav", joined)
        self.assertNotIn("doorbell.wav", joined, "待っていたものは捨てる")

    def test_stop_clears_the_queue(self):
        self.p.afplay = self._script("afplay-slow3", "#!/bin/sh\nsleep 0.5\n")
        self.p.play(self.sound(), settings=self.settings, queue=True)
        self.p.play(self.sound("builtin:doorbell"), settings=self.settings, queue=True)
        time.sleep(0.1)
        self.p.stop()
        time.sleep(0.4)
        self.assertEqual(len(self.p._queue), 0)
        self.assertNotIn("doorbell.wav", "\n".join(self.recorded()))

    def test_a_failing_item_does_not_stop_the_queue(self):
        real = self.p.play_blocking
        calls = []

        def flaky(action, *, settings, label=""):
            calls.append(action["sound"])
            if len(calls) == 1:
                raise RuntimeError("わざと失敗")
            return real(action, settings=settings, label=label)
        self.p.play_blocking = flaky
        self.p.play(self.sound(), settings=self.settings, queue=True)
        self.p.play(self.sound("builtin:doorbell"), settings=self.settings, queue=True)
        end = time.time() + 5
        while len(calls) < 2 and time.time() < end:
            time.sleep(0.02)
        self.assertEqual(calls, ["builtin:ding", "builtin:doorbell"])
        self.assertTrue(any("再生中にエラー" in m for _lv, m in self.msgs))
