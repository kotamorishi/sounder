"""web/lib.js のテストを Python のテストから走らせる。

macOS 標準の JavaScriptCore(jsc) を使うので、追加インストールは要らない。
"""

import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JSC = Path("/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc")


class TestWebLib(unittest.TestCase):
    @unittest.skipUnless(JSC.exists(), "jsc が無い環境ではスキップ")
    def test_lib_js(self):
        r = subprocess.run([str(JSC), "tests/web_lib_test.js"], cwd=ROOT,
                           capture_output=True, text=True, timeout=60)
        out = (r.stdout + r.stderr).strip()
        self.assertEqual(r.returncode, 0, out)
        self.assertIn("すべて通過", out)

    @unittest.skipUnless(JSC.exists(), "jsc が無い環境ではスキップ")
    def test_app_js_starts_without_errors(self):
        """偽のサーバ応答で起動させ、初期描画がエラーなく終わることを確かめる。"""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        check = Path(tmp.name) / "start.js"
        check.write_text(
            "load('web/lib.js');\n"
            "load('web/app.js');\n"
            "drainMicrotasks();\n"                       # 画面の初期描画まで進める
            "var t = document.querySelector('#toast');\n"
            "if (t && t.hidden === false && t.textContent) {\n"
            "  print('画面にエラーが出ました: ' + t.textContent);\n"
            "  throw new Error('起動時エラー');\n"
            "}\n"
            "print('ok ' + (globalThis.__calls || []).join(' '));\n", "utf-8")
        # app.js は document を触るので、最低限の器を用意してから読み込む
        stub = Path(tmp.name) / "dom_stub.js"
        stub.write_text(DOM_STUB, "utf-8")
        r = subprocess.run([str(JSC), str(stub), str(check)], cwd=ROOT,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, (r.stdout + r.stderr))
        self.assertIn("/api/state", r.stdout)
        self.assertIn("/api/calendar", r.stdout)


DOM_STUB = """
/* app.js を読み込むだけのための最小の器（描画はしない） */
var noop = function () { return null; };
function Node() {}
Node.prototype.appendChild = noop;
Node.prototype.append = noop;
Node.prototype.addEventListener = noop;
Node.prototype.setAttribute = noop;
Node.prototype.remove = noop;
Node.prototype.click = noop;
Node.prototype.showModal = noop;
Node.prototype.close = noop;
Node.prototype.focus = noop;
Object.defineProperty(Node.prototype, 'classList', {
  get: function () { return { add: noop, remove: noop, toggle: noop, contains: function () { return false; } }; },
});
Object.defineProperty(Node.prototype, 'dataset', { get: function () { return {}; } });
Object.defineProperty(Node.prototype, 'style', { get: function () { return {}; } });
Object.defineProperty(Node.prototype, 'options', { get: function () { return []; } });
var __nodes = {};
var document = {
  createElement: function () { return new Node(); },
  createTextNode: function () { return new Node(); },
  // 同じセレクタには同じ器を返す（あとから中身を確かめられるように）
  querySelector: function (sel) {
    if (!__nodes[sel]) { __nodes[sel] = new Node(); __nodes[sel].hidden = true; }
    return __nodes[sel];
  },
  querySelectorAll: function () { return []; },
  addEventListener: noop,
  activeElement: { tagName: 'BODY' },
};
var window = { addEventListener: noop, scrollTo: noop };
var location = { hash: '', origin: 'http://127.0.0.1:8777' };
var setInterval = noop, setTimeout = noop, clearTimeout = noop;
var FAKE = {
  '/api/state': {
    settings: { default_volume: 0.6, speak_rate: 180, master_enabled: true,
                default_voice: '', quiet_hours: { enabled: false, start: '23:00', end: '07:00' } },
    schedules: [{ id: 'a', name: 'てすと', enabled: true, kind: 'weekly', summary: '平日 08:15',
                  next_at: '2026-09-24T08:15:00', lead_times: [10], note: 'めも',
                  action: { type: 'both', sound: 'builtin:ding', text: 'はい', volume: 0.6, repeat: 2 } }],
    sounds: { builtin: [{ ref: 'builtin:ding', label: 'ピーン' }],
              user: [{ ref: 'user:a.mp3', label: 'a.mp3' }], system: [] },
    voices: [{ name: 'Kyoko', locale: 'ja_JP' }, { name: 'Alex', locale: 'en_US' }],
    next_events: [{ schedule_id: 'a', name: 'てすと', tag: 'lead', lead: 10,
                    at: '2026-09-24T08:05:00' }],
    log: [{ ts: '2026-09-24T00:00:00', level: 'fired', message: '鳴らしました' }],
    started_at: '2026-09-24T00:00:00',
  },
  '/api/calendar': {
    start: '2026-09-21',
    days: [{ date: '2026-09-21', weekday: 0, day: 21, month: 9, is_today: true,
             events: [{ schedule_id: 'a', name: 'てすと', at: '2026-09-21T08:05:00',
                        time: '08:05', tag: 'lead', lead: 10, enabled: true, kind: 'weekly',
                        action_type: 'sound', sound: 'builtin:ding', quiet: false, past: false }] }],
  },
};
globalThis.__calls = [];
var fetch = function (path) {
  globalThis.__calls.push(String(path).split('?')[0]);
  var key = String(path).split('?')[0];
  var body = JSON.stringify(FAKE[key] || {});
  return Promise.resolve({ ok: true, status: 200, text: function () { return Promise.resolve(body); } });
};
var confirm = function () { return false; };
var btoa = function (s) { return s; };
var Uint8Array = Uint8Array || function () {};
"""


if __name__ == "__main__":
    unittest.main()
