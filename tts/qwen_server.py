"""Qwen3-TTS を動かして、sounder に読み上げの音声（WAV）を返す小さなサーバ。

sounder 本体は標準ライブラリだけで動かしたいので、モデルはこの別プロセスに閉じ込める。
専用の venv（scripts/install-tts.sh が作る）で動かし、127.0.0.1 だけで待ち受ける。

声は 2 種類。
  - 用意された話者（ono_anna, ryan など）… CustomVoice モデル。style="announcer" で
    アナウンサー風の話し方を指示できる
  - 言葉で説明して作った声 … VoiceDesign モデルで見本を 1 本作って data/voices/<id>/ に保存し、
    以後は Base モデルがその見本の声で読む（毎回同じ声になるように）

  GET    /speakers            → {"model", "speakers": [...], "styles": [...], "designed": [...]}
  POST   /synthesize          {"text", "speaker", "language", "style"?} → audio/wav
                              speaker は "ono_anna" か "design:<id>"
  POST   /design              {"name", "description", "language"?} → 作った声の情報
  DELETE /design/<id>
"""

from __future__ import annotations

import argparse
import gc
import io
import json
import re
import shutil
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import mlx.core as mx
import numpy as np
import soundfile as sf
from mlx_audio.tts.utils import load_model

CUSTOM_MODEL = "mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-bf16"
BASE_MODEL = "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16"
DESIGN_MODEL = "mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-bf16"
MAX_TEXT = 1000
ID_RE = re.compile(r"^[0-9a-f]{8}$")

# 話し方の指示（CustomVoice の instruct）。文章の言語に合わせて選ぶ
STYLES = {
    "announcer": {
        "japanese": "ニュースを読むアナウンサーのように、落ち着いて、はっきりと、自然なナレーションの調子で読んでください。",
        "english": "Read like a calm news announcer: clear, steady and natural narration.",
    },
}
# 作った声の見本として読ませる文（Base モデルがこの声をまねる手がかりになる）
REF_TEXT = {
    "japanese": "おはようございます。朝のお知らせをお伝えします。今日は全国的に晴れて、過ごしやすい一日になるでしょう。",
    "english": "Good morning. Here is this morning's announcement. It will be sunny and pleasant across the country today.",
}


def log(msg: str) -> None:
    print(f"[sounder-tts] {time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}", flush=True)


def to_wav(audio: np.ndarray, rate: int) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, audio, rate, format="WAV", subtype="PCM_16")
    return buf.getvalue()


def join(results) -> np.ndarray:
    return np.concatenate([np.array(r.audio) for r in results])


