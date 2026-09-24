"""機械学習の読み上げ（Qwen3-TTS・AivisSpeech）への橋渡し。

どちらのエンジンも別プロセスで常駐させ、127.0.0.1 の HTTP で WAV を受け取る。
sounder 本体は標準ライブラリのまま。
  - Qwen3-TTS   … tts/qwen_server.py（専用の venv）。声の名前は "qwen:ono_anna"
  - AivisSpeech … AivisSpeech.app の中のエンジン。声の名前は "aivis:<スタイル id>"
同じ文章・声・速さの組み合わせは data/tts-cache に取っておき、2 回目からはすぐ鳴らす。

エンジンはメモリを数 GB 使うので、しばらく使われなければ止め（sleep_if_idle）、
次に声が要るときに launchctl で起こす（ensure_up）。止まっている間も声の一覧は
最後に見えた内容を出し続ける（予定に選んだ声が「見つからない」にならないように）。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
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
    """HTTP で WAV を返すエンジンの共通部分（キャッシュ・失敗時の扱い・起こす／休ませる）。"""

    prefix = ""
    title = ""
    service = ""        # LaunchAgent のラベル（scripts/install-*.sh が登録する）
    health_path = "/"
    start_timeout = 180.0

    def __init__(self, cache_dir: Path, *, url: str, log=None) -> None:
        self.cache_dir = cache_dir
        self.url = url.rstrip("/")
        self._log = log or (lambda *a, **k: None)
        # SOUNDER_MANAGE_TTS=0 で起こす／休ませるをしない（テストが本物の LaunchAgent に触らないように）
        manage = os.environ.get("SOUNDER_MANAGE_TTS", "1") != "0"
        self.launchctl = shutil.which("launchctl") if manage else None
        self.last_used = time.monotonic()
        self._start_lock = threading.Lock()
        self._registered: tuple[float, bool] = (0.0, False)
        self._known: list[dict] | None = None

    def handles(self, voice: str) -> bool:
        return (voice or "").startswith(self.prefix)

    def _get_json(self, path: str):
        with urllib.request.urlopen(f"{self.url}{path}", timeout=1.0) as r:
            return json.load(r)

    # --- 声の一覧（止まっている間は最後に見えたもの） -------------------------

    def _fetch_voices(self) -> list[dict]:
        raise NotImplementedError

    def _known_path(self) -> Path:
        return self.cache_dir / f"voices-{self.prefix.rstrip(':')}.json"

    def voices(self) -> list[dict]:
        """動いていれば話者を声の一覧の形で返す。休ませている間は前回の一覧に asleep=True を付ける。"""
        try:
            live = self._fetch_voices()
        except (OSError, ValueError, AttributeError, TypeError):
            live = None
        if live is not None:
            if live != self._known:
                self._known = live
                try:
                    self.cache_dir.mkdir(parents=True, exist_ok=True)
                    self._known_path().write_text(json.dumps(live, ensure_ascii=False))
                except OSError:
                    pass
            return [dict(v, asleep=False) for v in live]
        if not self.registered():
            return []  # 自動起動に登録されていない（解除した）なら出さない
        if self._known is None:
            try:
                self._known = json.loads(self._known_path().read_text())
            except (OSError, ValueError):
                self._known = []
        return [dict(v, asleep=True) for v in self._known]

    # --- 起こす／休ませる ------------------------------------------------------

    def is_up(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self.url}{self.health_path}", timeout=1.0):
                return True
        except (OSError, ValueError):
            return False

    def _launchctl(self, *args: str) -> bool:
        if not self.launchctl or not self.service:
            return False
        target = f"gui/{os.getuid()}/{self.service}"
        try:
            r = subprocess.run([self.launchctl, *args, target], capture_output=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            return False
        return r.returncode == 0

    def registered(self) -> bool:
        """LaunchAgent に登録されているか（1 分だけ覚えておく）。"""
        checked, ok = self._registered
        if time.monotonic() - checked > 60:
            ok = self._launchctl("print")
            self._registered = (time.monotonic(), ok)
        return ok

    def ensure_up(self) -> bool:
        """動いていなければ起こして、応答するまで待つ。"""
        if self.is_up():
            return True
        if not self.registered():
            return False
        with self._start_lock:
            if self.is_up():
                return True
            self._log("info", f"{self.title} を起こしています")
            self._launchctl("kickstart")
            end = time.monotonic() + self.start_timeout
            while time.monotonic() < end:
                if self.is_up():
                    return True
                time.sleep(0.5)
        return False

    def sleep_if_idle(self, idle_seconds: float) -> bool:
        """最後に使ってから idle_seconds 過ぎていたら止める。止めたら True。"""
        if idle_seconds <= 0 or time.monotonic() - self.last_used < idle_seconds:
            return False
        if self._start_lock.locked() or not self.registered() or not self.is_up():
            return False
        self.voices()  # 止める前に声の一覧を覚えておく（休んでいる間も一覧に出すため）
        if self._launchctl("kill", "SIGTERM"):
            self._log("info", f"{self.title} をしばらく使っていないので休ませました（メモリを空けるため）")
            return True
        return False

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
        self.last_used = time.monotonic()
        if not self.ensure_up():
            self._log("error", f"{self.title} が動いていません。標準の声で読み上げます")
            return None
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
        self.last_used = time.monotonic()
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
    service = "com.local.sounder-tts"
    health_path = "/speakers"

    def __init__(self, cache_dir: Path, *, url: str = DEFAULT_URL, log=None) -> None:
        super().__init__(cache_dir, url=url, log=log)

    def _fetch_voices(self) -> list[dict]:
        speakers = self._get_json("/speakers").get("speakers") or []
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
    service = "com.local.sounder-aivis"
    health_path = "/version"

    def __init__(self, cache_dir: Path, *, url: str = AIVIS_URL, log=None) -> None:
        super().__init__(cache_dir, url=url, log=log)

    def _fetch_voices(self) -> list[dict]:
        """話者 × スタイル（ノーマル・あまあま など）を 1 つずつの声として返す。"""
        speakers = self._get_json("/speakers")
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
