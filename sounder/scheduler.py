"""スケジュールの発火判定とバックグラウンドのティックループ。"""

from __future__ import annotations

import calendar
import threading
import time
from datetime import date, datetime, timedelta

DAY_LABELS = ["月", "火", "水", "木", "金", "土", "日"]
LAST_DAY = "last"

# スリープ復帰などで取りこぼした通知を、何秒前までなら鳴らすか
DEFAULT_GRACE = 120.0


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _at(day: date, minutes: int) -> datetime:
    return datetime.combine(day, datetime.min.time()) + timedelta(minutes=minutes)


def _matches_day_of_month(want, day: date) -> bool:
    """day_of_month（1〜31 または 'last'）がその日に当たるか。

    31 日指定は 31 日がある月だけ鳴る。2 月も含めて毎月鳴らしたい場合は 'last'（月末）を使う。
    """
    if want == LAST_DAY:
        return day.day == calendar.monthrange(day.year, day.month)[1]
    return day.day == want


def day_occurrences(sched: dict, day: date) -> list[tuple[datetime, str, int]]:
    """その日が基準日となる発火時刻を (時刻, 種別, 予告分) で返す。

    種別は "main" か "lead"。予告は基準時刻より前なので、前日にまたがることもある。
    """
    kind = sched["kind"]
    mains: list[datetime] = []

    if kind == "weekly":
        if day.weekday() in sched["days"]:
            mains.append(_at(day, _minutes(sched["time"])))
    elif kind == "monthly":
        if _matches_day_of_month(sched["day_of_month"], day):
            mains.append(_at(day, _minutes(sched["time"])))
    elif kind == "yearly":
        if day.month == sched["month"] and _matches_day_of_month(sched["day_of_month"], day):
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


# 次回を探すときにどれだけ先まで見るか（種別ごと）
HORIZONS = {"weekly": 21, "interval": 21, "once": 400, "monthly": 70, "yearly": 400}


def next_events(schedules: list[dict], *, now: datetime | None = None,
                limit: int = 8, per_schedule: int = 3,
                settings: dict | None = None) -> list[dict]:
    """有効なスケジュールの次回発火予定を時刻順に返す。

    settings を渡すと、禁止時間に当たるものに quiet=True を付ける。
    """
    now = now or datetime.now()
    settings = settings or {}
    found: list[tuple[datetime, dict]] = []
    for s in schedules:
        if not s.get("enabled"):
            continue
        day = now.date()
        end = day + timedelta(days=HORIZONS.get(s["kind"], 21))
        picked = 0
        while day <= end and picked < per_schedule:
            for when, tag, lead in sorted(day_occurrences(s, day)):
                if when <= now:
                    continue
                found.append((when, {
                    "schedule_id": s["id"], "name": s["name"], "tag": tag,
                    "lead": lead, "at": when.isoformat(timespec="seconds"),
                    "quiet": in_quiet_hours(settings, when),
                }))
                picked += 1
                if picked >= per_schedule:
                    break
            day += timedelta(days=1)
    found.sort(key=lambda x: x[0])
    return [e for _w, e in found[:limit]]


def outcome(sched: dict, when: datetime, tag: str, lead: int,
            log_entries: list[dict]) -> str | None:
    """過ぎた発火がどうなったか（fired / skipped / missed）を実行ログから探す。

    Scheduler._fire が残すログ（予定 id・名前・時刻）と突き合わせる。見つからなければ None。
    """
    label = f"{sched['name']}（{lead}分前の予告）" if tag == "lead" else sched["name"]
    stamp = f"{when:%m/%d %H:%M}"
    for e in log_entries:
        level = e.get("level")
        if level not in ("fired", "skipped", "missed") or e.get("schedule_id") != sched["id"]:
            continue
        msg = e.get("message") or ""
        if not (msg.startswith(label + " を") or msg.startswith(label + ":")):
            continue
        if level == "missed":
            if stamp in msg:
                return level
            continue
        try:
            ts = datetime.fromisoformat(e["ts"])
        except (KeyError, TypeError, ValueError):
            continue
        # tick は 1 秒ごとなので、ログの時刻は発火時刻の直後（猶予内）になる
        if when - timedelta(seconds=5) <= ts <= when + timedelta(seconds=DEFAULT_GRACE + 5):
            return level
    return None


