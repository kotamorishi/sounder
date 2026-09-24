"""効果音ラボの取り込みと、効果音・ランダム再生の指定のテスト（ネットワークには出ない）。"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sounder import effects  # noqa: E402
from sounder.player import SoundNotFound  # noqa: E402
from tests.test_player import Base as PlayerBase  # noqa: E402

PAGE = """<main role="main"><h1>ボタン・システム音[1]</h1><div id="s"><ul>
<li><span>決定ボタンを押す1</span>ピッ。アプリなどに<a href="mp3/decision1.mp3" download="a.mp3"><img></a></li>
<li><span>決定ボタンを押す2</span>電子音<a href="mp3/decision2.mp3" download="b.mp3"><img></a></li>
<li><span>警告音1</span>ビー<a href="mp3/warning1.mp3" download="c.mp3"><img></a></li>
</ul></div></main>"""


class TestParse(unittest.TestCase):
    def test_parse_page(self):
        title, items = effects.parse_page(PAGE)
        self.assertEqual(title, "ボタン・システム音")
        self.assertEqual(items[0], {"title": "決定ボタンを押す1", "desc": "ピッ。アプリなどに",
                                    "href": "mp3/decision1.mp3"})
        self.assertEqual(len(items), 3)

    def test_category_of(self):
        self.assertEqual(effects.category_of("https://soundeffect-lab.info/sound/button/"), "button")
        self.assertEqual(effects.category_of("https://soundeffect-lab.info/sound/voice/game.html"),
                         "game")

    def test_fetch_writes_files_and_manifest(self):
        got = {}

        def fake_get(url, referer=None):
            got[url] = referer
            return PAGE.encode() if url.endswith("/button/") else b"ID3mp3"

        orig = effects._get
        effects._get = fake_get
        self.addCleanup(setattr, effects, "_get", orig)
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            n = effects.fetch("https://soundeffect-lab.info/sound/button/", root,
                              out=lambda *a: None, delay=0)
            self.assertEqual(n, 3)
            m = json.loads((root / "button" / "manifest.json").read_text())
            self.assertEqual([f["file"] for f in m["files"]],
                             ["decision1.mp3", "decision2.mp3", "warning1.mp3"])
            # 取得済みは取り直さない
            self.assertEqual(effects.fetch("https://soundeffect-lab.info/sound/button/", root,
                                           out=lambda *a: None, delay=0), 0)
        self.assertEqual(got["https://soundeffect-lab.info/sound/button/mp3/decision1.mp3"],
                         "https://soundeffect-lab.info/sound/button/")


class TestEffectSounds(PlayerBase):
    def setUp(self):
        super().setUp()
        d = self.home / "effects" / "button"
        d.mkdir(parents=True)
        files = [("decision1.mp3", "決定ボタンを押す1"), ("decision2.mp3", "決定ボタンを押す2"),
                 ("warning1.mp3", "警告音1")]
        for f, _ in files:
            (d / f).write_bytes(b"ID3")
        (d / "manifest.json").write_text(json.dumps({
            "title": "ボタン・システム音",
            "files": [{"file": f, "title": t, "desc": ""} for f, t in files]
                     + [{"file": "missing.mp3", "title": "無い音", "desc": ""}],
        }, ensure_ascii=False))
        self.p.effects_dir = self.home / "effects"

    def test_effect_ref(self):
        path = self.p.resolve("effect:button/warning1.mp3")
        self.assertEqual(path.name, "warning1.mp3")
        with self.assertRaises(SoundNotFound):
            self.p.resolve("effect:button/missing.mp3")
        with self.assertRaises(SoundNotFound):
            self.p.resolve("effect:../builtin/ding.wav")

    def test_random_from_category_and_group(self):
        seen = {self.p.resolve("random:button").name for _ in range(60)}
        self.assertEqual(seen, {"decision1.mp3", "decision2.mp3", "warning1.mp3"})
        group = {self.p.resolve("random:button/決定ボタンを押す").name for _ in range(40)}
        self.assertEqual(group, {"decision1.mp3", "decision2.mp3"})
        with self.assertRaises(SoundNotFound):
            self.p.resolve("random:nothing")

    def test_library_lists_effects_and_random_choices(self):
        lib = self.p.library()
        self.assertEqual([e["ref"] for e in lib["effects"]],
                         ["effect:button/decision1.mp3", "effect:button/decision2.mp3",
                          "effect:button/warning1.mp3"])
        self.assertEqual([(r["ref"], r["label"]) for r in lib["random"]], [
            ("random:button", "ランダム・ボタン・システム音（3種）"),
            ("random:button/決定ボタンを押す", "ランダム・決定ボタンを押す（2種）"),
        ])

    def test_random_sound_plays(self):
        self.p.play_blocking({"type": "sound", "sound": "random:button"},
                             settings={"default_volume": 0.6})
        self.assertIn(str(self.home / "effects" / "button"), self.recorded()[0])

    def test_no_effects_downloaded(self):
        self.p.effects_dir = self.home / "nothing"
        lib = self.p.library()
        self.assertEqual((lib["effects"], lib["random"]), ([], []))


if __name__ == "__main__":
    unittest.main()
