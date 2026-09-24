"""スケジュールの発火判定とバックグラウンドのティックループ。"""

from __future__ import annotations

import threading
import time
from datetime import date, datetime, timedelta

DAY_LABELS = ["月", "火", "水", "木", "金", "土", "日"]

# スリープ復帰などで取りこぼした通知を、何秒前までなら鳴らすか
DEFAULT_GRACE = 120.0


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _at(day: date, minutes: int) -> datetime:
    return datetime.combine(day, datetime.min.time()) + timedelta(minutes=minutes)


def day_occurrences(sched: dict, day: date) -> list[tuple[datetime, str, int]]:
    """その日が基準日となる発火時刻を (時刻, 種別, 予告分) で返す。

    種別は "main" か "lead"。予告は基準時刻より前なので、前日にまたがることもある。
    """
    kind = sched["kind"]
    mains: list[datetime] = []

    if kind == "daily":
        if day.weekday() in sched["days"]:
            mains.append(_at(day, _minutes(sched["time"])))
    elif kind == "once":
        if sched["date"] == day.isoformat():
            mains.append(_at(day, _minutes(sched["time"])))
    elif kind == "interval":
        if day.weekday() in sched.get("days", list(range(7))):
            start = _minutes(sched["window"]["start"])
            end = _minutes(sched["window"]["end"])
            if end <= start:
                end += 1440  # 日付をまたぐ時間帯
            step = max(1, int(sched["every_minutes"]))
            t = _minutes(sched.get("anchor", sched["window"]["start"]))
            while t < start:
                t += step
            while t <= end:
                mains.append(_at(day, t))
                t += step

    out: list[tuple[datetime, str, int]] = []
    for m in mains:
        out.append((m, "main", 0))
        for lead in sched.get("lead_times") or []:
            out.append((m - timedelta(minutes=lead), "lead", lead))
    return out


def events_between(sched: dict, lo: datetime, hi: datetime) -> list[tuple[datetime, str, int]]:
    """lo < t <= hi に入る発火時刻を返す。"""
    found = []
    day = (lo - timedelta(days=1)).date()
    last = (hi + timedelta(days=1)).date()
    while day <= last:
        for when, tag, lead in day_occurrences(sched, day):
            if lo < when <= hi:
                found.append((when, tag, lead))
        day += timedelta(days=1)
    return sorted(found)


def next_events(schedules: list[dict], *, now: datetime | None = None,
                limit: int = 8, horizon_days: int = 21) -> list[dict]:
    """有効なスケジュールの次回発火予定を時刻順に返す。"""
    now = now or datetime.now()
    found: list[tuple[datetime, dict]] = []
    for s in schedules:
        if not s.get("enabled"):
            continue
        day = now.date()
        end = day + timedelta(days=horizon_days)
        picked = 0
        while day <= end and picked < 3:
            for when, tag, lead in sorted(day_occurrences(s, day)):
                if when <= now:
                    continue
                found.append((when, {
                    "schedule_id": s["id"], "name": s["name"], "tag": tag,
                    "lead": lead, "at": when.isoformat(timespec="seconds"),
                }))
                picked += 1
                if picked >= 3:
                    break
            day += timedelta(days=1)
    found.sort(key=lambda x: x[0])
    return [e for _w, e in found[:limit]]


def in_quiet_hours(settings: dict, when: datetime) -> bool:
    q = settings.get("quiet_hours") or {}
    if not q.get("enabled"):
        return False
    start, end = _minutes(q["start"]), _minutes(q["end"])
    cur = when.hour * 60 + when.minute
    if start == end:
        return False
    if start < end:
        return start <= cur < end
    return cur >= start or cur < end  # 日付をまたぐ


