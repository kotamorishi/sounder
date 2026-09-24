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

// --- 過ぎた予定の判定 ---
ok(SL.isPast({ kind: 'once', date: '2026-09-23', time: '08:00' }, NOW), 'isPast 過去');
ok(!SL.isPast({ kind: 'once', date: '2026-09-25', time: '08:00' }, NOW), 'isPast 未来');
ok(!SL.isPast({ kind: 'weekly', date: '2026-09-23', time: '08:00' }, NOW), 'isPast 毎週は対象外');
ok(!SL.isPast({ kind: 'once', time: '08:00' }, NOW), 'isPast 日付なし');
ok(!SL.isPast({ kind: 'once', date: '2026-09-25' }, NOW), 'isPast 時刻なし');

// --- B案の画面の言い換え ---
eq(SL.SECTIONS.map(function (x) { return x[1]; }), ['weekly', 'monthly', 'yearly', 'interval', 'once'],
   'SECTIONS の並び');
eq(SL.shortHM('08:05'), '8:05', 'shortHM 先頭の0');
eq(SL.shortHM('18:05'), '18:05', 'shortHM 2桁');
eq(SL.shortHM(null), '', 'shortHM 空');
eq(SL.daysLabel([0, 1, 2, 3, 4]), '平日', 'daysLabel 平日');
eq(SL.daysLabel([6, 5]), '週末', 'daysLabel 週末');
eq(SL.daysLabel([0, 1, 2, 3, 4, 5, 6]), '毎日', 'daysLabel 毎日');
eq(SL.daysLabel([4, 1]), '火・金', 'daysLabel 個別');
eq(SL.daysLabel([]), '曜日なし', 'daysLabel 空');
eq(SL.onceLabel('2026-09-27'), '9/27（日）', 'onceLabel');
eq(SL.onceLabel(''), '', 'onceLabel 空');
eq(SL.onceLabel('めちゃくちゃ'), '', 'onceLabel 不正');
eq(SL.leadLabel([10, 5]), '10分前・5分前', 'leadLabel');
eq(SL.leadLabel(null), '', 'leadLabel なし');
eq(SL.dateRule({ kind: 'monthly', day_of_month: 25 }), '毎月25日', 'dateRule 毎月');
eq(SL.dateRule({ kind: 'monthly', day_of_month: 'last' }), '毎月末', 'dateRule 毎月末');
eq(SL.dateRule({ kind: 'yearly', month: 1, day_of_month: 1 }), '毎年1月1日', 'dateRule 毎年');
eq(SL.dateRule({ kind: 'yearly', month: 2, day_of_month: 'last' }), '毎年2月末', 'dateRule 毎年 月末');

eq(SL.repeatSummary({ kind: 'weekly', days: [1, 4] }), '火・金', 'repeatSummary 毎週');
eq(SL.repeatSummary({ kind: 'monthly', day_of_month: 'last' }), '毎月末', 'repeatSummary 毎月');
eq(SL.repeatSummary({ kind: 'yearly', month: '12', day_of_month: '24' }), '毎年12月24日', 'repeatSummary 毎年');
eq(SL.repeatSummary({ kind: 'once', date: '2026-09-27' }), '1回だけ 9/27（日）', 'repeatSummary 1回');
eq(SL.repeatSummary({ kind: 'interval', every_minutes: 60, days: [0, 1, 2, 3, 4, 5, 6] }),
   '60分ごと', 'repeatSummary 間隔 毎日');
eq(SL.repeatSummary({ kind: 'interval', every_minutes: 90, days: [0, 1, 2, 3, 4] }),
   '90分ごと · 平日', 'repeatSummary 間隔 平日');

var W = { name: '朝', kind: 'weekly', time: '07:05', days: [0, 1, 2, 3, 4] };
var M = { name: '支払い', kind: 'monthly', time: '10:00', day_of_month: 25 };
var Y = { name: '記念日', kind: 'yearly', time: '09:00', month: 1, day_of_month: 1 };
var O = { name: '病院', kind: 'once', time: '06:30', date: '2026-09-27' };
var I = { name: '時報', kind: 'interval', every_minutes: 60, days: [0, 1, 2, 3, 4, 5, 6],
          window: { start: '09:00', end: '21:00' } };
