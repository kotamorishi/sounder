"""Claude API を使う機能（AI 連携で「Claude API」を選んでいるときだけ）。

  1. parse_schedule … 「毎週火金の7時半にゴミ出し、15分前にも」→ 予定の下書き（保存はしない。画面で確かめてから）
  2. check_closure  … 冬の朝、TDSB が悪天候などで休校かをウェブ検索で確かめる（休校なら学校の休みとして扱う）
  3. read_notice    … 学校のお知らせ（PDF・写真）から日付と行事を読み取る（選んだものを予定にする）

どれも答えは決まった形（JSON スキーマ）で受け取り、sounder 側の検証（config.validate_schedule など）を通す。
ai.AI._request（Claude API を urllib で直接呼ぶ）を使う。
"""

from __future__ import annotations

import json
import threading
from datetime import date, datetime
from pathlib import Path

from . import ai as ai_mod
from . import tones

WEEKDAYS = "月火水木金土日"
SOUND_KEYS = [f"builtin:{k}" for k in tones.BUILTINS]
SOUND_HELP = "、".join(f"{f'builtin:{k}'}={label}" for k, (label, _fn) in tones.BUILTINS.items())

# 天気・休校を探すときに見てよいサイト（公式と、トロントの主なニュース）
# （thestar.com は Anthropic の検索から見られないので入れない。入れると 400 になる）
CLOSURE_DOMAINS = ["tdsb.on.ca", "cbc.ca", "cp24.com", "toronto.citynews.ca", "globalnews.ca"]


class ClaudeUnavailable(ValueError):
    """Claude API を選んでいない、キーが無い、断られた、など。"""


def _claude(settings: dict) -> dict:
    cfg = ai_mod.settings_of(settings)
    if not cfg["enabled"] or cfg.get("provider") != "anthropic":
        raise ClaudeUnavailable("設定の AI 連携で「Claude API」を選んでください")
    if not cfg.get("claude_api_key"):
        raise ClaudeUnavailable("Claude API の API キーが入っていません")
    return cfg


def _check(d: dict) -> None:
    if d.get("stop_reason") == "refusal":
        raise ClaudeUnavailable("Claude がこの依頼を断りました")
    if d.get("stop_reason") == "max_tokens":
        raise ClaudeUnavailable("Claude の答えが途中で切れました")


def _json_answer(d: dict) -> dict:
    """structured outputs の答え（text ブロックの JSON）を取り出す。"""
    _check(d)
    text = "".join(b.get("text", "") for b in d.get("content") or [] if b.get("type") == "text")
    try:
        return json.loads(text)
    except ValueError:
        raise ClaudeUnavailable("Claude の答えを読めませんでした") from None


def _body(cfg: dict, content, schema: dict, *, effort: str = "low") -> dict:
    return {
        "model": cfg.get("claude_model") or ai_mod.CLAUDE_MODEL,
        "max_tokens": 16000,
        "fallbacks": "default",
        "output_config": {"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        "messages": [{"role": "user", "content": content}],
    }


# --- 1. 言葉で予定を作る -----------------------------------------------------------

SCHEDULE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "kind", "time", "days", "date", "day_of_month", "month", "every_minutes",
                 "window_start", "window_end", "lead_times", "speak_text", "sound", "lead_speak_remaining",
                 "skip", "outing", "note", "understood"],
    "properties": {
        "name": {"type": "string", "description": "予定の短い名前（例: ゴミ出し）"},
        "kind": {"type": "string", "enum": ["weekly", "monthly", "yearly", "once", "interval"]},
        "time": {"type": "string", "description": "鳴らす時刻 HH:MM（24 時間）。interval では空"},
        "days": {"type": "array", "items": {"type": "integer", "enum": [0, 1, 2, 3, 4, 5, 6]},
                 "description": "曜日（0=月 … 6=日）。weekly と interval で使う"},
        "date": {"type": "string", "description": "once の日付 YYYY-MM-DD。それ以外は空"},
        "day_of_month": {"type": "string", "description": "monthly / yearly の日（\"1\"〜\"31\" か月末なら \"last\"）。それ以外は空"},
        "month": {"type": "integer", "description": "yearly の月（1〜12）。それ以外は 0"},
        "every_minutes": {"type": "integer", "description": "interval の間隔（分）。それ以外は 0"},
        "window_start": {"type": "string", "description": "interval の開始 HH:MM。それ以外は空"},
        "window_end": {"type": "string", "description": "interval の終了 HH:MM。それ以外は空"},
        "lead_times": {"type": "array", "items": {"type": "integer"},
                       "description": "本番の何分前に予告するか（例: [15, 5]）。なければ []"},
        "speak_text": {"type": "string", "description": "本番で読み上げる文（話し言葉）。読み上げないなら空"},
        "sound": {"type": "string", "enum": [""] + SOUND_KEYS, "description": "本番で鳴らす音。音なしなら空"},
        "lead_speak_remaining": {"type": "boolean", "description": "予告で「○○まで、あと N 分です」と読み上げるか"},
        "skip": {"type": "array", "items": {"type": "string", "enum": ["on_holidays", "tdsb_elementary", "tdsb_secondary"]},
                 "description": "鳴らさない日: on_holidays=オンタリオ州の祝日、tdsb_elementary=TDSB 小学校の休校日、tdsb_secondary=中高の休校日"},
        "outing": {"type": "boolean", "description": "出かける予定で、天気と服装も読み上げるとよいか"},
        "note": {"type": "string", "description": "メモ（不要なら空）"},
        "understood": {"type": "boolean", "description": "予定として理解できたか。曖昧で作れないなら false"},
    },
}


