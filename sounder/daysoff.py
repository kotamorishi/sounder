"""「お休みの日」の暦。予定ごとに選んで、その日は鳴らさないようにする。

- on_holidays      … オンタリオ州の祝日（Employment Standards Act の 9 日）。毎年この Mac で計算する
- tdsb_elementary  … TDSB（トロント教育委員会）の小学校（JK〜8 年生）が休みの日
- tdsb_secondary   … TDSB の中高（9〜12 年生）が休みの日

TDSB は予定表を PDF でしか出していないので、公式の「Key Dates」から書き写して下の
TDSB_YEARS に持つ。毎年、次の年度の分が出たら書き足す（README の手順を参照）。
データがない日は「分からない」として None を返し、鳴らす側に倒す（鳴らし忘れを防ぐ）。
"""

from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """その月の第 n ○曜日（weekday は月曜=0）。"""
    d = date(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def easter(year: int) -> date:
    """復活祭（グレゴリオ暦、Anonymous Gregorian algorithm）。"""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7  # noqa: E741
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return date(year, month, day)


@lru_cache(maxsize=16)
def ontario_holidays(year: int) -> dict[date, str]:
    """オンタリオ州の祝日。土日に重なったものは、次の平日を振替休日にする。

    シビック・ホリデー（8 月第 1 月曜）とイースター・マンデーは州の祝日ではないので含めない
    （学校の休みは TDSB の暦の側で扱う）。
    """
    fixed = [
        (date(year, 1, 1), "元日"),
        (_nth_weekday(year, 2, 0, 3), "ファミリー・デー"),
        (easter(year) - timedelta(days=2), "グッドフライデー"),
        # 5 月 25 日より前の最後の月曜
        (date(year, 5, 24) - timedelta(days=date(year, 5, 24).weekday()), "ビクトリア・デー"),
        (date(year, 7, 1), "カナダ・デー"),
        (_nth_weekday(year, 9, 0, 1), "レイバー・デー"),
        (_nth_weekday(year, 10, 0, 2), "サンクスギビング"),
        (date(year, 12, 25), "クリスマス"),
        (date(year, 12, 26), "ボクシング・デー"),
    ]
    out = dict(fixed)
    for d, name in fixed:
        if d.weekday() < 5:
            continue
        sub = d
        while sub.weekday() >= 5 or sub in out:
            sub += timedelta(days=1)
        out[sub] = f"{name}の振替休日"
    return out


def _span(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


# 出典: https://www.tdsb.on.ca/About-Us/School-Year-Calendar の「Key Dates」PDF
TDSB_YEARS = [
    {
        "year": "2026-27",
        "start": date(2026, 9, 1),          # 年度の始まり（9/1）
        "end": date(2027, 8, 31),           # 夏休みの終わり（ここまでは分かっている）
        "first_class": date(2026, 9, 8),
        "last_class": {"elementary": date(2027, 6, 29), "secondary": date(2027, 6, 28)},
        "pa_days": {
            "elementary": [date(2026, 9, 2), date(2026, 9, 3), date(2026, 11, 20),
                           date(2027, 1, 15), date(2027, 2, 12), date(2027, 6, 4),
                           date(2027, 6, 30)],
            "secondary": [date(2026, 9, 2), date(2026, 9, 3), date(2026, 11, 20),
                          date(2027, 2, 2), date(2027, 2, 12), date(2027, 6, 29),
                          date(2027, 6, 30)],
        },
        "breaks": [
            (date(2026, 12, 21), date(2027, 1, 1), "冬休み"),
            (date(2027, 3, 15), date(2027, 3, 19), "ミッドウィンター・ブレイク"),
            (date(2027, 3, 29), date(2027, 3, 29), "イースター・マンデー"),
        ],
    },
]

CALENDARS = {
    "on_holidays": "オンタリオ州の祝日",
    "tdsb_elementary": "TDSB の休校日（小学校 JK〜8 年生）",
    "tdsb_secondary": "TDSB の休校日（中高 9〜12 年生）",
}


def _tdsb_reason(panel: str, day: date) -> str | None:
    for y in TDSB_YEARS:
        if not y["start"] <= day <= y["end"]:
            continue
        if day in y["pa_days"][panel]:
            return "PA デー"
        for start, end, name in y["breaks"]:
            if start <= day <= end:
                return name
        holiday = ontario_holidays(day.year).get(day)
        if holiday:
            return holiday
        if day < y["first_class"]:
            return "新学期前"
        if day > y["last_class"][panel]:
            return "夏休み"
        if day.weekday() >= 5:
            return "週末"
        return None
    return None


def reason(cal: str, day: date) -> str | None:
    """その暦でその日が休みなら理由（「PA デー」など）、休みでなければ（分からなければ）None。"""
    if cal == "on_holidays":
        return ontario_holidays(day.year).get(day)
    if cal == "tdsb_elementary":
        return _tdsb_reason("elementary", day)
    if cal == "tdsb_secondary":
        return _tdsb_reason("secondary", day)
    return None


def off_reason(skip: list[str], day: date) -> str | None:
    """予定の skip（暦の id の並び）のどれかでその日が休みなら、その理由。"""
    for cal in skip or []:
        r = reason(cal, day)
        if r:
            return r
    return None


def known_until(cal: str) -> date | None:
    """その暦がいつまで分かっているか（祝日は計算なので None = いつまでも）。"""
    if cal.startswith("tdsb_"):
        return max((y["end"] for y in TDSB_YEARS), default=None)
    return None


def catalog() -> list[dict]:
    """画面に出す暦の一覧。"""
    out = []
    for cal, label in CALENDARS.items():
        until = known_until(cal)
        out.append({"id": cal, "label": label,
                    "known_until": until.isoformat() if until else None})
    return out
