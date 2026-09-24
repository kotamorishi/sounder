"""設定ファイル（JSON）の読み書きと検証。"""

from __future__ import annotations

import calendar
import json
import os
import re
import threading
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any

TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
KINDS = ("weekly", "monthly", "yearly", "once", "interval")
# 旧名（v0.1 の設定ファイル）を読み込めるようにする
KIND_ALIASES = {"daily": "weekly"}
LAST_DAY = "last"  # 「毎月末」を表す day_of_month の値
ACTION_TYPES = ("sound", "speak", "both")

DEFAULT_SETTINGS: dict[str, Any] = {
    "default_volume": 0.6,
    "master_enabled": True,
    # 静音時間帯：この範囲に入る通知はスキップする（深夜の誤作動対策）
    "quiet_hours": {"enabled": False, "start": "23:00", "end": "07:00"},
    "default_voice": "",
    "speak_rate": 180,
}


class ValidationError(ValueError):
    """入力値が不正なときに投げる。HTTP 400 として返す。"""


def _req_str(d: dict, key: str, label: str, maxlen: int = 200) -> str:
    v = d.get(key)
    if not isinstance(v, str) or not v.strip():
        raise ValidationError(f"{label}を入力してください")
    v = v.strip()
    if len(v) > maxlen:
        raise ValidationError(f"{label}が長すぎます（{maxlen}文字以内）")
    return v


def _time(v: Any, label: str) -> str:
    if not isinstance(v, str) or not TIME_RE.match(v):
        raise ValidationError(f"{label}は HH:MM 形式で指定してください")
    return v


def _volume(v: Any, fallback: float) -> float:
    if v is None or v == "":
        return fallback
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ValidationError("音量は数値で指定してください")
    if not 0.0 <= f <= 2.0:
        raise ValidationError("音量は 0〜2 の範囲で指定してください")
    return round(f, 3)


def _int_in(v: Any, lo: int, hi: int, label: str, fallback: int | None = None) -> int:
    if v in (None, "") and fallback is not None:
        return fallback
    try:
        i = int(v)
    except (TypeError, ValueError):
        raise ValidationError(f"{label}は整数で指定してください")
    if not lo <= i <= hi:
        raise ValidationError(f"{label}は {lo}〜{hi} の範囲で指定してください")
    return i


def _days(value: Any, *, required: bool) -> list[int]:
    """曜日の配列を検証する。required でなければ空なら毎日扱い。"""
    if not isinstance(value, list) or not value:
        if required:
            raise ValidationError("曜日を 1 つ以上選んでください")
        return [0, 1, 2, 3, 4, 5, 6]
    return sorted({_int_in(d, 0, 6, "曜日") for d in value})


def _day_of_month(value: Any) -> int | str:
    """1〜31、または「月末」を表す 'last'。"""
    if value == LAST_DAY:
        return LAST_DAY
    return _int_in(value, 1, 31, "日")


def _check_real_date(month: int, day: int) -> None:
    """2月30日のような存在しない日付を弾く（うるう年は許す）。"""
    if day > calendar.monthrange(2024, month)[1]:  # 2024 はうるう年
        raise ValidationError(f"{month}月{day}日は存在しません")


