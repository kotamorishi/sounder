"""Qwen3-TTS を常駐させて、sounder に読み上げの音声（WAV）を返す小さなサーバ。

sounder 本体は標準ライブラリだけで動かしたいので、モデルはこの別プロセスに閉じ込める。
専用の venv（scripts/install-tts.sh が作る）で動かし、127.0.0.1 だけで待ち受ける。

  GET  /speakers     → {"model": ..., "speakers": ["ono_anna", "ryan", ...]}
  POST /synthesize   {"text": ..., "speaker": ..., "language": "japanese"|"english"|"auto"}
                     → audio/wav（16bit PCM）
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import soundfile as sf
from mlx_audio.tts.utils import load_model

DEFAULT_MODEL = "mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-bf16"
MAX_TEXT = 1000


def log(msg: str) -> None:
    print(f"[sounder-tts] {time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}", flush=True)


class Engine:
    def __init__(self, model_id: str) -> None:
        t = time.time()
        self.model_id = model_id
        self.model = load_model(model_id)
        # MLX の推論は同時に走らせない
        self.lock = threading.Lock()
        self.speakers = sorted(self._speakers())
        log(f"モデルを読み込みました（{time.time() - t:.1f} 秒）: {model_id}")

    def _speakers(self) -> list[str]:
        get = getattr(self.model, "get_supported_speakers", None)
        if get:
            return [s.lower() for s in get()]
        return [s.lower() for s in self.model.config.talker_config.spk_id]

    def synthesize(self, text: str, speaker: str, language: str) -> bytes:
        t = time.time()
        with self.lock:
            results = list(self.model.generate_custom_voice(
                text=text, speaker=speaker, language=language, instruct=""))
        audio = np.concatenate([np.array(r.audio) for r in results])
        buf = io.BytesIO()
        sf.write(buf, audio, self.model.sample_rate, format="WAV", subtype="PCM_16")
        log(f"{speaker}: {time.time() - t:.1f} 秒で {len(audio) / self.model.sample_rate:.1f} 秒分を生成")
        return buf.getvalue()


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

    def _json(self, code: int, obj: dict) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json")

    def do_GET(self) -> None:
        if self.path == "/speakers":
            self._json(200, {"model": self.engine.model_id, "speakers": self.engine.speakers})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path != "/synthesize":
            self._json(404, {"error": "not found"})
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            text = str(body.get("text") or "").strip()
            speaker = str(body.get("speaker") or "").strip().lower()
            language = str(body.get("language") or "auto").strip().lower()
        except (ValueError, TypeError):
            self._json(400, {"error": "JSON が不正です"})
            return
        if not text or len(text) > MAX_TEXT:
            self._json(400, {"error": f"text は 1〜{MAX_TEXT} 文字にしてください"})
            return
        if speaker not in self.engine.speakers:
            self._json(400, {"error": f"未知の speaker です: {speaker}"})
            return
        try:
            wav = self.engine.synthesize(text, speaker, language)
        except Exception as exc:  # 1 件の失敗でサーバを落とさない
            log(f"生成に失敗しました: {exc!r}")
            self._json(500, {"error": repr(exc)[:300]})
            return
        self._send(200, wav, "audio/wav")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8778)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    args = ap.parse_args(argv)
    Handler.engine = Engine(args.model)
    # 最初の 1 回は遅いので、起動時に空打ちしておく
    eng = Handler.engine
    eng.synthesize("準備ができました。",
                   "ono_anna" if "ono_anna" in eng.speakers else eng.speakers[0], "japanese")
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    log(f"待ち受けを始めました http://127.0.0.1:{args.port}/")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
