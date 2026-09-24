"""内蔵サウンド合成のテスト。"""

import sys
import tempfile
import unittest
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sounder import tones  # noqa: E402


class TestNote(unittest.TestCase):
    def test_a4_is_440(self):
        self.assertAlmostEqual(tones.note("A4"), 440.0)

    def test_octave_doubles_frequency(self):
        self.assertAlmostEqual(tones.note("A5"), 880.0)
        self.assertAlmostEqual(tones.note("A3"), 220.0)

    def test_semitones(self):
        self.assertAlmostEqual(tones.note("C5"), 523.2511, places=3)
        self.assertAlmostEqual(tones.note("C#5"), tones.note("Db5"), places=6)
        self.assertLess(tones.note("Bb4"), tones.note("B4"))

    def test_all_builtin_note_names_parse(self):
        for name in ["C4", "F#3", "Gb6", "B2", "E5"]:
            self.assertGreater(tones.note(name), 0)


class TestTrack(unittest.TestCase):
    def test_add_extends_and_mixes(self):
        t = tones.Track()
        t.add(0.0, [1.0, 1.0])
        t.add(0.0, [0.5, 0.5])
        self.assertEqual(list(t.buf), [1.5, 1.5])

    def test_add_at_offset_pads_with_silence(self):
        t = tones.Track()
        t.add(1.0, [1.0])
        self.assertEqual(len(t.buf), tones.SAMPLE_RATE + 1)
        self.assertEqual(t.buf[0], 0.0)
        self.assertAlmostEqual(t.duration(), (tones.SAMPLE_RATE + 1) / tones.SAMPLE_RATE)

    def test_normalized_scales_to_peak(self):
        t = tones.Track()
        t.add(0.0, [0.1, -0.05])
        out = t.normalized(peak=0.8)
        self.assertAlmostEqual(max(abs(v) for v in out), 0.8)

    def test_normalized_handles_empty_and_silence(self):
        self.assertEqual(len(tones.Track().normalized()), 0)
        t = tones.Track()
        t.add(0.0, [0.0, 0.0])
        self.assertEqual(list(t.normalized()), [0.0, 0.0])

    def test_write_produces_readable_wav(self):
        t = tones.Track()
        t.add(0.0, tones.tone(440.0, 0.05))
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "sub" / "x.wav"
            t.write(path)
            with wave.open(str(path)) as w:
                self.assertEqual(w.getnchannels(), 1)
                self.assertEqual(w.getsampwidth(), 2)
                self.assertEqual(w.getframerate(), tones.SAMPLE_RATE)
                self.assertGreater(w.getnframes(), 0)


class TestVoices(unittest.TestCase):
    def test_tone_has_expected_length_and_range(self):
        s = tones.tone(440.0, 0.1, amp=1.0)
        self.assertEqual(len(s), int(0.1 * tones.SAMPLE_RATE))
        self.assertLessEqual(max(abs(v) for v in s), 2.5)

    def test_tone_decays(self):
        s = tones.tone(440.0, 0.4, decay=8.0)
        head = max(abs(v) for v in s[:1000])
        tail = max(abs(v) for v in s[-1000:])
        self.assertLess(tail, head)

    def test_tone_attack_starts_quiet(self):
        s = tones.tone(440.0, 0.2, attack=0.02)
        self.assertLess(abs(s[0]), 0.01)

    def test_tone_with_vibrato_runs(self):
        s = tones.tone(440.0, 0.05, vibrato=0.5)
        self.assertEqual(len(s), int(0.05 * tones.SAMPLE_RATE))

    def test_tone_skips_harmonics_above_nyquist(self):
        low = tones.tone(60.0, 0.05, timbre=((1.0, 1.0),))
        high = tones.tone(20000.0, 0.05, timbre=((1.0, 1.0), (4.0, 1.0)))
        self.assertEqual(len(low), len(high))

    def test_beep_is_flat_in_the_middle(self):
        s = tones.beep(440.0, 0.2)
        mid = max(abs(v) for v in s[len(s) // 2 - 200:len(s) // 2 + 200])
        self.assertGreater(mid, 0.5)
        self.assertLess(abs(s[0]), 0.05)
        self.assertLess(abs(s[-1]), 0.05)

    def test_beep_skips_harmonics_above_nyquist(self):
        s = tones.beep(21000.0, 0.02, timbre=((1.0, 1.0), (2.0, 1.0)))
        self.assertEqual(len(s), int(0.02 * tones.SAMPLE_RATE))


class TestBuiltins(unittest.TestCase):
    def test_every_builtin_renders_non_silent_audio(self):
        for key, (label, fn) in tones.BUILTINS.items():
            with self.subTest(key):
                track = fn()
                self.assertTrue(label, f"{key} にラベルがない")
                self.assertGreater(track.duration(), 0.1, f"{key} が短すぎる")
                self.assertGreater(max(abs(v) for v in track.buf), 0.01, f"{key} が無音")

    def test_generate_all_writes_files_once(self):
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d)
            made = tones.generate_all(dest)
            self.assertEqual(sorted(made), sorted(tones.BUILTINS))
            for key in tones.BUILTINS:
                self.assertTrue((dest / f"{key}.wav").exists())
            # 2 回目は既存を作り直さない
            self.assertEqual(tones.generate_all(dest), [])
            # force なら作り直す
            self.assertEqual(len(tones.generate_all(dest, force=True)), len(tones.BUILTINS))


if __name__ == "__main__":
    unittest.main()


class TestCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = []

    def tearDown(self):
        self.tmp.cleanup()

    def run_main(self, argv):
        import io
        from contextlib import redirect_stdout
        with redirect_stdout(io.StringIO()) as f:
            rc = tones.main(argv)
        return rc, f.getvalue()

    def test_creates_missing_files(self):
        """1 種類だけに絞って速く確かめる。"""
        real = tones.BUILTINS
        tones.BUILTINS = {"ding": real["ding"]}
        try:
            rc, out = self.run_main([self.tmp.name])
            self.assertEqual(rc, 0)
            self.assertIn("ding", out)
            self.assertTrue((Path(self.tmp.name) / "ding.wav").exists())
            _rc, again = self.run_main([self.tmp.name])
            self.assertIn("すべて揃っています", again)
            _rc, forced = self.run_main([self.tmp.name, "--force"])
            self.assertIn("ding", forced)
        finally:
            tones.BUILTINS = real

    def test_entry_point(self):
        import io
        import runpy
        from contextlib import redirect_stdout
        real_argv = sys.argv
        # すでに揃っている本物の置き場を渡すので、作り直しは起きない
        sys.argv = ["tones", str(Path(__file__).resolve().parent.parent / "sounds" / "builtin")]
        try:
            with redirect_stdout(io.StringIO()) as f, self.assertRaises(SystemExit) as cm:
                runpy.run_module("sounder.tones", run_name="__main__")
        finally:
            sys.argv = real_argv
        self.assertEqual(cm.exception.code, 0)
        self.assertIn("すべて揃っています", f.getvalue())
