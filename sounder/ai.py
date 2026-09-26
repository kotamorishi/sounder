"""AI との連携（オプション）。OpenAI 互換の API（vLLM・Ollama・LM Studio など）に文を作らせる。

いまできること:
  - 朝のお知らせ（briefing）… 決まった時刻に、今日のカレンダーの予定と祝日・休校日をまとめて読み上げる

設定 settings["ai"] で有効にしたときだけ動く。予定の中身は、設定した AI サーバにだけ送る。
AI が答えないとき（止まっている・遅い）は、AI を使わない決まった文で読み上げる（鳴らし忘れない）。
お知らせの文は鳴る少し前（PREPARE_AHEAD）に作っておき、日付と予定の中身が同じあいだは作り直さない。
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta

from . import daysoff
from .calendarfeed import settings_of as calendar_settings

DEFAULTS = {
    "enabled": False,
    "url": "http://spark-1:8000/v1",   # OpenAI 互換の API の入り口（/chat/completions の手前まで）
    "model": "",                       # "" なら /models の最初のもの
    "api_key": "",
    "briefing": {"enabled": False, "time": "07:30", "days": [0, 1, 2, 3, 4, 5, 6],
                 "sound": "builtin:melody_morning", "voice": ""},
}
WEEKDAYS = "月火水木金土日"
PREPARE_AHEAD = 10 * 60      # 何秒前からお知らせの文を作り始めるか
TIMEOUT = 60


def settings_of(settings: dict) -> dict:
    cfg = {**DEFAULTS, **(settings.get("ai") or {})}
    cfg["briefing"] = {**DEFAULTS["briefing"], **(cfg.get("briefing") or {})}
    return cfg


def _clock(hhmm: str) -> str:
    """'14:00' → '14時'、'14:30' → '14時30分'（読み上げやすく）"""
    h, m = int(hhmm[:2]), int(hhmm[3:5])
    return f"{h}時" if m == 0 else f"{h}時{m}分"


class AI:
    def __init__(self, calendar=None, *, log=None, store=None) -> None:
        self.calendar = calendar        # calendarfeed.CalendarFeed
        self.store = store              # 休校日の暦を使っている予定があれば、その休みも伝える
        self._log = log or (lambda *a, **k: None)
        self._lock = threading.Lock()
        self._texts: dict[str, str] = {}   # 鍵（日付＋予定の中身）→ 作った文
        self._busy: set[str] = set()

    # --- AI サーバ ---------------------------------------------------------

    def _request(self, cfg: dict, path: str, body: dict | None = None, timeout: float = TIMEOUT):
        headers = {"Content-Type": "application/json"}
        if cfg.get("api_key"):
            headers["Authorization"] = f"Bearer {cfg['api_key']}"
        req = urllib.request.Request(cfg["url"].rstrip("/") + path, headers=headers,
                                     data=json.dumps(body).encode() if body is not None else None)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)

    def models(self, cfg: dict) -> list[str]:
        return [m.get("id") for m in self._request(cfg, "/models", timeout=10).get("data") or []]

    def check(self, settings: dict) -> dict:
        """接続テスト（設定画面のボタン）。"""
        cfg = settings_of(settings)
        try:
            models = self.models(cfg)
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": f"{cfg['url']} に接続できません（{exc}）"}
        return {"ok": bool(models), "models": models,
                "model": cfg["model"] or (models[0] if models else "")}

    def complete(self, settings: dict, prompt: str, *, max_tokens: int = 500) -> str:
        cfg = settings_of(settings)
        model = cfg["model"] or self.models(cfg)[0]
        d = self._request(cfg, "/chat/completions", {
            "model": model, "temperature": 0.3, "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        })
        return (d["choices"][0]["message"]["content"] or "").strip()

    # --- 朝のお知らせ ------------------------------------------------------

    def _day(self, settings: dict, day: date) -> dict:
        """その日の材料（カレンダーの予定・祝日・休校日）。"""
        events = []
        cal = self.calendar.data() if self.calendar else {}
        wanted = set(calendar_settings(settings).get("calendars") or [])
        for ev in cal.get("events") or []:
            if wanted and ev.get("calendar_id") not in wanted:
                continue
            try:
                start = datetime.fromisoformat(ev["start"]).astimezone().replace(tzinfo=None)
                end = datetime.fromisoformat(ev.get("end") or ev["start"]).astimezone().replace(tzinfo=None)
            except (KeyError, ValueError):
                continue
            if ev.get("all_day"):
                if not (start.date() <= day < max(end.date(), start.date() + timedelta(days=1))):
                    continue
                events.append({"all_day": True, "title": (ev.get("title") or "").strip()})
            elif start.date() == day:
                events.append({"all_day": False, "start": start.strftime("%H:%M"),
                               "end": end.strftime("%H:%M"), "title": (ev.get("title") or "").strip(),
                               "location": ev.get("location") or ""})
        notes = []
        schedules = self.store.schedules() if self.store else []
        cals = ["on_holidays"] + [c for c in daysoff.CALENDARS if c.startswith("tdsb_")
                                  and any(c in (s.get("skip") or []) for s in schedules)]
        for c in dict.fromkeys(cals):
            r = daysoff.reason(c, day)
            if r and r != "週末" and r not in notes:
                notes.append(r if c == "on_holidays" else f"学校は{r}で休み")
        return {"date": day, "events": events, "notes": notes}

    def _key(self, info: dict) -> str:
        raw = json.dumps({"d": info["date"].isoformat(), "e": info["events"], "n": info["notes"]},
                         ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    @staticmethod
    def prompt(info: dict) -> str:
        d = info["date"]
        lines = []
        for e in info["events"]:
            if e["all_day"]:
                lines.append(f"- 終日: {e['title']}")
            else:
                loc = f"（場所: {e['location']}）" if e.get("location") else ""
                lines.append(f"- {e['start']}〜{e['end']}: {e['title']}{loc}")
        # 祝日・休みが無い日は書かない（「祝日ではありません」と言い出すので）
        notes = f"今日の祝日・休み: {'、'.join(info['notes'])}\n" if info["notes"] else ""
        return (
            f"今日は{d.year}年{d.month}月{d.day}日（{WEEKDAYS[d.weekday()]}曜日）です。\n"
            f"{notes}"
            f"家族のカレンダーの今日の予定:\n" + ("\n".join(lines) or "（予定なし）") + "\n\n"
            "朝、家のスピーカーで読み上げる「今日の予定のお知らせ」を作ってください。\n"
            "条件: 日本語の話し言葉。30秒以内（200字程度まで）。時刻は「14時」「14時30分」のように。"
            "箇条書き・記号・絵文字・かっこ書きは使わない。英語の予定名はそのまま。"
            "祝日・休みが書いてあれば最初に触れ、書いていなければ祝日の話はしない。"
            "予定が無ければ、ゆっくり過ごせる日だと短く伝える。"
            "前置きや説明は書かず、読み上げる文だけを出力する。"
        )

    @staticmethod
    def fallback(info: dict) -> str:
        """AI が使えないときの決まった文。"""
        d = info["date"]
        parts = [f"おはようございます。{d.month}月{d.day}日、{WEEKDAYS[d.weekday()]}曜日です。"]
        if info["notes"]:
            parts.append("今日は" + "、".join(info["notes"]) + "です。")
        timed = [e for e in info["events"] if not e["all_day"]]
        allday = [e for e in info["events"] if e["all_day"]]
        if not info["events"]:
            parts.append("今日のカレンダーの予定はありません。")
        for e in allday:
            parts.append(f"今日は{e['title']}です。")
        for e in timed:
            parts.append(f"{_clock(e['start'])}から、{e['title']}。")
        return "".join(parts)

    def briefing_text(self, settings: dict, day: date, *, wait: bool = False) -> str:
        """その日のお知らせの文。作ってあればそれ、無ければ作り始めて（wait なら待って）返す。"""
        info = self._day(settings, day)
        key = self._key(info)
        with self._lock:
            if key in self._texts:
                return self._texts[key]
            busy = key in self._busy
            if not busy:
                self._busy.add(key)
        if not busy:
            if wait:
                self._generate(settings, info, key)
            else:
                threading.Thread(target=self._generate, args=(settings, info, key),
                                 name="ai-briefing", daemon=True).start()
        with self._lock:
            return self._texts.get(key) or self.fallback(info)

    def _generate(self, settings: dict, info: dict, key: str) -> None:
        t = time.time()
        try:
            text = self.complete(settings, self.prompt(info))
            if not text:
                raise ValueError("空の答え")
            self._log("info", f"AI が今日のお知らせを作りました（{time.time() - t:.1f} 秒）")
        except (OSError, ValueError, KeyError, IndexError) as exc:
            text = self.fallback(info)
            self._log("error", f"AI に接続できないので、決まった文でお知らせします（{exc}）")
        with self._lock:
            self._texts = {k: v for k, v in list(self._texts.items())[-20:]}
            self._texts[key] = text
            self._busy.discard(key)

    # --- スケジューラから --------------------------------------------------

    def schedules(self, settings: dict) -> list[dict]:
        """朝のお知らせを、毎週の予定の形で返す（オフなら空）。文は作ってあるものを使う。"""
        cfg = settings_of(settings)
        b = cfg["briefing"]
        if not cfg["enabled"] or not b["enabled"]:
            return []
        now = datetime.now()
        at = datetime.combine(now.date(), datetime.strptime(b["time"], "%H:%M").time())
        day = now.date() if now <= at + timedelta(minutes=5) else now.date() + timedelta(days=1)
        info = self._day(settings, day)
        with self._lock:
            text = self._texts.get(self._key(info)) or self.fallback(info)
        return [{
            "id": "ai-briefing", "source": "ai", "enabled": True, "kind": "weekly",
            "name": "今日の予定のお知らせ", "time": b["time"], "days": sorted(b["days"]),
            "lead_times": [], "lead_action": None, "skip": [],
            "action": self.briefing_action(settings, text),
        }]

    @staticmethod
    def briefing_action(settings: dict, text: str) -> dict:
        """お知らせを鳴らす中身（前に鳴らす音 ＋ 読み上げ）。"""
        b = settings_of(settings)["briefing"]
        action = {"type": "both" if b["sound"] else "speak", "text": text, "voice": b["voice"],
                  "volume": settings.get("default_volume", 0.6), "repeat": 1}
        if b["sound"]:
            action["sound"] = b["sound"]
        return action

    def prepare(self, now: datetime, settings: dict) -> None:
        """鳴る少し前に、お知らせの文を AI に作らせておく（スケジューラが毎回呼ぶ）。"""
        cfg = settings_of(settings)
        b = cfg["briefing"]
        if not cfg["enabled"] or not b["enabled"] or now.weekday() not in b["days"]:
            return
        at = datetime.combine(now.date(), datetime.strptime(b["time"], "%H:%M").time())
        if timedelta(0) <= at - now <= timedelta(seconds=PREPARE_AHEAD):
            self.briefing_text(settings, now.date())