eq(SL.rowTime(W), { text: '7:05', mid: false }, 'rowTime 毎週');
eq(SL.rowTime(M), { text: '10:00', mid: false }, 'rowTime 毎月');
eq(SL.rowTime(O), { text: '9/27（日）6:30', mid: true }, 'rowTime 1回');
eq(SL.rowTime(I), { text: '9:00 – 21:00', mid: true }, 'rowTime 間隔');
eq(SL.rowLabelParts(W), ['朝', ' · 平日'], 'rowLabelParts 毎週');
eq(SL.rowLabelParts(M), ['支払い', ' · 毎月25日'], 'rowLabelParts 毎月');
eq(SL.rowLabelParts(Y), ['記念日', ' · 毎年1月1日'], 'rowLabelParts 毎年');
eq(SL.rowLabelParts(O), ['病院'], 'rowLabelParts 1回');
eq(SL.rowLabelParts(I), ['時報', ' · 60分ごと', ' · 毎日'], 'rowLabelParts 間隔');
eq(SL.sortKey(W), '07:05', 'sortKey 毎週');
eq(SL.sortKey(M), '25 10:00', 'sortKey 毎月');
ok(SL.sortKey({ kind: 'monthly', day_of_month: 'last', time: '07:00' }) > SL.sortKey(M), 'sortKey 月末は後ろ');
eq(SL.sortKey(Y), '01-01 09:00', 'sortKey 毎年');
ok(SL.sortKey({ kind: 'yearly', month: 1, day_of_month: 'last', time: '07:00' }) > SL.sortKey(Y),
   'sortKey 毎年 月末は後ろ');
eq(SL.sortKey(O), '2026-09-27 06:30', 'sortKey 1回');
eq(SL.sortKey(I), '09:00', 'sortKey 間隔');
eq(SL.sortKey({ kind: 'interval' }), '', 'sortKey 間隔 時間帯なし');
eq(SL.sortKey({ kind: 'weekly' }), '', 'sortKey 時刻なし');

// --- 禁止時間 ---
var NIGHT = { enabled: true, start: '23:00', end: '07:00' };
var DAY = { enabled: true, start: '12:00', end: '13:00' };
ok(SL.inQuiet(NIGHT, '23:30'), 'inQuiet またぎ 夜');
ok(SL.inQuiet(NIGHT, '06:59'), 'inQuiet またぎ 朝');
ok(!SL.inQuiet(NIGHT, '07:00'), 'inQuiet 終了時刻は含まない');
ok(SL.inQuiet(DAY, 12 * 60), 'inQuiet 分で渡す');
ok(!SL.inQuiet(DAY, '13:00'), 'inQuiet 日中 外');
ok(!SL.inQuiet({ enabled: false, start: '00:00', end: '23:59' }, '12:00'), 'inQuiet オフ');
ok(!SL.inQuiet(null, '12:00'), 'inQuiet 設定なし');
ok(!SL.inQuiet({ enabled: true, start: '09:00', end: '09:00' }, '09:00'), 'inQuiet 同時刻');
ok(!SL.inQuiet(DAY, 'めちゃくちゃ'), 'inQuiet 不正な時刻');
eq(SL.quietState(W, NIGHT), '', 'quietState 毎週 かからない');
eq(SL.quietState(Object.assign({}, W, { time: '06:30' }), NIGHT), 'all', 'quietState 毎週 かかる');
eq(SL.quietState(I, DAY), 'some', 'quietState 間隔 一部');
eq(SL.quietState(I, { enabled: true, start: '08:00', end: '22:00' }), 'all', 'quietState 間隔 全部');
eq(SL.quietState(I, NIGHT), '', 'quietState 間隔 かからない');
eq(SL.quietState({ kind: 'interval', every_minutes: 30, window: { start: '22:00', end: '01:00' } }, NIGHT),
   'some', 'quietState 間隔 日付をまたぐ');
