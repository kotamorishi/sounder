'use strict';

const DAYS = ['月', '火', '水', '木', '金', '土', '日'];
const LEAD_CHOICES = [1, 3, 5, 10, 15, 20, 30, 45, 60];

let state = {
  settings: {}, schedules: [], sounds: { builtin: [], user: [], system: [] },
  voices: [], next_events: [], log: [], builtin_labels: {},
};
let editing = null;   // 編集中のスケジュール id（新規は null）
let draft = null;     // 編集中の下書き

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

// ---------------------------------------------------------------- 通信

async function api(method, path, body) {
  const opt = { method, headers: {} };
  if (body !== undefined) {
    opt.headers['Content-Type'] = 'application/json';
    opt.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(path, opt);
  } catch (e) {
    throw new Error('サーバに接続できませんでした');
  }
  const text = await res.text();
  let data = {};
  if (text) { try { data = JSON.parse(text); } catch { /* 空でよい */ } }
  if (!res.ok) throw new Error(data.error || `エラー (${res.status})`);
  return data;
}

let toastTimer = null;
function toast(msg, isError) {
  const el = $('#toast');
  el.textContent = msg;
  el.classList.toggle('is-error', !!isError);
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, isError ? 5000 : 2200);
}

// ---------------------------------------------------------------- 表示用の整形

function fmtTime(iso) {
  const d = new Date(iso);
  const today = new Date();
  const sameDay = d.toDateString() === today.toDateString();
  const tomorrow = new Date(today.getTime() + 86400000);
  const hm = String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
  if (sameDay) return `今日 ${hm}`;
  if (d.toDateString() === tomorrow.toDateString()) return `明日 ${hm}`;
  return `${d.getMonth() + 1}/${d.getDate()}(${DAYS[(d.getDay() + 6) % 7]}) ${hm}`;
}

function fmtRelative(iso) {
  const ms = new Date(iso) - new Date();
  if (ms < 0) return '';
  const min = Math.floor(ms / 60000);
  if (min < 1) return 'あと1分未満';
  if (min < 60) return `あと${min}分`;
  const h = Math.floor(min / 60);
  if (h < 24) return `あと${h}時間${min % 60 ? (min % 60) + '分' : ''}`;
  return `あと${Math.floor(h / 24)}日`;
}

function soundLabel(ref) {
  if (!ref) return '—';
  for (const group of ['builtin', 'user', 'system']) {
    const hit = (state.sounds[group] || []).find((s) => s.ref === ref);
    if (hit) return hit.label;
  }
  return state.builtin_labels[ref] || ref.replace(/^[a-z]+:/, '');
}

function actionSummary(s) {
  const a = s.action || {};
  const bits = [];
  if (a.type === 'sound' || a.type === 'both') bits.push(`🔊 ${soundLabel(a.sound)}`);
  if (a.type === 'speak' || a.type === 'both') bits.push(`💬 ${a.text}`);
  if (a.repeat > 1) bits.push(`×${a.repeat}`);
  return bits;
}

// ---------------------------------------------------------------- 一覧の描画

function renderUpNext() {
  const ol = $('#upnext');
  ol.innerHTML = '';
  if (!state.next_events.length) {
    ol.innerHTML = '<li class="none">予定はありません</li>';
    return;
  }
  for (const e of state.next_events) {
    const li = document.createElement('li');
    const when = document.createElement('span');
    when.className = 'when';
    when.textContent = fmtTime(e.at);
    const name = document.createElement('span');
    name.textContent = e.name;
    li.append(when, name);
    if (e.tag === 'lead') {
      const lead = document.createElement('span');
      lead.className = 'lead';
      lead.textContent = `${e.lead}分前の予告`;
      li.append(lead);
    }
    const rel = document.createElement('span');
    rel.className = 'rel';
    rel.textContent = fmtRelative(e.at);
    li.append(rel);
    ol.append(li);
  }
}

