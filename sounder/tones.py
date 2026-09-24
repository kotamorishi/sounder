"""内蔵チャイム／メロディーの生成（標準ライブラリのみ）。

外部依存なしで WAV を合成する。初回起動時に sounds/builtin/ へ書き出す。
"""

from __future__ import annotations

import array
import math
import struct
import wave
from pathlib import Path

SAMPLE_RATE = 44100

# 音名 -> 周波数（A4 = 440Hz）
_NOTE_OFFSETS = {"C": -9, "D": -7, "E": -5, "F": -4, "G": -2, "A": 0, "B": 2}


def note(name: str) -> float:
    """'A4' 'C#5' 'Bb3' のような音名を周波数に変換する。"""
    letter = name[0].upper()
    idx = 1
    semi = _NOTE_OFFSETS[letter]
    while idx < len(name) and name[idx] in "#b":
        semi += 1 if name[idx] == "#" else -1
        idx += 1
    octave = int(name[idx:])
    return 440.0 * (2.0 ** ((semi + (octave - 4) * 12) / 12.0))


class Track:
    """秒単位の位置に音を重ねていくモノラルのミキサ。"""

    def __init__(self) -> None:
        self.buf: array.array = array.array("d")

    def _ensure(self, n: int) -> None:
        if len(self.buf) < n:
            self.buf.extend([0.0] * (n - len(self.buf)))

    def add(self, at: float, samples) -> None:
        start = int(at * SAMPLE_RATE)
        self._ensure(start + len(samples))
        buf = self.buf
        for i, v in enumerate(samples):
            buf[start + i] += v

    def duration(self) -> float:
        return len(self.buf) / SAMPLE_RATE

    def normalized(self, peak: float = 0.86):
        if not self.buf:
            return array.array("d")
        hi = max(abs(v) for v in self.buf)
        if hi <= 1e-9:
            return self.buf
        gain = peak / hi
        return array.array("d", (v * gain for v in self.buf))

    def write(self, path: Path, peak: float = 0.86) -> None:
        data = self.normalized(peak)
        frames = array.array("h", (int(max(-1.0, min(1.0, v)) * 32767) for v in data))
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(frames.tobytes())


# --- 音色 ------------------------------------------------------------------

# (倍率, 音量) の並び。金属的な響きを作るため非整数倍音も混ぜる。
BELL = ((1.0, 1.0), (2.0, 0.42), (3.0, 0.16), (4.2, 0.09), (5.4, 0.05))
TUBE = ((1.0, 1.0), (2.76, 0.28), (5.4, 0.12), (8.9, 0.05))
SOFT = ((1.0, 1.0), (2.0, 0.22), (3.0, 0.06))
MARIMBA = ((1.0, 1.0), (4.0, 0.3), (9.2, 0.08))
ORGAN = ((1.0, 1.0), (2.0, 0.5), (3.0, 0.25), (4.0, 0.12))


def tone(freq: float, dur: float, amp: float = 1.0, decay: float = 3.2,
         timbre=BELL, attack: float = 0.006, vibrato: float = 0.0):
    """減衰する打弦／打鐘系の音を 1 つ作る。"""
    n = int(dur * SAMPLE_RATE)
    out = array.array("d", [0.0] * n)
    atk = max(1, int(attack * SAMPLE_RATE))
    for mult, mamp in timbre:
        f = freq * mult
        if f > SAMPLE_RATE * 0.45:
            continue
        # 高次倍音は速く減衰させると自然に聞こえる
        d = decay * (1.0 + 0.55 * (mult - 1.0))
        w = 2.0 * math.pi * f / SAMPLE_RATE
        for i in range(n):
            env = math.exp(-d * i / SAMPLE_RATE)
            if i < atk:
                env *= i / atk
            ph = w * i
            if vibrato:
                ph += vibrato * math.sin(2.0 * math.pi * 5.0 * i / SAMPLE_RATE)
            out[i] += amp * mamp * env * math.sin(ph)
    return out


def beep(freq: float, dur: float, amp: float = 1.0, timbre=ORGAN):
    """立ち上がり／終わりだけを丸めた矩形に近い電子音。"""
    n = int(dur * SAMPLE_RATE)
    out = array.array("d", [0.0] * n)
    edge = max(1, int(0.008 * SAMPLE_RATE))
    for mult, mamp in timbre:
        f = freq * mult
        if f > SAMPLE_RATE * 0.45:
            continue
        w = 2.0 * math.pi * f / SAMPLE_RATE
        for i in range(n):
            env = 1.0
            if i < edge:
                env = i / edge
            elif i > n - edge:
                env = max(0.0, (n - i) / edge)
            out[i] += amp * mamp * env * math.sin(w * i)
    return out


# --- 内蔵サウンド定義 ------------------------------------------------------

def _doorbell(times: int = 1) -> Track:
    t = Track()
    at = 0.0
    for _ in range(times):
        t.add(at, tone(note("E5"), 1.4, 1.0, decay=3.0, timbre=TUBE))
        t.add(at + 0.42, tone(note("C5"), 1.9, 1.0, decay=2.4, timbre=TUBE))
        at += 2.1
    return t


def _ding() -> Track:
    t = Track()
    t.add(0.0, tone(note("A5"), 1.8, 1.0, decay=2.6, timbre=BELL))
    return t


