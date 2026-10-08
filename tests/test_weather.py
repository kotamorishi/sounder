"""天気（WeatherKit の補助アプリが書いた weather.json）のテスト。"""

import json
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sounder import ai, config, weather  # noqa: E402

TODAY = date.today()


def at(hour):
    return datetime.combine(TODAY, datetime.min.time()).replace(hour=hour).astimezone().isoformat()


def hour(h, temp, feels, chance, precip="rain", cond="cloudy", wind=10):
    return {"time": at(h), "temperature_c": temp, "apparent_c": feels, "precipitation_chance": chance,
            "precipitation": precip, "condition": cond, "wind_kph": wind}


SAMPLE = {
    "status": "ok", "generated": at(6),
    "current": {"condition": "mostlyClear", "temperature_c": 17.6},
    "days": [{"date": at(0), "condition": "rain", "high_c": 21.4, "low_c": 12.2, "precipitation_chance": 70}],
    "hours": [hour(6, 9, 7, 0)] + [hour(h, 11 + h - 7, 9 + h - 7, c) for h, c in
                                   ((7, 10), (8, 10), (9, 20), (10, 30), (11, 60), (12, 80), (13, 40), (14, 20))]
             + [hour(16, 20, 20, 90), hour(17, 19, 19, 90)],     # 15 時より後の雨は数えない
    "attribution": {"legal_url": "https://weatherkit.apple.com/legal-attribution.html"},
}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.path = self.home / "weather.json"
        self.path.write_text(json.dumps(SAMPLE))
        self.w = weather.Weather(self.path, self.home / "SounderWeather")


class TestWeather(Base):
    def test_today_is_only_7_to_15(self):
        w = self.w.today(TODAY)
        self.assertEqual((w["low"], w["high"], w["feels_min"], w["feels_max"]), (11, 18, 9, 16))
        self.assertEqual(w["chance"], 80)                      # 16・17 時の 90% は入れない
        self.assertEqual(w["wet"], [11, 12])
        self.assertEqual(w["lines"], [
            "くもり。7時から15時までの気温は11度から18度（体感9度から16度）、15時までの降水確率は最大80パーセント",
            "11時ごろから13時ごろまで雨の可能性が高い",
        ])
        self.assertIn("ジャケットを着ていきましょう", w["clothing"])
        self.assertIn("傘かレインコート", w["clothing"])
        self.assertEqual(self.w.today(TODAY + timedelta(days=5)), {})

    def test_status_summary(self):
        st = self.w.status({"weather": {"name": "Toronto", "lat": 43.65, "lon": -79.38}})
        self.assertEqual(st["summary"], "晴れ 18度（今日 21度／12度、降水確率 70%）")
        self.assertFalse(st["installed"])

    def test_error_and_missing(self):
        self.path.write_text(json.dumps({"status": "error", "error": "auth"}))
        self.assertEqual(self.w.today_lines(TODAY), [])
        self.assertEqual(self.w.status({})["status"], "error")
        self.path.unlink()
        self.assertEqual(self.w.status({})["status"], "missing")

    def test_unknown_condition_passes_through(self):
        self.assertEqual(weather.condition("mostlyClear"), "晴れ")
        self.assertEqual(weather.condition("somethingNew"), "somethingNew")

    def test_geocode_uses_the_helper(self):
        app = self.home / "SounderWeather"
        app.write_text('#!/bin/sh\necho \'[{"name":"Toronto, ON, Canada","lat":43.651600000000002,"lon":-79.3831}]\'\n')
        app.chmod(0o755)
        self.assertEqual(self.w.geocode("Toronto"), [{"name": "Toronto, ON, Canada", "lat": 43.6516, "lon": -79.3831}])

    def test_geocode_without_the_helper(self):
        with self.assertRaises(ValueError):
            self.w.geocode("Toronto")


class TestBriefingWithWeather(Base):
    def test_prompt_and_fallback_mention_the_weather(self):
        a = ai.AI(None, weather=self.w)
        info = a._day({}, TODAY)
        prompt = a.prompt(info)
        self.assertIn("今日の天気（朝7時〜午後3時）: くもり。7時から15時までの気温は11度から18度", prompt)
        self.assertIn("服装の目安: 寒いので厚手のジャケットを着ていきましょう", prompt)
        fb = a.fallback(info)
        self.assertIn("15時までの降水確率は最大80パーセント", fb)
        self.assertIn("ジャケットを着ていきましょう", fb)

    def test_no_weather_line_without_data(self):
        self.path.unlink()
        a = ai.AI(None, weather=self.w)
        self.assertNotIn("今日の天気", a.prompt(a._day({}, TODAY)))


class TestClothing(unittest.TestCase):
    def wear(self, low, high=None, chance=0, snow=False, windy=False):
        return weather.clothing({"feels_min": low, "feels_max": low if high is None else high,
                                 "chance": chance, "snow": snow, "windy": windy})

    def test_by_feels_like_temperature(self):
        self.assertTrue(self.wear(27).startswith("暑いので半袖"))
        self.assertTrue(self.wear(21).startswith("半袖か薄い長袖"))
        self.assertTrue(self.wear(16).startswith("長袖に、薄い上着"))
        self.assertTrue(self.wear(12).startswith("ジャケットを着ていきましょう"))
        self.assertTrue(self.wear(7).startswith("寒いので厚手のジャケット"))
        self.assertTrue(self.wear(2).startswith("冬のジャケットに、帽子と手袋"))
        self.assertIn("スノーパンツ", self.wear(-5))
        self.assertIn("スノーブーツ", self.wear(-15))

    def test_extras(self):
        self.assertIn("脱ぎ着しやすい服", self.wear(6, 16))
        self.assertIn("スノーパンツとスノーブーツ", self.wear(3, snow=True))
        self.assertEqual(self.wear(-5, snow=True).count("スノーパンツ"), 1)   # 重ねて言わない
        self.assertIn("傘かレインコート、長靴", self.wear(8, chance=70))
        self.assertIn("雨の予報なので、傘を", self.wear(18, chance=70))
        self.assertIn("折りたたみ傘", self.wear(18, chance=35))
        self.assertIn("フードのある上着", self.wear(8, windy=True))
        self.assertEqual(weather.clothing({"feels_min": None}), "")


class TestSettings(unittest.TestCase):
    def test_validate_and_survive_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            config.Store(path).update_settings({"weather": {"name": "Toronto", "lat": "43.65161", "lon": -79.38}})
            self.assertEqual(config.Store(path).settings["weather"], {"name": "Toronto", "lat": 43.6516, "lon": -79.38})

    def test_rejects_bad_values(self):
        cur = dict(config.DEFAULT_SETTINGS)
        for bad in ({"lat": 91, "lon": 0}, {"lat": "x", "lon": 0}, "nope"):
            with self.assertRaises(config.ValidationError):
                config.validate_settings({"weather": bad}, cur)
        self.assertEqual(config.validate_settings({"weather": {"lat": None}}, cur)["weather"]["lat"], None)


if __name__ == "__main__":
    unittest.main()
