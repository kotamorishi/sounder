"""読み上げた音声の置き場（data/tts-cache）。

声・速さ・文章が同じなら、前に作った WAV をそのまま鳴らす（合成しなおさない）。
鍵は「声・速さ・文章」の SHA-256（声の名前は "qwen:…" "aivis:…" "say:…" で種類ごとに分かれる）。
鳴らすたびにファイルの更新時刻を今にして、30 日使われなかったものは消す（見回りは 1 日 1 回）。

読み上げの声は、エンジンや文によって音の大きさがかなりばらつき、チャイムより 5〜15dB 小さいことが多い。
取っておくときに音の大きさ（RMS）をチャイムと同じくらい（TARGET_DBFS）にそろえる（16bit PCM の WAV のみ）。
"""

from __future__ import annotations

import array
import hashlib
import io
import math
import os
import sys
import threading
import time
import wave
from pathlib import Path

MAX_AGE_DAYS = 30
PRUNE_EVERY = 24 * 3600.0
# 内蔵チャイムの大きさ（-15〜-17 dBFS）にそろえる。ピークは 0.95 を超えないようにする
TARGET_DBFS = -16.0
PEAK_LIMIT = 0.95


def normalize_wav(data: bytes) -> bytes:
    """16bit PCM の WAV を、目標の大きさにそろえて返す。読めない形式ならそのまま返す。"""
    try:
        with wave.open(io.BytesIO(data)) as w:
            params = w.getparams()
            frames = w.readframes(w.getnframes())
    except (wave.Error, EOFError):
        return data
    if params.sampwidth != 2 or not frames:
        return data
    samples = array.array("h", frames)
    if sys.byteorder == "big":
        samples.byteswap()
    peak = max(abs(x) for x in samples) or 1
    rms = math.sqrt(sum(x * x for x in samples) / len(samples)) or 1.0
    gain = min(32768 * 10 ** (TARGET_DBFS / 20) / rms, PEAK_LIMIT * 32767 / peak)
    if abs(gain - 1.0) < 0.05:
        return data
    out = array.array("h", (max(-32768, min(32767, round(x * gain))) for x in samples))
    if sys.byteorder == "big":
        out.byteswap()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setparams(params)
        w.writeframes(out.tobytes())
    return buf.getvalue()


class SpeechCache:
    def __init__(self, directory: Path, *, max_age_days: float = MAX_AGE_DAYS) -> None:
        self.dir = directory
        self.max_age = max_age_days * 86400.0
        self._last_prune = 0.0
        self._lock = threading.Lock()

    def path(self, voice: str, rate, text: str) -> Path:
        key = hashlib.sha256(f"{voice}\n{rate or ''}\n{text}".encode()).hexdigest()[:32]
        return self.dir / f"{key}.wav"

    def get(self, path: Path) -> Path | None:
        """あれば使ったことにして（更新時刻を今に）返す。"""
        try:
            os.utime(path)
        except OSError:
            return None
        return path

    def put(self, path: Path, wav: bytes) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        # 先読みと本番が同時に作っても壊れないよう、一時ファイルはスレッドごとに分ける
        tmp = path.with_suffix(f".{threading.get_ident()}.part")
        tmp.write_bytes(normalize_wav(wav))
        os.replace(tmp, path)
        self.prune()
        return path

    def adopt(self, path: Path) -> Path:
        """別の手段（say -o など）で path に書いたものを置き場に入れたことにする（大きさもそろえる）。"""
        try:
            data = path.read_bytes()
            fixed = normalize_wav(data)
            if fixed is not data:
                tmp = path.with_suffix(f".{threading.get_ident()}.part")
                tmp.write_bytes(fixed)
                os.replace(tmp, path)
        except OSError:
            pass
        self.prune()
        return path

    def prune(self, *, force: bool = False) -> int:
        """30 日使われなかったものを消す。消した数を返す。"""
        now = time.time()
        with self._lock:
            if not force and now - self._last_prune < PRUNE_EVERY:
                return 0
            self._last_prune = now
        removed = 0
        for p in self.dir.glob("*.wav"):
            try:
                if now - p.stat().st_mtime > self.max_age:
                    p.unlink()
                    removed += 1
            except OSError:
                pass
        return removed