function renderSchedules() {
  const list = $('#sched-list');
  list.innerHTML = '';
  $('#sched-count').textContent = state.schedules.length ? `(${state.schedules.length})` : '';
  $('#sched-empty').hidden = state.schedules.length > 0;

  for (const s of state.schedules) {
    const card = document.createElement('div');
    card.className = 'card' + (s.enabled ? '' : ' is-off');

    const top = document.createElement('div');
    top.className = 'card-top';
    const name = document.createElement('span');
    name.className = 'card-name';
    name.textContent = s.name;
    top.append(name);

    const sw = document.createElement('label');
    sw.className = 'switch';
    sw.title = '有効／無効';
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.checked = !!s.enabled;
    cb.addEventListener('change', () => toggleSchedule(s, cb));
    const track = document.createElement('span');
    track.className = 'track';
    sw.append(cb, track);
    top.append(sw);
    card.append(top);

    const when = document.createElement('div');
    when.className = 'card-when';
    when.textContent = s.summary;
    card.append(when);

    const what = document.createElement('div');
    what.className = 'card-what';
    for (const b of actionSummary(s)) {
      const tag = document.createElement('span');
      tag.className = 'tag accent';
      tag.textContent = b;
      what.append(tag);
    }
    const vol = document.createElement('span');
    vol.className = 'tag';
    vol.textContent = `音量 ${Math.round((s.action.volume ?? 0.6) * 100)}%`;
    what.append(vol);
    card.append(what);

    if (s.note) {
      const note = document.createElement('div');
      note.className = 'card-note';
      note.textContent = s.note;
      card.append(note);
    }

    const next = document.createElement('div');
    next.className = 'card-next';
    if (s.enabled && s.next_at) next.textContent = `次回 ${fmtTime(s.next_at)}（${fmtRelative(s.next_at)}）`;
    else if (!s.enabled) next.textContent = '停止中';
    else next.textContent = '予定なし';
    if (s.last_fired) next.textContent += `　／　前回 ${fmtTime(s.last_fired)}`;
    card.append(next);

    const acts = document.createElement('div');
    acts.className = 'card-acts';
    acts.append(
      mkBtn('▶ 試聴', 'btn-quiet', () => testSchedule(s, 'main')),
      ...(s.lead_times && s.lead_times.length
        ? [mkBtn('▶ 予告を試聴', 'btn-quiet', () => testSchedule(s, 'lead'))] : []),
      mkBtn('編集', '', () => openEditor(s)),
      mkBtn('複製', 'btn-quiet', () => duplicate(s)),
      mkBtn('削除', 'btn-danger', () => removeSchedule(s)),
    );
    card.append(acts);
    list.append(card);
  }
}

function mkBtn(label, cls, onClick) {
  const b = document.createElement('button');
  b.type = 'button';
  b.className = 'btn ' + cls;
  b.textContent = label;
  b.addEventListener('click', onClick);
  return b;
}

function renderSounds() {
  const fill = (id, items, deletable) => {
    const box = $(id);
    box.innerHTML = '';
    if (!items.length) {
      const p = document.createElement('span');
      p.className = 'hint';
      p.textContent = 'ありません';
      box.append(p);
      return;
    }
    for (const s of items) {
      const chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'chip';
      chip.append(document.createTextNode('▶ ' + s.label));
      chip.addEventListener('click', () => preview(s.ref, chip));
      if (deletable) {
        const del = document.createElement('span');
        del.className = 'del';
        del.textContent = '✕';
        del.title = '削除';
        del.addEventListener('click', (ev) => { ev.stopPropagation(); deleteSound(s); });
        chip.append(del);
      }
      box.append(chip);
    }
  };
  fill('#lib-builtin', state.sounds.builtin, false);
  fill('#lib-user', state.sounds.user, true);
  fill('#lib-system', state.sounds.system, false);
}

function renderSettings() {
  const st = state.settings;
  $('#set-volume').value = st.default_volume ?? 0.6;
  $('#vol-out').textContent = Math.round((st.default_volume ?? 0.6) * 100) + '%';
  $('#set-rate').value = st.speak_rate ?? 180;
  $('#rate-out').textContent = st.speak_rate ?? 180;
  $('#quiet-enabled').checked = !!(st.quiet_hours && st.quiet_hours.enabled);
  $('#quiet-start').value = (st.quiet_hours || {}).start || '23:00';
  $('#quiet-end').value = (st.quiet_hours || {}).end || '07:00';
  $('#master').checked = st.master_enabled !== false;
  $('#master-label').textContent = st.master_enabled !== false ? 'オン' : 'オフ';
  fillVoices($('#set-voice'), st.default_voice);
}

