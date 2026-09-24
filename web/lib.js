/* sounder の純関数だけを集めたもの。DOM を触らないので単体テストできる。
   ブラウザでは window.SL、テスト（jsc）では globalThis.SL として読む。 */
(function (root) {
  'use strict';

  var DAYS = ['月', '火', '水', '木', '金', '土', '日'];

  function pad2(n) { return String(n).padStart(2, '0'); }

  /** 検索用に文字をそろえる（全角半角・大文字小文字・カタカナ→ひらがな） */
  function foldText(s) {
    return String(s == null ? '' : s).normalize('NFKC').toLowerCase()
      .replace(/[ァ-ヶ]/g, function (c) { return String.fromCharCode(c.charCodeAt(0) - 0x60); });
  }

  /** 空白で区切った語が、fields のどこかに全部含まれるか（空の検索は何にでも当たる） */
  function matchText(q, fields) {
    var words = foldText(q).split(/\s+/).filter(Boolean);
    if (!words.length) return true;
    var hay = foldText((fields || []).join(' '));
    return words.every(function (w) { return hay.indexOf(w) >= 0; });
  }

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
    if (d.kind !== 'once' && skipLabel(d.skip)) base += '（' + skipLabel(d.skip) + '）';
    return base;
  }

  /** お休みの日の説明（'祝日・休校日は休み'）。skip が空なら '' */
  function skipLabel(skip) {
    skip = skip || [];
    var parts = [];
    if (skip.indexOf('on_holidays') >= 0) parts.push('祝日');
    if (skip.some(function (c) { return c.indexOf('tdsb_') === 0; })) parts.push('休校日');
    return parts.length ? parts.join('・') + 'は休み' : '';
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

  // ---------------------------------------------------------------- B案の画面で使う言い換え

  /** 予定一覧の区分（この順に並べる） */
  var SECTIONS = [['毎週', 'weekly'], ['毎月', 'monthly'], ['毎年', 'yearly'],
    ['一定間隔', 'interval'], ['1回だけ', 'once']];

  /** '08:05' -> '8:05' */
  function shortHM(hhmm) { return String(hhmm || '').replace(/^0(\d)/, '$1'); }

  function toMin(hhmm) {
    var m = String(hhmm || '').match(/^(\d{1,2}):(\d{2})/);
    return m ? +m[1] * 60 + +m[2] : NaN;
  }

  /** 曜日を「火・金」のように短く（一覧・繰り返しの値に使う） */
  function daysLabel(days) {
    var d = (days || []).slice().sort(function (a, b) { return a - b; });
    var k = d.join(',');
    if (k === '0,1,2,3,4,5,6') return '毎日';
    if (k === '0,1,2,3,4') return '平日';
    if (k === '5,6') return '週末';
    if (!d.length) return '曜日なし';
    return d.map(function (i) { return DAYS[i]; }).join('・');
  }

  /** '2026-09-27' -> '9/27（日）' */
  function onceLabel(ymd) {
    if (!ymd) return '';
    var d = parseLocal(ymd);
    if (isNaN(d.getTime())) return '';
    return (d.getMonth() + 1) + '/' + d.getDate() + '（' + DAYS[(d.getDay() + 6) % 7] + '）';
  }

  function leadLabel(leads) {
    return (leads || []).map(function (m) { return m + '分前'; }).join('・');
  }

  /** 毎月・毎年の日付 '毎月25日' / '毎月末' / '毎年1月1日' / '毎年2月末' */
  function dateRule(s) {
    if (s.kind === 'monthly') return describeDom(s.day_of_month);
    return '毎年' + s.month + '月' + describeDom(s.day_of_month, true);
  }

  /** 編集シートの「繰り返し ›」の値 */
  function repeatSummary(d) {
    if (d.kind === 'once') return '1回だけ ' + onceLabel(d.date);
    if (d.kind === 'monthly' || d.kind === 'yearly') return dateRule(d);
    if (d.kind === 'interval') {
      var dl = daysLabel(d.days);
      return d.every_minutes + '分ごと' + (dl === '毎日' ? '' : ' · ' + dl);
    }
    return daysLabel(d.days);
  }

  /** 一覧の行: 大きく出す時刻（mid=true は少し小さい字で出す長さのもの） */
  function rowTime(s) {
    if (s.kind === 'interval') {
      return { text: shortHM(s.window.start) + ' – ' + shortHM(s.window.end), mid: true };
    }
    if (s.kind === 'once') return { text: onceLabel(s.date) + shortHM(s.time), mid: true };
    return { text: shortHM(s.time), mid: false };
  }

  /** 一覧の行: 「名前 · 平日」を意味のまとまりに分けたもの（先頭の空白はまとまりの区切り） */
  function rowLabelParts(s) {
    if (s.kind === 'once') return [s.name];
    var parts;
    if (s.kind === 'interval') {
      parts = [s.name, ' · ' + s.every_minutes + '分ごと', ' · ' + daysLabel(s.days)];
    } else if (s.kind === 'monthly' || s.kind === 'yearly') {
      parts = [s.name, ' · ' + dateRule(s)];
    } else {
      parts = [s.name, ' · ' + daysLabel(s.days)];
    }
    if (skipLabel(s.skip)) parts.push('（' + skipLabel(s.skip) + '）');
    return parts;
  }

  /** 一覧の並び順（区分の中で時刻順） */
  function sortKey(s) {
    if (s.kind === 'once') return s.date + ' ' + s.time;
    if (s.kind === 'interval') return (s.window || {}).start || '';
    if (s.kind === 'yearly') {
      return pad2(s.month) + '-' + (s.day_of_month === 'last' ? '32' : pad2(s.day_of_month)) + ' ' + s.time;
    }
    if (s.kind === 'monthly') {
      return (s.day_of_month === 'last' ? '32' : pad2(s.day_of_month)) + ' ' + s.time;
    }
    return s.time || '';
  }

  /** その時刻（'HH:MM' か分）が禁止時間に入るか。サーバの in_quiet_hours と同じ判定。 */
  function inQuiet(q, hhmm) {
    if (!q || !q.enabled) return false;
    var start = toMin(q.start), end = toMin(q.end);
    var cur = typeof hhmm === 'number' ? hhmm : toMin(hhmm);
    if (start === end || isNaN(cur)) return false;
    if (start < end) return start <= cur && cur < end;
    return cur >= start || cur < end;
  }

  /** 予定の本番が禁止時間にかかるか: 'all'（全部鳴らない）/ 'some'（一部）/ ''（かからない） */
  function quietState(s, q) {
    if (!q || !q.enabled) return '';
    if (s.kind !== 'interval') return inQuiet(q, s.time) ? 'all' : '';
    var w = s.window || {};
    var start = toMin(w.start), end = toMin(w.end);
    if (end <= start) end += 1440;
    var step = Math.max(1, Number(s.every_minutes) || 60);
    var hit = 0, n = 0;
    for (var t = start; t <= end; t += step) {
      n++;
      if (inQuiet(q, t % 1440)) hit++;
    }
    if (!hit) return '';
    return hit === n ? 'all' : 'some';
  }

  /** 次の予定の帯に添える注意（鳴らない理由）。鳴るなら空文字 */
  function nextNote(next, settings) {
    settings = settings || {};
    if (!next) return '';
    if (settings.master_enabled === false) return '全体がオフなので鳴りません';
    if (next.quiet) {
      var q = settings.quiet_hours || {};
      return '禁止時間（' + shortHM(q.start) + '〜' + shortHM(q.end) + '）なので鳴りません';
    }
    return '';
  }

  /** タイムラインの 1 件の状態 [表示する文, 鳴らないので破線にするか] */
  function eventState(e, master) {
    if (e.off) return [e.off + (e.past ? 'のため鳴らしませんでした' : 'のため鳴りません'), true];
    if (e.past) {
      if (e.result === 'fired') return ['再生しました', false];
      if (e.result === 'skipped') {
        return [e.quiet ? '禁止時間のため鳴らしませんでした' : '鳴らしませんでした（全体オフ）', false];
      }
      if (e.result === 'missed') return ['過ぎていたため鳴らしませんでした', false];
      if (!e.enabled) return ['オフの予定', false];
      return ['過ぎました', false];
    }
    if (!e.enabled) return ['オフの予定なので鳴りません', true];
    if (master === false) return ['全体がオフなので鳴りません', true];
    if (e.quiet) return ['禁止時間なので鳴りません', true];
    return [null, false];
  }

  /** /api/calendar の days を、発火時刻の日付ごとに振り分け直す。
      0 時台の予定の予告は前日に鳴るので、前日の欄に入れる。予告には本番の時刻 main_at を付ける。 */
  function eventsByDate(days) {
    var out = {};
    (days || []).forEach(function (d) {
      d.events.forEach(function (e) {
        var ev = Object.assign({}, e);
        if (e.tag === 'lead') {
          var m = new Date(parseLocal(e.at).getTime() + e.lead * 60000);
          ev.main_at = isoDate(m) + 'T' + pad2(m.getHours()) + ':' + pad2(m.getMinutes()) + ':00';
        } else {
          ev.main_at = e.at;
        }
        var key = e.at.slice(0, 10);
        (out[key] = out[key] || []).push(ev);
      });
    });
    Object.keys(out).forEach(function (k) {
      out[k].sort(function (a, b) {
        if (a.at !== b.at) return a.at < b.at ? -1 : 1;
        return (a.tag === 'lead' ? 0 : 1) - (b.tag === 'lead' ? 0 : 1);
      });
    });
    return out;
  }

  /** 1回だけの予定の過去警告の文（過ぎていなければ空） */
  function pastWarning(d, now) {
    return isPast(d, now) ? 'この日時はもう過ぎています。保存しても鳴りません。' : '';
  }

  root.SL = {
    DAYS: DAYS, pad2: pad2, isoDate: isoDate, parseLocal: parseLocal, addDays: addDays,
    weekStart: weekStart, sameDay: sameDay, weekRangeLabel: weekRangeLabel,
    fmtClock: fmtClock, fmtWhen: fmtWhen, fmtCountdown: fmtCountdown,
    describeDays: describeDays, describeDom: describeDom, describeRecurrence: describeRecurrence,
    describeQuiet: describeQuiet, volumePct: volumePct, isPast: isPast, skipLabel: skipLabel,
    domOptions: domOptions, monthOptions: monthOptions, intervalOptions: intervalOptions,
    domHint: domHint,
    SECTIONS: SECTIONS, shortHM: shortHM, daysLabel: daysLabel, onceLabel: onceLabel,
    leadLabel: leadLabel, dateRule: dateRule, repeatSummary: repeatSummary, rowTime: rowTime,
    rowLabelParts: rowLabelParts, sortKey: sortKey, inQuiet: inQuiet, quietState: quietState,
    nextNote: nextNote, eventState: eventState, eventsByDate: eventsByDate,
    pastWarning: pastWarning, foldText: foldText, matchText: matchText,
  };
})(typeof globalThis !== 'undefined' ? globalThis : this);