def parse_schedule(ai, settings: dict, text: str, today: date | None = None) -> dict:
    """文章 → 予定の下書き（validate_schedule に通せる形）。理解できなければ ClaudeUnavailable。"""
    cfg = _claude(settings)
    today = today or date.today()
    prompt = (
        f"今日は{today.isoformat()}（{WEEKDAYS[today.weekday()]}曜日）、場所はカナダ・トロントの家庭です。\n"
        "次の依頼を、家のスピーカーで鳴らす予定に変換してください。\n"
        "決まり: 曜日の指定があれば weekly（「毎日」は全曜日、「平日」は 0〜4）。日付が 1 回だけなら once。"
        "毎月の日付なら monthly、毎年なら yearly、「○分ごと」なら interval。時刻は 24 時間の HH:MM。"
        "読み上げは、依頼に合った短い話し言葉（例:「ゴミ出しの時間です。」）。音の指定がなければ "
        "builtin:doorbell を使う。「祝日は休み」なら skip に on_holidays、「学校の休みの日は鳴らさない」なら "
        "tdsb_elementary（中高と言われたら tdsb_secondary）。出かける予定（登校・お出かけ・お迎えなど）なら outing を true。\n"
        f"使える音: {SOUND_HELP}\n"
        f"依頼: {text}"
    )
    d = _json_answer(ai._request(cfg, "/messages", _body(cfg, prompt, SCHEDULE_SCHEMA)))
    if not d.get("understood"):
        raise ClaudeUnavailable("予定として読み取れませんでした。曜日や時刻を入れてもう一度どうぞ")
    action_type = "both" if d["sound"] and d["speak_text"] else "speak" if d["speak_text"] else "sound"
    raw = {
        "name": d["name"][:80] or "予定", "kind": d["kind"], "note": d["note"],
        "lead_times": d["lead_times"][:8], "skip": d["skip"], "outing": d["outing"],
        "action": {"type": action_type, "sound": d["sound"] or "builtin:doorbell", "text": d["speak_text"],
                   "volume": settings.get("default_volume", 0.6)},
        "lead_action": {"sound": "builtin:melody_notice", "speak_remaining": d["lead_speak_remaining"]},
    }
    if d["kind"] in ("weekly", "monthly", "yearly", "once"):
        raw["time"] = d["time"]
    if d["kind"] in ("weekly", "interval"):
        raw["days"] = d["days"]
    if d["kind"] in ("monthly", "yearly"):
        raw["day_of_month"] = d["day_of_month"]
    if d["kind"] == "yearly":
        raw["month"] = d["month"]
    if d["kind"] == "once":
        raw["date"] = d["date"]
    if d["kind"] == "interval":
        raw.update(every_minutes=d["every_minutes"],
                   window={"start": d["window_start"], "end": d["window_end"]})
    return raw


# --- 2. 休校（スノーデー）の確認 ---------------------------------------------------

REPORT_TOOL = {
    "name": "report_school_status",
    "description": "今日の TDSB（トロント教育委員会）の学校が、悪天候などで休校かどうかを報告する。調べ終えたら必ず 1 回呼ぶ。",
    "strict": True,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["closed", "reason", "source_url", "confidence"],
        "properties": {
            "closed": {"type": "boolean", "description": "今日 TDSB の学校が休校（全校の授業なし）なら true"},
            "reason": {"type": "string", "description": "休校の理由を日本語で短く（例: 大雪のため）。休校でなければ空"},
            "source_url": {"type": "string", "description": "根拠にしたページの URL。見つからなければ空"},
            "confidence": {"type": "string", "enum": ["high", "low"],
                           "description": "今日の日付の公式発表やニュースで確かめられたら high"},
        },
    },
}