function fillVoices(sel, chosen) {
  sel.innerHTML = '';
  const none = document.createElement('option');
  none.value = '';
  none.textContent = '（システム既定）';
  sel.append(none);
  let ja = null, other = null;
  for (const v of state.voices) {
    const grp = v.locale.startsWith('ja') ? (ja ||= mkGroup(sel, '日本語')) : (other ||= mkGroup(sel, 'その他の言語'));
    const o = document.createElement('option');
    o.value = v.name;
    o.textContent = `${v.name}（${v.locale}）`;
    grp.append(o);
  }
  sel.value = chosen || '';
}

function mkGroup(sel, label) {
  const g = document.createElement('optgroup');
  g.label = label;
  sel.append(g);
  return g;
}

function fillSounds(sel, chosen) {
  sel.innerHTML = '';
  const groups = [['内蔵', 'builtin'], ['自分のファイル', 'user'], ['システム', 'system']];
  for (const [label, key] of groups) {
    const items = state.sounds[key] || [];
    if (!items.length) continue;
    const g = mkGroup(sel, label);
    for (const s of items) {
      const o = document.createElement('option');
      o.value = s.ref;
      o.textContent = s.label;
      g.append(o);
    }
  }
  if (chosen && !Array.from(sel.options).some((o) => o.value === chosen)) {
    const o = document.createElement('option');
    o.value = chosen;
    o.textContent = chosen + '（見つかりません）';
    sel.append(o);
  }
  sel.value = chosen || (state.sounds.builtin[0] || {}).ref || '';
}

function renderLog() {
  const ul = $('#log');
  ul.innerHTML = '';
  if (!state.log.length) {
    ul.innerHTML = '<li><span class="ts">—</span><span>まだ記録がありません</span></li>';
    return;
  }
  for (const e of state.log) {
    const li = document.createElement('li');
    const ts = document.createElement('span');
    ts.className = 'ts';
    ts.textContent = e.ts.replace('T', ' ').slice(5);
    const lv = document.createElement('span');
    lv.className = 'lv lv-' + e.level;
    lv.textContent = { fired: '再生', error: 'エラー', missed: '取りこぼし', skipped: 'スキップ', test: '試聴', info: '情報' }[e.level] || e.level;
    const msg = document.createElement('span');
    msg.textContent = e.message;
    li.append(ts, lv, msg);
    ul.append(li);
  }
}

function renderAbout() {
  const dl = $('#about');
  dl.innerHTML = '';
  const rows = [
    ['接続先', location.origin],
    ['サーバ起動', state.started_at ? state.started_at.replace('T', ' ') : '—'],
    ['内蔵サウンド', `${state.sounds.builtin.length} 種類`],
    ['読み上げ音声', `${state.voices.length} 種類`],
    ['通信', 'この Mac の中だけ（外部送信なし）'],
  ];
  for (const [k, v] of rows) {
    const dt = document.createElement('dt');
    dt.textContent = k;
    const dd = document.createElement('dd');
    dd.textContent = v;
    dl.append(dt, dd);
  }
}

function renderAll() {
  renderUpNext();
  renderSchedules();
  renderSounds();
  renderSettings();
  renderLog();
  renderAbout();
}

// ---------------------------------------------------------------- 操作

async function refresh() {
  try {
    const data = await api('GET', '/api/state');
    state = Object.assign(state, data);
    renderAll();
  } catch (e) {
    toast(e.message, true);
  }
}

async function poll() {
  try {
    const data = await api('GET', '/api/now');
    state.next_events = data.next_events;
    state.log = data.log;
    renderUpNext();
    renderLog();
  } catch { /* 一時的な失敗は黙って見送る */ }
}

