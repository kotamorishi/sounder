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
from datetime import date, datetime, timedelta
from pathlib import Path

LABEL = "com.local.sounder-weather"
# pickup: 帰り（お迎え）の時刻。お出かけの読み上げで、その時刻の天気も伝える（"" で伝えない）
DEFAULTS = {"name": "", "lat": None, "lon": None, "pickup": "15:30"}
# 過去の天気を何日分とっておくか（昨日との比べ・今季いちばんなどに使う）
HISTORY_DAYS = 400
# 「今季初」「今季いちばん」と言うのに要る記録の日数（記録が少ないうちは言わない）
SEASON_MIN_DAYS = 7

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


def _local(iso: str) -> datetime:
    return datetime.fromisoformat(iso).astimezone().replace(tzinfo=None)


def _hm_words(t: datetime) -> str:
    """15:30 → 「3時半」、17:58 → 「5時58分」（午後は 12 を引く）。"""
    h = t.hour % 12 or 12
    if t.minute == 0:
        return f"{h}時"
    return f"{h}時半" if t.minute == 30 else f"{h}時{t.minute}分"


def season_start(day: date) -> date:
    """寒さの「今季」は 8 月から、暖かさの「今季」は 2 月から数える。"""
    if day.month >= 8:
        return date(day.year, 8, 1)
    if day.month >= 2:
        return date(day.year, 2, 1)
    return date(day.year - 1, 8, 1)


def clock_change(day: date) -> int:
    """day の 0 時から翌日の 0 時までに時計が何時間動くか（冬時間に戻る日は -1、夏時間は +1）。
    切り替えは夜中の 2 時なので、朝のお知らせの時点ではもう切り替わっている。"""
    a = datetime.combine(day, datetime.min.time()).astimezone().utcoffset()
    b = datetime.combine(day + timedelta(days=1), datetime.min.time()).astimezone().utcoffset()
    return round((b - a).total_seconds() / 3600)


