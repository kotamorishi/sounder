"""web/lib.js と web/app.js のテストを Python のテストから走らせる。

macOS 標準の JavaScriptCore(jsc) を使うので、追加インストールは要らない。
jsc の無い環境（Linux など）では、node があればそれで同じテストを走らせる。
"""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JSC = Path("/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc")
NODE = shutil.which("node")

# node で jsc と同じ書き方（load / print / drainMicrotasks）を使えるようにする器。
# 各ファイルは同じグローバルで順に実行する（ブラウザの <script> を並べたのと同じ）。
NODE_RUNNER = """
const vm = require('vm'), fs = require('fs');
globalThis.print = (...a) => console.log(a.join(' '));
globalThis.load = (f) => vm.runInThisContext(fs.readFileSync(f, 'utf8'), { filename: f });
const later = setImmediate;
// jsc の drainMicrotasks の代わり。node では同期的に流せないので、続きを後回しにする
globalThis.settle = (f) => later(() => {
  try { f(); } catch (e) { console.error(e && e.stack || e); process.exitCode = 1; }
});
for (const f of process.argv.slice(2)) load(f);
"""


def run_js(files, tmp):
    """jsc か node で JS ファイルを順に実行する。どちらも無ければ None。"""
    if JSC.exists():
        return subprocess.run([str(JSC), *map(str, files)], cwd=ROOT,
                              capture_output=True, text=True, timeout=60)
    if NODE:
        runner = Path(tmp) / "runner.js"
        runner.write_text(NODE_RUNNER, "utf-8")
        return subprocess.run([NODE, str(runner), *map(str, files)], cwd=ROOT,
                              capture_output=True, text=True, timeout=60)
    return None


HAS_JS = JSC.exists() or bool(NODE)


