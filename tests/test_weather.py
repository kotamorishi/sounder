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


SAMPLE = {
    "status": "ok", "generated": at(6),
    "current": {"condition": "mostlyClear", "temperature_c": 17.6},
    "days": [{"date": at(0), "condition": "rain", "high_c": 21.4, "low_c": 12.2, "precipitation_chance": 70}],
    "hours": [{"time": at(h), "precipitation_chance": c} for h, c in ((9, 10), (15, 60), (16, 80), (17, 20))],
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
    def test_today_lines(self):
        self.assertEqual(self.w.today_lines(TODAY), [
            "雨、最高21度、最低12度、降水確率70パーセント",
            "15時ごろから17時ごろまで雨や雪の可能性が高い",
        ])
        self.assertEqual(self.w.today_lines(TODAY + timedelta(days=5)), [])

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
        self.assertIn("今日の天気: 雨、最高21度", a.prompt(info))
        self.assertIn("天気は雨、最高21度、最低12度、降水確率70パーセント。", a.fallback(info))

    def test_no_weather_line_without_data(self):
        self.path.unlink()
        a = ai.AI(None, weather=self.w)
        self.assertNotIn("今日の天気", a.prompt(a._day({}, TODAY)))


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
