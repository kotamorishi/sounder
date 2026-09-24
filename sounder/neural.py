"""機械学習の読み上げ（Qwen3-TTS・AivisSpeech）への橋渡し。

どちらのエンジンも別プロセスで常駐させ、127.0.0.1 の HTTP で WAV を受け取る。
sounder 本体は標準ライブラリのまま。
  - Qwen3-TTS   … tts/qwen_server.py（専用の venv）。声の名前は "qwen:ono_anna"
  - AivisSpeech … AivisSpeech.app の中のエンジン。声の名前は "aivis:<スタイル id>"
同じ文章・声・速さの組み合わせは data/tts-cache に取っておき、2 回目からはすぐ鳴らす。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PREFIX = "qwen:"
AIVIS_PREFIX = "aivis:"
DEFAULT_URL = "http://127.0.0.1:8778"
AIVIS_URL = "http://127.0.0.1:10101"
CACHE_LIMIT = 300
# say の話す速さ（1 分あたりの語数）のうち、AivisSpeech の等速にあたる値
BASE_RATE = 180
# 話者ごとの母語。その他（serena, vivian など）は中国語
LOCALES = {"ono_anna": "ja_JP", "ryan": "en_US", "aiden": "en_US", "sohee": "ko_KR"}
KANA = re.compile(r"[぀-ヿ]")
CJK = re.compile(r"[一-鿿]")


def is_neural(voice: str) -> bool:
    return (voice or "").startswith((PREFIX, AIVIS_PREFIX))


def language_of(text: str) -> str:
    """かなを含めば日本語、漢字だけならモデル任せ、それ以外は英語として読ませる。"""
    if KANA.search(text):
        return "japanese"
    if CJK.search(text):
        return "auto"
    return "english"


def label_of(speaker: str) -> str:
    return speaker.replace("_", " ").title() + "（Qwen3-TTS）"


class _Engine:
    """HTTP で WAV を返すエンジンの共通部分（キャッシュと失敗時の扱い）。"""

    prefix = ""
    title = ""

    def __init__(self, cache_dir: Path, *, url: str, log=None) -> None:
        self.cache_dir = cache_dir
        self.url = url.rstrip("/")
        self._log = log or (lambda *a, **k: None)

    def handles(self, voice: str) -> bool:
        return (voice or "").startswith(self.prefix)

    def _get_json(self, path: str):
        with urllib.request.urlopen(f"{self.url}{path}", timeout=1.0) as r:
            return json.load(r)

    def cache_path(self, text: str, voice: str, rate: int | None = None) -> Path:
        key = hashlib.sha256(f"{voice}\n{rate or ''}\n{text}".encode()).hexdigest()[:32]
        return self.cache_dir / f"{key}.wav"

    def _cache_rate(self, rate: int | None) -> int | None:
        """速さが音声に効くエンジンだけ、キャッシュの鍵に速さを入れる。"""
        return None

    def render(self, text: str, voice: str, rate: int | None = None) -> Path | None:
        """WAV のパスを返す。作れなければ None（呼び出し側が say に切り替える）。"""
        path = self.cache_path(text, voice, self._cache_rate(rate))
        if path.is_file():
            return path
        try:
            wav = self._synthesize(text, voice, rate)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:200]
            self._log("error", f"{self.title} で読み上げを作れませんでした: {detail}")
            return None
        except (OSError, ValueError) as exc:
            self._log("error", f"{self.title} に接続できません（{exc}）。標準の声で読み上げます")
            return None
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        # 先読みと本番が同時に作っても壊れないよう、一時ファイルはスレッドごとに分ける
        tmp = path.with_suffix(f".{threading.get_ident()}.part")
        tmp.write_bytes(wav)
        os.replace(tmp, path)
        self._prune()
        return path

    def _synthesize(self, text: str, voice: str, rate: int | None) -> bytes:
        raise NotImplementedError

    def _prune(self) -> None:
        files = sorted(self.cache_dir.glob("*.wav"), key=lambda p: p.stat().st_mtime)
        for p in files[:-CACHE_LIMIT]:
            try:
                p.unlink()
            except OSError:
                pass


class NeuralTTS(_Engine):
    """Qwen3-TTS（tts/qwen_server.py）。話す速さは変えられない。"""

    prefix = PREFIX
    title = "Qwen3-TTS"

    def __init__(self, cache_dir: Path, *, url: str = DEFAULT_URL, log=None) -> None:
        super().__init__(cache_dir, url=url, log=log)

    def voices(self) -> list[dict]:
        """サーバが動いていれば、その話者を声の一覧の形で返す。止まっていれば空。"""
        try:
            speakers = self._get_json("/speakers").get("speakers") or []
        except (OSError, ValueError, AttributeError):
            return []
        out = [{"name": PREFIX + s, "label": label_of(s), "locale": LOCALES.get(s, "zh_CN")}
               for s in speakers]
        out.sort(key=lambda v: (not v["locale"].startswith("ja"), v["name"]))
        return out

    def _synthesize(self, text: str, voice: str, rate: int | None) -> bytes:
        body = json.dumps({"text": text, "speaker": voice[len(PREFIX):],
                           "language": language_of(text)}).encode()
        req = urllib.request.Request(f"{self.url}/synthesize", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.read()


class AivisTTS(_Engine):
    """AivisSpeech Engine（VOICEVOX 互換の API）。日本語専用。話す速さも反映する。"""

    prefix = AIVIS_PREFIX
    title = "AivisSpeech"

    def __init__(self, cache_dir: Path, *, url: str = AIVIS_URL, log=None) -> None:
        super().__init__(cache_dir, url=url, log=log)

    def voices(self) -> list[dict]:
        """話者 × スタイル（ノーマル・あまあま など）を 1 つずつの声として返す。"""
        try:
            speakers = self._get_json("/speakers")
        except (OSError, ValueError):
            return []
        out = []
        for sp in speakers if isinstance(speakers, list) else []:
            for st in sp.get("styles") or []:
                out.append({"name": f"{AIVIS_PREFIX}{st['id']}",
                            "label": f"{sp['name']}・{st['name']}（AivisSpeech）",
                            "locale": "ja_JP"})
        return out

    def _cache_rate(self, rate: int | None) -> int | None:
        return int(rate or BASE_RATE)

    def _synthesize(self, text: str, voice: str, rate: int | None) -> bytes:
        style = voice[len(AIVIS_PREFIX):]
        q = urllib.parse.urlencode({"text": text, "speaker": style})
        req = urllib.request.Request(f"{self.url}/audio_query?{q}", method="POST")
        with urllib.request.urlopen(req, timeout=60) as r:
            query = json.load(r)
        # say の速さ（180 が普通）を、AivisSpeech の倍率（1.0 が普通）に置き換える
        query["speedScale"] = round(min(2.0, max(0.5, (rate or BASE_RATE) / BASE_RATE)), 2)
        req = urllib.request.Request(f"{self.url}/synthesis?speaker={urllib.parse.quote(style)}",
                                     data=json.dumps(query).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.read()