def describe(sched: dict) -> str:
    """UI とログ用の 1 行説明。"""
    kind = sched["kind"]
    if kind == "daily":
        days = sched["days"]
        if days == [0, 1, 2, 3, 4, 5, 6]:
            when = "毎日"
        elif days == [0, 1, 2, 3, 4]:
            when = "平日"
        elif days == [5, 6]:
            when = "週末"
        else:
            when = "".join(DAY_LABELS[d] for d in days) + "曜"
        base = f"{when} {sched['time']}"
    elif kind == "once":
        base = f"{sched['date']} {sched['time']}"
    else:
        w = sched["window"]
        base = f"{w['start']}〜{w['end']} の {sched['every_minutes']}分ごと"
    if sched.get("lead_times"):
        base += "（" + "・".join(f"{m}分前" for m in sched["lead_times"]) + "に予告）"
    return base


class Scheduler:
    """1 秒ごとに時計を見て、その間に来た発火時刻を鳴らす。"""

    def __init__(self, store, player, log, *, grace: float = DEFAULT_GRACE) -> None:
        self.store = store
        self.player = player
        self.log = log
        self.grace = grace
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_tick = datetime.now()
        self._fired: set[str] = set()

    def start(self) -> None:
        self._last_tick = datetime.now()
        self._thread = threading.Thread(target=self._loop, name="scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick(datetime.now())
            except Exception as exc:  # ループは絶対に止めない
                self.log("error", f"スケジューラでエラーが発生しました: {exc!r}")
            self._stop.wait(1.0)

    def tick(self, now: datetime) -> None:
        lo, self._last_tick = self._last_tick, now
        if now < lo:  # 時計が巻き戻った（手動変更・夏時間）
            lo = now - timedelta(seconds=1)
        settings = self.store.settings
        for sched in self.store.schedules():
            if not sched.get("enabled"):
                continue
            for when, tag, lead in events_between(sched, lo, now):
                key = f"{sched['id']}|{when.isoformat()}|{tag}{lead}"
                if key in self._fired:
                    continue
                self._fired.add(key)
                self._fire(sched, when, tag, lead, now, settings)
        if len(self._fired) > 4000:
            self._fired = set(list(self._fired)[-1000:])

    def _fire(self, sched: dict, when: datetime, tag: str, lead: int,
              now: datetime, settings: dict) -> None:
        name = sched["name"]
        label = f"{name}（{lead}分前の予告）" if tag == "lead" else name
        behind = (now - when).total_seconds()

        if behind > self.grace:
            self.log("missed", f"{label}: {when:%m/%d %H:%M} の予定を過ぎていたため鳴らしませんでした"
                               f"（{int(behind // 60)}分遅れ）", schedule_id=sched["id"])
            return
        if not settings.get("master_enabled", True):
            self.log("skipped", f"{label}: 全体がオフのためスキップしました", schedule_id=sched["id"])
            return
        if in_quiet_hours(settings, when):
            self.log("skipped", f"{label}: 静音時間帯のためスキップしました", schedule_id=sched["id"])
            return

        action = self._action_for(sched, tag, lead, settings)
        self.log("fired", f"{label} を再生しました", schedule_id=sched["id"])
        self.store.mark_fired(sched["id"], when)
        self.player.play(action, settings=settings, label=label)

        if sched["kind"] == "once" and tag == "main":
            try:
                self.store.patch(sched["id"], {**sched, "enabled": False})
                self.log("info", f"{name}: 単発の予定なのでオフにしました", schedule_id=sched["id"])
            except KeyError:
                pass

    def _action_for(self, sched: dict, tag: str, lead: int, settings: dict) -> dict:
        if tag == "main":
            return sched["action"]
        la = dict(sched.get("lead_action") or {"type": "sound", "sound": "builtin:melody_notice"})
        if la.pop("speak_remaining", False):
            text = f"{sched['name']}まで、あと{lead}分です。"
            la = {"type": "both", "sound": la.get("sound"), "text": text,
                  "voice": la.get("voice") or settings.get("default_voice"),
                  "rate": settings.get("speak_rate", 180),
                  "volume": la.get("volume", settings.get("default_volume")), "repeat": 1}
        return la
