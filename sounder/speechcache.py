"""読み上げた音声の置き場（data/tts-cache）。

声・速さ・文章が同じなら、前に作った WAV をそのまま鳴らす（合成しなおさない）。
鍵は「声・速さ・文章」の SHA-256（声の名前は "qwen:…" "aivis:…" "say:…" で種類ごとに分かれる）。
鳴らすたびにファイルの更新時刻を今にして、30 日使われなかったものは消す（見回りは 1 日 1 回）。
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from pathlib import Path

MAX_AGE_DAYS = 30
PRUNE_EVERY = 24 * 3600.0


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
        tmp.write_bytes(wav)
        os.replace(tmp, path)
        self.prune()
        return path

    def adopt(self, path: Path) -> Path:
        """別の手段（say -o など）で path に書いたものを置き場に入れたことにする。"""
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
