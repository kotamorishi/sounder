"""機械学習の読み上げ（Qwen3-TTS）への橋渡し。

モデルは別プロセス（tts/qwen_server.py、専用の venv）で常駐させ、
127.0.0.1 の HTTP で WAV を受け取る。sounder 本体は標準ライブラリのまま。
同じ文章と声の組み合わせは data/tts-cache に取っておき、2 回目からはすぐ鳴らす。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import urllib.error
import urllib.request
from pathlib import Path

PREFIX = "qwen:"
DEFAULT_URL = "http://127.0.0.1:8778"
CACHE_LIMIT = 300
# 話者ごとの母語。その他（serena, vivian など）は中国語
LOCALES = {"ono_anna": "ja_JP", "ryan": "en_US", "aiden": "en_US", "sohee": "ko_KR"}
KANA = re.compile(r"[぀-ヿ]")
CJK = re.compile(r"[一-鿿]")


def is_neural(voice: str) -> bool:
    return (voice or "").startswith(PREFIX)


def language_of(text: str) -> str:
    """かなを含めば日本語、漢字だけならモデル任せ、それ以外は英語として読ませる。"""
    if KANA.search(text):
        return "japanese"
    if CJK.search(text):
        return "auto"
    return "english"


def label_of(speaker: str) -> str:
    return speaker.replace("_", " ").title() + "（Qwen3-TTS）"


class NeuralTTS:
    def __init__(self, cache_dir: Path, *, url: str = DEFAULT_URL, log=None) -> None:
        self.cache_dir = cache_dir
        self.url = url.rstrip("/")
        self._log = log or (lambda *a, **k: None)

    def voices(self) -> list[dict]:
        """サーバが動いていれば、その話者を声の一覧の形で返す。止まっていれば空。"""
        try:
            with urllib.request.urlopen(f"{self.url}/speakers", timeout=1.0) as r:
                speakers = json.load(r).get("speakers") or []
        except (OSError, ValueError):
            return []
        out = [{"name": PREFIX + s, "label": label_of(s), "locale": LOCALES.get(s, "zh_CN")}
               for s in speakers]
        out.sort(key=lambda v: (not v["locale"].startswith("ja"), v["name"]))
        return out

    def cache_path(self, text: str, voice: str) -> Path:
        key = hashlib.sha256(f"{voice}\n{text}".encode()).hexdigest()[:32]
        return self.cache_dir / f"{key}.wav"

    def render(self, text: str, voice: str) -> Path | None:
        """WAV のパスを返す。作れなければ None（呼び出し側が say に切り替える）。"""
        path = self.cache_path(text, voice)
        if path.is_file():
            return path
        body = json.dumps({"text": text, "speaker": voice[len(PREFIX):],
                           "language": language_of(text)}).encode()
        req = urllib.request.Request(f"{self.url}/synthesize", data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                wav = r.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:200]
            self._log("error", f"Qwen3-TTS で読み上げを作れませんでした: {detail}")
            return None
        except OSError as exc:
            self._log("error", f"Qwen3-TTS に接続できません（{exc}）。標準の声で読み上げます")
            return None
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        # 先読みと本番が同時に作っても壊れないよう、一時ファイルはスレッドごとに分ける
        tmp = path.with_suffix(f".{threading.get_ident()}.part")
        tmp.write_bytes(wav)
        os.replace(tmp, path)
        self._prune()
        return path

    def _prune(self) -> None:
        files = sorted(self.cache_dir.glob("*.wav"), key=lambda p: p.stat().st_mtime)
        for p in files[:-CACHE_LIMIT]:
            try:
                p.unlink()
            except OSError:
                pass