def check_closure(ai, settings: dict, day: date) -> dict:
    """その日 TDSB が休校かを、ウェブ検索で確かめる。{closed, reason, source_url, confidence}。"""
    cfg = _claude(settings)
    prompt = (
        f"今日は{day.isoformat()}（{WEEKDAYS[day.weekday()]}曜日）です。"
        "トロントの TDSB（Toronto District School Board）の学校が、今日、悪天候（大雪・吹雪・凍結など）や緊急事態で"
        "休校になっているかを、TDSB の公式サイトやトロントのニュースで確かめてください。"
        "通常の PA デーや長期休みは対象外です。古い年の記事と取り違えないよう、今日の日付の発表かを必ず確かめてください。"
        "確かめ終えたら report_school_status を 1 回だけ呼んでください。"
    )
    messages = [{"role": "user", "content": prompt}]
    body = {
        "model": cfg.get("claude_model") or ai_mod.CLAUDE_MODEL,
        "max_tokens": 16000,
        "fallbacks": "default",
        "output_config": {"effort": "medium"},
        "tools": [{"type": "web_search_20260209", "name": "web_search", "max_uses": 5,
                   "allowed_domains": CLOSURE_DOMAINS}, REPORT_TOOL],
        "tool_choice": {"type": "auto"},
        "messages": messages,
    }
    for _ in range(4):   # 検索が長引くと pause_turn で止まるので、そのまま続きを頼む
        d = ai._request(cfg, "/messages", body, timeout=180)
        _check(d)
        for block in d.get("content") or []:
            if block.get("type") == "tool_use" and block.get("name") == REPORT_TOOL["name"]:
                r = block.get("input") or {}
                return {"closed": bool(r.get("closed")), "reason": str(r.get("reason") or "")[:100],
                        "source_url": str(r.get("source_url") or "")[:300],
                        "confidence": "high" if r.get("confidence") == "high" else "low"}
        if d.get("stop_reason") != "pause_turn":
            break
        messages.append({"role": "assistant", "content": d["content"]})
    raise ClaudeUnavailable("休校かどうかの答えをもらえませんでした")


class Closures:
    """確かめた休校の記録（data/closures.json）。休校の日は daysoff の学校の休みとして扱う。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._data = self._load()

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            return {}

    def get(self, day: date) -> dict | None:
        with self._lock:
            return self._data.get(day.isoformat())

    def put(self, day: date, record: dict) -> None:
        with self._lock:
            self._data[day.isoformat()] = {**record, "checked": datetime.now().isoformat(timespec="seconds")}
            # 古い記録は 120 日で捨てる
            keep = sorted(self._data)[-120:]
            self._data = {k: self._data[k] for k in keep}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".part")
            tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=1), "utf-8")
            tmp.replace(self.path)

    def closed_days(self) -> dict:
        """休校が確かな日 → 理由（daysoff の学校の休みに足す）。"""
        with self._lock:
            return {date.fromisoformat(k): (v.get("reason") or "休校")
                    for k, v in self._data.items() if v.get("closed") and v.get("confidence") == "high"}


# --- 3. 学校のお知らせを読む ------------------------------------------------------

NOTICE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["events"],
    "properties": {
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["title", "date", "time", "kind", "note"],
                "properties": {
                    "title": {"type": "string", "description": "行事の名前（日本語で短く。英語の固有名詞はそのまま）"},
                    "date": {"type": "string", "description": "日付 YYYY-MM-DD"},
                    "time": {"type": "string", "description": "開始時刻 HH:MM。書いていなければ空"},
                    "kind": {"type": "string", "enum": ["event", "no_school", "early_dismissal", "deadline", "other"],
                             "description": "event=行事、no_school=学校が休み、early_dismissal=早帰り、deadline=提出・申込の締め切り"},
                    "note": {"type": "string", "description": "持ち物や注意など、家族が知っておくとよいこと（短く。なければ空）"},
                },
            },
        },
    },
}

MEDIA = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
         ".gif": "image/gif", ".webp": "image/webp"}


def read_notice(ai, settings: dict, filename: str, data_b64: str, today: date | None = None) -> list[dict]:
    """お知らせ（PDF か画像、base64）から行事の一覧を読み取る。"""
    cfg = _claude(settings)
    media = MEDIA.get(Path(filename or "").suffix.lower())
    if not media:
        raise ClaudeUnavailable("PDF か画像（jpg / png / gif / webp）を選んでください")
    today = today or date.today()
    source = {"type": "base64", "media_type": media, "data": data_b64}
    block = {"type": "document", "source": source} if media == "application/pdf" else {"type": "image", "source": source}
    prompt = (
        f"今日は{today.isoformat()}です。これは子どもの学校（カナダ・トロント）からのお知らせです。"
        "家族のカレンダーに入れるとよい日付つきの行事・休み・早帰り・締め切りを、すべて抜き出してください。"
        "年が書いていない日付は、今日以降で一番近い年にしてください。過ぎた日付は入れないでください。"
    )
    d = _json_answer(ai._request(cfg, "/messages", _body(cfg, [block, {"type": "text", "text": prompt}],
                                                          NOTICE_SCHEMA, effort="medium"), timeout=180))
    out = []
    for e in d.get("events") or []:
        try:
            day = date.fromisoformat(e.get("date") or "")
        except ValueError:
            continue
        if day < today:
            continue
        time = e.get("time") or ""
        if time and (len(time) != 5 or time[2] != ":"):
            time = ""
        out.append({"title": (e.get("title") or "")[:80], "date": day.isoformat(), "time": time,
                    "kind": e.get("kind") or "other", "note": (e.get("note") or "")[:200]})
    out.sort(key=lambda e: (e["date"], e["time"]))
    return out