class Weather:
    def __init__(self, path: Path, app_path: Path) -> None:
        self.path = path
        self.app = app_path          # SounderWeather.app/Contents/MacOS/SounderWeather
        self.history_path = path.with_name("weather-history.json")
        self._lock = threading.Lock()
        self._mtime = None
        self._data: dict = {}
        self._history: dict | None = None

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
                if self._data.get("status") == "ok":
                    self._record(self._data)
            return self._data

    # --- 過去の天気の記録（data/weather-history.json）
    def history(self) -> dict:
        """{"YYYY-MM-DD": {"feels": {"8": 7.1, ...}, "low", "high", "sunset": "HH:MM", "snow"}}"""
        if self._history is None:
            try:
                self._history = json.loads(self.history_path.read_text("utf-8"))
            except (OSError, ValueError):
                self._history = {}
        return self._history

    def _record(self, d: dict) -> None:
        """新しい予報で上書きしていく。過ぎた時間は予報から消えるので、最後に見た値（ほぼ実際の値）が残る。"""
        hist = self.history()
        for h in d.get("hours") or []:
            try:
                at = _local(h["time"])
            except (KeyError, ValueError):
                continue
            feels = h.get("apparent_c", h.get("temperature_c"))
            if feels is not None:
                hist.setdefault(at.date().isoformat(), {}).setdefault("feels", {})[str(at.hour)] = feels
        for x in d.get("days") or []:
            key = str(x.get("date", ""))[:10]
            if not key:
                continue
            rec = hist.setdefault(key, {})
            rec.update(low=x.get("low_c"), high=x.get("high_c"),
                       snow=x.get("condition") in SNOWY_CONDITIONS
                       or (x.get("precipitation") in SNOWY and (x.get("precipitation_chance") or 0) >= 30))
            try:
                rec["sunset"] = _local(x["sunset"]).strftime("%H:%M")
            except (KeyError, TypeError, ValueError):
                pass
        for key in sorted(hist)[:-HISTORY_DAYS]:
            del hist[key]
        try:
            tmp = self.history_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(hist, ensure_ascii=False, sort_keys=True), "utf-8")
            os.replace(tmp, self.history_path)
        except OSError:
            pass

    def compare_yesterday(self, at: datetime) -> str:
        """「昨日より4度寒いです。」 at の時刻の体感温度を、昨日の同じ時刻と比べる。差が小さければ ""。"""
        self.data()   # 新しい予報を記録に入れてから比べる
        hist = self.history()
        today = (hist.get(at.date().isoformat()) or {}).get("feels", {}).get(str(at.hour))
        before = (hist.get((at.date() - timedelta(days=1)).isoformat()) or {}).get("feels", {}).get(str(at.hour))
        if today is None or before is None:
            return ""
        diff = round(today - before)
        if diff <= -3:
            return f"昨日のこの時間より{-diff}度寒いです。"
        if diff >= 3:
            return f"昨日のこの時間より{diff}度暖かいです。"
        return ""

    def pickup(self, at: datetime, settings: dict) -> str:
        """帰り（お迎え）の時刻の天気。「帰りの3時半ごろは晴れて14度。朝より暖かいので、上着は手に持って帰れそうです。」"""
        hm = settings_of(settings).get("pickup") or ""
        try:
            back = datetime.combine(at.date(), datetime.strptime(hm, "%H:%M").time())
        except ValueError:
            return ""
        if back <= at + timedelta(hours=1):
            return ""   # 帰りの時刻のあとに出かける予定には言わない
        hours = {}
        for h in self.data().get("hours") or []:
            try:
                t = _local(h["time"])
            except (KeyError, ValueError):
                continue
            hours[(t.date(), t.hour)] = h
        h = hours.get((back.date(), back.hour))
        if not h:
            return ""
        temp = h.get("temperature_c")
        feels = h.get("apparent_c", temp)
        text = f"帰りの{_hm_words(back)}ごろは{condition(h.get('condition'))}"
        text += f"、{round(temp)}度。" if temp is not None else "。"
        chance = h.get("precipitation_chance") or 0
        morning = hours.get((at.date(), at.hour)) or {}
        m_feels = morning.get("apparent_c", morning.get("temperature_c"))
        kind = "雪" if h.get("precipitation") in SNOWY or h.get("condition") in SNOWY_CONDITIONS else "雨"
        if chance >= 50 and (morning.get("precipitation_chance") or 0) < 50:
            text += f"帰りは{kind}になりそうです。"
        elif feels is not None and m_feels is not None and feels - m_feels >= 6:
            text += "朝よりずっと暖かくなるので、上着は脱いでも大丈夫そうです。"
        elif feels is not None and m_feels is not None and m_feels - feels >= 4:
            text += "朝より寒くなるので、上着を忘れずに持って帰りましょう。"
        return text

    def season_notes(self, day: date) -> list[str]:
        """季節の節目の一言。今季いちばんの冷え込み・今季初の氷点下や雪・時計の切り替え・日の入り。"""
        notes = []
        self.data()
        hist = self.history()
        key = day.isoformat()
        rec = hist.get(key) or {}
        start = season_start(day).isoformat()
        past = [v for k, v in hist.items() if start <= k < key]
        enough = len(past) >= SEASON_MIN_DAYS
        low = rec.get("low")
        if enough and low is not None:
            lows = [v["low"] for v in past if v.get("low") is not None]
            highs = [v["high"] for v in past if v.get("high") is not None]
            cold_season = day.month >= 8 or day.month == 1
            if low < 0 and lows and all(x >= 0 for x in lows) and cold_season:
                notes.append(f"今日は今季初めて氷点下まで下がる予報です（最低{round(low)}度）。")
            elif cold_season and lows and low < min(lows) - 0.5:
                notes.append(f"今季いちばんの冷え込みです（最低{round(low)}度）。")
            elif not cold_season and highs and rec.get("high") is not None and rec["high"] > max(highs) + 0.5:
                notes.append(f"今年いちばんの暖かさになりそうです（最高{round(rec['high'])}度）。")
        if enough and rec.get("snow") and not any(v.get("snow") for v in past):
            notes.append("今季初めての雪の予報です。")
        # 時計の切り替え（d の夜中 2 時に切り替わる。金曜には週末の分、月曜には週末にあった分も言う）
        for i in range(-3, 4):
            d = day + timedelta(days=i)
            change = clock_change(d)
            if not change:
                continue
            which = "冬時間" if change < 0 else "夏時間"
            move = "1時間戻ります" if change < 0 else "1時間進みます"
            if i == 0:
                notes.append(f"今日から{which}です。夜中に時計が{move[:-2]}ました。")
            elif i == 1:
                notes.append(f"今夜、時計が{move}。明日から{which}です。")
            elif i > 1:
                notes.append(f"{'月火水木金土日'[(d - timedelta(days=1)).weekday()]}曜日の夜、時計が{move}。{which}になります。")
            else:
                notes.append(f"{'月火水木金土日'[d.weekday()]}曜日から{which}になりました。")
            break
        # 日の入りが、ちょうどの時刻をまたいだ日
        y = (hist.get((day - timedelta(days=1)).isoformat()) or {}).get("sunset")
        t = rec.get("sunset")
        if y and t and y[:2] != t[:2]:
            h = int(max(y, t)[:2]) % 12 or 12
            hh, mm = int(t[:2]) % 12 or 12, int(t[3:5])
            if t < y:
                notes.append(f"日の入りが{h}時より前になりました。今日は{hh}時{mm}分です。")
            else:
                notes.append(f"日の入りが{h}時を過ぎるようになりました。今日は{hh}時{mm}分です。")
        return notes

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

    def outing(self, at: datetime, hours: int = 6, settings: dict | None = None, full: bool = True) -> str:
        """at に出かけるときの一言。天気が分からなければ ""。

        その時刻から hours 時間の天気と服装。full なら、昨日との比べ・帰りの時刻の天気・季節の節目も足して、
        毎日同じ話にならないようにする（その朝の最初のお知らせだけ。あとの予告は短く）。
        """
        base = outing_sentence(self.today(at.date(), start_hour=at.hour,
                                          end_hour=min(24, at.hour + hours)))
        if not base or not full:
            return base
        vs = self.compare_yesterday(at)
        if vs:   # 体感温度のすぐあとに入れる
            mark = "度です。"
            i = base.find(mark)
            base = base[:i + len(mark)] + vs + base[i + len(mark):] if i >= 0 else vs + base
        extra = [self.pickup(at, settings or {})] + self.season_notes(at.date())
        return base + "".join(x for x in extra if x)

    def today_lines(self, day: date) -> list[str]:
        return self.today(day).get("lines") or []