async function toggleSchedule(s, cb) {
  try {
    const data = await api('PATCH', `/api/schedules/${s.id}`, { enabled: cb.checked });
    Object.assign(s, data.schedule);
    renderSchedules();
    poll();
  } catch (e) {
    cb.checked = !cb.checked;
    toast(e.message, true);
  }
}

async function testSchedule(s, which) {
  try {
    await api('POST', `/api/schedules/${s.id}/test`, { which });
    toast(which === 'lead' ? '予告を再生しました' : '再生しました');
  } catch (e) { toast(e.message, true); }
}

async function removeSchedule(s) {
  if (!confirm(`「${s.name}」を削除しますか？`)) return;
  try {
    await api('DELETE', `/api/schedules/${s.id}`);
    toast('削除しました');
    refresh();
  } catch (e) { toast(e.message, true); }
}

function duplicate(s) {
  const copy = JSON.parse(JSON.stringify(s));
  copy.id = null;
  copy.name = s.name + ' のコピー';
  copy.last_fired = null;
  openEditor(copy, true);
}

async function preview(ref, chip) {
  try {
    await api('POST', '/api/preview', { type: 'sound', sound: ref });
    if (chip) {
      $$('.chip.playing').forEach((c) => c.classList.remove('playing'));
      chip.classList.add('playing');
      setTimeout(() => chip.classList.remove('playing'), 2500);
    }
  } catch (e) { toast(e.message, true); }
}

async function deleteSound(s) {
  const name = s.ref.slice('user:'.length);
  if (!confirm(`「${s.label}」を削除しますか？`)) return;
  try {
    const data = await api('DELETE', `/api/sounds/${encodeURIComponent(name)}`);
    state.sounds = data.sounds;
    renderSounds();
    toast('削除しました');
  } catch (e) { toast(e.message, true); }
}

async function uploadFiles(files) {
  const status = $('#upload-status');
  for (const file of files) {
    status.textContent = `${file.name} を追加中…`;
    try {
      const buf = await file.arrayBuffer();
      let bin = '';
      const bytes = new Uint8Array(buf);
      for (let i = 0; i < bytes.length; i += 0x8000) {
        bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
      }
      const data = await api('POST', '/api/sounds', { filename: file.name, data: btoa(bin) });
      state.sounds = data.sounds;
      renderSounds();
      status.textContent = `${data.sound.label} を追加しました`;
    } catch (e) {
      status.textContent = '';
      toast(`${file.name}: ${e.message}`, true);
    }
  }
  $('#upload').value = '';
  setTimeout(() => { status.textContent = ''; }, 4000);
}

// ---------------------------------------------------------------- 編集ダイアログ

function blank() {
  const now = new Date();
  const hh = String(now.getHours()).padStart(2, '0');
  return {
    id: null, name: '', enabled: true, kind: 'daily', time: `${hh}:00`,
    date: new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 10),
    days: [0, 1, 2, 3, 4], every_minutes: 60,
    window: { start: '09:00', end: '21:00' }, lead_times: [], note: '',
    action: {
      type: 'sound', sound: 'builtin:doorbell',
      volume: state.settings.default_volume ?? 0.6, repeat: 1,
      text: '', voice: state.settings.default_voice || '', rate: state.settings.speak_rate ?? 180,
    },
    lead_action: { sound: 'builtin:melody_notice', speak_remaining: false },
  };
}