def validate_schedule(raw: Any, *, keep_id: str | None = None) -> dict[str, Any]:
    """Web UI から来たスケジュールを検証して正規化する。"""
    if not isinstance(raw, dict):
        raise ValidationError("スケジュールの形式が不正です")

    kind = KIND_ALIASES.get(raw.get("kind"), raw.get("kind"))
    if kind not in KINDS:
        raise ValidationError("繰り返し方の指定が不正です")

    s: dict[str, Any] = {
        "id": keep_id or raw.get("id") or uuid.uuid4().hex[:12],
        "name": _req_str(raw, "name", "名前", 80),
        "enabled": bool(raw.get("enabled", True)),
        "kind": kind,
        "note": (raw.get("note") or "").strip()[:500],
    }

    if kind == "weekly":
        s["time"] = _time(raw.get("time"), "時刻")
        s["days"] = _days(raw.get("days"), required=True)
    elif kind == "monthly":
        s["time"] = _time(raw.get("time"), "時刻")
        s["day_of_month"] = _day_of_month(raw.get("day_of_month"))
    elif kind == "yearly":
        s["time"] = _time(raw.get("time"), "時刻")
        s["month"] = _int_in(raw.get("month"), 1, 12, "月")
        s["day_of_month"] = _day_of_month(raw.get("day_of_month"))
        if s["day_of_month"] != LAST_DAY:
            _check_real_date(s["month"], s["day_of_month"])
    elif kind == "once":
        d = raw.get("date")
        if not isinstance(d, str) or not DATE_RE.match(d):
            raise ValidationError("日付は YYYY-MM-DD 形式で指定してください")
        try:
            date.fromisoformat(d)
        except ValueError:
            raise ValidationError("日付が存在しません")
        s["date"] = d
        s["time"] = _time(raw.get("time"), "時刻")
    else:  # interval
        s["every_minutes"] = _int_in(raw.get("every_minutes"), 1, 1440, "間隔（分）")
        win = raw.get("window") or {}
        start = _time(win.get("start", "09:00"), "開始時刻")
        end = _time(win.get("end", "21:00"), "終了時刻")
        s["window"] = {"start": start, "end": end}
        s["anchor"] = _time(raw.get("anchor", start), "基準時刻")
        s["days"] = _days(raw.get("days"), required=False)

    # 事前予告（お出かけ 10 分前など）
    leads = raw.get("lead_times") or []
    if not isinstance(leads, list):
        raise ValidationError("事前予告の形式が不正です")
    if len(leads) > 8:
        raise ValidationError("事前予告は 8 件までです")
    s["lead_times"] = sorted({_int_in(m, 1, 720, "事前予告（分）") for m in leads}, reverse=True)

    act = raw.get("action") or {}
    atype = act.get("type", "sound")
    if atype not in ACTION_TYPES:
        raise ValidationError("動作の種別が不正です")
    action: dict[str, Any] = {
        "type": atype,
        "volume": _volume(act.get("volume"), DEFAULT_SETTINGS["default_volume"]),
        "repeat": _int_in(act.get("repeat"), 1, 10, "繰り返し回数", 1),
    }
    if atype in ("sound", "both"):
        action["sound"] = _req_str(act, "sound", "サウンド", 300)
    if atype in ("speak", "both"):
        action["text"] = _req_str(act, "text", "読み上げる文章", 500)
        action["voice"] = (act.get("voice") or "").strip()[:80]
        action["rate"] = _int_in(act.get("rate"), 90, 400, "話す速さ", 180)
    s["action"] = action

    lead = raw.get("lead_action") or {}
    if s["lead_times"]:
        s["lead_action"] = {
            "type": "sound",
            "sound": (lead.get("sound") or "builtin:melody_notice").strip()[:300],
            "volume": _volume(lead.get("volume"), action["volume"]),
            "repeat": 1,
            "speak_remaining": bool(lead.get("speak_remaining", False)),
            "voice": (lead.get("voice") or "").strip()[:80],
        }
    else:
        s["lead_action"] = None

    s["last_fired"] = raw.get("last_fired") if isinstance(raw.get("last_fired"), str) else None
    return s


