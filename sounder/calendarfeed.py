"""カレンダーの予定を読み上げの予定にする（カレンダー連携）。

補助アプリ（tools/calendar、SounderCalendar.app）が 5 分ごとに data/calendar.json へ
これからの予定を書き出す。ここではそれを読み、設定で選んだカレンダーの予定ごとに
「開始の N 分前に読み上げる」1 回きりの予定（kind="once"）を組み立てる。
組み立てた予定は保存しない（毎回 calendar.json から作り直す）ので、予定を消したり動かしたりすれば
それに合わせて変わる。発火・先読み・禁止時間・タイムラインは、ふつうの予定と同じ仕組みに乗る。
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timedelta
from pathlib import Path

DEFAULTS = {
    "enabled": False,
    "calendars": [],          # 読み上げるカレンダーの id
    "lead": 10,               # 開始の何分前に読むか（0 = 開始時刻）
    "sound": "builtin:melody_notice",   # 読み上げの前に鳴らす音（"" なら読み上げだけ）
    "voice": "",              # "" なら設定の既定の声
}
LEADS = (0, 5, 10, 15, 30, 60)


def settings_of(settings: dict) -> dict:
    return {**DEFAULTS, **(settings.get("calendar") or {})}


def sentence(title: str, lead: int) -> str:
    title = title.strip() or "予定"
    return f"{title}の時間です。" if lead <= 0 else f"{lead}分後に、{title}があります。"


class CalendarFeed:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._mtime = None
        self._data: dict = {}

    def data(self) -> dict:
        """calendar.json の中身（変わったときだけ読み直す）。無ければ空。"""
        with self._lock:
            try:
                mtime = self.path.stat().st_mtime
            except OSError:
                self._mtime, self._data = None, {}
                return {}
            if mtime != self._mtime:
                try:
                    self._data = json.loads(self.path.read_text("utf-8"))
                except (OSError, ValueError):
                    self._data = {}
                self._mtime = mtime
            return self._data

    def status(self) -> dict:
        """設定画面に出す連携の状態。"""
        d = self.data()
        return {
            "installed": bool(d),
            "status": d.get("status") or ("missing" if not d else "unknown"),
            "generated": d.get("generated"),
            "calendars": [{k: c.get(k) for k in ("id", "title", "source", "color")}
                          for c in d.get("calendars") or []],
        }

    def schedules(self, settings: dict) -> list[dict]:
        """読み上げる予定（kind="once" の形）。連携がオフなら空。"""
        cfg = settings_of(settings)
        if not cfg["enabled"] or not cfg["calendars"]:
            return []
        wanted = set(cfg["calendars"])
        names = {c.get("id"): c.get("title") for c in self.data().get("calendars") or []}
        lead = int(cfg["lead"])
        out = []
        for ev in self.data().get("events") or []:
            if ev.get("all_day") or ev.get("calendar_id") not in wanted:
                continue
            try:
                start = datetime.fromisoformat(ev["start"]).astimezone().replace(tzinfo=None)
            except (KeyError, ValueError):
                continue
            at = start - timedelta(minutes=lead)
            title = (ev.get("title") or "").strip() or "予定"
            text = sentence(title, lead)
            action = {"type": "both" if cfg["sound"] else "speak", "text": text,
                      "voice": cfg["voice"], "volume": settings.get("default_volume", 0.6),
                      "repeat": 1}
            if cfg["sound"]:
                action["sound"] = cfg["sound"]
            key = hashlib.sha256(f"{ev.get('id')}|{lead}".encode()).hexdigest()[:12]
            out.append({
                "id": f"cal-{key}", "source": "calendar", "enabled": True, "kind": "once",
                "name": title, "date": at.date().isoformat(), "time": at.strftime("%H:%M"),
                "calendar": names.get(ev.get("calendar_id"), ""), "starts": start.strftime("%H:%M"),
                "lead_times": [], "lead_action": None, "skip": [], "action": action,
            })
        return out
