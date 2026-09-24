/* web/lib.js のテスト。JavaScriptCore(jsc) で実行する。
   使い方: jsc tests/web_lib_test.js -- web/lib.js            */

var failures = [];
var used = {};

function eq(actual, expected, what) {
  var a = JSON.stringify(actual), b = JSON.stringify(expected);
  if (a !== b) failures.push(what + ': ' + a + ' != ' + b);
}
function ok(cond, what) { if (!cond) failures.push(what); }

load('web/lib.js');

// どの関数を呼んだか記録して、最後に未使用が無いことを確かめる
var RAW = SL;
SL = new Proxy(RAW, {
  get: function (t, k) { used[k] = true; return t[k]; },
});

var NOW = new Date(2026, 8, 24, 1, 30);   // 2026-09-24(木) 01:30

// --- 日付まわり ---
eq(SL.pad2(7), '07', 'pad2');
eq(SL.isoDate(new Date(2026, 0, 5)), '2026-01-05', 'isoDate');
eq(SL.isoDate(SL.parseLocal('2026-09-24T08:15:00')), '2026-09-24', 'parseLocal 日時');
eq(SL.parseLocal('2026-09-24').getHours(), 0, 'parseLocal 日付のみ');
eq(SL.parseLocal('2026-09-24 08:15').getMinutes(), 15, 'parseLocal 空白区切り');
ok(isNaN(SL.parseLocal('めちゃくちゃ').getTime()), 'parseLocal 不正な入力');
eq(SL.isoDate(SL.addDays(new Date(2026, 8, 30), 1)), '2026-10-01', 'addDays 月をまたぐ');
eq(SL.isoDate(SL.weekStart(new Date(2026, 8, 24))), '2026-09-21', 'weekStart 木曜から');
eq(SL.isoDate(SL.weekStart(new Date(2026, 8, 27))), '2026-09-21', 'weekStart 日曜から');
eq(SL.isoDate(SL.weekStart(new Date(2026, 8, 21))), '2026-09-21', 'weekStart 月曜から');
ok(SL.sameDay(new Date(2026, 8, 24, 1), new Date(2026, 8, 24, 23)), 'sameDay 同じ日');
ok(!SL.sameDay(new Date(2026, 8, 24), new Date(2026, 8, 25)), 'sameDay 違う日');
ok(!SL.sameDay(new Date(2026, 8, 24), new Date(2025, 8, 24)), 'sameDay 違う年');
eq(SL.weekRangeLabel('2026-09-21'), '9/21 – 9/27', 'weekRangeLabel');

// --- 表示の文字列 ---
eq(SL.fmtClock(new Date(2026, 8, 24, 9, 5)), '9/24(木) 9:05', 'fmtClock');
eq(SL.fmtWhen('2026-09-24T08:15:00', NOW), '今日 8:15', 'fmtWhen 今日');
eq(SL.fmtWhen('2026-09-25T08:15:00', NOW), '明日 8:15', 'fmtWhen 明日');
eq(SL.fmtWhen('2026-09-23T08:15:00', NOW), '昨日 8:15', 'fmtWhen 昨日');
eq(SL.fmtWhen('2026-09-28T08:05:00', NOW), '9/28(月) 8:05', 'fmtWhen 先の日');
eq(SL.fmtCountdown('2026-09-24T01:00:00', NOW), '', 'fmtCountdown 過去');
eq(SL.fmtCountdown('2026-09-24T01:30:30', NOW), 'あと1分未満', 'fmtCountdown 1分未満');
eq(SL.fmtCountdown('2026-09-24T01:45:00', NOW), 'あと15分', 'fmtCountdown 分');
eq(SL.fmtCountdown('2026-09-24T08:15:00', NOW), 'あと6時間45分', 'fmtCountdown 時間と分');
eq(SL.fmtCountdown('2026-09-24T07:30:00', NOW), 'あと6時間', 'fmtCountdown ちょうど');
eq(SL.fmtCountdown('2026-09-26T03:30:00', NOW), 'あと2日2時間', 'fmtCountdown 日');
eq(SL.fmtCountdown('2026-09-26T01:30:00', NOW), 'あと2日', 'fmtCountdown ちょうど日');
eq(SL.fmtCountdown('2027-01-01T00:00:00', NOW), 'あと3か月ほど', 'fmtCountdown 遠い先');

