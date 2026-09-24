"""HTTP サーバ。REST API と Web UI を配信する。既定で 127.0.0.1 のみ待ち受ける。"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import mimetypes
import os
import secrets
import signal
import socket
import socketserver
import sys
import threading
from datetime import date, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import scheduler as sched_mod
from . import daysoff, neural, tones
from .config import Store, ValidationError
from .eventlog import EventLog
from .player import Player, SoundNotFound
from .scheduler import Scheduler
from .speechcache import SpeechCache

MAX_UPLOAD_BYTES = 30 * 1024 * 1024   # 1 ファイルの上限
MAX_BODY = MAX_UPLOAD_BYTES * 4 // 3 + 1024 * 1024  # base64 化した分の余裕を見る
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
# mimetypes が知らない拡張子を補う（環境によっては Web フォントの型も登録されていない）
EXTRA_TYPES = {
    ".webmanifest": "application/manifest+json",
    ".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf",
}
# 同梱フォントは中身が変わらないので長くキャッシュさせる（それ以外は no-store）
FONT_CACHE = "public, max-age=31536000, immutable"
# 合言葉なしでも配る静的ファイル（合言葉の入力画面が使う。秘密は含まない）
PUBLIC_FILES = {"/icon-180.png", "/icon-192.png", "/icon-512.png", "/icon-maskable-512.png",
                "/favicon.png", "/manifest.webmanifest", "/sw.js"}


class App:
    """サーバ全体で共有する状態のまとめ。"""

    def __init__(self, home: Path, token: str | None = None) -> None:
        self.home = home
        self.web_dir = Path(__file__).resolve().parent.parent / "web"
        self.builtin_dir = home / "sounds" / "builtin"
        self.user_dir = home / "sounds" / "user"
        self.log = EventLog(home / "data" / "events.log")
        made = tones.generate_all(self.builtin_dir)
        if made:
            self.log("info", f"内蔵サウンドを生成しました（{len(made)}件）")
        cache = home / "data" / "tts-cache"
        tts = [
            neural.NeuralTTS(cache, log=self.log,
                             url=os.environ.get("SOUNDER_TTS_URL", neural.DEFAULT_URL)),
            neural.AivisTTS(cache, log=self.log,
                            url=os.environ.get("SOUNDER_AIVIS_URL", neural.AIVIS_URL)),
        ]
        self.player = Player(self.builtin_dir, self.user_dir, log=self.log, tts=tts,
                             speech_cache=SpeechCache(cache))
        # 保存時にサウンドの存在を確かめる（鳴らす瞬間に気づくのでは遅い）
        self.store = Store(home / "data" / "config.json", sound_check=self.player.resolve)
        self.scheduler = Scheduler(self.store, self.player, self.log)
        self.token = token
        self.started_at = datetime.now()
        self._pick_default_voice()

    def _pick_default_voice(self) -> None:
        """読み上げの声が未設定なら、日本語の声を選んでおく。"""
        if self.store.settings.get("default_voice"):
            return
        ja = [v["name"] for v in self.player.voices() if v["locale"].startswith("ja")]
        if not ja:
            return
        kyoko = [n for n in ja if n.startswith("Kyoko")]
        self.store.update_settings({"default_voice": (kyoko or ja)[0]})


class Handler(BaseHTTPRequestHandler):
    server_version = "sounder"
    protocol_version = "HTTP/1.1"
    app: App

    # --- 共通処理 ---------------------------------------------------------

    def log_message(self, fmt: str, *args) -> None:  # アクセスログは静かに
        pass

    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None,
              cache: str = "no-store") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, data, code: int = 200, extra: dict | None = None) -> None:
        self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8", extra)

    def _error(self, code: int, message: str) -> None:
        self._json({"error": message}, code)

    def _body(self) -> bytes:
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return b""
        if n > MAX_BODY:
            raise ValidationError("データが大きすぎます")
        return self.rfile.read(n) if n > 0 else b""

    def _json_body(self) -> dict:
        raw = self._body()
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ValidationError("JSON として読めませんでした")
        if not isinstance(data, dict):
            raise ValidationError("JSON オブジェクトを送ってください")
        return data

    def _authorized(self, query: dict) -> bool:
        """トークン運用時のみチェックする（既定のローカル運用では常に True）。"""
        token = self.app.token
        if not token:
            return True
        given = (self.headers.get("X-Sounder-Token")
                 or (query.get("t") or [None])[0]
                 or self._cookie("sounder_token"))
        return bool(given) and secrets.compare_digest(given, token)

    def _cookie(self, name: str) -> str | None:
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == name:
                return unquote(v)
        return None

    def _same_origin(self) -> bool:
        """別サイトのページから書き換えられるのを防ぐ。"""
        origin = self.headers.get("Origin")
        if not origin:
            return True  # curl など。ブラウザは必ず Origin を付ける
        host = self.headers.get("Host") or ""
        try:
            o = urlparse(origin)
        except ValueError:
            return False
        return o.netloc == host

    # --- ルーティング -----------------------------------------------------

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_HEAD(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_PUT(self) -> None:
        self._dispatch("PUT")

    def do_PATCH(self) -> None:
        self._dispatch("PATCH")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = parse_qs(parsed.query)
        try:
            if not self._authorized(query) and not self._public(method, path):
                if path.startswith("/api/") or method != "GET":
                    self._error(HTTPStatus.UNAUTHORIZED, "合言葉が必要です")
                else:
                    self._unlock_page()   # 画面を開こうとした人には入力欄を出す
                return
            if method != "GET" and not self._same_origin():
                self._error(HTTPStatus.FORBIDDEN, "リクエスト元が不正です")
                return
            if path.startswith("/api/"):
                self._api(method, path[4:], query)
            elif method == "GET":
                self._static(path, query)
            else:
                self._error(HTTPStatus.NOT_FOUND, "そのAPIはありません")
        except ValidationError as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except SoundNotFound as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except KeyError:
            self._error(HTTPStatus.NOT_FOUND, "見つかりません")
        except BrokenPipeError:
            pass
        except Exception as exc:  # noqa: BLE001
            self.app.log("error", f"{method} {path} で例外: {exc!r}")
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"サーバ内部エラー: {exc}")

    @staticmethod
    def _public(method: str, path: str) -> bool:
        """合言葉の入力画面から読むフォントとアイコンだけは、合言葉なしで配る。"""
        if method != "GET" or ".." in path.split("/"):
            return False  # /fonts/../app.js のような遡りで本体を読ませない
        return path in PUBLIC_FILES or path.startswith("/fonts/")

    def _unlock_page(self) -> None:
        """合言葉が無いときに出す小さな入力画面。"""
        page = self.app.web_dir / "unlock.html"
        if not page.is_file():
            self._error(HTTPStatus.UNAUTHORIZED, "合言葉が必要です")
            return
        self._send(HTTPStatus.UNAUTHORIZED, page.read_bytes(), "text/html; charset=utf-8")

    # --- 静的ファイル -----------------------------------------------------

    def _static(self, path: str, query: dict) -> None:
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        target = (self.app.web_dir / rel).resolve()
        web = self.app.web_dir.resolve()
        if web != target and web not in target.parents:
            self._error(HTTPStatus.FORBIDDEN, "アクセスできません")
            return
        if not target.is_file():
            self._error(HTTPStatus.NOT_FOUND, "ページがありません")
            return
        ctype = (EXTRA_TYPES.get(target.suffix)
                 or mimetypes.guess_type(target.name)[0]
                 or "application/octet-stream")
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        extra = {}
        # ?t=... でアクセスされたらトークンを cookie に覚えさせる（LAN 公開時のみ）
        tok = (query.get("t") or [None])[0]
        if tok and self.app.token and secrets.compare_digest(tok, self.app.token):
            extra["Set-Cookie"] = f"sounder_token={tok}; Path=/; SameSite=Strict; Max-Age=31536000"
        cache = FONT_CACHE if target.parent == web / "fonts" else "no-store"
        self._send(HTTPStatus.OK, target.read_bytes(), ctype, extra, cache=cache)

    # --- API --------------------------------------------------------------

    def _api(self, method: str, route: str, query: dict) -> None:
        app = self.app
        parts = [p for p in route.strip("/").split("/") if p]

        if parts == ["state"] and method == "GET":
            self._json(self._state())
            return

        if parts == ["now"] and method == "GET":
            self._json({
                "now": datetime.now().isoformat(timespec="seconds"),
                "playing": app.player.is_playing(),
                "next_events": sched_mod.next_events(
                    app.store.schedules(), limit=6, settings=app.store.settings),
                "log": app.log.recent(25),
            })
            return

        if parts == ["calendar"] and method == "GET":
            start = (query.get("start") or [""])[0]
            try:
                first = date.fromisoformat(start) if start else date.today()
            except ValueError:
                raise ValidationError("start は YYYY-MM-DD 形式で指定してください")
            span = min(31, max(1, int((query.get("days") or [7])[0] or 7)))
            settings = app.store.settings
            self._json({
                "start": first.isoformat(),
                "master_enabled": settings.get("master_enabled", True),
                "quiet_hours": settings.get("quiet_hours"),
                "days": sched_mod.calendar_days(
                    app.store.schedules(), first, span, settings=settings,
                    log_entries=app.log.recent(300)),
            })
            return

        if parts[:2] == ["voices", "design"]:
            qwen = next((e for e in app.player.tts if isinstance(e, neural.NeuralTTS)), None)
            if qwen is None:
                raise ValidationError("Qwen3-TTS が使えません")
            if parts == ["voices", "design"] and method == "POST":
                body = self._json_body()
                name = (body.get("name") or "").strip()
                desc = (body.get("description") or "").strip()
                lang = body.get("language") or "japanese"
                if not name or not desc:
                    raise ValidationError("名前と声の説明を入れてください")
                if len(name) > 40 or len(desc) > 500:
                    raise ValidationError("名前は 40 文字、説明は 500 文字までです")
                if lang not in ("japanese", "english"):
                    raise ValidationError("言語の指定が不正です")
                try:
                    made = qwen.design(name, desc, lang)
                except ValueError as exc:
                    raise ValidationError(f"声を作れませんでした: {exc}")
                app.log("info", f"声「{name}」を作りました")
                self._json({"voice": made, "voices": app.player.voices()})
                return
            if len(parts) == 3 and method == "DELETE":
                if not qwen.delete_design(parts[2]):
                    raise ValidationError("声を削除できませんでした")
                app.log("info", "作った声を削除しました")
                self._json({"voices": app.player.voices()})
                return

        if parts == ["settings"] and method in ("PUT", "PATCH"):
            self._json({"settings": app.store.update_settings(self._json_body())})
            return

        if parts == ["schedules"]:
            if method == "GET":
                self._json({"schedules": self._decorate(app.store.schedules())})
                return
            if method == "POST":
                s = app.store.add(self._json_body())
                app.log("info", f"{s['name']} を追加しました", schedule_id=s["id"])
                self._json({"schedule": self._decorate([s])[0]}, HTTPStatus.CREATED)
                return

        if len(parts) >= 2 and parts[0] == "schedules":
            sid = parts[1]
            tail = parts[2] if len(parts) > 2 else None
            if tail is None:
                if method == "GET":
                    s = app.store.get(sid)
                    if not s:
                        raise KeyError(sid)
                    self._json({"schedule": self._decorate([s])[0]})
                    return
                if method == "PUT":
                    s = app.store.replace(sid, self._json_body())
                    app.log("info", f"{s['name']} を更新しました", schedule_id=sid)
                    self._json({"schedule": self._decorate([s])[0]})
                    return
                if method == "PATCH":
                    s = app.store.patch(sid, self._json_body())
                    self._json({"schedule": self._decorate([s])[0]})
                    return
                if method == "DELETE":
                    s = app.store.get(sid)
                    app.store.delete(sid)
                    app.log("info", f"{(s or {}).get('name', sid)} を削除しました")
                    self._json({"ok": True})
                    return
            elif tail == "test" and method == "POST":
                s = app.store.get(sid)
                if not s:
                    raise KeyError(sid)
                which = (self._json_body().get("which") or "main")
                if which == "lead" and s.get("lead_action"):
                    lead = (s.get("lead_times") or [5])[0]
                    action = app.scheduler.action_for(s, "lead", lead, app.store.settings)
                else:
                    action = s["action"]
                app.player.play(action, settings=app.store.settings, label=f"試聴:{s['name']}")
                app.log("test", f"{s['name']} を試聴しました", schedule_id=sid)
                self._json({"ok": True})
                return

        if parts == ["preview"] and method == "POST":
            body = self._json_body()
            action = {
                "type": body.get("type", "sound"),
                "sound": body.get("sound"),
                "text": body.get("text"),
                "voice": body.get("voice"),
                "rate": body.get("rate"),
                "volume": body.get("volume", app.store.settings["default_volume"]),
                "repeat": 1,
            }
            if action["type"] in ("sound", "both") and not action.get("sound"):
                raise ValidationError("サウンドを選んでください")
            if action["type"] in ("speak", "both") and not (action.get("text") or "").strip():
                raise ValidationError("読み上げる文章を入れてください")
            if action.get("sound"):
                app.player.resolve(action["sound"])
            app.player.play(action, settings=app.store.settings, label="試聴")
            self._json({"ok": True})
            return

        if parts == ["stop"] and method == "POST":
            app.player.stop()
            self._json({"ok": True})
            return

        if parts == ["sounds"]:
            if method == "GET":
                self._json({"sounds": app.player.library()})
                return
            if method == "POST":
                body = self._json_body()
                name = body.get("filename") or ""
                try:
                    data = base64.b64decode(body.get("data") or "", validate=True)
                except (binascii.Error, ValueError):
                    raise ValidationError("ファイルの内容を読めませんでした")
                if not data:
                    raise ValidationError("ファイルが空です")
                if len(data) > MAX_UPLOAD_BYTES:
                    raise ValidationError(
                        f"{MAX_UPLOAD_BYTES // (1024 * 1024)}MB 以下のファイルにしてください")
                info = app.player.save_upload(name, data)
                app.log("info", f"サウンド {info['label']} を追加しました")
                self._json({"sound": info, "sounds": app.player.library()},
                           HTTPStatus.CREATED)
                return
        if len(parts) == 2 and parts[0] == "sounds" and method == "DELETE":
            app.player.delete_upload(parts[1])
            app.log("info", f"サウンド {parts[1]} を削除しました")
            self._json({"ok": True, "sounds": app.player.library()})
            return

        if parts == ["log"] and method == "GET":
            limit = min(200, max(1, int((query.get("limit") or [100])[0])))
            self._json({"log": app.log.recent(limit)})
            return

        self._error(HTTPStatus.NOT_FOUND, "そのAPIはありません")

    def _decorate(self, schedules: list[dict]) -> list[dict]:
        for s in schedules:
            s["summary"] = sched_mod.describe(s)
            nxt = sched_mod.next_events([s], limit=1)
            s["next_at"] = nxt[0]["at"] if nxt else None
        return schedules

    def _state(self) -> dict:
        app = self.app
        return {
            "now": datetime.now().isoformat(timespec="seconds"),
            "settings": app.store.settings,
            "schedules": self._decorate(app.store.schedules()),
            "sounds": app.player.library(),
            "voices": app.player.voices(),
            "calendars": daysoff.catalog(),
            "next_events": sched_mod.next_events(
                app.store.schedules(), limit=6, settings=app.store.settings),
            "log": app.log.recent(30),
            "playing": app.player.is_playing(),
            "host": f"{self.headers.get('Host')}",
            "started_at": app.started_at.isoformat(timespec="seconds"),
            "builtin_labels": {f"builtin:{k}": v[0] for k, v in tones.BUILTINS.items()},
        }


def make_server(app: App, host: str, port: int) -> ThreadingHTTPServer:
    """app に紐付いた HTTP サーバを作る（テストからも使う）。"""

    class Server(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = True
        address_family = socket.AF_INET6 if ":" in host else socket.AF_INET

        def server_bind(self):
            # HTTPServer.server_bind は socket.getfqdn() で逆引き DNS を引き、
            # 環境によっては数十秒待たされる。ローカル完結なので引かせない。
            socketserver.TCPServer.server_bind(self)
            self.server_name = self.server_address[0]
            self.server_port = self.server_address[1]

    # ハンドラごとに app を束ねる（1 プロセスで複数立てられるようにする）
    bound = type("BoundHandler", (Handler,), {"app": app})
    return Server((host, port), bound)


def serve(home: Path, host: str, port: int, token: str | None) -> int:
    app = App(home, token)
    try:
        httpd = make_server(app, host, port)
    except OSError as exc:
        print(f"[sounder] {host}:{port} を開けませんでした: {exc}", file=sys.stderr)
        return 1

    app.scheduler.start()
    shown = host if host not in ("0.0.0.0", "::") else "127.0.0.1"
    url = f"http://{shown}:{port}/"
    if token:
        url += f"?t={token}"
    app.log("info", f"sounder を起動しました  {url}")
    if host not in LOOPBACK and not token:
        print("[sounder] 警告: ローカル以外に公開しています。--token の利用を検討してください。",
              file=sys.stderr)

    def shutdown(_sig=None, _frm=None):
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    try:
        httpd.serve_forever(poll_interval=0.5)
    finally:
        app.scheduler.stop()
        app.player.stop()
        httpd.server_close()
        app.log("info", "sounder を終了しました")
    return 0


def main(argv: list[str] | None = None) -> int:
    default_home = Path(os.environ.get("SOUNDER_HOME")
                        or Path(__file__).resolve().parent.parent)
    p = argparse.ArgumentParser(prog="sounder", description="時間になったら音を鳴らす（macOS 用）")
    p.add_argument("--host", default="127.0.0.1",
                   help="待ち受けアドレス（既定: 127.0.0.1 = このMacの中だけ）")
    p.add_argument("--port", type=int, default=8777, help="ポート（既定: 8777）")
    p.add_argument("--home", type=Path, default=default_home,
                   help="設定とサウンドの置き場所")
    p.add_argument("--token", default=os.environ.get("SOUNDER_TOKEN"),
                   help="LAN に公開する場合の合言葉")
    p.add_argument("--print-token", action="store_true",
                   help="ランダムなトークンを生成して使う")
    a = p.parse_args(argv)
    token = a.token
    if a.print_token and not token:
        token = secrets.token_urlsafe(16)
    return serve(a.home.expanduser().resolve(), a.host, a.port, token)