function openEditor(sched, asNew) {
  draft = sched ? JSON.parse(JSON.stringify(sched)) : blank();
  if (!draft.action) draft.action = blank().action;
  if (!draft.lead_action) draft.lead_action = { sound: 'builtin:melody_notice', speak_remaining: false };
  if (!draft.window) draft.window = { start: '09:00', end: '21:00' };
  if (!draft.date) draft.date = blank().date;
  if (!draft.time) draft.time = blank().time;
  editing = asNew ? null : (sched ? sched.id : null);
  $('#editor-title').textContent = editing ? '予定を編集' : '新しい予定';
  $('#editor-error').hidden = true;

  $('#f-name').value = draft.name;
  $('#f-time').value = draft.time;
  $('#f-date').value = draft.date;
  $('#f-every').value = draft.every_minutes || 60;
  $('#f-win-start').value = draft.window.start;
  $('#f-win-end').value = draft.window.end;
  $('#f-note').value = draft.note || '';
  $('#f-text').value = draft.action.text || '';
  $('#f-rate').value = draft.action.rate || 180;
  $('#f-volume').value = draft.action.volume ?? 0.6;
  $('#f-repeat').value = draft.action.repeat || 1;
  fillSounds($('#f-sound'), draft.action.sound);
  fillSounds($('#f-lead-sound'), draft.lead_action.sound);
  fillVoices($('#f-voice'), draft.action.voice);
  $('#f-lead-speak').checked = !!draft.lead_action.speak_remaining;

  setSegmented('#f-kind', 'kind', draft.kind);
  setSegmented('#f-atype', 'atype', draft.action.type);
  renderDays();
  renderLeads();
  syncOutputs();
  $('#editor').showModal();
  setTimeout(() => $('#f-name').focus(), 30);
}

function setSegmented(sel, attr, value) {
  $$(`${sel} button`).forEach((b) => b.classList.toggle('is-active', b.dataset[attr] === value));
  const key = attr === 'kind' ? 'kindFor' : 'atypeFor';
  $$(`[data-${attr}-for]`).forEach((el) => {
    el.hidden = !el.dataset[key].split(' ').includes(value);
  });
}

function renderDays() {
  const box = $('#f-days');
  box.innerHTML = '';
  DAYS.forEach((label, i) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.textContent = label;
    b.className = (draft.days || []).includes(i) ? 'is-on' : '';
    b.addEventListener('click', () => {
      const set = new Set(draft.days || []);
      set.has(i) ? set.delete(i) : set.add(i);
      draft.days = Array.from(set).sort((x, y) => x - y);
      renderDays();
    });
    box.append(b);
  });
  $$('#panel-schedules .quick button').forEach(() => {});
  $$('[data-days]').forEach((b) => {
    const want = b.dataset.days.split(',').map(Number);
    const cur = (draft.days || []).join(',');
    b.classList.toggle('is-on', want.join(',') === cur);
  });
}

function renderLeads() {
  const quick = $('#lead-quick');
  quick.innerHTML = '';
  for (const m of LEAD_CHOICES) {
    const b = document.createElement('button');
    b.type = 'button';
    b.textContent = `${m}分前`;
    b.className = (draft.lead_times || []).includes(m) ? 'is-on' : '';
    b.addEventListener('click', () => {
      const set = new Set(draft.lead_times || []);
      set.has(m) ? set.delete(m) : set.add(m);
      draft.lead_times = Array.from(set).sort((x, y) => y - x);
      renderLeads();
    });
    quick.append(b);
  }
  const chips = $('#lead-chips');
  chips.innerHTML = '';
  if ((draft.lead_times || []).length) {
    const span = document.createElement('span');
    span.className = 'hint';
    span.style.margin = '0';
    span.textContent = draft.lead_times.map((m) => `${m}分前`).join('・') + ' に予告を鳴らします';
    chips.append(span);
  }
  $$('[data-leads-only]').forEach((el) => { el.hidden = !(draft.lead_times || []).length; });
}

function syncOutputs() {
  $('#f-vol-out').textContent = Math.round(Number($('#f-volume').value) * 100) + '%';
  $('#f-rate-out').textContent = $('#f-rate').value;
}

