"""アイコン生成のテスト。"""

import io
import struct
import sys
import tempfile
import unittest
import zlib
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sounder import icon  # noqa: E402

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def read_png(data: bytes):
    """幅・高さ・RGBA の画素を取り出す（テスト用の最小の読み取り）。"""
    assert data[:8] == PNG_MAGIC
    pos, width, height, idat = 8, 0, 0, b""
    while pos < len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        tag = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        if tag == b"IHDR":
            width, height, depth, color = struct.unpack(">IIBB", body[:10])
            assert (depth, color) == (8, 6), "8bit RGBA のはず"
        elif tag == b"IDAT":
            idat += body
        pos += 12 + length
    raw = zlib.decompress(idat)
    stride = width * 4
    rows = []
    for y in range(height):
        start = y * (stride + 1)
        assert raw[start] == 0, "フィルタは 0 のはず"
        rows.append(raw[start + 1:start + 1 + stride])
    return width, height, rows


class TestShape(unittest.TestCase):
    def test_bell_inside_and_outside(self):
        self.assertEqual(icon._bell_alpha(0.5, 0.45), 1.0, "本体の中")
        self.assertEqual(icon._bell_alpha(0.5, 0.215), 1.0, "持ち手")
        self.assertEqual(icon._bell_alpha(0.5, 0.68), 1.0, "裾のふち")
        self.assertEqual(icon._bell_alpha(0.5, 0.762), 1.0, "振り子")
        self.assertEqual(icon._bell_alpha(0.02, 0.5), 0.0, "左端は外")
        self.assertEqual(icon._bell_alpha(0.5, 0.05), 0.0, "上端は外")
        self.assertEqual(icon._bell_alpha(0.5, 0.95), 0.0, "下端は外")
        self.assertEqual(icon._bell_alpha(0.06, 0.70), 0.0, "ふちの外側")
        self.assertEqual(icon._bell_alpha(0.03, 0.66), 0.0, "ふちの角の外")

    def test_bell_widens_toward_the_bottom(self):
        self.assertEqual(icon._bell_alpha(0.5 - 0.30, 0.62), 1.0)
        self.assertEqual(icon._bell_alpha(0.5 - 0.30, 0.30), 0.0)

    def test_rounded_corners(self):
        self.assertEqual(icon._rounded(0.5, 0.5, 0.22), 1.0, "真ん中")
        self.assertEqual(icon._rounded(0.5, 0.02, 0.22), 1.0, "辺の上")
        self.assertEqual(icon._rounded(0.01, 0.01, 0.22), 0.0, "角の外")
        self.assertEqual(icon._rounded(0.15, 0.15, 0.22), 1.0, "角の内側")


class TestRender(unittest.TestCase):
    def test_png_is_well_formed(self):
        data = icon.render(24)
        w, h, rows = read_png(data)
        self.assertEqual((w, h), (24, 24))
        self.assertEqual(len(rows), 24)

    def test_colors(self):
        w, _h, rows = icon.render(24) and read_png(icon.render(24))

        def px(x, y):
            return tuple(rows[y][x * 4:x * 4 + 4])

        self.assertEqual(px(12, 11)[:3], icon.FG, "ベルは明るい色")
        self.assertEqual(px(2, 12)[:3], icon.BG, "左端は背景色")
        self.assertEqual(px(0, 0), (255, 255, 255, 255), "角の外は白で埋める")
        self.assertTrue(all(p[3] == 255 for p in [px(0, 0), px(12, 12)]), "不透明")

    def test_transparent_corners(self):
        _w, _h, rows = read_png(icon.render(24, transparent=True))
        self.assertEqual(rows[0][3], 0, "角は透明")
        self.assertEqual(rows[12][12 * 4 + 3], 255, "中心は不透明")


class TestCli(unittest.TestCase):
    def test_writes_the_files(self):
        real = icon.SIZES
        icon.SIZES = ((16, "icon-16.png", False),)   # 速く回すため小さく
        try:
            with tempfile.TemporaryDirectory() as d:
                out = Path(d) / "web"
                with redirect_stdout(io.StringIO()) as f:
                    self.assertEqual(icon.main([str(out)]), 0)
                self.assertTrue((out / "icon-16.png").exists())
                self.assertIn("icon-16.png", f.getvalue())
        finally:
            icon.SIZES = real

    def test_entry_point(self):
        import runpy
        real_sizes, real_argv = icon.SIZES, sys.argv
        icon.SIZES = ((8, "icon-8.png", False),)
        try:
            with tempfile.TemporaryDirectory() as d:
                sys.argv = ["icon", d]
                with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as cm:
                    runpy.run_module("sounder.icon", run_name="__main__")
                self.assertEqual(cm.exception.code, 0)
        finally:
            icon.SIZES, sys.argv = real_sizes, real_argv


if __name__ == "__main__":
    unittest.main()


class TestFavicon(unittest.TestCase):
    def test_background_is_transparent_and_bell_is_accent(self):
        w, h, rows = read_png(icon.render_glyph(32))
        self.assertEqual((w, h), (32, 32))
        px = lambda x, y: tuple(rows[y][x * 4:x * 4 + 4])  # noqa: E731
        self.assertEqual(px(0, 0)[3], 0, "角は透明")
        self.assertEqual(px(31, 31)[3], 0, "角は透明")
        self.assertEqual(px(16, 14), icon.BG + (255,), "ベルの中はアクセント色で不透明")