// --- 繰り返しの言い換え ---
eq(SL.describeDays([0, 1, 2, 3, 4]), '平日', 'describeDays 平日');
eq(SL.describeDays([5, 6]), '週末', 'describeDays 週末');
eq(SL.describeDays([0, 1, 2, 3, 4, 5, 6]), '毎日', 'describeDays 毎日');
eq(SL.describeDays([0, 2, 4]), '月水金曜', 'describeDays 個別');
eq(SL.describeDays([]), '曜日を選んでください', 'describeDays 空');
eq(SL.describeDays([4, 0]), '月金曜', 'describeDays 並べ替え');
eq(SL.describeDom('last'), '毎月末', 'describeDom 月末');
eq(SL.describeDom('last', true), '末', 'describeDom 月末 bare');
eq(SL.describeDom(5), '毎月5日', 'describeDom 日');
eq(SL.describeDom(5, true), '5日', 'describeDom 日 bare');

eq(SL.describeRecurrence({ kind: 'weekly', time: '08:15', days: [0, 1, 2, 3, 4], lead_times: [] }),
   '平日 08:15', 'describeRecurrence 毎週');
eq(SL.describeRecurrence({ kind: 'weekly', time: '08:15', days: [0], lead_times: [10, 5] }),
   '月曜 08:15（10分前・5分前に予告）', 'describeRecurrence 予告つき');
eq(SL.describeRecurrence({ kind: 'monthly', time: '10:00', day_of_month: '25' }),
   '毎月25日 10:00', 'describeRecurrence 毎月');
eq(SL.describeRecurrence({ kind: 'monthly', time: '10:00', day_of_month: 'last' }),
   '毎月末 10:00', 'describeRecurrence 毎月末');
eq(SL.describeRecurrence({ kind: 'yearly', time: '09:00', month: '4', day_of_month: '1' }),
   '毎年4月1日 09:00', 'describeRecurrence 毎年');
eq(SL.describeRecurrence({ kind: 'once', time: '07:00', date: '2026-09-25' }),
   '2026-09-25 07:00', 'describeRecurrence 1回だけ');
eq(SL.describeRecurrence({ kind: 'interval', every_minutes: '60', days: [0, 1, 2, 3, 4, 5, 6],
                           window: { start: '09:00', end: '21:00' } }),
   '毎日 09:00〜21:00 の 60分ごと', 'describeRecurrence 間隔');

// --- 設定まわり ---
eq(SL.describeQuiet(null), '禁止時間は使っていません。夜中でも予定どおり鳴ります。', 'describeQuiet なし');
eq(SL.describeQuiet({ enabled: false }), '禁止時間は使っていません。夜中でも予定どおり鳴ります。', 'describeQuiet オフ');
eq(SL.describeQuiet({ enabled: true, start: '23:00', end: '07:00' }),
   '23:00 から 07:00 までに来た予定は鳴らしません（日付をまたぎます）。', 'describeQuiet またぎ');
eq(SL.describeQuiet({ enabled: true, start: '09:00', end: '17:00' }),
   '09:00 から 17:00 までに来た予定は鳴らしません。', 'describeQuiet 日中');
eq(SL.volumePct(0.6), '60%', 'volumePct');
eq(SL.volumePct(null), '60%', 'volumePct 既定');
eq(SL.volumePct('1.25'), '125%', 'volumePct 文字列');

// --- 選択肢 ---
eq(SL.domOptions().length, 32, 'domOptions 件数');
eq(SL.domOptions()[0], { value: '1', label: '1日' }, 'domOptions 先頭');
eq(SL.domOptions()[31].value, 'last', 'domOptions 末尾は月末');
eq(SL.monthOptions().length, 12, 'monthOptions 件数');
eq(SL.monthOptions()[11], { value: '12', label: '12月' }, 'monthOptions 末尾');
var iv = SL.intervalOptions();
eq(iv[0], { value: '5', label: '5分ごと' }, 'intervalOptions 分');
eq(iv[iv.length - 1], { value: '720', label: '12時間ごと' }, 'intervalOptions 時間');
eq(SL.domHint('last'), '2月は28日（うるう年は29日）、その他は30日か31日に鳴ります。', 'domHint 月末');
ok(SL.domHint('31').indexOf('鳴りません') > 0, 'domHint 31日');
eq(SL.domHint('15'), '', 'domHint ふつうの日');
eq(SL.DAYS.length, 7, 'DAYS');

// すべての公開関数を一度は呼んだか
Object.keys(RAW).forEach(function (k) {
  if (!used[k]) failures.push('未テストの関数: ' + k);
});

if (failures.length) {
  failures.forEach(function (f) { print('NG  ' + f); });
  print('失敗 ' + failures.length + ' 件');
  throw new Error('web/lib.js のテストに失敗しました');
}
print('web/lib.js: すべて通過（公開 ' + Object.keys(RAW).length + ' 項目）');