def validate_settings(raw: Any, current: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValidationError("設定の形式が不正です")
    out = dict(current)
    if "default_volume" in raw:
        out["default_volume"] = _volume(raw["default_volume"], current["default_volume"])
    if "master_enabled" in raw:
        out["master_enabled"] = bool(raw["master_enabled"])
    if "default_voice" in raw:
        out["default_voice"] = (raw["default_voice"] or "").strip()[:80]
    if "speak_rate" in raw:
        out["speak_rate"] = _int_in(raw["speak_rate"], 90, 400, "話す速さ", current["speak_rate"])
    if "quiet_hours" in raw:
        q = raw["quiet_hours"] or {}
        out["quiet_hours"] = {
            "enabled": bool(q.get("enabled", False)),
            "start": _time(q.get("start", current["quiet_hours"]["start"]), "静音開始"),
            "end": _time(q.get("end", current["quiet_hours"]["end"]), "静音終了"),
        }
    return out


class Store:
    """config.json を保持する。読み書きはロックで直列化し、原子的に保存する。"""

    def __init__(self, path: Path, sound_check=None) -> None:
        self.path = path
        # 保存前にサウンド参照を確かめるフック（Player.resolve を挿す）
        self.sound_check = sound_check
        self._lock = threading.RLock()
        self._data = self._load()

    def _check_sounds(self, s: dict[str, Any]) -> None:
        if not self.sound_check:
            return
        refs = [(s["action"].get("sound"), "サウンド")]
        if s.get("lead_action"):
            refs.append((s["lead_action"].get("sound"), "予告のサウンド"))
        for ref, label in refs:
            if not ref:
                continue
            try:
                self.sound_check(ref)
            except Exception as exc:
                raise ValidationError(f"{label}が見つかりません（{exc}）")

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "settings": dict(DEFAULT_SETTINGS), "schedules": []}
        try:
            data = json.loads(self.path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            # 壊れた設定で起動不能になるより、退避して初期状態から始める
            backup = self.path.with_suffix(f".broken-{datetime.now():%Y%m%d%H%M%S}.json")
            try:
                self.path.rename(backup)
                print(f"[sounder] 設定を読めませんでした（{exc}）。{backup.name} に退避しました。")
            except OSError:
                pass
            return {"version": 1, "settings": dict(DEFAULT_SETTINGS), "schedules": []}
        settings = dict(DEFAULT_SETTINGS)
        loaded = data.get("settings")
        if isinstance(loaded, dict):
            for k, v in loaded.items():
                if k in settings:
                    settings[k] = v
        schedules = []
        for raw in data.get("schedules") or []:
            try:
                schedules.append(validate_schedule(raw, keep_id=raw.get("id")))
            except ValidationError as exc:
                print(f"[sounder] スケジュール {raw.get('name')!r} を読み飛ばしました: {exc}")
        return {"version": 1, "settings": settings, "schedules": schedules}

    # --- 読み取り ---------------------------------------------------------

    @property
    def settings(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._data["settings"])

    def schedules(self) -> list[dict[str, Any]]:
        with self._lock:
            return json.loads(json.dumps(self._data["schedules"]))

    def get(self, sid: str) -> dict[str, Any] | None:
        with self._lock:
            for s in self._data["schedules"]:
                if s["id"] == sid:
                    return json.loads(json.dumps(s))
        return None

    # --- 書き込み ---------------------------------------------------------

    def _save_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), "utf-8")
        os.replace(tmp, self.path)

    def add(self, raw: Any) -> dict[str, Any]:
        s = validate_schedule(raw)
        self._check_sounds(s)
        with self._lock:
            if len(self._data["schedules"]) >= 200:
                raise ValidationError("スケジュールは 200 件までです")
            self._data["schedules"].append(s)
            self._save_locked()
        return s

    def replace(self, sid: str, raw: Any) -> dict[str, Any]:
        with self._lock:
            for i, old in enumerate(self._data["schedules"]):
                if old["id"] == sid:
                    s = validate_schedule(raw, keep_id=sid)
                    self._check_sounds(s)
                    s["last_fired"] = old.get("last_fired")
                    self._data["schedules"][i] = s
                    self._save_locked()
                    return s
        raise KeyError(sid)

    def patch(self, sid: str, changes: dict[str, Any]) -> dict[str, Any]:
        """enabled の切り替えなど部分更新。"""
        with self._lock:
            for i, old in enumerate(self._data["schedules"]):
                if old["id"] == sid:
                    merged = dict(old)
                    merged.update(changes)
                    s = validate_schedule(merged, keep_id=sid)
                    self._check_sounds(s)
                    s["last_fired"] = merged.get("last_fired") if isinstance(
                        merged.get("last_fired"), str) else old.get("last_fired")
                    self._data["schedules"][i] = s
                    self._save_locked()
                    return s
        raise KeyError(sid)

    def delete(self, sid: str) -> None:
        with self._lock:
            before = len(self._data["schedules"])
            self._data["schedules"] = [s for s in self._data["schedules"] if s["id"] != sid]
            if len(self._data["schedules"]) == before:
                raise KeyError(sid)
            self._save_locked()

    def mark_fired(self, sid: str, when: datetime) -> None:
        with self._lock:
            for s in self._data["schedules"]:
                if s["id"] == sid:
                    s["last_fired"] = when.isoformat(timespec="seconds")
                    self._save_locked()
                    return

    def update_settings(self, raw: Any) -> dict[str, Any]:
        with self._lock:
            self._data["settings"] = validate_settings(raw, self._data["settings"])
            self._save_locked()
            return dict(self._data["settings"])