class Engine:
    def __init__(self, voices_dir: Path) -> None:
        self.voices_dir = voices_dir
        # MLX の推論は同時に走らせない
        self.lock = threading.Lock()
        self.custom = self._load(CUSTOM_MODEL)
        self.base = None      # 作った声を初めて使うときに読み込む
        get = getattr(self.custom, "get_supported_speakers", None)
        names = get() if get else self.custom.config.talker_config.spk_id
        self.speakers = sorted(s.lower() for s in names)

    @staticmethod
    def _load(model_id: str):
        t = time.time()
        m = load_model(model_id)
        log(f"モデルを読み込みました（{time.time() - t:.1f} 秒）: {model_id}")
        return m

    # --- 作った声 ---------------------------------------------------------

    def designed(self) -> list[dict]:
        out = []
        for meta in sorted(self.voices_dir.glob("*/meta.json")):
            try:
                m = json.loads(meta.read_text())
            except (OSError, ValueError):
                continue
            out.append({k: m.get(k) for k in ("id", "name", "description", "language", "created")})
        out.sort(key=lambda m: m.get("created") or "")
        return out

    def design(self, name: str, description: str, language: str) -> dict:
        ref_text = REF_TEXT.get(language, REF_TEXT["japanese"])
        with self.lock:
            model = self._load(DESIGN_MODEL)
            try:
                t = time.time()
                audio = join(model.generate_voice_design(
                    text=ref_text, language=language, instruct=description))
                rate = model.sample_rate
                log(f"声を作りました（{time.time() - t:.1f} 秒）: {name}")
            finally:
                # VoiceDesign は作るときにしか使わないので、すぐメモリから降ろす
                del model
                gc.collect()
                mx.clear_cache()
        vid = uuid.uuid4().hex[:8]
        d = self.voices_dir / vid
        d.mkdir(parents=True)
        sf.write(d / "ref.wav", audio, rate)
        meta = {"id": vid, "name": name, "description": description, "language": language,
                "ref_text": ref_text, "created": time.strftime("%Y-%m-%dT%H:%M:%S")}
        (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
        return {k: meta[k] for k in ("id", "name", "description", "language", "created")}

    def delete(self, vid: str) -> bool:
        d = self.voices_dir / vid
        if not ID_RE.match(vid) or not (d / "meta.json").is_file():
            return False
        shutil.rmtree(d)
        return True

    # --- 読み上げ ---------------------------------------------------------

    def synthesize(self, text: str, speaker: str, language: str, style: str) -> bytes:
        t = time.time()
        lang = language if language in ("japanese", "english") else "japanese"
        if speaker.startswith("design:"):
            vid = speaker[len("design:"):]
            d = self.voices_dir / vid
            meta = json.loads((d / "meta.json").read_text())
            with self.lock:
                if self.base is None:
                    self.base = self._load(BASE_MODEL)
                audio = join(self.base.generate(text=text, ref_audio=str(d / "ref.wav"),
                                                ref_text=meta["ref_text"], lang_code=language))
                rate = self.base.sample_rate
        else:
            instruct = STYLES.get(style, {}).get(lang, "")
            with self.lock:
                audio = join(self.custom.generate_custom_voice(
                    text=text, speaker=speaker, language=language, instruct=instruct))
                rate = self.custom.sample_rate
        log(f"{speaker}{'@' + style if style else ''}: {time.time() - t:.1f} 秒で "
            f"{len(audio) / rate:.1f} 秒分を生成")
        return to_wav(audio, rate)


class Handler(BaseHTTPRequestHandler):
    engine: Engine
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args) -> None:  # アクセスログは出さない
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json")

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        if not isinstance(body, dict):
            raise ValueError
        return body

    def do_GET(self) -> None:
        if self.path == "/speakers":
            self._json(200, {"model": CUSTOM_MODEL, "speakers": self.engine.speakers,
                             "styles": sorted(STYLES), "designed": self.engine.designed()})
        else:
            self._json(404, {"error": "not found"})

    def do_DELETE(self) -> None:
        m = re.match(r"^/design/([0-9a-f]{8})$", self.path)
        if m and self.engine.delete(m.group(1)):
            self._json(200, {"ok": True})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        try:
            body = self._body()
        except (ValueError, TypeError):
            self._json(400, {"error": "JSON が不正です"})
            return
        if self.path == "/design":
            self._design(body)
        elif self.path == "/synthesize":
            self._synthesize(body)
        else:
            self._json(404, {"error": "not found"})

    def _design(self, body: dict) -> None:
        name = str(body.get("name") or "").strip()[:40]
        desc = str(body.get("description") or "").strip()[:500]
        lang = str(body.get("language") or "japanese").strip().lower()
        if not name or not desc:
            self._json(400, {"error": "名前と声の説明を入れてください"})
            return
        if lang not in REF_TEXT:
            self._json(400, {"error": "language は japanese か english です"})
            return
        try:
            self._json(200, self.engine.design(name, desc, lang))
        except Exception as exc:  # 1 件の失敗でサーバを落とさない
            log(f"声を作れませんでした: {exc!r}")
            self._json(500, {"error": repr(exc)[:300]})

    def _synthesize(self, body: dict) -> None:
        text = str(body.get("text") or "").strip()
        speaker = str(body.get("speaker") or "").strip().lower()
        language = str(body.get("language") or "auto").strip().lower()
        style = str(body.get("style") or "").strip().lower()
        if not text or len(text) > MAX_TEXT:
            self._json(400, {"error": f"text は 1〜{MAX_TEXT} 文字にしてください"})
            return
        designed = speaker.startswith("design:")
        if designed:
            vid = speaker[len("design:"):]
            if not ID_RE.match(vid) or not (self.engine.voices_dir / vid / "meta.json").is_file():
                self._json(400, {"error": f"作った声が見つかりません: {vid}"})
                return
        elif speaker not in self.engine.speakers:
            self._json(400, {"error": f"未知の speaker です: {speaker}"})
            return
        if style and style not in STYLES:
            self._json(400, {"error": f"未知の style です: {style}"})
            return
        try:
            wav = self.engine.synthesize(text, speaker, language, style)
        except Exception as exc:  # 1 件の失敗でサーバを落とさない
            log(f"生成に失敗しました: {exc!r}")
            self._json(500, {"error": repr(exc)[:300]})
            return
        self._send(200, wav, "audio/wav")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8778)
    ap.add_argument("--voices-dir", type=Path,
                    default=Path(__file__).resolve().parent.parent / "data" / "voices")
    args = ap.parse_args(argv)
    args.voices_dir.mkdir(parents=True, exist_ok=True)
    eng = Handler.engine = Engine(args.voices_dir)
    # 最初の 1 回は遅いので、起動時に空打ちしておく
    eng.synthesize("準備ができました。",
                   "ono_anna" if "ono_anna" in eng.speakers else eng.speakers[0], "japanese", "")
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    log(f"待ち受けを始めました http://127.0.0.1:{args.port}/")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