function collect() {
  const kind = $$('#f-kind button').find((b) => b.classList.contains('is-active')).dataset.kind;
  const atype = $$('#f-atype button').find((b) => b.classList.contains('is-active')).dataset.atype;
  const payload = {
    name: $('#f-name').value.trim(),
    enabled: draft.enabled !== false,
    kind, note: $('#f-note').value.trim(),
    lead_times: draft.lead_times || [],
    action: {
      type: atype,
      sound: $('#f-sound').value,
      text: $('#f-text').value.trim(),
      voice: $('#f-voice').value,
      rate: Number($('#f-rate').value),
      volume: Number($('#f-volume').value),
      repeat: Number($('#f-repeat').value),
    },
    lead_action: {
      sound: $('#f-lead-sound').value,
      speak_remaining: $('#f-lead-speak').checked,
      volume: Number($('#f-volume').value),
    },
  };
  if (kind === 'daily') { payload.time = $('#f-time').value; payload.days = draft.days || []; }
  if (kind === 'once') { payload.time = $('#f-time').value; payload.date = $('#f-date').value; }
  if (kind === 'interval') {
    payload.every_minutes = Number($('#f-every').value);
    payload.window = { start: $('#f-win-start').value, end: $('#f-win-end').value };
    payload.anchor = $('#f-win-start').value;
    payload.days = (draft.days || []).length ? draft.days : [0, 1, 2, 3, 4, 5, 6];
  }
  return payload;
}

async function save() {
  const payload = collect();
  const err = $('#editor-error');
  try {
    if (editing) await api('PUT', `/api/schedules/${editing}`, payload);
    else await api('POST', '/api/schedules', payload);
    $('#editor').close();
    toast(editing ? '更新しました' : '追加しました');
    refresh();
  } catch (e) {
    err.textContent = e.message;
    err.hidden = false;
  }
}

// ---------------------------------------------------------------- プリセット

const PRESETS = [
  {
    title: 'お出かけの合図', desc: '平日 8:15 にピンポーン、10分前と5分前に予告',
    make: () => Object.assign(blank(), {
      name: 'お出かけの時間', kind: 'daily', time: '08:15', days: [0, 1, 2, 3, 4],
      lead_times: [10, 5],
      action: { type: 'both', sound: 'builtin:doorbell', text: 'お出かけの時間です。',
        volume: 0.7, repeat: 1, voice: state.settings.default_voice || '', rate: 180 },
      lead_action: { sound: 'builtin:melody_notice', speak_remaining: true },
    }),
  },
  {
    title: '朝のメロディー', desc: '毎日 7:00 にやさしいメロディー',
    make: () => Object.assign(blank(), {
      name: '朝のメロディー', kind: 'daily', time: '07:00', days: [0, 1, 2, 3, 4, 5, 6],
      action: { type: 'sound', sound: 'builtin:melody_morning', volume: 0.55, repeat: 1 },
    }),
  },
  {
    title: '時報', desc: '9:00〜21:00 の毎正時にウェストミンスター',
    make: () => Object.assign(blank(), {
      name: '時報', kind: 'interval', every_minutes: 60,
      window: { start: '09:00', end: '21:00' }, days: [0, 1, 2, 3, 4, 5, 6],
      action: { type: 'sound', sound: 'builtin:westminster', volume: 0.45, repeat: 1 },
    }),
  },
  {
    title: '休憩のうながし', desc: '10:00〜18:00 の 90分ごとに通知音',
    make: () => Object.assign(blank(), {
      name: '休憩しよう', kind: 'interval', every_minutes: 90,
      window: { start: '10:00', end: '18:00' }, days: [0, 1, 2, 3, 4],
      action: { type: 'sound', sound: 'builtin:melody_notice', volume: 0.5, repeat: 1 },
    }),
  },
  {
    title: 'ゴミ出し', desc: '火・金 7:30 に読み上げ付きで知らせる',
    make: () => Object.assign(blank(), {
      name: 'ゴミ出し', kind: 'daily', time: '07:30', days: [1, 4], lead_times: [15],
      action: { type: 'both', sound: 'builtin:chime_up', text: 'ゴミ出しの日です。',
        volume: 0.6, repeat: 1, voice: state.settings.default_voice || '', rate: 180 },
      lead_action: { sound: 'builtin:ding', speak_remaining: false },
    }),
  },
  {
    title: 'おやすみの合図', desc: '毎日 22:30 にやさしいメロディー',
    make: () => Object.assign(blank(), {
      name: 'おやすみ', kind: 'daily', time: '22:30', days: [0, 1, 2, 3, 4, 5, 6],
      action: { type: 'sound', sound: 'builtin:melody_relax', volume: 0.4, repeat: 1 },
    }),
  },
];