class TestWebLib(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    @unittest.skipUnless(HAS_JS, "jsc も node も無い環境ではスキップ")
    def test_lib_js(self):
        r = run_js(["tests/web_lib_test.js"], self.tmp.name)
        out = (r.stdout + r.stderr).strip()
        self.assertEqual(r.returncode, 0, out)
        self.assertIn("すべて通過", out)

    def start_app(self, hash_="", after=""):
        """偽のサーバ応答で起動させ、初期描画のあとに after を実行する。"""
        check = Path(self.tmp.name) / "start.js"
        check.write_text(
            f"location.hash = {hash_!r};\n"
            "load('web/lib.js');\n"
            "load('web/app.js');\n"
            "settle(function () {\n"                  # 画面の初期描画まで進める
            "  var t = document.querySelector('#toast');\n"
            "  if (t && t.hidden === false && t.textContent) {\n"
            "    print('画面にエラーが出ました: ' + t.textContent);\n"
            "    throw new Error('起動時エラー');\n"
            "  }\n"
            f"  {after}\n"
            "  if (t.classList.contains('is-error')) {\n"
            "    print('操作でエラーが出ました: ' + t.textContent);\n"
            "    throw new Error('操作時エラー');\n"
            "  }\n"
            "  print('ok ' + (globalThis.__calls || []).join(' '));\n"
            "});\n", "utf-8")
        # app.js は document を触るので、最低限の器を用意してから読み込む
        stub = Path(self.tmp.name) / "dom_stub.js"
        stub.write_text(DOM_STUB, "utf-8")
        r = run_js([stub, check], self.tmp.name)
        self.assertEqual(r.returncode, 0, (r.stdout + r.stderr))
        self.assertIn("ok ", r.stdout, r.stdout + r.stderr)
        return r.stdout

    @unittest.skipUnless(HAS_JS, "jsc も node も無い環境ではスキップ")
    def test_app_js_starts_without_errors(self):
        out = self.start_app()
        self.assertIn("/api/state", out)

    @unittest.skipUnless(HAS_JS, "jsc も node も無い環境ではスキップ")
    def test_timeline_tab_uses_the_calendar_api(self):
        out = self.start_app("#timeline")
        self.assertIn("/api/calendar", out)

    @unittest.skipUnless(HAS_JS, "jsc も node も無い環境ではスキップ")
    def test_editor_and_its_pages_can_be_driven(self):
        """編集シートを開き、各子画面・複製・保存用の組み立てまでエラーなく通ること。"""
        after = (
            "openEditor(state.schedules[0]);\n"
            "['#pg-repeat', '#pg-sound', '#pg-speak', '#pg-lead'].forEach(function (p) {\n"
            "  pushPage(p); popPage();\n"
            "});\n"
            "['weekly', 'monthly', 'yearly', 'once', 'interval'].forEach(function (k) {\n"
            "  draft.kind = k; renderRepeatPage(); renderEditorValues();\n"
            "  var p = collect();\n"
            "  if (p.kind !== k) throw new Error('kind が変わりました: ' + k);\n"
            "});\n"
            "draft.kind = 'monthly'; draft.day_of_month = 'last';\n"
            "if (collect().day_of_month !== 'last') throw new Error('月末が送られません');\n"
            "draft.kind = 'yearly'; draft.month = '12'; draft.day_of_month = '24';\n"
            "var y = collect();\n"
            "if (y.month !== 12 || y.day_of_month !== 24) throw new Error('毎年の日付が違います');\n"
            "copyCurrent();\n"
            "if (editing !== null) throw new Error('複製が新規になっていません');\n"
            "openEditor(null, { dateISO: '2020-01-01', kind: 'once' });\n"
            "if (!SL.pastWarning(draft)) throw new Error('過去の日時の警告が出ません');\n"
            "document.querySelector('#toast').classList.remove('is-error');\n"
            "showTab('timeline', true); renderTimeline(false);\n"
            "showTab('settings', true); showTab('sounds', true);\n"
        )
        self.start_app("", after)


DOM_STUB = r"""
/* app.js を読み込むだけのための最小の器（描画はしない） */
var noop = function () { return null; };
function Node() {
  this.childNodes = [];
  this._attrs = {};
  this._cls = {};
  this.dataset = {};
  this.style = { setProperty: noop };
  this.offsetHeight = 0;
  var self = this;
  this.classList = {
    add: function () { for (var i = 0; i < arguments.length; i++) self._cls[arguments[i]] = 1; },
    remove: function () { for (var i = 0; i < arguments.length; i++) delete self._cls[arguments[i]]; },
    toggle: function (c, on) {
      if (on === undefined) on = !self._cls[c];
      if (on) self._cls[c] = 1; else delete self._cls[c];
    },
    contains: function (c) { return !!self._cls[c]; },
  };
}
Node.prototype.append = function () {
  for (var i = 0; i < arguments.length; i++) this.childNodes.push(arguments[i]);
};
Node.prototype.prepend = Node.prototype.append;
Node.prototype.appendChild = Node.prototype.append;
Node.prototype.addEventListener = noop;
Node.prototype.setAttribute = function (k, v) { this._attrs[k] = String(v); };
Node.prototype.getAttribute = function (k) { return k in this._attrs ? this._attrs[k] : null; };
Node.prototype.remove = noop;
Node.prototype.click = noop;
Node.prototype.showModal = function () { this.open = true; };
Node.prototype.close = function () { this.open = false; };
Node.prototype.focus = noop;
Node.prototype.scrollTo = noop;
Node.prototype.showPicker = noop;
Node.prototype.querySelector = function () { return new Node(); };
Node.prototype.querySelectorAll = function () { return []; };
Node.prototype.closest = function () { return new Node(); };
Node.prototype.getBoundingClientRect = function () { return { top: 0, left: 0, width: 0, height: 0 }; };
var __nodes = {};
var document = {
  createElement: function () { return new Node(); },
  createElementNS: function () { return new Node(); },
  createTextNode: function () { return new Node(); },
  // 同じセレクタには同じ器を返す（あとから中身を確かめられるように）
  querySelector: function (sel) {
    if (!__nodes[sel]) { __nodes[sel] = new Node(); __nodes[sel].hidden = true; }
    return __nodes[sel];
  },
  querySelectorAll: function () { return []; },
  addEventListener: noop,
  activeElement: { tagName: 'BODY' },
  documentElement: new Node(),
  body: new Node(),
  hidden: false,
};
var window = { addEventListener: noop, scrollTo: noop, scrollY: 0 };
var location = { hash: '', origin: 'http://127.0.0.1:8777' };
var setInterval = noop, setTimeout = noop, clearTimeout = noop, clearInterval = noop;
var SCHED = { id: 'a', name: 'とても長い名前のお出かけの時間の予定', enabled: true, kind: 'weekly',
              time: '08:15', days: [0, 1, 2, 3, 4], summary: '平日 08:15',
              next_at: '2026-09-24T08:15:00', lead_times: [10], note: 'めも',
              action: { type: 'both', sound: 'builtin:ding', text: 'はい', volume: 0.6, repeat: 2 },
              lead_action: { sound: 'builtin:ding', speak_remaining: true } };
var FAKE = {
  '/api/state': {
    settings: { default_volume: 0.6, speak_rate: 180, master_enabled: true,
                default_voice: 'Kyoko', quiet_hours: { enabled: true, start: '23:00', end: '07:00' } },
    schedules: [SCHED,
      { id: 'b', name: '支払い', enabled: true, kind: 'monthly', time: '06:30', day_of_month: 'last',
        lead_times: [], action: { type: 'sound', sound: 'user:a.mp3', volume: 0.6, repeat: 1 } },
      { id: 'c', name: '記念日', enabled: false, kind: 'yearly', time: '09:00', month: 1, day_of_month: 1,
        lead_times: [], action: { type: 'speak', text: 'おめでとう', volume: 0.6, repeat: 1 } },
      { id: 'd', name: '病院', enabled: true, kind: 'once', time: '07:00', date: '2020-01-01',
        lead_times: [], action: { type: 'sound', sound: 'system:Ping', volume: 0.6, repeat: 1 } },
      { id: 'e', name: '時報', enabled: true, kind: 'interval', every_minutes: 7,
        window: { start: '21:00', end: '23:30' }, days: [0, 1, 2, 3, 4, 5, 6], lead_times: [],
        action: { type: 'sound', sound: 'builtin:ding', volume: 0.6, repeat: 1 } }],
    sounds: { builtin: [{ ref: 'builtin:ding', label: 'ピーン' }],
              user: [{ ref: 'user:a.mp3', label: 'a.mp3' }], system: [] },
    voices: [{ name: 'Kyoko', locale: 'ja_JP' }, { name: 'Alex', locale: 'en_US' }],
    next_events: [{ schedule_id: 'a', name: 'てすと', tag: 'lead', lead: 10, quiet: true,
                    at: '2026-09-24T08:05:00' }],
    log: [{ ts: '2026-09-24T00:00:00', level: 'fired', message: '鳴らしました' }],
    started_at: '2026-09-24T00:00:00',
    builtin_labels: { 'builtin:ding': 'ピーン' },
  },
  '/api/now': { next_events: [], log: [], playing: false },
  '/api/calendar': {
    start: '2026-09-21', master_enabled: true,
    days: [{ date: '2026-09-21', weekday: 0, day: 21, month: 9, is_today: true,
             events: [{ schedule_id: 'a', name: 'てすと', at: '2026-09-21T08:05:00',
                        time: '08:05', tag: 'lead', lead: 10, enabled: true, kind: 'weekly',
                        action_type: 'sound', sound: 'builtin:ding', quiet: false, past: true,
                        result: 'fired' },
                      { schedule_id: 'a', name: 'てすと', at: '2026-09-21T08:15:00',
                        time: '08:15', tag: 'main', lead: 0, enabled: true, kind: 'weekly',
                        action_type: 'both', sound: 'builtin:ding', quiet: false, past: false,
                        result: null },
                      { schedule_id: 'e', name: '時報', at: '2026-09-21T23:00:00',
                        time: '23:00', tag: 'main', lead: 0, enabled: true, kind: 'interval',
                        action_type: 'sound', sound: 'builtin:ding', quiet: true, past: false,
                        result: null }] }],
  },
};
globalThis.__calls = [];
var fetch = function (path) {
  globalThis.__calls.push(String(path).split('?')[0]);
  var key = String(path).split('?')[0];
  var body = JSON.stringify(FAKE[key] || {});
  return Promise.resolve({ ok: true, status: 200, text: function () { return Promise.resolve(body); } });
};
var btoa = function (s) { return s; };
if (typeof settle !== 'function') {
  // jsc: 溜まった Promise の続きを流してから確かめる
  globalThis.settle = function (f) { drainMicrotasks(); f(); };
}
"""


if __name__ == "__main__":
    unittest.main()
