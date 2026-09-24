/* sounder の純関数だけを集めたもの。DOM を触らないので単体テストできる。
   ブラウザでは window.SL、テスト（jsc）では globalThis.SL として読む。 */
(function (root) {
  'use strict';

  var DAYS = ['月', '火', '水', '木', '金', '土', '日'];

  function pad2(n) { return String(n).padStart(2, '0'); }

  /** Date -> '2026-09-24'（ローカル時刻のまま。UTC にずらさない） */
  function isoDate(d) {
    return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate());
  }

  /** '2026-09-24' や '2026-09-24T08:15:00' -> Date（ローカル解釈） */
  function parseLocal(s) {
    var m = String(s).match(/^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?/);
    if (!m) return new Date(NaN);
    return new Date(+m[1], +m[2] - 1, +m[3], +(m[4] || 0), +(m[5] || 0), +(m[6] || 0));
  }

  function addDays(d, n) {
    var c = new Date(d.getTime());
    c.setDate(c.getDate() + n);
    return c;
  }

  /** その週の月曜日（週の始まりは月曜） */
  function weekStart(d) {
    var c = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    return addDays(c, -((c.getDay() + 6) % 7));
  }

  function sameDay(a, b) {
    return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth()
      && a.getDate() === b.getDate();
  }

  /** 週の見出し '9/22 – 9/28' */
  function weekRangeLabel(startISO) {
    var a = parseLocal(startISO), b = addDays(a, 6);
    return (a.getMonth() + 1) + '/' + a.getDate() + ' – ' + (b.getMonth() + 1) + '/' + b.getDate();
  }

  /** 時計表示 '9/24(木) 0:15' */
  function fmtClock(d) {
    return (d.getMonth() + 1) + '/' + d.getDate() + '(' + DAYS[(d.getDay() + 6) % 7] + ') '
      + d.getHours() + ':' + pad2(d.getMinutes());
  }

  /** 予定時刻の見出し '今日 8:15' / '明日 8:15' / '9/28(月) 8:15' */
  function fmtWhen(iso, now) {
    var d = parseLocal(iso);
    now = now || new Date();
    var hm = d.getHours() + ':' + pad2(d.getMinutes());
    if (sameDay(d, now)) return '今日 ' + hm;
    if (sameDay(d, addDays(now, 1))) return '明日 ' + hm;
    if (sameDay(d, addDays(now, -1))) return '昨日 ' + hm;
    return (d.getMonth() + 1) + '/' + d.getDate() + '(' + DAYS[(d.getDay() + 6) % 7] + ') ' + hm;
  }

  /** 残り時間 'あと7時間50分'。過ぎていれば空文字 */
  function fmtCountdown(iso, now) {
    var ms = parseLocal(iso) - (now || new Date());
    if (ms < 0) return '';
    var min = Math.floor(ms / 60000);
    if (min < 1) return 'あと1分未満';
    if (min < 60) return 'あと' + min + '分';
    var h = Math.floor(min / 60);
    if (h < 24) return 'あと' + h + '時間' + (min % 60 ? (min % 60) + '分' : '');
    var days = Math.floor(h / 24);
    if (days < 31) return 'あと' + days + '日' + (h % 24 ? (h % 24) + '時間' : '');
    return 'あと' + Math.floor(days / 30) + 'か月ほど';
  }

  /** 曜日の並びを短く言い換える */
  function describeDays(days) {
    var d = (days || []).slice().sort(function (a, b) { return a - b; });
    if (d.length === 7) return '毎日';
    if (String(d) === '0,1,2,3,4') return '平日';
    if (String(d) === '5,6') return '週末';
    if (!d.length) return '曜日を選んでください';
    return d.map(function (i) { return DAYS[i]; }).join('') + '曜';
  }

  function describeDom(dom, bare) {
    if (dom === 'last') return bare ? '末' : '毎月末';
    return bare ? dom + '日' : '毎月' + dom + '日';
  }

  /** 編集画面のその場プレビュー。サーバの describe() と同じ言い方に揃えている。 */
  function describeRecurrence(d) {
    var base;
    if (d.kind === 'weekly') base = describeDays(d.days) + ' ' + (d.time || '');
    else if (d.kind === 'monthly') base = describeDom(d.day_of_month) + ' ' + (d.time || '');
    else if (d.kind === 'yearly') {
      base = '毎年' + d.month + '月' + describeDom(d.day_of_month, true) + ' ' + (d.time || '');
    } else if (d.kind === 'once') base = (d.date || '') + ' ' + (d.time || '');
    else {
      var w = d.window || {};
      base = describeDays(d.days) + ' ' + w.start + '〜' + w.end + ' の ' + d.every_minutes + '分ごと';
    }
    base = base.trim();
    if ((d.lead_times || []).length) {
      base += '（' + d.lead_times.map(function (m) { return m + '分前'; }).join('・') + 'に予告）';
    }
    return base;
  }

  /** 「1回だけ」の予定がもう過ぎているか */
  function isPast(d, now) {
    if (d.kind !== 'once' || !d.date || !d.time) return false;
    return parseLocal(d.date + 'T' + d.time + ':00') <= (now || new Date());
  }

  /** 禁止時間の説明文 */
  function describeQuiet(q) {
    if (!q || !q.enabled) return '禁止時間は使っていません。夜中でも予定どおり鳴ります。';
    var wrap = q.start > q.end ? '（日付をまたぎます）' : '';
    return q.start + ' から ' + q.end + ' までに来た予定は鳴らしません' + wrap + '。';
  }

  function volumePct(v) { return Math.round((v == null ? 0.6 : Number(v)) * 100) + '%'; }

  /** 1〜31 と「月末」の選択肢 */
  function domOptions() {
    var out = [];
    for (var i = 1; i <= 31; i++) out.push({ value: String(i), label: i + '日' });
    out.push({ value: 'last', label: '月末（28〜31日）' });
    return out;
  }

  function monthOptions() {
    var out = [];
    for (var i = 1; i <= 12; i++) out.push({ value: String(i), label: i + '月' });
    return out;
  }

  var INTERVALS = [5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 240, 360, 720];
  function intervalOptions() {
    return INTERVALS.map(function (m) {
      return { value: String(m), label: m % 60 === 0 && m >= 60 ? (m / 60) + '時間ごと' : m + '分ごと' };
    });
  }

  /** 31 日指定のときだけ注意を出す */
  function domHint(dom) {
    if (dom === 'last') return '2月は28日（うるう年は29日）、その他は30日か31日に鳴ります。';
    if (Number(dom) >= 29) return dom + '日が無い月は鳴りません。毎月必ず鳴らしたいときは「月末」を選んでください。';
    return '';
  }

  root.SL = {
    DAYS: DAYS, pad2: pad2, isoDate: isoDate, parseLocal: parseLocal, addDays: addDays,
    weekStart: weekStart, sameDay: sameDay, weekRangeLabel: weekRangeLabel,
    fmtClock: fmtClock, fmtWhen: fmtWhen, fmtCountdown: fmtCountdown,
    describeDays: describeDays, describeDom: describeDom, describeRecurrence: describeRecurrence,
    describeQuiet: describeQuiet, volumePct: volumePct, isPast: isPast,
    domOptions: domOptions, monthOptions: monthOptions, intervalOptions: intervalOptions,
    domHint: domHint,
  };
})(typeof globalThis !== 'undefined' ? globalThis : this);
