"""設定の保存層と入力検証の細かい分岐のテスト。"""

import json
import pathlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sounder import config  # noqa: E402
from sounder.config import ValidationError  # noqa: E402


def raw(**kw):
    base = {"name": "テスト", "kind": "weekly", "time": "08:30", "days": [0],
            "action": {"type": "sound", "sound": "builtin:ding"}}
    base.update(kw)
    return base


class TestValidation(unittest.TestCase):
    def bad(self, message_part, **kw):
        with self.assertRaises(ValidationError) as cm:
            config.validate_schedule(raw(**kw))
        self.assertIn(message_part, str(cm.exception))

    def test_not_a_dict(self):
        with self.assertRaises(ValidationError):
            config.validate_schedule(["これは辞書ではない"])

    def test_name_too_long(self):
        self.bad("長すぎます", name="あ" * 81)

    def test_volume_must_be_a_number(self):
        self.bad("数値", action={"type": "sound", "sound": "builtin:ding", "volume": "おおきく"})

    def test_repeat_must_be_an_integer(self):
        self.bad("整数", action={"type": "sound", "sound": "builtin:ding", "repeat": "さんかい"})

    def test_repeat_out_of_range(self):
        self.bad("範囲", action={"type": "sound", "sound": "builtin:ding", "repeat": 99})

    def test_once_needs_a_well_formed_date(self):
        self.bad("YYYY-MM-DD", kind="once", date="2026/09/25", days=None)
        self.bad("存在しません", kind="once", date="2026-02-30", days=None)

    def test_yearly_rejects_impossible_dates(self):
        self.bad("存在しません", kind="yearly", month=2, day_of_month=30, days=None)
        # 2月29日は うるう年に鳴るので許す
        s = config.validate_schedule(raw(kind="yearly", month=2, day_of_month=29, days=None))
        self.assertEqual(s["day_of_month"], 29)

    def test_monthly_last_day(self):
        s = config.validate_schedule(raw(kind="monthly", day_of_month="last", days=None))
        self.assertEqual(s["day_of_month"], "last")

    def test_lead_times_must_be_a_list_and_capped(self):
        self.bad("形式", lead_times={"10": True})
        self.bad("8 件", lead_times=[1, 2, 3, 4, 5, 6, 7, 8, 9])

    def test_unknown_action_type(self):
        self.bad("動作の種別", action={"type": "dance"})

    def test_speak_keeps_voice_and_rate(self):
        s = config.validate_schedule(raw(action={
            "type": "speak", "text": "こんにちは", "voice": "Kyoko", "rate": 250}))
        self.assertEqual(s["action"]["voice"], "Kyoko")
        self.assertEqual(s["action"]["rate"], 250)

    def test_speak_rate_defaults(self):
        s = config.validate_schedule(raw(action={"type": "speak", "text": "はい"}))
        self.assertEqual(s["action"]["rate"], 180)
        self.assertEqual(s["action"]["voice"], "")

    def test_interval_without_days_means_every_day(self):
        s = config.validate_schedule(raw(kind="interval", every_minutes=30, days=None))
        self.assertEqual(s["days"], [0, 1, 2, 3, 4, 5, 6])

    def test_weekly_requires_days(self):
        self.bad("曜日", days=[])

    def test_note_is_trimmed(self):
        s = config.validate_schedule(raw(note="  めも  "))
        self.assertEqual(s["note"], "めも")

    def test_old_daily_kind_is_read_as_weekly(self):
        self.assertEqual(config.validate_schedule(raw(kind="daily"))["kind"], "weekly")


class TestSettingsValidation(unittest.TestCase):
    def test_not_a_dict(self):
        with self.assertRaises(ValidationError):
            config.validate_settings("設定", dict(config.DEFAULT_SETTINGS))

    def test_partial_update_keeps_the_rest(self):
        out = config.validate_settings({"speak_rate": 300}, dict(config.DEFAULT_SETTINGS))
        self.assertEqual(out["speak_rate"], 300)
        self.assertEqual(out["default_volume"], config.DEFAULT_SETTINGS["default_volume"])

    def test_volume_empty_string_falls_back(self):
        out = config.validate_settings({"default_volume": ""}, dict(config.DEFAULT_SETTINGS))
        self.assertEqual(out["default_volume"], config.DEFAULT_SETTINGS["default_volume"])


class TestStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "config.json"
        self.store = config.Store(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_add_get_replace_patch_delete(self):
        s = self.store.add(raw(name="いち"))
        self.assertEqual(self.store.get(s["id"])["name"], "いち")
        self.assertIsNone(self.store.get("いない"))
        self.store.replace(s["id"], raw(name="に"))
        self.assertEqual(self.store.get(s["id"])["name"], "に")
        self.store.patch(s["id"], {"enabled": False})
        self.assertFalse(self.store.get(s["id"])["enabled"])
        self.store.delete(s["id"])
        self.assertEqual(self.store.schedules(), [])

    def test_missing_id_raises(self):
        for fn in (lambda: self.store.replace("x", raw()),
                   lambda: self.store.patch("x", {"enabled": False}),
                   lambda: self.store.delete("x")):
            with self.assertRaises(KeyError):
                fn()

    def test_mark_fired_records_and_ignores_unknown(self):
        from datetime import datetime
        s = self.store.add(raw())
        self.store.mark_fired(s["id"], datetime(2026, 9, 21, 8, 30))
        self.assertEqual(self.store.get(s["id"])["last_fired"], "2026-09-21T08:30:00")
        self.store.mark_fired("いない", datetime.now())  # 何も起きない

    def test_patch_keeps_last_fired(self):
        from datetime import datetime
        s = self.store.add(raw())
        self.store.mark_fired(s["id"], datetime(2026, 9, 21, 8, 30))
        self.store.patch(s["id"], {"enabled": False})
        self.assertEqual(self.store.get(s["id"])["last_fired"], "2026-09-21T08:30:00")

    def test_saved_to_disk_and_reloaded(self):
        self.store.add(raw(name="ほぞん"))
        again = config.Store(self.path)
        self.assertEqual([s["name"] for s in again.schedules()], ["ほぞん"])

    def test_sound_check_hook_blocks_bad_refs(self):
        def check(ref):
            if ref != "builtin:ding":
                raise RuntimeError(f"{ref} がない")
        store = config.Store(Path(self.tmp.name) / "c2.json", sound_check=check)
        store.add(raw())
        with self.assertRaises(ValidationError):
            store.add(raw(action={"type": "sound", "sound": "builtin:nope"}))
        with self.assertRaises(ValidationError):
            store.add(raw(lead_times=[5], lead_action={"sound": "builtin:nope"}))
        # 読み上げだけの予定はサウンド参照が無くても通る
        store.add(raw(action={"type": "speak", "text": "はい"}))

    def test_replace_and_patch_also_check_sounds(self):
        def check(ref):
            if ref != "builtin:ding":
                raise RuntimeError("だめ")
        store = config.Store(Path(self.tmp.name) / "c3.json", sound_check=check)
        s = store.add(raw())
        with self.assertRaises(ValidationError):
            store.replace(s["id"], raw(action={"type": "sound", "sound": "builtin:x"}))
        with self.assertRaises(ValidationError):
            store.patch(s["id"], {"action": {"type": "sound", "sound": "builtin:x"}})

    def test_too_many_schedules(self):
        for i in range(200):
            self.store.add(raw(name=f"s{i}"))
        with self.assertRaises(ValidationError):
            self.store.add(raw())

    def test_broken_schedule_in_file_is_skipped(self):
        self.path.write_text(json.dumps({
            "version": 1, "settings": {},
            "schedules": [raw(name="よい"), raw(name="わるい", time="99:99")],
        }, ensure_ascii=False), "utf-8")
        store = config.Store(self.path)
        self.assertEqual([s["name"] for s in store.schedules()], ["よい"])

    def test_unknown_settings_keys_are_ignored(self):
        self.path.write_text(json.dumps({
            "settings": {"default_volume": 0.3, "しらない設定": 1}, "schedules": [],
        }, ensure_ascii=False), "utf-8")
        store = config.Store(self.path)
        self.assertEqual(store.settings["default_volume"], 0.3)
        self.assertNotIn("しらない設定", store.settings)

    def test_broken_file_is_moved_aside(self):
        self.path.write_text("{ これはJSONではない", "utf-8")
        store = config.Store(self.path)
        self.assertEqual(store.schedules(), [])
        self.assertTrue(list(self.path.parent.glob("config.broken-*.json")))

    def test_broken_file_that_cannot_be_moved_still_starts(self):
        """退避に失敗しても起動できること。"""
        self.path.write_text("{ こわれている", "utf-8")
        real = pathlib.Path.rename

        def boom(self, target):
            raise OSError("動かせない")
        pathlib.Path.rename = boom
        try:
            store = config.Store(self.path)
        finally:
            pathlib.Path.rename = real
        self.assertEqual(store.schedules(), [])

    def test_settings_update(self):
        out = self.store.update_settings({"default_volume": 0.2})
        self.assertEqual(out["default_volume"], 0.2)
        self.assertEqual(config.Store(self.path).settings["default_volume"], 0.2)


if __name__ == "__main__":
    unittest.main()