def calendar_days(schedules: list[dict], start: date, days: int,
                  *, settings: dict | None = None, now: datetime | None = None,
                  log_entries: list[dict] | None = None) -> list[dict]:
    """週カレンダー・タイムライン用。start から days 日分、各日の発火予定を時刻順に並べて返す。

    無効なスケジュールも enabled=False として含める（画面で薄く見せるため）。
    禁止時間に当たる予定には quiet=True を付ける。過ぎた予定には、実行ログ（log_entries）から
    分かった結果を result（fired / skipped / missed / None）として付ける。
    """
    now = now or datetime.now()
    settings = settings or {}
    log_entries = log_entries or []
    out = []
    for i in range(days):
        day = start + timedelta(days=i)
        events = []
        for s in schedules:
            for when, tag, lead in day_occurrences(s, day):
                events.append({
                    "schedule_id": s["id"],
                    "name": s["name"],
                    "at": when.isoformat(timespec="seconds"),
                    "time": when.strftime("%H:%M"),
                    "tag": tag,
                    "lead": lead,
                    "enabled": bool(s.get("enabled")),
                    "kind": s["kind"],
                    "action_type": "sound" if tag == "lead" else s["action"]["type"],
                    "sound": ((s.get("lead_action") or {}) if tag == "lead"
                              else s["action"]).get("sound"),
                    "quiet": in_quiet_hours(settings, when),
                    "past": when <= now,
                    "result": outcome(s, when, tag, lead, log_entries) if when <= now else None,
                })
        events.sort(key=lambda e: e["at"])
        out.append({
            "date": day.isoformat(),
            "weekday": day.weekday(),
            "day": day.day,
            "month": day.month,
            "is_today": day == now.date(),
            "events": events,
        })
    return out


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
    if kind == "weekly":
        base = f"{describe_days(sched['days'])} {sched['time']}"
    elif kind == "monthly":
        base = f"{describe_day_of_month(sched['day_of_month'])} {sched['time']}"
    elif kind == "yearly":
        base = f"毎年{sched['month']}月{describe_day_of_month(sched['day_of_month'], bare=True)} {sched['time']}"
    elif kind == "once":
        base = f"{sched['date']} {sched['time']}"
    else:
        w = sched["window"]
        base = f"{describe_days(sched['days'])} {w['start']}〜{w['end']} の {sched['every_minutes']}分ごと"
    if sched.get("lead_times"):
        base += "（" + "・".join(f"{m}分前" for m in sched["lead_times"]) + "に予告）"
    return base


def describe_days(days: list[int]) -> str:
    if days == [0, 1, 2, 3, 4, 5, 6]:
        return "毎日"
    if days == [0, 1, 2, 3, 4]:
        return "平日"
    if days == [5, 6]:
        return "週末"
    return "".join(DAY_LABELS[d] for d in days) + "曜"


def describe_day_of_month(day, *, bare: bool = False) -> str:
    if day == LAST_DAY:
        return "月末" if bare else "毎月末"
    return f"{day}日" if bare else f"毎月{day}日"


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

    def _fire(self, sched: dict, when: datetime, tag: str, lead: int, now, settings) -> None:
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
            self.log("skipped", f"{label}: 禁止時間のためスキップしました", schedule_id=sched["id"])
            return

        action = self.action_for(sched, tag, lead, settings)
        self.log("fired", f"{label} を再生しました", schedule_id=sched["id"])
        self.store.mark_fired(sched["id"], when)
        self.player.play(action, settings=settings, label=label, queue=True)

        if sched["kind"] == "once" and tag == "main":
            try:
                self.store.patch(sched["id"], {**sched, "enabled": False})
                self.log("info", f"{name}: 単発の予定なのでオフにしました", schedule_id=sched["id"])
            except KeyError:
                pass

    def action_for(self, sched: dict, tag: str, lead: int, settings: dict) -> dict:
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
