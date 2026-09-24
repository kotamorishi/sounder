"""アプリのアイコン（PNG）を生成する。標準ライブラリだけで描く。

ホーム画面に追加したときに、ページのスクリーンショットではなく
ちゃんとしたアイコンが出るようにするためのもの。

    python3 -m sounder.icon web
"""

from __future__ import annotations

import math
import struct
import sys
import zlib
from pathlib import Path

# 画面の accent と同じ色
BG = (180, 85, 31)
FG = (255, 252, 248)
SAMPLES = 3  # 1 ピクセルあたりの supersampling（ギザギザを消す）


def _bell_alpha(u: float, v: float) -> float:
    """正規化座標 (0〜1) がベルの内側なら 1、外なら 0。"""
    x = u - 0.5
    # 持ち手（上の小さな円）
    if (x * x + (v - 0.212) ** 2) <= 0.032 ** 2:
        return 1.0
    # 本体（上は丸い肩、下はゆるやかに広がる裾）
    top, bottom = 0.235, 0.655
    if top <= v <= bottom:
        t = (v - top) / (bottom - top)
        if t < 0.38:                      # 肩は円弧で丸く
            k = (0.38 - t) / 0.38
            half = 0.062 + 0.205 * math.sqrt(max(0.0, 1.0 - k * k))
        else:                             # 裾はわずかに反らせて広げる
            k = (t - 0.38) / 0.62
            half = 0.267 + 0.078 * math.pow(k, 2.1)
        if abs(x) <= half:
            return 1.0
    # 裾のふち
    if bottom <= v <= bottom + 0.060 and abs(x) <= 0.360:
        r = 0.031
        # 角を丸める
        if abs(x) <= 0.360 - r:
            return 1.0
        cx = 0.360 - r
        cy = bottom + r if v < bottom + r else bottom + 0.060 - r
        if (abs(x) - cx) ** 2 + (v - cy) ** 2 <= r * r:
            return 1.0
    # 振り子
    if (x * x + (v - 0.762) ** 2) <= 0.050 ** 2:
        return 1.0
    return 0.0


def _rounded(u: float, v: float, radius: float) -> float:
    """角を丸めた四角の内側なら 1。"""
    x = min(u, 1 - u)
    y = min(v, 1 - v)
    if x >= radius or y >= radius:
        return 1.0
    dx, dy = radius - x, radius - y
    return 1.0 if dx * dx + dy * dy <= radius * radius else 0.0


def render(size: int, *, radius: float = 0.22, transparent: bool = False) -> bytes:
    """size×size の PNG を作って返す。"""
    rows = []
    step = 1.0 / (size * SAMPLES)
    for py in range(size):
        row = bytearray()
        for px in range(size):
            bell = bg = 0.0
            for sy in range(SAMPLES):
                v = (py * SAMPLES + sy + 0.5) * step
                for sx in range(SAMPLES):
                    u = (px * SAMPLES + sx + 0.5) * step
                    inside = _rounded(u, v, radius)
                    bg += inside
                    bell += inside * _bell_alpha(u, v)
            n = SAMPLES * SAMPLES
            bg /= n
            bell /= n
            if transparent:
                alpha = bg
            else:
                alpha = 1.0
            # 背景色の上にベルを乗せる
            rgb = tuple(
                round(BG[i] * (1 - bell) + FG[i] * bell) if bg > 0 else 0
                for i in range(3)
            )
            a = round(255 * alpha * (bg if transparent else 1.0)) if transparent else 255
            if not transparent and bg < 1.0:
                # 角の外側は白で埋める（不透明アイコン向け）
                rgb = tuple(round(rgb[i] * bg + 255 * (1 - bg)) for i in range(3))
            row += bytes((rgb[0], rgb[1], rgb[2], a))
        rows.append(bytes(row))
    return _png(size, size, rows)


def _png(width: int, height: int, rows: list[bytes]) -> bytes:
    raw = b"".join(b"\x00" + r for r in rows)   # フィルタ種別 0
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)  # 8bit RGBA
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


SIZES = ((180, "icon-180.png", False), (512, "icon-512.png", False),
         (192, "icon-192.png", False))


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    dest = Path(argv[0] if argv else "web")
    dest.mkdir(parents=True, exist_ok=True)
    for size, name, transparent in SIZES:
        (dest / name).write_bytes(render(size, transparent=transparent))
    print("作成:", "、".join(name for _s, name, _t in SIZES))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
