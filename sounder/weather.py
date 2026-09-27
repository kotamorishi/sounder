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

    def today_lines(self, day: date) -> list[str]:
        """朝のお知らせ用の、その日の天気の説明（AI に渡す材料と、決まった文の両方に使う）。"""
        d = self.data()
        if d.get("status") != "ok":
            return []
        days = {str(x.get("date", ""))[:10]: x for x in d.get("days") or []}
        t = days.get(day.isoformat())
        if not t:
            return []
        lines = [f"{condition(t.get('condition'))}、最高{_deg(t.get('high_c'))}、最低{_deg(t.get('low_c'))}、"
                 f"降水確率{round(t.get('precipitation_chance') or 0)}パーセント"]
        # その日のうち雨や雪の確率が高い時間帯（50% 以上）
        wet = []
        for h in d.get("hours") or []:
            try:
                at = datetime.fromisoformat(h["time"]).astimezone().replace(tzinfo=None)
            except (KeyError, ValueError):
                continue
            if at.date() == day and (h.get("precipitation_chance") or 0) >= 50:
                wet.append(at.hour)
        if wet:
            lines.append(f"{wet[0]}時ごろから{wet[-1] + 1}時ごろまで雨や雪の可能性が高い")
        return lines