eq(SL.quietState({ kind: 'interval', every_minutes: 0, window: { start: '22:00', end: '22:00' } }, NIGHT),
   'some', 'quietState 間隔 既定の刻み・終日');
eq(SL.quietState(W, null), '', 'quietState 設定なし');

eq(SL.nextNote(null, {}), '', 'nextNote 予定なし');
eq(SL.nextNote({ quiet: false }, { master_enabled: true }), '', 'nextNote 鳴る');
eq(SL.nextNote({ quiet: false }, { master_enabled: false }), '全体がオフなので鳴りません', 'nextNote 全体オフ');
eq(SL.nextNote({ quiet: true }, { quiet_hours: NIGHT }), '禁止時間（23:00〜7:00）なので鳴りません',
   'nextNote 禁止時間');
eq(SL.nextNote({ quiet: true }), '禁止時間（〜）なので鳴りません', 'nextNote 設定なし');

// --- タイムラインの状態 ---
eq(SL.eventState({ past: true, result: 'fired' }, true), ['再生しました', false], 'eventState 再生');
eq(SL.eventState({ past: true, result: 'skipped', quiet: true }, true),
   ['禁止時間のため鳴らしませんでした', false], 'eventState 禁止時間でスキップ');
eq(SL.eventState({ past: true, result: 'skipped', quiet: false }, true),
   ['鳴らしませんでした（全体オフ）', false], 'eventState 全体オフでスキップ');
eq(SL.eventState({ past: true, result: 'missed' }, true),
   ['過ぎていたため鳴らしませんでした', false], 'eventState 取りこぼし');
eq(SL.eventState({ past: true, enabled: false }, true), ['オフの予定', false], 'eventState 過去のオフ');
eq(SL.eventState({ past: true, enabled: true }, true), ['過ぎました', false], 'eventState 過去');
eq(SL.eventState({ enabled: false }, true), ['オフの予定なので鳴りません', true], 'eventState オフ');
eq(SL.eventState({ enabled: true }, false), ['全体がオフなので鳴りません', true], 'eventState 全体オフ');
eq(SL.eventState({ enabled: true, quiet: true }, true), ['禁止時間なので鳴りません', true], 'eventState 禁止時間');
eq(SL.eventState({ enabled: true }, true), [null, false], 'eventState 鳴る');

var byDate = SL.eventsByDate([
  { date: '2026-09-21', events: [
    { at: '2026-09-21T08:15:00', tag: 'main', lead: 0 },
    { at: '2026-09-21T08:05:00', tag: 'lead', lead: 10 },
  ] },
  { date: '2026-09-22', events: [
    { at: '2026-09-21T23:40:00', tag: 'lead', lead: 30 },   // 火曜 0:10 の予告は月曜に鳴る
    { at: '2026-09-22T00:10:00', tag: 'main', lead: 0 },
    { at: '2026-09-22T00:10:00', tag: 'lead', lead: 0 },
  ] },
]);
eq(Object.keys(byDate).sort(), ['2026-09-21', '2026-09-22'], 'eventsByDate 日付');
eq(byDate['2026-09-21'].map(function (e) { return e.at.slice(11, 16); }), ['08:05', '08:15', '23:40'],
   'eventsByDate 時刻順・前日の予告');
eq(byDate['2026-09-21'][0].main_at, '2026-09-21T08:15:00', 'eventsByDate 予告の本番時刻');
eq(byDate['2026-09-21'][2].main_at, '2026-09-22T00:10:00', 'eventsByDate 日付をまたぐ本番時刻');
eq(byDate['2026-09-22'].map(function (e) { return e.tag; }), ['lead', 'main'], 'eventsByDate 同時刻は予告が先');
eq(SL.eventsByDate(null), {}, 'eventsByDate 空');

eq(SL.pastWarning({ kind: 'once', date: '2026-09-23', time: '08:00' }, NOW),
   'この日時はもう過ぎています。保存しても鳴りません。', 'pastWarning 過去');
eq(SL.pastWarning({ kind: 'once', date: '2026-09-25', time: '08:00' }, NOW), '', 'pastWarning 未来');

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