def _bell_soft() -> Track:
    t = Track()
    t.add(0.0, tone(note("F5"), 2.6, 1.0, decay=1.7, timbre=SOFT))
    t.add(0.02, tone(note("C6"), 2.2, 0.45, decay=2.2, timbre=SOFT))
    return t


def _chime_up() -> Track:
    t = Track()
    for i, nm in enumerate(["C5", "E5", "G5", "C6"]):
        t.add(i * 0.2, tone(note(nm), 2.2, 0.9, decay=2.2, timbre=MARIMBA))
    return t


def _chime_down() -> Track:
    t = Track()
    for i, nm in enumerate(["C6", "G5", "E5", "C5"]):
        t.add(i * 0.2, tone(note(nm), 2.2, 0.9, decay=2.2, timbre=MARIMBA))
    return t


def _westminster() -> Track:
    t = Track()
    for i, nm in enumerate(["E5", "C5", "D5", "G4"]):
        t.add(i * 0.62, tone(note(nm), 3.2, 1.0, decay=1.25, timbre=TUBE))
    return t


def _cuckoo() -> Track:
    t = Track()
    for k in range(2):
        at = k * 1.1
        t.add(at, tone(note("G5"), 0.4, 0.9, decay=7.0, timbre=SOFT))
        t.add(at + 0.26, tone(note("E5"), 0.55, 0.85, decay=6.0, timbre=SOFT))
    return t


def _melody_morning() -> Track:
    """やわらかい上昇アルペジオ。起床・開始の合図向け。"""
    t = Track()
    seq = [("C5", 0.0), ("E5", 0.22), ("G5", 0.44), ("B5", 0.66),
           ("A5", 0.95), ("G5", 1.17), ("E5", 1.39), ("G5", 1.68), ("C6", 1.9)]
    for nm, at in seq:
        t.add(at, tone(note(nm), 2.4, 0.85, decay=2.0, timbre=MARIMBA))
    t.add(1.9, tone(note("C4"), 3.0, 0.35, decay=1.2, timbre=SOFT))
    return t


def _melody_relax() -> Track:
    """ゆったりした 5 音音階のメロディー。休憩・就寝の合図向け。"""
    t = Track()
    seq = [("F4", 0.0), ("A4", 0.45), ("C5", 0.9), ("D5", 1.35),
           ("C5", 1.9), ("A4", 2.35), ("F4", 2.8)]
    for nm, at in seq:
        t.add(at, tone(note(nm), 3.2, 0.8, decay=1.15, timbre=SOFT))
    return t


def _melody_notice() -> Track:
    """短い 3 音の通知音。"""
    t = Track()
    for i, nm in enumerate(["G5", "C6", "E6"]):
        t.add(i * 0.13, tone(note(nm), 1.6, 0.85, decay=3.4, timbre=MARIMBA))
    return t


def _alarm() -> Track:
    """少し急かす繰り返しビープ。出発直前の合図向け。"""
    t = Track()
    at = 0.0
    for _ in range(3):
        for k in range(4):
            t.add(at + k * 0.16, beep(note("A5") if k % 2 == 0 else note("E5"), 0.11, 0.8))
        at += 1.0
    return t


def _beep3() -> Track:
    t = Track()
    for k in range(3):
        t.add(k * 0.18, beep(note("C6"), 0.1, 0.8))
    return t


def _count_in() -> Track:
    """カチ・カチ・カチ・ポーン（出発 5 秒前のカウントダウン風）。"""
    t = Track()
    for k in range(3):
        t.add(k * 0.45, tone(note("C6"), 0.3, 0.5, decay=14.0, timbre=SOFT))
    t.add(1.45, tone(note("G5"), 2.2, 1.0, decay=2.0, timbre=TUBE))
    return t


BUILTINS: dict[str, tuple[str, callable]] = {
    "doorbell": ("ピンポーン（チャイム）", lambda: _doorbell(1)),
    "doorbell2": ("ピンポーン×2", lambda: _doorbell(2)),
    "ding": ("ピーン（単音）", _ding),
    "bell_soft": ("やわらかいベル", _bell_soft),
    "chime_up": ("上昇チャイム", _chime_up),
    "chime_down": ("下降チャイム", _chime_down),
    "westminster": ("ウェストミンスター（時報）", _westminster),
    "cuckoo": ("カッコウ", _cuckoo),
    "melody_morning": ("朝のメロディー", _melody_morning),
    "melody_relax": ("やさしいメロディー", _melody_relax),
    "melody_notice": ("通知メロディー", _melody_notice),
    "alarm": ("アラーム（急かす）", _alarm),
    "beep3": ("ピピピ", _beep3),
    "count_in": ("カウントダウン", _count_in),
}


def generate_all(dest: Path, force: bool = False) -> list[str]:
    """内蔵サウンドを dest に WAV として書き出す。作成したものの一覧を返す。"""
    made = []
    for key, (_label, fn) in BUILTINS.items():
        path = dest / f"{key}.wav"
        if force or not path.exists():
            fn().write(path)
            made.append(key)
    return made


if __name__ == "__main__":
    import sys
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "sounds/builtin")
    print("generated:", ", ".join(generate_all(out, force=True)) or "(none)")
