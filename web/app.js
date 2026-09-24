'use strict';

/* 画面の組み立て。純関数は lib.js（SL）に置いている。 */

var state = {
  settings: {}, schedules: [], sounds: { builtin: [], user: [], system: [] },
  voices: [], next_events: [], log: [], started_at: null,
};
var weekStartISO = SL.isoDate(SL.weekStart(new Date()));
var weekDays = [];
var filter = 'all';
var draft = null;      // 編集中の予定
var editingId = null;  // 既存を編集しているときだけ id
var daySheetDate = null;

var $ = function (s) { return document.querySelector(s); };
var $$ = function (s) { return Array.prototype.slice.call(document.querySelectorAll(s)); };

function el(tag, cls, text) {
  var e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

// ------------------------------------------------------------------ 通信

async function api(method, path, body) {
  var opt = { method: method, headers: {} };
  if (body !== undefined) {
    opt.headers['Content-Type'] = 'application/json';
    opt.body = JSON.stringify(body);
  }
  var res;
  try {
    res = await fetch(path, opt);
  } catch (e) {
    throw new Error('サーバに接続できませんでした');
  }
  var text = await res.text();
  var data = {};
  if (text) { try { data = JSON.parse(text); } catch (e) { /* 空でよい */ } }
  if (!res.ok) throw new Error(data.error || ('エラー (' + res.status + ')'));
  return data;
}

var toastTimer = null;
function toast(msg, isError) {
  var t = $('#toast');
  t.textContent = msg;
  t.classList.toggle('is-error', !!isError);
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(function () { t.hidden = true; }, isError ? 5000 : 2000);
}

function fail(e) { toast(e.message || String(e), true); }

// ------------------------------------------------------------------ 画面の切り替え

var VIEWS = ['week', 'list', 'sounds', 'settings'];

function setView(name, keepHash) {
  if (VIEWS.indexOf(name) < 0) name = 'week';
  if (!keepHash) location.hash = name === 'week' ? '' : name;
  $$('.tab').forEach(function (t) { t.classList.toggle('is-active', t.dataset.view === name); });
  $$('.view').forEach(function (v) { v.classList.toggle('is-active', v.id === 'view-' + name); });
  $('#fab').hidden = (name !== 'week' && name !== 'list');
  window.scrollTo(0, 0);
}

// ------------------------------------------------------------------ 週カレンダー

async function loadWeek() {
  try {
    var data = await api('GET', '/api/calendar?start=' + weekStartISO + '&days=7');
    weekDays = data.days;
    renderWeek();
    renderToday();
  } catch (e) { fail(e); }
}

function renderWeek() {
  $('#week-label').textContent = SL.weekRangeLabel(weekStartISO);
  var box = $('#week');
  box.innerHTML = '';
  var thisWeek = SL.isoDate(SL.weekStart(new Date()));

  weekDays.forEach(function (d) {
    var cell = el('div', 'wday');
    cell.setAttribute('role', 'gridcell');
    if (d.is_today) cell.classList.add('is-today');
    if (d.weekday >= 5) cell.classList.add('is-weekend');
    if (weekStartISO !== thisWeek) cell.classList.add('is-other');

    var head = el('div', 'wday-head');
    head.append(el('span', 'wday-dow', SL.DAYS[d.weekday]));
    head.append(el('span', 'wday-num', String(d.day)));
    cell.append(head);

    var shown = d.events.slice(0, 4);
    shown.forEach(function (ev) {
      var b = el('button', 'ev');
      b.type = 'button';
      b.append(document.createTextNode(ev.time));
      b.append(el('span', 'ev-name', ev.name));
      if (ev.tag === 'lead') b.classList.add('is-lead');
      if (!ev.enabled) b.classList.add('is-off');
      if (ev.quiet) b.classList.add('is-quiet');
      if (ev.past) b.classList.add('is-past');
      b.title = ev.name + (ev.tag === 'lead' ? '（' + ev.lead + '分前の予告）' : '');
      b.addEventListener('click', function (e) {
        e.stopPropagation();
        openEditorById(ev.schedule_id);
      });
      cell.append(b);
    });
    if (d.events.length > shown.length) {
      var more = el('button', 'ev-more', '+' + (d.events.length - shown.length));
      more.type = 'button';
      more.addEventListener('click', function (e) { e.stopPropagation(); openDay(d); });
      cell.append(more);
    }
    cell.addEventListener('click', function () { openDay(d); });
    box.append(cell);
  });
}

function renderStartHere() {
  var box = $('#start-here');
  box.hidden = state.schedules.length > 0;
  if (box.hidden) return;
  var grid = $('#start-grid');
  grid.innerHTML = '';
  PRESETS.slice(0, 4).forEach(function (p) {
    var b = el('button', 'preset');
    b.type = 'button';
    b.append(el('b', null, p.title), el('span', null, p.desc));
    b.addEventListener('click', function () { openEditor(p.make(), { asNew: true }); });
    grid.append(b);
  });
}

function renderToday() {
  $('#today-wrap').hidden = state.schedules.length === 0;
  var box = $('#today-list');
  box.innerHTML = '';
  var todayISO = SL.isoDate(new Date());
  var day = weekDays.filter(function (d) { return d.date === todayISO; })[0];
  var rows = day ? day.events.filter(function (e) { return !e.past && e.enabled; }) : [];
  $('#today-head').textContent = day ? '今日これから鳴るもの' : '今週の表示';
  if (!day) { box.append(el('p', 'today-none', '今日は別の週を見ています')); return; }
  if (!rows.length) { box.append(el('p', 'today-none', '今日はもう鳴りません')); return; }
  rows.slice(0, 8).forEach(function (ev, i) {
    var row = el('div', 'today-row' + (ev.tag === 'lead' ? ' is-lead' : '') + (i === 0 ? ' is-next' : ''));
    row.append(el('div', 'today-time', ev.time));
    row.append(el('div', 'today-name',
      ev.name + (ev.tag === 'lead' ? '（' + ev.lead + '分前の予告）' : '')));
    row.append(el('div', 'today-rel', SL.fmtCountdown(ev.at)));
    row.addEventListener('click', function () { openEditorById(ev.schedule_id); });
    box.append(row);
  });
}

function shiftWeek(n) {
  weekStartISO = SL.isoDate(SL.addDays(SL.parseLocal(weekStartISO), n * 7));
  loadWeek();
}

function renderHero() {
  var next = state.next_events[0];
  var hero = $('#hero');
  var note = $('#hero-note');
  var off = state.settings.master_enabled === false;
  note.hidden = true;
  if (off) {
    note.hidden = false;
    note.textContent = '全体がオフです。右上のスイッチを入れるまで何も鳴りません。';
  }
  if (!next) {
    $('#hero-when').hidden = true;
    $('#hero-name').textContent = state.schedules.length
      ? '予定はありません（すべてオフかもしれません）' : 'まだ予定がありません';
    $('#hero-count').textContent = '';
    $('#hero-test').hidden = true;
    hero.classList.remove('is-quiet');
    return;
  }
  $('#hero-when').hidden = false;
  $('#hero-when').textContent = SL.fmtWhen(next.at);
  $('#hero-name').textContent = next.name
    + (next.tag === 'lead' ? '（' + next.lead + '分前の予告）' : '');
  $('#hero-count').textContent = SL.fmtCountdown(next.at);
  var test = $('#hero-test');
  test.hidden = false;
  test.onclick = function () { testSchedule(next.schedule_id, next.tag); };
  hero.classList.toggle('is-quiet', !!next.quiet);
  if (next.quiet && !off) {
    note.hidden = false;
    note.textContent = '禁止時間に入るので、この予定は鳴りません。';
  }
}

// ------------------------------------------------------------------ その日の一覧

function openDay(d) {
  daySheetDate = d.date;
  var date = SL.parseLocal(d.date);
  $('#day-title').textContent = (date.getMonth() + 1) + '月' + date.getDate() + '日（'
    + SL.DAYS[d.weekday] + '）';
  var body = $('#day-body');
  body.innerHTML = '';
  if (!d.events.length) {
    body.append(el('p', 'day-empty', 'この日は何も鳴りません'));
  }
  d.events.forEach(function (ev) {
    var row = el('div', 'day-row');
    if (ev.tag === 'lead') row.classList.add('is-lead');
    if (!ev.enabled) row.classList.add('is-off');
    row.append(el('div', 'day-time', ev.time));
    var mid = el('div', 'day-body');
    mid.append(el('div', 'day-name', ev.name));
    var sub = [];
    if (ev.tag === 'lead') sub.push(ev.lead + '分前の予告');
    if (!ev.enabled) sub.push('オフ');
    if (ev.quiet) sub.push('禁止時間なので鳴りません');
    sub.push(soundLabel(ev.sound) || ({ speak: '読み上げ', both: '読み上げ' })[ev.action_type] || '');
    mid.append(el('div', 'day-sub', sub.filter(Boolean).join(' · ')));
    row.append(mid);
    var play = el('button', 'btn btn-ghost btn-sm', '▶');
    play.type = 'button';
    play.title = '試聴';
    play.addEventListener('click', function () { testSchedule(ev.schedule_id, ev.tag); });
    row.append(play);
    var edit = el('button', 'btn btn-sm', '編集');
    edit.type = 'button';
    edit.addEventListener('click', function () {
      $('#day-sheet').close();
      openEditorById(ev.schedule_id);
    });
    row.append(edit);
    body.append(row);
  });
  $('#day-sheet').showModal();
}

// ------------------------------------------------------------------ 予定の一覧

function soundLabel(ref) {
  if (!ref) return '';
  var groups = ['builtin', 'user', 'system'];
  for (var i = 0; i < groups.length; i++) {
    var hit = (state.sounds[groups[i]] || []).filter(function (s) { return s.ref === ref; })[0];
    if (hit) return hit.label;
  }
  return ref.replace(/^[a-z]+:/, '');
}

function renderList() {
  var box = $('#sched-list');
  box.innerHTML = '';
  var items = state.schedules.filter(function (s) {
    return filter === 'all' || (filter === 'on' ? s.enabled : !s.enabled);
  }).sort(function (a, b) {
    // 次に鳴る順。オフのものは後ろにまとめる
    if (!!a.enabled !== !!b.enabled) return a.enabled ? -1 : 1;
    if (!a.next_at || !b.next_at) return a.next_at ? -1 : (b.next_at ? 1 : 0);
    return a.next_at < b.next_at ? -1 : (a.next_at > b.next_at ? 1 : 0);
  });
  $('#sched-count').textContent = state.schedules.length ? '(' + state.schedules.length + ')' : '';
  $('#sched-empty').hidden = state.schedules.length > 0;

  items.forEach(function (s) {
    var card = el('div', 'card' + (s.enabled ? '' : ' is-off'));

    var main = el('div', 'card-main');
    var name = el('div', 'card-name', s.name);
    main.append(name);
    var sw = el('label', 'switch');
    sw.title = '有効／無効';
    var cb = el('input');
    cb.type = 'checkbox';
    cb.checked = !!s.enabled;
    cb.addEventListener('click', function (e) { e.stopPropagation(); });
    cb.addEventListener('change', function () { toggleSchedule(s, cb); });
    sw.append(cb, el('span', 'track'));
    main.append(sw);
    main.append(el('div', 'card-when', s.summary));
    main.addEventListener('click', function () { openEditor(s); });
    card.append(main);

    var tags = el('div', 'card-tags');
    var a = s.action;
    if (a.type === 'sound' || a.type === 'both') {
      tags.append(el('span', 'tag tag-sound', '🔊 ' + soundLabel(a.sound)));
    }
    if (a.type === 'speak' || a.type === 'both') {
      tags.append(el('span', 'tag tag-sound', '💬 ' + a.text));
    }
    if ((s.lead_times || []).length) {
      tags.append(el('span', 'tag tag-lead',
        s.lead_times.map(function (m) { return m + '分前'; }).join('・')));
    }
    tags.append(el('span', 'tag', SL.volumePct(a.volume)));
    if (a.repeat > 1) tags.append(el('span', 'tag', '×' + a.repeat));
    if (s.note) tags.append(el('span', 'tag', s.note));
    card.append(tags);

    var next = el('div', 'card-next');
    if (!s.enabled) next.append(el('span', null, 'オフ'));
    else if (s.next_at) {
      next.append(el('span', null, '次回 ' + SL.fmtWhen(s.next_at) + '・' + SL.fmtCountdown(s.next_at)));
    } else next.append(el('span', null, '次の予定なし'));
    var test = el('button', 'btn btn-ghost btn-sm', '▶ 試聴');
    test.type = 'button';
    test.addEventListener('click', function () { testSchedule(s.id, 'main'); });
    next.append(test);
    card.append(next);

    box.append(card);
  });
}

async function toggleSchedule(s, cb) {
  try {
    var data = await api('PATCH', '/api/schedules/' + s.id, { enabled: cb.checked });
    Object.assign(s, data.schedule);
    renderList();
    refreshLive();
  } catch (e) {
    cb.checked = !cb.checked;
    fail(e);
  }
}

async function testSchedule(id, which) {
  try {
    await api('POST', '/api/schedules/' + id + '/test', { which: which === 'lead' ? 'lead' : 'main' });
    toast(which === 'lead' ? '予告を鳴らしました' : '鳴らしました');
  } catch (e) { fail(e); }
}

// ------------------------------------------------------------------ サウンド

function renderSounds() {
  function fill(sel, items, deletable) {
    var box = $(sel);
    box.innerHTML = '';
    if (!items.length) {
      box.append(el('p', 'hint', 'ありません'));
      return;
    }
    items.forEach(function (s) {
      var b = el('button', 'sound');
      b.type = 'button';
      b.append(el('span', null, '▶'));
      b.append(el('span', 'sound-label', s.label));
      b.addEventListener('click', function () { preview(s.ref, b); });
      if (deletable) {
        var del = el('button', 'sound-del', '✕');
        del.type = 'button';
        del.title = '削除';
        del.addEventListener('click', function (e) {
          e.stopPropagation();
          deleteSound(s);
        });
        b.append(del);
      }
      box.append(b);
    });
  }
  fill('#lib-builtin', state.sounds.builtin, false);
  fill('#lib-user', state.sounds.user, true);
  fill('#lib-system', state.sounds.system, false);
}

async function preview(ref, node) {
  try {
    await api('POST', '/api/preview', { type: 'sound', sound: ref });
    if (node) {
      $$('.sound.is-playing').forEach(function (n) { n.classList.remove('is-playing'); });
      node.classList.add('is-playing');
      setTimeout(function () { node.classList.remove('is-playing'); }, 2500);
    }
  } catch (e) { fail(e); }
}

async function deleteSound(s) {
  if (!confirm('「' + s.label + '」を削除しますか？')) return;
  try {
    var data = await api('DELETE', '/api/sounds/' + encodeURIComponent(s.ref.slice(5)));
    state.sounds = data.sounds;
    renderSounds();
    toast('削除しました');
  } catch (e) { fail(e); }
}

async function uploadFiles(files) {
  var status = $('#upload-status');
  for (var i = 0; i < files.length; i++) {
    var file = files[i];
    status.textContent = file.name + ' を追加中…';
    try {
      var buf = await file.arrayBuffer();
      var bytes = new Uint8Array(buf);
      var bin = '';
      for (var j = 0; j < bytes.length; j += 0x8000) {
        bin += String.fromCharCode.apply(null, bytes.subarray(j, j + 0x8000));
      }
      var data = await api('POST', '/api/sounds', { filename: file.name, data: btoa(bin) });
      state.sounds = data.sounds;
      renderSounds();
      status.textContent = data.sound.label + ' を追加しました';
    } catch (e) {
      status.textContent = '';
      toast(file.name + '： ' + e.message, true);
    }
  }
  $('#upload').value = '';
  setTimeout(function () { status.textContent = ''; }, 4000);
}

// ------------------------------------------------------------------ 設定

function renderSettings() {
  var st = state.settings;
  $('#set-volume').value = st.default_volume;
  $('#vol-out').textContent = SL.volumePct(st.default_volume);
  $('#set-rate').value = st.speak_rate;
  $('#rate-out').textContent = st.speak_rate;
  $('#quiet-enabled').checked = !!(st.quiet_hours || {}).enabled;
  $('#quiet-start').value = (st.quiet_hours || {}).start;
  $('#quiet-end').value = (st.quiet_hours || {}).end;
  $('#quiet-desc').textContent = SL.describeQuiet(st.quiet_hours);
  $('#master').checked = st.master_enabled !== false;
  fillVoices($('#set-voice'), st.default_voice);
}

function fillVoices(sel, chosen) {
  sel.innerHTML = '';
  var none = el('option', null, '（システム既定）');
  none.value = '';
  sel.append(none);
  var groups = {};
  state.voices.forEach(function (v) {
    var key = v.locale.indexOf('ja') === 0 ? '日本語' : 'その他の言語';
    if (!groups[key]) {
      groups[key] = document.createElement('optgroup');
      groups[key].label = key;
      sel.append(groups[key]);
    }
    var o = el('option', null, v.name + '（' + v.locale + '）');
    o.value = v.name;
    groups[key].append(o);
  });
  if (chosen && !Array.prototype.some.call(sel.options, function (o) { return o.value === chosen; })) {
    var lost = el('option', null, chosen + '（この Mac にありません）');
    lost.value = chosen;
    sel.append(lost);
  }
  sel.value = chosen || '';
}

function renderLog() {
  var ul = $('#log');
  ul.innerHTML = '';
  var names = {
    fired: '再生', error: 'エラー', missed: '取りこぼし',
    skipped: 'スキップ', test: '試聴', info: '情報',
  };
  if (!state.log.length) {
    var li = el('li');
    li.append(el('span', 'ts', '—'), el('span', 'msg', 'まだ記録がありません'));
    ul.append(li);
    return;
  }
  state.log.forEach(function (e) {
    var li = el('li');
    li.append(el('span', 'ts', e.ts.replace('T', ' ').slice(5)));
    li.append(el('span', 'lv lv-' + e.level, names[e.level] || e.level));
    li.append(el('span', 'msg', e.message));
    ul.append(li);
  });
}

function renderAbout() {
  var dl = $('#about');
  dl.innerHTML = '';
  [['接続先', location.origin],
   ['サーバ起動', state.started_at ? state.started_at.replace('T', ' ') : '—'],
   ['内蔵サウンド', state.sounds.builtin.length + ' 種類'],
   ['読み上げの声', state.voices.length + ' 種類'],
   ['通信', 'この Mac の中だけ（外部送信なし）']].forEach(function (row) {
    dl.append(el('dt', null, row[0]), el('dd', null, row[1]));
  });
}

// ------------------------------------------------------------------ 編集画面

function blankDraft(dateISO) {
  var now = new Date();
  return {
    id: null, name: '', enabled: true, kind: 'weekly',
    time: '08:00',
    days: [0, 1, 2, 3, 4],
    day_of_month: String(now.getDate()),
    month: String(now.getMonth() + 1),
    date: dateISO || SL.isoDate(now),
    every_minutes: '60',
    window: { start: '09:00', end: '21:00' },
    lead_times: [],
    note: '',
    action: {
      type: 'sound', sound: 'builtin:doorbell', text: '', voice: state.settings.default_voice || '',
      rate: state.settings.speak_rate || 180, volume: state.settings.default_volume, repeat: 1,
    },
    lead_action: { sound: 'builtin:melody_notice', speak_remaining: false },
  };
}

function openEditorById(id) {
  var s = state.schedules.filter(function (x) { return x.id === id; })[0];
  if (s) openEditor(s);
}

function openEditor(sched, opts) {
  opts = opts || {};
  var base = blankDraft(opts.dateISO);
  if (sched) {
    draft = Object.assign(base, JSON.parse(JSON.stringify(sched)));
    draft.day_of_month = String(sched.day_of_month != null ? sched.day_of_month : base.day_of_month);
    draft.month = String(sched.month != null ? sched.month : base.month);
    draft.every_minutes = String(sched.every_minutes || base.every_minutes);
    draft.window = sched.window || base.window;
    draft.lead_action = sched.lead_action || base.lead_action;
    draft.days = sched.days || base.days;
    draft.time = sched.time || base.time;
    draft.date = opts.dateISO || sched.date || base.date;
  } else {
    draft = base;
    if (opts.kind) draft.kind = opts.kind;
    if (opts.dateISO) {
      draft.date = opts.dateISO;
      draft.days = [(SL.parseLocal(opts.dateISO).getDay() + 6) % 7];
    }
  }
  editingId = opts.asNew || !sched ? null : sched.id;
  $('#editor-title').textContent = editingId ? '予定を編集' : '新しい予定';
  $('#editor-extra').hidden = !editingId;
  $('#editor-error').hidden = true;

  $('#f-name').value = draft.name;
  $('#f-time').value = draft.time;
  $('#f-date').value = draft.date;
  $('#f-win-start').value = draft.window.start;
  $('#f-win-end').value = draft.window.end;
  $('#f-note').value = draft.note || '';
  $('#f-text').value = draft.action.text || '';
  $('#f-volume').value = draft.action.volume;
  $('#f-lead-speak').checked = !!draft.lead_action.speak_remaining;

  fillSelect($('#f-dom'), SL.domOptions(), draft.day_of_month);
  fillSelect($('#f-month'), SL.monthOptions(), draft.month);
  fillSelect($('#f-every'), SL.intervalOptions(), draft.every_minutes);
  fillSelect($('#f-repeat'), repeatOptions(), String(draft.action.repeat || 1));
  fillVoices($('#f-voice'), draft.action.voice);

  setSeg('#f-kind', 'kind', draft.kind);
  setSeg('#f-atype', 'atype', draft.action.type);
  renderDayButtons();
  renderLeadButtons();
  renderPickers();
  syncEditor();
  $('#editor').showModal();
  if (!editingId) setTimeout(function () { $('#f-name').focus(); }, 40);
}

function repeatOptions() {
  var out = [];
  for (var i = 1; i <= 10; i++) out.push({ value: String(i), label: i + '回' });
  return out;
}

function fillSelect(sel, options, value) {
  sel.innerHTML = '';
  options.forEach(function (o) {
    var opt = el('option', null, o.label);
    opt.value = o.value;
    sel.append(opt);
  });
  sel.value = value;
}

function setSeg(sel, attr, value) {
  $$(sel + ' button').forEach(function (b) {
    b.classList.toggle('is-active', b.dataset[attr] === value);
  });
  var key = attr + 'For';
  $$('[data-' + attr + '-for]').forEach(function (e) {
    e.hidden = e.dataset[key].split(' ').indexOf(value) < 0;
  });
}

function renderDayButtons() {
  var box = $('#f-days');
  box.innerHTML = '';
  SL.DAYS.forEach(function (label, i) {
    var b = el('button', draft.days.indexOf(i) >= 0 ? 'is-on' : '', label);
    b.type = 'button';
    b.addEventListener('click', function () {
      var at = draft.days.indexOf(i);
      if (at >= 0) draft.days.splice(at, 1); else draft.days.push(i);
      draft.days.sort(function (x, y) { return x - y; });
      renderDayButtons();
      syncEditor();
    });
    box.append(b);
  });
  $$('[data-days]').forEach(function (b) {
    b.classList.toggle('is-on', b.dataset.days === draft.days.join(','));
  });
}

var LEAD_CHOICES = [1, 3, 5, 10, 15, 20, 30, 45, 60, 90, 120];

function renderLeadButtons() {
  var box = $('#lead-quick');
  box.innerHTML = '';
  LEAD_CHOICES.forEach(function (m) {
    var on = draft.lead_times.indexOf(m) >= 0;
    var b = el('button', 'pill' + (on ? ' is-on' : ''), m + '分前');
    b.type = 'button';
    b.addEventListener('click', function () {
      var at = draft.lead_times.indexOf(m);
      if (at >= 0) draft.lead_times.splice(at, 1); else draft.lead_times.push(m);
      draft.lead_times.sort(function (x, y) { return y - x; });
      renderLeadButtons();
      syncEditor();
    });
    box.append(b);
  });
  $$('[data-leads-only]').forEach(function (e) { e.hidden = !draft.lead_times.length; });
}

function renderPickers() {
  renderPicker('#f-sound-picker', draft.action.sound, function (ref) {
    draft.action.sound = ref;
    renderPickers();
    syncEditor();
    preview(ref);
  });
  renderPicker('#f-lead-picker', draft.lead_action.sound, function (ref) {
    draft.lead_action.sound = ref;
    renderPickers();
    syncEditor();
    preview(ref);
  });
}

function renderPicker(sel, chosen, onPick) {
  var box = $(sel);
  box.innerHTML = '';
  [['内蔵', 'builtin'], ['自分のファイル', 'user'], ['システム', 'system']].forEach(function (g) {
    var items = state.sounds[g[1]] || [];
    if (!items.length) return;
    box.append(el('div', 'sound-picker-group', g[0]));
    items.forEach(function (s) {
      var b = el('button', 'pick' + (s.ref === chosen ? ' is-on' : ''), s.label);
      b.type = 'button';
      b.addEventListener('click', function () { onPick(s.ref); });
      box.append(b);
    });
  });
}

/** 入力を draft に取り込み、プレビュー文と各表示を更新する */
function syncEditor() {
  draft.name = $('#f-name').value;
  draft.time = $('#f-time').value;
  draft.date = $('#f-date').value;
  draft.day_of_month = $('#f-dom').value;
  draft.month = $('#f-month').value;
  draft.every_minutes = $('#f-every').value;
  draft.window = { start: $('#f-win-start').value, end: $('#f-win-end').value };
  draft.note = $('#f-note').value;
  draft.action.text = $('#f-text').value;
  draft.action.voice = $('#f-voice').value;
  draft.action.volume = Number($('#f-volume').value);
  draft.action.repeat = Number($('#f-repeat').value);
  draft.lead_action.speak_remaining = $('#f-lead-speak').checked;

  $('#f-summary').textContent = SL.describeRecurrence(draft);
  $('#f-vol-out').textContent = SL.volumePct(draft.action.volume);
  $('#dom-hint').textContent = SL.domHint(draft.day_of_month);
  $('#f-sound-name').textContent = soundLabel(draft.action.sound);
  $('#f-lead-name').textContent = soundLabel(draft.lead_action.sound);
}

function payload() {
  var p = {
    name: draft.name.trim(), enabled: draft.enabled !== false, kind: draft.kind,
    note: draft.note.trim(), lead_times: draft.lead_times,
    action: {
      type: draft.action.type, sound: draft.action.sound, text: draft.action.text.trim(),
      voice: draft.action.voice, rate: draft.action.rate || 180,
      volume: draft.action.volume, repeat: draft.action.repeat,
    },
    lead_action: {
      sound: draft.lead_action.sound,
      speak_remaining: draft.lead_action.speak_remaining,
      volume: draft.action.volume,
    },
  };
  if (draft.kind === 'weekly') { p.time = draft.time; p.days = draft.days; }
  if (draft.kind === 'monthly') { p.time = draft.time; p.day_of_month = domValue(); }
  if (draft.kind === 'yearly') {
    p.time = draft.time;
    p.day_of_month = domValue();
    p.month = Number(draft.month);
  }
  if (draft.kind === 'once') { p.time = draft.time; p.date = draft.date; }
  if (draft.kind === 'interval') {
    p.every_minutes = Number(draft.every_minutes);
    p.window = draft.window;
    p.anchor = draft.window.start;
    p.days = draft.days.length ? draft.days : [0, 1, 2, 3, 4, 5, 6];
  }
  return p;
}

function domValue() {
  return draft.day_of_month === 'last' ? 'last' : Number(draft.day_of_month);
}

async function save() {
  syncEditor();
  var err = $('#editor-error');
  try {
    if (editingId) await api('PUT', '/api/schedules/' + editingId, payload());
    else await api('POST', '/api/schedules', payload());
    $('#editor').close();
    toast(editingId ? '更新しました' : '追加しました');
    await refresh();
  } catch (e) {
    err.textContent = e.message;
    err.hidden = false;
    $('.sheet-body').scrollTop = 0;
  }
}

function copyCurrent() {
  syncEditor();
  var copy = JSON.parse(JSON.stringify(draft));
  copy.id = null;
  copy.last_fired = null;
  copy.name = (draft.name || '予定') + ' のコピー';
  $('#editor').close();
  openEditor(copy, { asNew: true });
}

async function removeCurrent() {
  if (!editingId) return;
  if (!confirm('「' + draft.name + '」を削除しますか？')) return;
  try {
    await api('DELETE', '/api/schedules/' + editingId);
    $('#editor').close();
    toast('削除しました');
    await refresh();
  } catch (e) { fail(e); }
}

// ------------------------------------------------------------------ よく使う型

var PRESETS = [
  { title: 'お出かけの合図', desc: '平日 8:15 にピンポーン。10分前と5分前に予告',
    make: function () {
      var d = blankDraft();
      d.name = 'お出かけの時間'; d.kind = 'weekly'; d.time = '08:15';
      d.days = [0, 1, 2, 3, 4]; d.lead_times = [10, 5];
      d.action = Object.assign(d.action, { type: 'both', sound: 'builtin:doorbell',
        text: 'お出かけの時間です。', volume: 0.7 });
      d.lead_action = { sound: 'builtin:melody_notice', speak_remaining: true };
      return d;
    } },
  { title: '朝のメロディー', desc: '毎日 7:00 にやさしいメロディー',
    make: function () {
      var d = blankDraft();
      d.name = '朝のメロディー'; d.time = '07:00'; d.days = [0, 1, 2, 3, 4, 5, 6];
      d.action = Object.assign(d.action, { type: 'sound', sound: 'builtin:melody_morning', volume: 0.55 });
      return d;
    } },
  { title: '時報', desc: '9:00〜21:00 の毎正時にウェストミンスター',
    make: function () {
      var d = blankDraft();
      d.name = '時報'; d.kind = 'interval'; d.every_minutes = '60';
      d.window = { start: '09:00', end: '21:00' }; d.days = [0, 1, 2, 3, 4, 5, 6];
      d.action = Object.assign(d.action, { type: 'sound', sound: 'builtin:westminster', volume: 0.45 });
      return d;
    } },
  { title: '休憩のうながし', desc: '平日 10:00〜18:00 の 90分ごと',
    make: function () {
      var d = blankDraft();
      d.name = '休憩しよう'; d.kind = 'interval'; d.every_minutes = '90';
      d.window = { start: '10:00', end: '18:00' }; d.days = [0, 1, 2, 3, 4];
      d.action = Object.assign(d.action, { type: 'sound', sound: 'builtin:melody_notice', volume: 0.5 });
      return d;
    } },
  { title: 'ゴミ出し', desc: '火・金 7:30 に読み上げ付き。15分前に予告',
    make: function () {
      var d = blankDraft();
      d.name = 'ゴミ出し'; d.time = '07:30'; d.days = [1, 4]; d.lead_times = [15];
      d.action = Object.assign(d.action, { type: 'both', sound: 'builtin:chime_up',
        text: 'ゴミ出しの日です。', volume: 0.6 });
      d.lead_action = { sound: 'builtin:ding', speak_remaining: false };
      return d;
    } },
  { title: '毎月の支払い', desc: '毎月25日 10:00 に読み上げで知らせる',
    make: function () {
      var d = blankDraft();
      d.name = '支払い日'; d.kind = 'monthly'; d.day_of_month = '25'; d.time = '10:00';
      d.action = Object.assign(d.action, { type: 'both', sound: 'builtin:chime_up',
        text: '今日は支払いの日です。', volume: 0.6 });
      return d;
    } },
  { title: '記念日', desc: '毎年 決まった日に鳴らす',
    make: function () {
      var d = blankDraft();
      d.name = '記念日'; d.kind = 'yearly'; d.month = '1'; d.day_of_month = '1'; d.time = '09:00';
      d.action = Object.assign(d.action, { type: 'sound', sound: 'builtin:melody_morning', volume: 0.6 });
      return d;
    } },
  { title: 'おやすみの合図', desc: '毎日 22:30 にやさしいメロディー',
    make: function () {
      var d = blankDraft();
      d.name = 'おやすみ'; d.time = '22:30'; d.days = [0, 1, 2, 3, 4, 5, 6];
      d.action = Object.assign(d.action, { type: 'sound', sound: 'builtin:melody_relax', volume: 0.4 });
      return d;
    } },
];

function renderPresets() {
  var box = $('#presets');
  box.innerHTML = '';
  PRESETS.forEach(function (p) {
    var b = el('button', 'preset');
    b.type = 'button';
    b.append(el('b', null, p.title), el('span', null, p.desc));
    b.addEventListener('click', function () {
      draft = p.make();
      openEditor(draft, { asNew: true });
    });
    box.append(b);
  });
}

// ------------------------------------------------------------------ 読み込みと更新

async function refresh() {
  try {
    var data = await api('GET', '/api/state');
    Object.assign(state, data);
    renderHero();
    renderStartHere();
    renderList();
    renderSounds();
    renderSettings();
    renderLog();
    renderAbout();
    await loadWeek();
  } catch (e) { fail(e); }
}

async function refreshLive() {
  try {
    var data = await api('GET', '/api/now');
    state.next_events = data.next_events;
    state.log = data.log;
    renderHero();
    renderLog();
  } catch (e) { /* 一時的な失敗は黙って見送る */ }
}

function tickClock() {
  $('#clock').textContent = SL.fmtClock(new Date());
  var next = state.next_events[0];
  if (next) $('#hero-count').textContent = SL.fmtCountdown(next.at);
}

// ------------------------------------------------------------------ 配線

function wire() {
  $$('.tab').forEach(function (t) {
    t.addEventListener('click', function () { setView(t.dataset.view); });
  });
  $$('#filters .pill').forEach(function (b) {
    b.addEventListener('click', function () {
      filter = b.dataset.filter;
      $$('#filters .pill').forEach(function (x) { x.classList.toggle('is-on', x === b); });
      renderList();
    });
  });

  $('#stop-btn').addEventListener('click', async function () {
    try { await api('POST', '/api/stop'); toast('止めました'); } catch (e) { fail(e); }
  });
  $('#master').addEventListener('change', async function (ev) {
    try {
      var data = await api('PUT', '/api/settings', { master_enabled: ev.target.checked });
      state.settings = data.settings;
      renderSettings();
      toast(ev.target.checked ? '全体をオンにしました' : '全体をオフにしました');
    } catch (e) { ev.target.checked = !ev.target.checked; fail(e); }
  });

  $('#week-prev').addEventListener('click', function () { shiftWeek(-1); });
  $('#week-next').addEventListener('click', function () { shiftWeek(1); });
  $('#week-label').addEventListener('click', function () {
    weekStartISO = SL.isoDate(SL.weekStart(new Date()));
    loadWeek();
  });

  $('#fab').addEventListener('click', function () { openEditor(null); });
  $('#new-btn').addEventListener('click', function () { openEditor(null); });
  $('#start-blank').addEventListener('click', function () { openEditor(null); });
  $('#day-add').addEventListener('click', function () {
    var iso = daySheetDate;
    $('#day-sheet').close();
    openEditor(null, { dateISO: iso, kind: 'once' });
  });
  $('#day-close').addEventListener('click', function () { $('#day-sheet').close(); });

  $('#editor-cancel').addEventListener('click', function () { $('#editor').close(); });
  $('#editor-save').addEventListener('click', save);
  $('#editor-delete').addEventListener('click', removeCurrent);
  $('#editor-copy').addEventListener('click', copyCurrent);

  $$('#f-kind button').forEach(function (b) {
    b.addEventListener('click', function () {
      draft.kind = b.dataset.kind;
      setSeg('#f-kind', 'kind', draft.kind);
      syncEditor();
    });
  });
  $$('#f-atype button').forEach(function (b) {
    b.addEventListener('click', function () {
      draft.action.type = b.dataset.atype;
      setSeg('#f-atype', 'atype', draft.action.type);
      syncEditor();
    });
  });
  $$('[data-days]').forEach(function (b) {
    b.addEventListener('click', function () {
      draft.days = b.dataset.days.split(',').map(Number);
      renderDayButtons();
      syncEditor();
    });
  });
  ['#f-name', '#f-time', '#f-date', '#f-dom', '#f-month', '#f-every', '#f-win-start',
   '#f-win-end', '#f-note', '#f-text', '#f-voice', '#f-volume', '#f-repeat', '#f-lead-speak']
    .forEach(function (sel) {
      $(sel).addEventListener('input', syncEditor);
      $(sel).addEventListener('change', syncEditor);
    });

  $('#f-test').addEventListener('click', function () {
    syncEditor();
    var a = draft.action;
    api('POST', '/api/preview', {
      type: a.type, sound: a.sound, text: a.text || '読み上げの見本です',
      voice: a.voice, rate: a.rate, volume: a.volume,
    }).catch(fail);
  });
  $('#f-lead-test').addEventListener('click', function () {
    syncEditor();
    var m = draft.lead_times[0] || 5;
    var speak = draft.lead_action.speak_remaining;
    api('POST', '/api/preview', {
      type: speak ? 'both' : 'sound', sound: draft.lead_action.sound,
      text: (draft.name || 'その予定') + 'まで、あと' + m + '分です。',
      voice: draft.action.voice, volume: draft.action.volume,
    }).catch(fail);
  });

  $('#set-volume').addEventListener('input', function (e) {
    $('#vol-out').textContent = SL.volumePct(e.target.value);
  });
  $('#vol-test').addEventListener('click', function () {
    api('POST', '/api/preview', {
      type: 'sound', sound: 'builtin:ding', volume: Number($('#set-volume').value),
    }).catch(fail);
  });
  $('#set-rate').addEventListener('input', function (e) {
    $('#rate-out').textContent = e.target.value;
  });
  $('#voice-test').addEventListener('click', function () {
    api('POST', '/api/preview', {
      type: 'speak', text: 'お出かけの時間です。', voice: $('#set-voice').value,
      rate: Number($('#set-rate').value), volume: Number($('#set-volume').value),
    }).catch(fail);
  });
  ['#quiet-enabled', '#quiet-start', '#quiet-end'].forEach(function (sel) {
    $(sel).addEventListener('change', function () {
      $('#quiet-desc').textContent = SL.describeQuiet({
        enabled: $('#quiet-enabled').checked,
        start: $('#quiet-start').value, end: $('#quiet-end').value,
      });
    });
  });
  $('#save-settings').addEventListener('click', async function () {
    try {
      var data = await api('PUT', '/api/settings', {
        default_volume: Number($('#set-volume').value),
        default_voice: $('#set-voice').value,
        speak_rate: Number($('#set-rate').value),
        quiet_hours: {
          enabled: $('#quiet-enabled').checked,
          start: $('#quiet-start').value,
          end: $('#quiet-end').value,
        },
      });
      state.settings = data.settings;
      renderSettings();
      await loadWeek();
      toast('設定を保存しました');
    } catch (e) { fail(e); }
  });

  $('#upload').addEventListener('change', function (e) {
    uploadFiles(Array.prototype.slice.call(e.target.files || []));
  });
  $('#reload-log').addEventListener('click', refreshLive);

  document.addEventListener('keydown', function (e) {
    var typing = ['INPUT', 'TEXTAREA', 'SELECT'].indexOf(document.activeElement.tagName) >= 0;
    if (typing || e.metaKey || e.ctrlKey) return;
    if (e.key === 'n' && !$('#editor').open) { e.preventDefault(); openEditor(null); }
    if (e.key === 'ArrowLeft' && $('#view-week').classList.contains('is-active')) shiftWeek(-1);
    if (e.key === 'ArrowRight' && $('#view-week').classList.contains('is-active')) shiftWeek(1);
  });
}

function viewFromHash() {
  return (location.hash || '').replace(/^#/, '') || 'week';
}

wire();
window.addEventListener('hashchange', function () { setView(viewFromHash(), true); });
setView(viewFromHash(), true);
renderPresets();
tickClock();
setInterval(tickClock, 1000);
setInterval(refreshLive, 15000);
refresh();