function renderPresets() {
  const box = $('#presets');
  box.innerHTML = '';
  for (const p of PRESETS) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'preset';
    const t = document.createElement('b');
    t.textContent = p.title;
    const d = document.createElement('span');
    d.textContent = p.desc;
    b.append(t, d);
    b.addEventListener('click', () => openEditor(p.make(), true));
    box.append(b);
  }
}

// ---------------------------------------------------------------- 初期化

function tickClock() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, '0');
  $('#clock').textContent =
    `${d.getMonth() + 1}/${d.getDate()}(${DAYS[(d.getDay() + 6) % 7]}) ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function wire() {
  $$('.tab').forEach((tab) => {
    tab.addEventListener('click', () => {
      $$('.tab').forEach((t) => t.classList.toggle('is-active', t === tab));
      $$('.panel').forEach((p) => p.classList.toggle('is-active', p.id === 'panel-' + tab.dataset.tab));
    });
  });

  $('#stop-btn').addEventListener('click', async () => {
    try { await api('POST', '/api/stop'); toast('止めました'); } catch (e) { toast(e.message, true); }
  });

  $('#master').addEventListener('change', async (ev) => {
    try {
      const data = await api('PUT', '/api/settings', { master_enabled: ev.target.checked });
      state.settings = data.settings;
      renderSettings();
      toast(ev.target.checked ? '全体をオンにしました' : '全体をオフにしました');
    } catch (e) { ev.target.checked = !ev.target.checked; toast(e.message, true); }
  });

  $('#new-btn').addEventListener('click', () => openEditor(null));
  $('#editor-save').addEventListener('click', save);
  $('#editor-cancel').addEventListener('click', () => $('#editor').close());
  $('#editor-close').addEventListener('click', () => $('#editor').close());
  $('#editor-form').addEventListener('submit', (ev) => { ev.preventDefault(); save(); });

  $$('#f-kind button').forEach((b) => b.addEventListener('click', () => {
    draft.kind = b.dataset.kind;
    setSegmented('#f-kind', 'kind', draft.kind);
  }));
  $$('#f-atype button').forEach((b) => b.addEventListener('click', () => {
    draft.action.type = b.dataset.atype;
    setSegmented('#f-atype', 'atype', draft.action.type);
  }));
  $$('[data-days]').forEach((b) => b.addEventListener('click', () => {
    draft.days = b.dataset.days.split(',').map(Number);
    renderDays();
  }));

  $('#f-volume').addEventListener('input', syncOutputs);
  $('#f-rate').addEventListener('input', syncOutputs);
  $('#f-sound-test').addEventListener('click', () => preview($('#f-sound').value));
  $('#f-lead-test').addEventListener('click', () => {
    if ($('#f-lead-speak').checked) {
      const m = (draft.lead_times || [5])[0];
      api('POST', '/api/preview', {
        type: 'both', sound: $('#f-lead-sound').value,
        text: `${$('#f-name').value || 'その予定'}まで、あと${m}分です。`,
        voice: $('#f-voice').value, volume: Number($('#f-volume').value),
      }).catch((e) => toast(e.message, true));
    } else {
      preview($('#f-lead-sound').value);
    }
  });

  $('#set-volume').addEventListener('input', (e) => {
    $('#vol-out').textContent = Math.round(Number(e.target.value) * 100) + '%';
  });
  $('#set-rate').addEventListener('input', (e) => { $('#rate-out').textContent = e.target.value; });
  $('#save-settings').addEventListener('click', async () => {
    try {
      const data = await api('PUT', '/api/settings', {
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
      toast('設定を保存しました');
    } catch (e) { toast(e.message, true); }
  });

  $('#upload').addEventListener('change', (e) => uploadFiles(Array.from(e.target.files || [])));
  $('#reload-log').addEventListener('click', poll);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'n' && !e.metaKey && !e.ctrlKey && !$('#editor').open &&
        !['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName)) {
      e.preventDefault();
      openEditor(null);
    }
  });
}

tickClock();
setInterval(tickClock, 1000);
wire();
renderPresets();
refresh().then(() => renderPresets());
setInterval(poll, 10000);
