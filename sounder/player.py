"""音の再生（afplay）と読み上げ（say / Qwen3-TTS）。すべてこの Mac の中だけで完結する。"""

from __future__ import annotations

import os
import re
from collections import deque
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from . import neural, tones

SYSTEM_SOUND_DIRS = (
    Path("/System/Library/Sounds"),
    Path("/Library/Sounds"),
    Path.home() / "Library/Sounds",
)
AUDIO_EXT = {".wav", ".aiff", ".aif", ".mp3", ".m4a", ".aac", ".caf", ".flac", ".ogg", ".mp4"}
SAFE_NAME = re.compile(r"^[^/\\\x00]{1,120}$")
# 声の一覧に出す言語（ロケールの先頭）。ほかの言語の声は使わないので出さない
VOICE_LANGS = ("ja", "en")


class SoundNotFound(Exception):
    """指定されたサウンドが見つからない。"""


class Player:
    """再生を直列化し、停止できるようにする。

    afplay を subprocess で呼ぶ。1 つの再生ジョブが走っている間に新しい再生が
    来たら、古いジョブは止めて新しい方を鳴らす（鳴り続けて重なるのを防ぐ）。
    """

    def __init__(self, builtin_dir: Path, user_dir: Path, *, log=None,
                 neural_tts: neural.NeuralTTS | None = None) -> None:
        self.builtin_dir = builtin_dir
        self.user_dir = user_dir
        self._log = log or (lambda *a, **k: None)
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._job = 0
        # 同じ時刻に複数の予定が来ても打ち消し合わないよう、順番待ちに並べる
        self._queue: deque[tuple[dict, dict, str]] = deque()
        self._draining = False
        self._tmpdir = Path(tempfile.mkdtemp(prefix="sounder-say-"))
        self.afplay = shutil.which("afplay")
        self.say = shutil.which("say")
        self.neural = neural_tts

    # --- サウンドの解決 ---------------------------------------------------

    def resolve(self, ref: str) -> Path:
        """'builtin:ding' のような参照を実ファイルへ解決する。"""
        if not isinstance(ref, str) or not ref.strip():
            raise SoundNotFound("サウンドが指定されていません")
        ref = ref.strip()
        scheme, _, name = ref.partition(":")
        if not _:
            scheme, name = "builtin", ref

        if scheme == "builtin":
            path = self.builtin_dir / f"{name}.wav"
            if name not in tones.BUILTINS or not path.exists():
                raise SoundNotFound(f"内蔵サウンド {name!r} がありません")
            return path
        if scheme == "user":
            if not SAFE_NAME.match(name):
                raise SoundNotFound("ファイル名が不正です")
            path = (self.user_dir / name).resolve()
            if self.user_dir.resolve() not in path.parents or not path.is_file():
                raise SoundNotFound(f"アップロード済みの {name!r} がありません")
            return path
        if scheme == "system":
            if not SAFE_NAME.match(name):
                raise SoundNotFound("ファイル名が不正です")
            for d in SYSTEM_SOUND_DIRS:
                p = d / f"{name}.aiff"
                if p.is_file():
                    return p
            raise SoundNotFound(f"システムサウンド {name!r} がありません")
        if scheme == "file":
            path = Path(name).expanduser()
            if not path.is_absolute() or not path.is_file():
                raise SoundNotFound(f"ファイルが見つかりません: {name}")
            return path
        raise SoundNotFound(f"未知のサウンド指定です: {ref}")

    def library(self) -> dict[str, list[dict]]:
        """Web UI に出すサウンド一覧。"""
        builtin = [
            {"ref": f"builtin:{k}", "label": label}
            for k, (label, _fn) in tones.BUILTINS.items()
            if (self.builtin_dir / f"{k}.wav").exists()
        ]
        system = []
        seen = set()
        for d in SYSTEM_SOUND_DIRS:
            if not d.is_dir():
                continue
            for p in sorted(d.glob("*.aiff")):
                if p.stem in seen:
                    continue
                seen.add(p.stem)
                system.append({"ref": f"system:{p.stem}", "label": p.stem})
        user = []
        if self.user_dir.is_dir():
            for p in sorted(self.user_dir.iterdir()):
                if p.is_file() and p.suffix.lower() in AUDIO_EXT:
                    user.append({
                        "ref": f"user:{p.name}",
                        "label": p.name,
                        "size": p.stat().st_size,
                    })
        return {"builtin": builtin, "system": system, "user": user}

    def voices(self) -> list[dict]:
        """読み上げに使える声（日本語と英語だけ）。Qwen3-TTS（動いていれば）を先に、次に say の声を日本語から。"""
        extra = self.neural.voices() if self.neural else []
        return [v for v in extra + self._say_voices() if v["locale"].startswith(VOICE_LANGS)]

    def _say_voices(self) -> list[dict]:
        if not self.say:
            return []
        try:
            out = subprocess.run([self.say, "-v", "?"], capture_output=True,
                                 text=True, timeout=15).stdout
        except (OSError, subprocess.SubprocessError):
            return []
        voices, seen = [], set()
        for line in out.splitlines():
            # 例: "Kyoko (Japanese (Japan)) ja_JP    # こんにちは"
            # 名前に空白が入るものがあるので、ロケールと # を手がかりに切り出す
            m = re.match(r"^(.*?)\s+([a-z]{2,3}(?:[_-][A-Za-z]{2,4})?)\s*#", line)
            if not m:
                continue
            name = m.group(1).strip()
            if not name or name in seen:
                continue
            seen.add(name)
            voices.append({"name": name, "locale": m.group(2)})
        voices.sort(key=lambda v: (not v["locale"].startswith("ja"), v["name"].lower()))
        return voices

    # --- 再生 -------------------------------------------------------------

    def _signal_group(self, proc: subprocess.Popen, sig: int) -> None:
        """プロセスグループごと止める。afplay が子を持つ場合も取り残さない。"""
        try:
            os.killpg(os.getpgid(proc.pid), sig)
        except OSError:
            try:
                proc.send_signal(sig)
            except OSError:
                pass

    def stop(self) -> None:
        """鳴っている音を止め、順番待ちも捨てる（画面の停止ボタン）。"""
        with self._lock:
            self._queue.clear()
        self._stop_current()

    def _stop_current(self) -> None:
        """いま鳴っているものだけを止める（順番待ちはそのまま）。"""
        with self._lock:
            self._job += 1
            proc, self._proc = self._proc, None
        if proc and proc.poll() is None:
            self._signal_group(proc, signal.SIGTERM)
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._signal_group(proc, signal.SIGKILL)

    def is_playing(self) -> bool:
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    def _run(self, argv: list[str], job: int, timeout: float = 600.0) -> bool:
        """1 コマンドを実行する。途中で新しいジョブが来たら False を返す。"""
        try:
            proc = subprocess.Popen(argv, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.PIPE, start_new_session=True)
        except OSError as exc:
            self._log("error", f"再生に失敗しました: {exc}")
            return False
        with self._lock:
            stale = job != self._job
            if not stale:
                self._proc = proc
        if stale:  # 待っている間に別の再生が始まっていた
            self._signal_group(proc, signal.SIGTERM)
            proc.communicate()
            return False
        try:
            _out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._signal_group(proc, signal.SIGKILL)
            proc.communicate()  # 後始末（パイプを閉じてゾンビを残さない）
            self._log("error", "再生が長すぎるため停止しました")
            return False
        finally:
            with self._lock:
                if self._proc is proc:
                    self._proc = None
        if proc.returncode not in (0, -15, 143) and err:
            self._log("error", f"再生エラー: {err.decode('utf-8', 'replace').strip()[:200]}")
        return job == self._job

    def _say_to_file(self, text: str, voice: str, rate: int) -> Path | None:
        """say の出力を AIFF に落とす。音量を afplay 側で揃えるため。"""
        if not self.say:
            return None
        out = self._tmpdir / f"say-{int(time.time()*1000)}.wav"
        # WAVE + リトルエンディアン。AIFF はビッグエンディアンしか受け付けない
        argv = [self.say, "-o", str(out), "--file-format=WAVE", "--data-format=LEI16@22050"]
        if voice:
            argv += ["-v", voice]
        if rate:
            argv += ["-r", str(int(rate))]
        argv += ["--", text]
        try:
            r = subprocess.run(argv, capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError) as exc:
            self._log("error", f"読み上げの生成に失敗しました: {exc}")
            return None
        if r.returncode != 0 or not out.exists():
            msg = r.stderr.decode("utf-8", "replace").strip()[:200]
            self._log("error", f"読み上げの生成に失敗しました: {msg}")
            return None
        return out

    def play(self, action: dict, *, settings: dict, label: str = "",
             queue: bool = False) -> None:
        """action を非同期で再生する（呼び出し側はブロックしない）。

        queue=True なら、鳴っているものの後ろに並べる（時刻が重なった予定用）。
        queue=False なら、鳴っているものを止めて今すぐ鳴らす（試聴用）。
        """
        if not queue:
            with self._lock:
                self._queue.clear()
            self._stop_current()
        with self._lock:
            self._queue.append((action, settings, label))
            if self._draining:
                return
            self._draining = True
        threading.Thread(target=self._drain, name="player", daemon=True).start()

    def _drain(self) -> None:
        while True:
            with self._lock:
                if not self._queue:
                    self._draining = False
                    return
                action, settings, label = self._queue.popleft()
            try:
                self.play_blocking(action, settings=settings, label=label)
            except Exception as exc:  # 1 件の失敗で順番待ちを止めない
                self._log("error", f"再生中にエラーが発生しました: {exc!r}")

    def play_blocking(self, action: dict, *, settings: dict, label: str = "") -> None:
        if not self.afplay:
            self._log("error", "afplay が見つかりません（macOS 以外では動きません）")
            return
        self._stop_current()
        with self._lock:
            self._job += 1
            job = self._job

        atype = action.get("type", "sound")
        volume = action.get("volume", settings.get("default_volume", 0.6))
        repeat = max(1, int(action.get("repeat", 1)))
        items: list[list[str]] = []

        if atype in ("sound", "both"):
            try:
                path = self.resolve(action.get("sound", ""))
            except SoundNotFound as exc:
                self._log("error", f"{label}: {exc}")
                path = None
            if path:
                items.append([self.afplay, "-v", f"{volume:.3f}", str(path)])

        if atype in ("speak", "both"):
            voice = action.get("voice") or settings.get("default_voice") or ""
            rate = action.get("rate") or settings.get("speak_rate") or 180
            tmp = None
            if neural.is_neural(voice):
                tmp = self.neural.render(action["text"], voice) if self.neural else None
                voice = ""  # 作れなかったら say のシステム既定の声で代わりに読む
            if tmp is None:
                tmp = self._say_to_file(action["text"], voice, rate)
            if tmp:
                items.append([self.afplay, "-v", f"{volume:.3f}", str(tmp)])

        if not items:
            return
        for r in range(repeat):
            if r:
                time.sleep(0.45)
                with self._lock:
                    if job != self._job:
                        return
            for argv in items:
                if not self._run(argv, job):
                    return
        # say の一時ファイルを片付ける
        for argv in items:
            p = Path(argv[-1])
            if p.parent == self._tmpdir:
                try:
                    os.unlink(p)
                except OSError:
                    pass

    def prefetch(self, action: dict, *, settings: dict) -> None:
        """もうすぐ鳴る予定の読み上げを先に作っておく（Qwen3-TTS は生成に数秒かかるため）。"""
        if action.get("type") not in ("speak", "both") or not self.neural:
            return
        voice = action.get("voice") or settings.get("default_voice") or ""
        text = action.get("text") or ""
        if neural.is_neural(voice) and text.strip():
            threading.Thread(target=self.neural.render, args=(text, voice),
                             name="tts-prefetch", daemon=True).start()

    # --- アップロード -----------------------------------------------------

    def save_upload(self, filename: str, data: bytes) -> dict:
        name = os.path.basename(filename or "").strip()
        if not name or not SAFE_NAME.match(name) or name.startswith("."):
            raise SoundNotFound("ファイル名が不正です")
        ext = Path(name).suffix.lower()
        if ext not in AUDIO_EXT:
            raise SoundNotFound(f"対応していない拡張子です（{', '.join(sorted(AUDIO_EXT))}）")
        self.user_dir.mkdir(parents=True, exist_ok=True)
        path = self.user_dir / name
        stem, i = Path(name).stem, 1
        while path.exists():
            path = self.user_dir / f"{stem}-{i}{ext}"
            i += 1
        path.write_bytes(data)
        return {"ref": f"user:{path.name}", "label": path.name, "size": len(data)}

    def delete_upload(self, name: str) -> None:
        if not SAFE_NAME.match(name or ""):
            raise SoundNotFound("ファイル名が不正です")
        path = (self.user_dir / name).resolve()
        if self.user_dir.resolve() not in path.parents or not path.is_file():
            raise SoundNotFound("ファイルが見つかりません")
        path.unlink()
