"""天気（オプション）。補助アプリ SounderWeather.app（WeatherKit）が書き出した data/weather.json を読む。

補助アプリは 30 分ごとに、設定 settings["weather"] の地点の今の天気と今日・明日の予報を書き出す。
ここでは、朝のお知らせ（ai.py）に渡す短い文と、設定画面に出す状態を作る。
地点の検索（地名 → 緯度・経度）も補助アプリに頼む（Apple の地名検索。位置情報の許可はいらない）。
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
from datetime import date, datetime
from pathlib import Path

LABEL = "com.local.sounder-weather"
DEFAULTS = {"name": "", "lat": None, "lon": None}

# WeatherKit の WeatherCondition → 読み上げやすい日本語
CONDITIONS = {
    "clear": "快晴", "mostlyClear": "晴れ", "partlyCloudy": "晴れ時々くもり", "mostlyCloudy": "くもり",
    "cloudy": "くもり", "foggy": "霧", "haze": "もや", "smoky": "煙霧", "breezy": "風がやや強い",
    "windy": "風が強い", "drizzle": "霧雨", "rain": "雨", "heavyRain": "大雨", "sunShowers": "晴れ時々にわか雨",
    "isolatedThunderstorms": "ところにより雷雨", "scatteredThunderstorms": "ところにより雷雨",
    "thunderstorms": "雷雨", "strongStorms": "激しい嵐", "tropicalStorm": "熱帯低気圧", "hurricane": "ハリケーン",
    "flurries": "小雪", "sunFlurries": "晴れ時々小雪", "snow": "雪", "heavySnow": "大雪", "blowingSnow": "地吹雪",
    "blizzard": "吹雪", "sleet": "みぞれ", "freezingDrizzle": "着氷性の霧雨", "freezingRain": "着氷性の雨",
    "wintryMix": "雪まじりの雨", "hail": "ひょう", "blowingDust": "砂ぼこり", "frigid": "厳しい寒さ",
    "hot": "厳しい暑さ",
}


def settings_of(settings: dict) -> dict:
    return {**DEFAULTS, **(settings.get("weather") or {})}


def condition(raw: str) -> str:
    return CONDITIONS.get(raw or "", raw or "")


def _deg(v) -> str:
    return f"{round(v)}度" if v is not None else ""


# 朝のお知らせで見る時間帯（7 時〜15 時。子どもが学校にいる時間）
MORNING = 7
AFTERNOON = 15
SNOWY = {"snow", "sleet", "hail", "mixed"}
SNOWY_CONDITIONS = {"snow", "heavySnow", "flurries", "sunFlurries", "blizzard", "blowingSnow", "sleet",
                    "wintryMix", "freezingRain", "freezingDrizzle"}

# 体感温度（その時間帯でいちばん低い値）→ 服装。上から順に当てはまるものを使う（トロントの子ども向け）
CLOTHING = [
    (25, "暑いので半袖で大丈夫。水筒を忘れずに"),
    (20, "半袖か薄い長袖で大丈夫"),
    (15, "長袖に、薄い上着（パーカーやカーディガン）があると安心"),
    (10, "ジャケットを着ていきましょう"),
    (5, "寒いので厚手のジャケットを着ていきましょう"),
    (0, "冬のジャケットに、帽子と手袋も"),
    (-10, "とても寒いので、冬のコートに帽子・手袋・ネックウォーマー、スノーパンツも"),
    (-99, "とても厳しい寒さです。一番暖かい冬のコート、帽子・手袋・ネックウォーマー、スノーパンツとスノーブーツで"),
]


def feel_word(feels) -> str:
    """体感温度 → 「寒いです」など。"""
    if feels is None:
        return ""
    for limit, word in ((28, "暑いです"), (23, "暖かいです"), (15, "過ごしやすい気温です"),
                        (10, "少し涼しいです"), (0, "寒いです")):
        if feels >= limit:
            return word
    return "とても寒いです"


def outing_sentence(info: dict) -> str:
    """お出かけの読み上げに足す一言（天気と服装）。例:
    「外は寒いです。体感温度は7度です。雨の心配はありません。寒いので厚手のジャケットを着ていきましょう。」"""
    if not info:
        return ""
    feels = info.get("feels_morning")
    parts = []
    if feels is not None:
        parts.append(f"外は{feel_word(feels)}。体感温度は{round(feels)}度です。")
    kind = "雪" if info.get("snow") else "雨"
    if info.get("wet"):
        parts.append(f"{info['wet'][0]}時ごろから{kind}が降りそうです。")
    elif info.get("chance", 0) >= 30:
        parts.append(f"にわか{kind}があるかもしれません。")
    else:
        parts.append("雨の心配はありません。")
    if info.get("clothing"):
        parts.append(info["clothing"] + "。")
    return "".join(parts)


def clothing(info: dict) -> str:
    """服装の目安（決まった規則で決める。AI には言い回しだけ任せる）。"""
    low = info.get("feels_min")
    if low is None:
        return ""
    advice = next(text for limit, text in CLOTHING if low >= limit)
    extra = []
    high = info.get("feels_max")
    if high is not None and high - low >= 8:
        extra.append("朝は寒くても昼は暖かくなるので、脱ぎ着しやすい服で")
    if info.get("snow"):
        if "スノーパンツ" not in advice:
            extra.append("雪の予報なので、スノーパンツとスノーブーツを")
    elif info.get("chance", 0) >= 50:
        extra.append("雨の予報なので、傘かレインコート、長靴を" if low < 10 else "雨の予報なので、傘を")
    elif info.get("chance", 0) >= 30:
        extra.append("にわか雨があるかもしれないので、折りたたみ傘があると安心")
    if info.get("windy") and low < 15:
        extra.append("風が強いので、フードのある上着がおすすめ")
    return "。".join([advice] + extra)


class Weather:
    def __init__(self, path: Path, app_path: Path) -> None:
        self.path = path
        self.app = app_path          # SounderWeather.app/Contents/MacOS/SounderWeather
        self._lock = threading.Lock()
        self._mtime = None
        self._data: dict = {}

    def data(self) -> dict:
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

    def installed(self) -> bool:
        return self.app.is_file()

    def refresh(self) -> None:
        """補助アプリを今すぐ動かす（地点を変えたとき）。"""
        subprocess.run(["launchctl", "kickstart", f"gui/{os.getuid()}/{LABEL}"],
                       capture_output=True, timeout=10)

    def geocode(self, query: str) -> list[dict]:
        if not self.installed():
            raise ValueError("天気の補助アプリがありません（scripts/install-weather.sh）")
        r = subprocess.run([str(self.app), "--geocode", query], capture_output=True, text=True, timeout=30)
        try:
            found = json.loads(r.stdout or "[]")
        except ValueError:
            found = []
        return [{"name": f.get("name"), "lat": round(float(f["lat"]), 4), "lon": round(float(f["lon"]), 4)}
                for f in found if "lat" in f and "lon" in f]

    def status(self, settings: dict) -> dict:
        """設定画面に出す状態。"""
        d = self.data()
        cfg = settings_of(settings)
        out = {"installed": self.installed(), "status": d.get("status") or ("missing" if not d else "unknown"),
               "generated": d.get("generated"), "location": cfg, "error": d.get("error"),
               "attribution": d.get("attribution")}
        cur, today = d.get("current") or {}, (d.get("days") or [{}])[0]
        if d.get("status") == "ok":
            out["summary"] = (f"{condition(cur.get('condition'))} {_deg(cur.get('temperature_c'))}"
                              f"（今日 {_deg(today.get('high_c'))}／{_deg(today.get('low_c'))}、"
                              f"降水確率 {round(today.get('precipitation_chance') or 0)}%）")
        return out

    def today(self, day: date, *, start_hour: int = MORNING, end_hour: int = AFTERNOON) -> dict:
        """朝のお知らせ用の、その日の朝〜午後（既定 7〜15 時）の天気。分からなければ空。

        lines: 天気の説明（AI に渡す材料と、決まった文の両方に使う）
        clothing: 体感温度と雨・雪から決めた服装の目安（clothing() を参照）
        """
        d = self.data()
        if d.get("status") != "ok":
            return {}
        hours = []
        for h in d.get("hours") or []:
            try:
                at = datetime.fromisoformat(h["time"]).astimezone().replace(tzinfo=None)
            except (KeyError, ValueError):
                continue
            if at.date() == day and start_hour <= at.hour < end_hour:
                hours.append((at.hour, h))
        days = {str(x.get("date", ""))[:10]: x for x in d.get("days") or []}
        t = days.get(day.isoformat())
        if not hours and not t:
            return {}
        if hours:
            temps = [h.get("temperature_c") for _, h in hours if h.get("temperature_c") is not None]
            feels = [h.get("apparent_c", h.get("temperature_c")) for _, h in hours]
            feels = [f for f in feels if f is not None]
            chance = max(round(h.get("precipitation_chance") or 0) for _, h in hours)
            wet = [(hr, h) for hr, h in hours if (h.get("precipitation_chance") or 0) >= 50]
            snow = any(h.get("precipitation") in SNOWY or h.get("condition") in SNOWY_CONDITIONS
                       for _, h in (wet or hours) if (h.get("precipitation_chance") or 0) >= 30)
            t = t or {}
            # 朝の様子（いちばん早い時間）を天気の言葉に使う。時間ごとの値が無ければ一日の予報で補う
            cond = condition(hours[0][1].get("condition") or t.get("condition"))
            low = min(temps) if temps else t.get("low_c")
            high = max(temps) if temps else t.get("high_c")
            info = {"low": low, "high": high,
                    "feels_min": min(feels) if feels else low, "feels_max": max(feels) if feels else high,
                    "feels_morning": feels[0] if feels else low, "chance": chance, "snow": snow,
                    "wet": [hr for hr, _ in wet], "condition": cond,
                    "windy": max((h.get("wind_kph") or 0) for _, h in hours) >= 30}
        else:   # 時間ごとの予報が無いとき（夜に取った分など）は一日の予報で
            info = {"low": t.get("low_c"), "high": t.get("high_c"), "feels_min": t.get("low_c"),
                    "feels_max": t.get("high_c"), "feels_morning": t.get("low_c"),
                    "chance": round(t.get("precipitation_chance") or 0),
                    "snow": t.get("condition") in SNOWY_CONDITIONS, "wet": [],
                    "condition": condition(t.get("condition")), "windy": False}
        lines = [f"{info['condition']}。{start_hour}時から{end_hour}時までの気温は"
                 f"{_deg(info['low'])}から{_deg(info['high'])}（体感{_deg(info['feels_min'])}から"
                 f"{_deg(info['feels_max'])}）、{end_hour}時までの降水確率は最大{info['chance']}パーセント"]
        if info["wet"]:
            kind = "雪" if info["snow"] else "雨"
            lines.append(f"{info['wet'][0]}時ごろから{info['wet'][-1] + 1}時ごろまで{kind}の可能性が高い")
        if info["windy"]:
            lines.append("風が強い")
        return {**info, "lines": lines, "clothing": clothing(info)}

    def outing(self, at: datetime, hours: int = 6) -> str:
        """at に出かけるときの一言（その時刻から hours 時間の天気と服装）。天気が分からなければ ""。"""
        return outing_sentence(self.today(at.date(), start_hour=at.hour,
                                          end_hour=min(24, at.hour + hours)))

    def today_lines(self, day: date) -> list[str]:
        return self.today(day).get("lines") or []
