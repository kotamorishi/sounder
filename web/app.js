'use strict';

/* 画面の組み立て（B案: iOS 時計アプリ型）。日付や表示文字列の純関数は lib.js（SL）に置いている。 */

const DAY_NAMES = ['月曜日', '火曜日', '水曜日', '木曜日', '金曜日', '土曜日', '日曜日'];
const LEAD_CHOICES = [1, 3, 5, 10, 15, 20, 30, 45, 60, 90, 120];
const SOUND_GROUPS = [['内蔵', 'builtin'], ['自分のファイル', 'user'], ['システム', 'system']];
const LOG_LABELS = { fired: '再生', error: 'エラー', missed: '取りこぼし', skipped: 'スキップ', test: '試聴', info: '情報' };
const VIEWS = ['schedules', 'timeline', 'sounds', 'settings'];
// 以前の画面のハッシュも受け付ける（ブックマーク用）
const OLD_HASH = { list: 'schedules', week: 'timeline' };

let state = {
  settings: {}, schedules: [], sounds: { builtin: [], user: [], system: [] },
  voices: [], calendars: [], next_events: [], log: [], builtin_labels: {}, playing: false,
};
let editing = null;   // 編集中のスケジュール id（新規は null）
let draft = null;     // 編集中の下書き
let currentTab = null;
const scrollByTab = {};

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

// ---------------------------------------------------------------- アイコン（固定の SVG。ユーザ文字列は入れない）

const SVG_NS = 'http://www.w3.org/2000/svg';
const ICONS = {
  bell: { d: ['M6 16v-5a6 6 0 0 1 12 0v5l2 2H4z', 'M10 21h4'], sw: 1.8 },
  power: { d: ['M12 3v8', 'M6.3 7a8 8 0 1 0 11.4 0'], sw: 2 },
  moon: { d: ['M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z'], sw: 1.8 },
  play: { d: ['M7 4l13 8-13 8z'], fill: true },
  stop: { rect: true, fill: true },
  check: { d: ['M5 12.5l4.5 4.5L19 7.5'], sw: 2.4 },
  chev: { d: ['M9 6l6 6-6 6'], sw: 2.4 },
  plus: { d: ['M12 5v14', 'M5 12h14'], sw: 2.2 },
  minus: { d: ['M6 12h12'], sw: 2.6 },
};

function icon(name, size = 16, cls = '') {
  const def = ICONS[name];
  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('width', size);
  svg.setAttribute('height', size);
  svg.setAttribute('aria-hidden', 'true');
  if (cls) svg.setAttribute('class', cls);
  if (def.fill) {
    svg.setAttribute('fill', 'currentColor');
  } else {
    svg.setAttribute('fill', 'none');
    svg.setAttribute('stroke', 'currentColor');
    svg.setAttribute('stroke-width', def.sw);
    svg.setAttribute('stroke-linecap', 'round');
    svg.setAttribute('stroke-linejoin', 'round');
  }
  if (def.rect) {
    const r = document.createElementNS(SVG_NS, 'rect');
    for (const [k, v] of Object.entries({ x: 6, y: 6, width: 12, height: 12, rx: 2 })) r.setAttribute(k, v);
    svg.append(r);
  }
  for (const d of def.d || []) {
    const p = document.createElementNS(SVG_NS, 'path');
    p.setAttribute('d', d);
    svg.append(p);
  }
  return svg;
}

/** 要素を作る小さなヘルパ。文字列は必ず textContent で入れる。 */
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = text;
  return e;
}

// 日本語の文を意味のまとまり（parts）ごとに折り返すための要素。
// まとまりの途中では改行せず、まとまりの間でだけ折り返す（1つが長すぎる場合だけ中で折り返す）。
function phrased(tag, cls, parts) {
  const e = el(tag, cls);
  let n = 0;
  for (const p of parts) {
    if (!p) continue;
    // 先頭の空白は inline-block の中だと消えるので、まとまりの外（折り返し可能な位置）に出す
    if (/^\s/.test(p) && n) e.append(' ');
    e.append(el('span', 'ph', p.trim()));
    n++;
  }
  return e;
}

function mkSwitch(checked, label, onChange) {
  const b = el('button', 'switch');
  b.type = 'button';
  b.setAttribute('role', 'switch');
  b.setAttribute('aria-label', label);
  setSwitch(b, checked);
  b.addEventListener('click', (ev) => {
    ev.stopPropagation();
    const next = b.getAttribute('aria-checked') !== 'true';
    setSwitch(b, next);
    onChange(next, b);
  });
  return b;
}

function setSwitch(b, on) { b.setAttribute('aria-checked', on ? 'true' : 'false'); }
function isOn(b) { return b.getAttribute('aria-checked') === 'true'; }

function checkCell(label, checked, onClick, sub) {
  const b = el('button', 'cell check-cell');
  b.type = 'button';
  b.setAttribute('role', 'menuitemradio');
  b.setAttribute('aria-checked', checked ? 'true' : 'false');
  const lab = el('span', 'cell-label', label);
  if (sub) { lab.append(document.createElement('br'), el('span', 'cell-sub', sub)); }
  b.append(lab, icon('check', 20, 'check'));
  b.addEventListener('click', onClick);
  return b;
}

function setRangeFill(input) {
  const min = Number(input.min || 0), max = Number(input.max || 100);
  const pct = ((Number(input.value) - min) / (max - min)) * 100;
  input.style.setProperty('--pct', `${pct}%`);
}

function fillSelect(sel, options, value) {
  sel.textContent = '';
  for (const o of options) {
    const opt = el('option', null, o.label);
    opt.value = o.value;
    sel.append(opt);
  }
  // 選択肢に無い値（以前に保存したもの）も失わないように足しておく
  if (value && !options.some((o) => o.value === String(value))) {
    const opt = el('option', null, String(value));
    opt.value = String(value);
    sel.append(opt);
  }
  sel.value = String(value);
}

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
  const t = $('#toast');
  // <dialog> はトップレイヤーに出るので、開いているならその中に入れる
  const host = $('dialog[open]') || document.body;
  if (t.parentElement !== host) host.append(t);
  t.textContent = msg;
  t.classList.toggle('is-error', !!isError);
  t.hidden = false;
  t.style.animation = 'none';
  void t.offsetWidth;
  t.style.animation = '';
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, isError ? 5000 : 1800);
}

function fail(e) { toast(e.message || String(e), true); }

// ---------------------------------------------------------------- 確認（iOS 風アクションシート）

let asResolve = null;
function confirmSheet(title, okLabel) {
  const d = $('#actionsheet');
  $('#as-title').textContent = title;
  $('#as-ok').textContent = okLabel;
  if (asResolve) asResolve(false);
  return new Promise((resolve) => {
    asResolve = resolve;
    d.showModal();
  });
}

function closeSheet(result) {
  const d = $('#actionsheet');
  if (d.open) d.close();
  if (asResolve) { const r = asResolve; asResolve = null; r(result); }
}

// ---------------------------------------------------------------- 表示用の整形

const hmOf = (d) => `${d.getHours()}:${SL.pad2(d.getMinutes())}`;
const hmAt = (iso) => hmOf(SL.parseLocal(iso));

function fmtTs(ts) {
  return (ts || '').replace('T', ' ').slice(5, 16);
}

function soundLabel(ref) {
  if (!ref) return '—';
  for (const group of ['builtin', 'user', 'system']) {
    const hit = (state.sounds[group] || []).find((s) => s.ref === ref);
    if (hit) return hit.label;
  }
  return state.builtin_labels[ref] || ref.replace(/^[a-z]+:/, '');
}

function whatParts(a) {
  a = a || {};
  const parts = [];
  if (a.type === 'sound') parts.push(soundLabel(a.sound));
  else if (a.type === 'both') parts.push(soundLabel(a.sound), '＋読み上げ');
  else if (a.type === 'speak') parts.push('読み上げ', `「${a.text || ''}」`);
  if (a.repeat > 1) parts.push(` ×${a.repeat}`);
  return parts;
}

// ---------------------------------------------------------------- 予定（ホーム）

function renderBanner() {
  const box = $('#banner');
  box.textContent = '';
  box.className = 'banner';
  const st = state.settings;
  const next = state.next_events[0];

  if (st.master_enabled === false) {
    box.classList.add('is-off');
    const tile = el('span', 'tile');
    tile.append(icon('power', 22));
    const text = el('div', 'banner-text');
    text.append(phrased('span', 'banner-main', ['全体が', 'オフです']),
      phrased('span', 'banner-sub', ['予定は', '鳴りません']));
    const on = el('button', 'pill-btn', 'オンにする');
    on.type = 'button';
    on.addEventListener('click', () => setMaster(true));
    box.append(tile, text, on);
    return;
  }

  const tile = el('span', 'tile');
  tile.append(icon(next && next.quiet ? 'moon' : 'bell', 22));
  const text = el('div', 'banner-text');
  if (next) {
    const rel = SL.fmtCountdown(next.at);
    const when = SL.fmtWhen(next.at);
    const soon = SL.parseLocal(next.at) - new Date() < 86400000 && rel;
    text.append(phrased('span', 'banner-sub', soon ? ['次は', ` ${rel}`, ` · ${when}`] : ['次は', ` ${when}`]),
      phrased('span', 'banner-main', next.tag === 'lead' ? [next.name, `（${next.lead}分前の予告）`] : [next.name]));
    const warn = SL.nextNote(next, st);
    if (warn) {
      box.classList.add('is-quiet');
      text.append(el('span', 'banner-warn', warn));
    }
  } else {
    text.append(el('span', 'banner-sub', '次の予定'),
      phrased('span', 'banner-main', state.schedules.length
        ? ['いまは鳴る予定がありません', '（すべてオフかもしれません）'] : ['まだ予定がありません']));
  }
  const q = st.quiet_hours || {};
  if (!(next && next.quiet) && SL.inQuiet(q, hmOf(new Date()))) {
    text.append(el('span', 'banner-note', `いまは禁止時間です（${SL.shortHM(q.start)}〜${SL.shortHM(q.end)}）`));
  }
  box.append(tile, text);
  if (next) {
    const test = el('button', 'round-btn');
    test.type = 'button';
    test.setAttribute('aria-label', `${next.name}を試聴`);
    test.append(icon('play', 16));
    test.addEventListener('click', () => testSchedule(next.schedule_id, next.tag));
    box.append(test);
  }
  const stop = el('button', 'round-btn' + (state.playing ? ' is-playing' : ''));
  stop.type = 'button';
  stop.setAttribute('aria-label', state.playing
    ? '鳴っている音と順番待ちを止める（再生中）' : '鳴っている音と順番待ちを止める');
  stop.append(icon('stop', 16));
  stop.addEventListener('click', stopSound);
  box.append(stop);
}

let editMode = false;

function renderSchedules() {
  const box = $('#sched-groups');
  box.textContent = '';
  box.classList.toggle('is-editing', editMode);
  const n = state.schedules.length;
  $('#sched-empty').hidden = n > 0;
  $('#edit-btn').hidden = n === 0;
  if (!n && editMode) setEditMode(false);

  for (const [title, kind] of SL.SECTIONS) {
    const rows = state.schedules.filter((s) => s.kind === kind)
      .sort((a, b) => SL.sortKey(a).localeCompare(SL.sortKey(b)));
    if (!rows.length) continue;
    const block = el('section', 'group-block');
    block.append(el('h2', 'group-head', title));
    const group = el('div', 'group');
    for (const s of rows) group.append(alarmRow(s));
    block.append(group);
    box.append(block);
  }
}

/** 鳴らない理由（一覧の行に出す）。鳴るなら空文字 */
function rowWarning(s) {
  if (!s.enabled) return '';
  if (s.kind === 'once' && SL.isPast(s)) return '日時が過ぎています';
  const q = SL.quietState(s, state.settings.quiet_hours);
  if (q === 'all') return '禁止時間なので鳴りません';
  if (q === 'some') return '禁止時間にかかる回は鳴りません';
  return '';
}

function alarmRow(s) {
  const row = el('div', 'alarm' + (s.enabled ? '' : ' is-off'));

  const del = el('button', 'alarm-del');
  del.type = 'button';
  del.setAttribute('aria-label', `${s.name}を削除`);
  const dot = el('span');
  dot.append(icon('minus', 14));
  del.append(dot);
  del.addEventListener('click', () => removeSchedule(s));

  const main = el('button', 'alarm-main');
  main.type = 'button';
  const time = SL.rowTime(s);
  const labelParts = SL.rowLabelParts(s);
  const warn = rowWarning(s);
  main.append(el('span', 'alarm-time' + (time.mid ? ' is-mid' : ''), time.text));
  main.append(phrased('span', 'alarm-label', labelParts));
  const detailParts = whatParts(s.action);
  if (s.lead_times && s.lead_times.length) detailParts.push(` · ${SL.leadLabel(s.lead_times)}に予告`);
  main.append(phrased('span', 'alarm-detail', detailParts));
  if (warn) {
    const w = el('span', 'alarm-warn');
    w.append(icon('moon', 12), el('span', null, warn));
    main.append(w);
  }
  if (s.note) main.append(el('span', 'alarm-note', s.note));
  main.setAttribute('aria-label',
    `${time.text} ${labelParts.join('')}。${detailParts.join('')}。${warn ? warn + '。' : ''}タップで編集`);
  main.addEventListener('click', () => openEditor(s));

  const dup = el('button', 'alarm-dup', '複製');
  dup.type = 'button';
  dup.setAttribute('aria-label', `${s.name}を複製`);
  dup.addEventListener('click', () => duplicate(s));

  const sw = mkSwitch(!!s.enabled, `${s.name}を有効にする`, (on, b) => toggleSchedule(s, on, b));

  row.append(del, main, dup, sw);
  return row;
}

function setEditMode(on) {
  editMode = on;
  $('#edit-btn').textContent = on ? '完了' : '編集';
  $('#edit-btn').classList.toggle('is-bold', on);
  $('#sched-groups').classList.toggle('is-editing', on);
}

function renderEmptyPresets() {
  const box = $('#empty-presets');
  box.textContent = '';
  for (const p of PRESETS) {
    const b = el('button', 'cell preset-cell');
    b.type = 'button';
    const lab = el('span', 'cell-label');
    lab.append(el('b', null, p.title), el('small', null, p.desc));
    b.append(lab, icon('chev', 14, 'chev'));
    b.addEventListener('click', () => openEditor(p.make(), { asNew: true, presets: true }));
    box.append(b);
  }
}

// ---------------------------------------------------------------- サウンド

let soundEdit = false;
let playingRef = null;

function renderSounds() {
  const box = $('#sound-groups');
  box.textContent = '';
  box.classList.toggle('is-editing', soundEdit);
  const hasUser = (state.sounds.user || []).length > 0;
  $('#sound-edit-btn').hidden = !hasUser;
  if (!hasUser && soundEdit) setSoundEdit(false);

  const notes = {
    builtin: 'この Mac の中で生成した音です。タップで試聴できます。',
    user: 'mp3 / m4a / wav / aiff など（30MB まで）。外部には送信しません。',
    system: 'macOS に入っている効果音です。',
  };
  for (const [title, key] of SOUND_GROUPS) {
    const items = state.sounds[key] || [];
    if (!items.length && key !== 'user') continue;
    const block = el('section', 'group-block');
    block.append(el('h2', 'group-head', title));
    const group = el('div', 'group');
    for (const s of items) group.append(soundRow(s, key === 'user'));
    if (key === 'user') group.append(addFileRow());
    block.append(group, el('p', 'footnote', notes[key]));
    box.append(block);
  }
}

function soundRow(s, deletable) {
  const row = el('div', 'cell sound-row' + (playingRef === s.ref ? ' is-playing' : ''));
  row.dataset.ref = s.ref;
  if (deletable) {
    const del = el('button', 'alarm-del');
    del.type = 'button';
    del.setAttribute('aria-label', `${s.label}を削除`);
    const dot = el('span');
    dot.append(icon('minus', 14));
    del.append(dot);
    del.addEventListener('click', () => deleteSound(s));
    row.append(del);
  }
  const play = el('button', 'sound-play');
  play.type = 'button';
  const playing = playingRef === s.ref;
  play.setAttribute('aria-label', `${s.label}を${playing ? '止める' : '試聴'}`);
  const dot = el('span', 'play-dot');
  dot.append(icon(playing ? 'stop' : 'play', 14));
  play.append(dot, el('span', 'name', s.label));
  play.addEventListener('click', () => {
    if (playingRef === s.ref) stopSound();
    else preview(s.ref);
  });
  row.append(play);
  return row;
}

let uploadStatus = '';
function addFileRow() {
  const b = el('button', 'cell add-cell');
  b.type = 'button';
  const dot = el('span', 'play-dot');
  dot.append(icon('plus', 16));
  b.append(dot, el('span', 'cell-label', 'ファイルを追加'));
  if (uploadStatus) b.append(el('span', 'cell-value', uploadStatus));
  b.disabled = !!uploadStatus && uploadStatus.endsWith('…');
  b.addEventListener('click', () => $('#upload').click());
  return b;
}

function setSoundEdit(on) {
  soundEdit = on;
  $('#sound-edit-btn').textContent = on ? '完了' : '編集';
  $('#sound-edit-btn').classList.toggle('is-bold', on);
  $('#sound-groups').classList.toggle('is-editing', on);
}

function markPlaying(ref) {
  playingRef = ref;
  renderSounds();
  if (ref) watchPlaying();
}

let playWatch = null;
function watchPlaying() {
  clearTimeout(playWatch);
  const started = Date.now();
  const check = async () => {
    try {
      const data = await api('GET', '/api/now');
      applyNow(data);
      if (!data.playing && Date.now() - started > 1200) { playingRef = null; renderSounds(); return; }
    } catch { /* 次で見る */ }
    if (Date.now() - started < 10 * 60 * 1000) playWatch = setTimeout(check, 1500);
  };
  playWatch = setTimeout(check, 700);
}

// ---------------------------------------------------------------- 設定

function renderSettings() {
  const st = state.settings;
  setSwitch($('#master'), st.master_enabled !== false);
  $('#set-volume').value = st.default_volume ?? 0.6;
  $('#vol-out').textContent = SL.volumePct(st.default_volume);
  $('#set-rate').value = st.speak_rate ?? 180;
  $('#rate-out').textContent = st.speak_rate ?? 180;
  setRangeFill($('#set-volume'));
  setRangeFill($('#set-rate'));
  const q = st.quiet_hours || {};
  setSwitch($('#quiet-enabled'), !!q.enabled);
  $('#quiet-start').value = q.start || '23:00';
  $('#quiet-end').value = q.end || '07:00';
  $('#quiet-desc').textContent = SL.describeQuiet(q);
  $('#set-voice-val').textContent = voiceLabel(st.default_voice) || 'システム既定';
  if (!$('#push-voice').hidden) renderVoiceList($('#set-voice-list'), st.default_voice, (v) => { saveSettings({ default_voice: v }, '声を保存しました'); previewSpeech({ voice: v, rate: Number($('#set-rate').value), volume: Number($('#set-volume').value) }); });
}

async function saveSettings(patch, okMsg) {
  try {
    const data = await api('PUT', '/api/settings', patch);
    state.settings = data.settings;
    renderSettings();
    renderSchedules();
    await poll();
    if (okMsg) toast(okMsg);
    if (currentTab === 'timeline') loadTimeline(false);
  } catch (e) {
    fail(e);
    renderSettings();
  }
}

function setMaster(on) {
  return saveSettings({ master_enabled: on }, on ? '全体をオンにしました' : '全体をオフにしました');
}

function renderVoiceList(box, chosen, onPick, firstTitle) {
  box.textContent = '';
  const mk = (title, items) => {
    const block = el('div', 'group-block');
    if (title) block.append(el('h3', 'group-head', title));
    const g = el('div', 'group');
    for (const [value, label, sub] of items) {
      const cell = checkCell(label, (chosen || '') === value, () => {
        for (const c of box.querySelectorAll('.check-cell')) c.setAttribute('aria-checked', 'false');
        cell.setAttribute('aria-checked', 'true');
        onPick(value);
      }, sub);
      g.append(cell);
    }
    block.append(g);
    box.append(block);
  };
  mk(firstTitle || null, [['', 'システム既定']]);
  const ja = state.voices.filter((v) => v.locale.startsWith('ja'));
  const other = state.voices.filter((v) => !v.locale.startsWith('ja'));
  if (ja.length) mk('日本語', ja.map((v) => [v.name, v.label || v.name, v.locale]));
  if (other.length) mk('英語', other.map((v) => [v.name, v.label || v.name, v.locale]));
  if (chosen && !state.voices.some((v) => v.name === chosen)) {
    mk('見つからない声', [[chosen, chosen, 'この Mac にありません']]);
  }
  if (!state.voices.length) {
    box.append(el('p', 'footnote', 'この環境では読み上げの声の一覧を取得できませんでした。'));
  } else {
    box.append(el('p', 'footnote', 'タップすると選んで、Mac で試聴します。'));
  }
}

// 声の表示名（Qwen3-TTS の声は内部名 "qwen:ono_anna" ではなく読みやすい名前で出す）
function voiceLabel(name) {
  const v = state.voices.find((x) => x.name === name);
  return (v && v.label) || name;
}

function renderLog() {
  const box = $('#log');
  box.textContent = '';
  if (!state.log.length) {
    const c = el('div', 'cell');
    c.append(el('span', 'cell-label', 'まだ記録がありません'));
    box.append(c);
    return;
  }
  for (const e of state.log) {
    const row = el('div', 'cell log-row');
    row.append(el('span', 'badge lv-' + e.level, LOG_LABELS[e.level] || e.level),
      el('span', 'log-ts', fmtTs(e.ts)), el('span', 'log-msg', e.message));
    box.append(row);
  }
}

async function loadLog() {
  try {
    const data = await api('GET', '/api/log?limit=200');
    state.log = data.log;
    renderLog();
  } catch (e) { fail(e); }
}

function renderAbout() {
  const box = $('#about');
  box.textContent = '';
  const rows = [
    ['接続先', location.origin],
    ['サーバ起動', state.started_at ? state.started_at.replace('T', ' ').slice(0, 16) : '—'],
    ['内蔵サウンド', `${state.sounds.builtin.length} 種類`],
    ['読み上げの声', `${state.voices.length} 種類`],
    ['通信', 'この Mac の中だけ'],
  ];
  for (const [k, v] of rows) {
    const c = el('div', 'cell');
    c.append(el('span', 'cell-label', k), el('span', 'cell-value', v));
    box.append(c);
  }
}

// ---------------------------------------------------------------- 子画面（設定タブ内）

function openPush(id) {
  const p = $(id);
  p.classList.remove('is-leaving');
  p.hidden = false;
  document.documentElement.classList.add('is-locked');
  const back = p.querySelector('[data-back]');
  setTimeout(() => back && back.focus({ preventScroll: true }), 50);
}

function closePush(p) {
  if (p.hidden) return;
  p.classList.add('is-leaving');
  setTimeout(() => {
    p.hidden = true;
    p.classList.remove('is-leaving');
    if (!$$('.push').some((x) => !x.hidden)) document.documentElement.classList.remove('is-locked');
  }, 200);
}

// ---------------------------------------------------------------- タイムライン

const PX_PER_MIN = 72 / 60;
let tlWeekStart = SL.weekStart(new Date());
let tlDay = SL.isoDate(new Date());
let tlEvents = {};          // 'YYYY-MM-DD' → events（発火時刻の日付で振り分け済み）
let tlNotes = {};           // 'YYYY-MM-DD' → その日の祝日・学校の休み（「PA デー」など）
let tlMaster = true;
let tlFetchedAt = 0;
let tlLayout = null;        // { yOf }
let tlMinute = null;        // 現在線を描いた時刻（分）

async function loadTimeline(scroll) {
  const start = SL.isoDate(tlWeekStart);
  try {
    // 翌週の月曜 0 時台の予定の予告は日曜に鳴るので、8 日分もらって振り分ける
    const data = await api('GET', `/api/calendar?start=${start}&days=8`);
    if (start !== SL.isoDate(tlWeekStart)) return; // 取得中に週が変わった
    tlEvents = SL.eventsByDate(data.days);
    tlNotes = Object.fromEntries(data.days.map((d) => [d.date, d.notes || []]));
    tlMaster = data.master_enabled !== false;
    tlFetchedAt = Date.now();
    renderTimeline(scroll);
  } catch (e) { fail(e); }
}

function renderWeek() {
  const box = $('#tl-week');
  box.textContent = '';
  const today = SL.isoDate(new Date());
  for (let i = 0; i < 7; i++) {
    const d = SL.addDays(tlWeekStart, i);
    const key = SL.isoDate(d);
    const b = el('button', 'day' + (key === today ? ' is-today' : ''));
    b.type = 'button';
    b.setAttribute('aria-pressed', key === tlDay ? 'true' : 'false');
    // 点は「その日に鳴る予定の数」（時報のような一定間隔は 1 つと数える）
    const mains = new Set((tlEvents[key] || []).filter((e) => e.tag === 'main' && e.enabled && !e.off)
      .map((e) => e.schedule_id)).size;
    b.setAttribute('aria-label', `${d.getMonth() + 1}月${d.getDate()}日（${SL.DAYS[i]}）${mains ? `、予定${mains}件` : ''}`);
    b.append(el('span', 'w', SL.DAYS[i]), el('span', 'n', String(d.getDate())),
      el('span', 'dots', '•'.repeat(Math.min(mains, 4))));
    b.addEventListener('click', () => { tlDay = key; renderTimeline(true); });
    box.append(b);
  }
}

function renderTlTitle() {
  $('#tl-month').textContent = SL.weekRangeLabel(SL.isoDate(tlWeekStart));
  const now = new Date();
  const notes = (tlNotes[tlDay] || []).join('・');
  $('#tl-day').textContent = (tlDay === SL.isoDate(now)
    ? `今日 · ${hmOf(now)}`
    : SL.onceLabel(tlDay)) + (notes ? ` · ${notes}` : '');
  $('#tl-today').hidden = tlDay === SL.isoDate(now);
}

function renderTimeline(scroll) {
  renderWeek();
  renderTlTitle();
  const axis = $('#tl-axis');
  axis.textContent = '';
  const events = tlEvents[tlDay] || [];
  const byId = Object.fromEntries(state.schedules.map((s) => [s.id, s]));
  const minOf = (iso) => { const d = SL.parseLocal(iso); return d.getHours() * 60 + d.getMinutes(); };

  // --- 描画するかたまりを作る
  const items = [];
  const leadsByMain = {};
  for (const e of events) {
    if (e.tag === 'lead') (leadsByMain[`${e.schedule_id}|${e.main_at}`] ||= []).push(e);
  }
  const chimeRows = {};
  for (const e of events) {
    if (e.tag === 'main' && e.kind === 'interval') {
      const key = `${e.schedule_id}|${Math.floor(minOf(e.at) / 60)}`;
      (chimeRows[key] ||= []).push(e);
    } else if (e.tag === 'main') {
      items.push({ min: minOf(e.at), node: tlCard(e, leadsByMain[`${e.schedule_id}|${e.at}`] || [], byId[e.schedule_id]) });
    } else if (e.tag === 'lead' && e.kind !== 'interval' && e.main_at.slice(0, 10) !== tlDay) {
      // 本番が翌日（0 時台）の予告は単独で出す
      items.push({ min: minOf(e.at), node: tlLeadOnly(e, byId[e.schedule_id]) });
    }
  }
  for (const rows of Object.values(chimeRows)) {
    items.push({ min: minOf(rows[0].at), node: tlChimes(rows, byId[rows[0].schedule_id]) });
  }
  // 現在時刻の線も並びに入れて、カードと重ならないようにする
  const now = new Date();
  const isToday = tlDay === SL.isoDate(now);
  if (isToday) {
    const n = el('div', 'tl-now');
    n.append(el('span', null, hmOf(now)), el('i'));
    items.push({ min: now.getHours() * 60 + now.getMinutes() + now.getSeconds() / 60, node: n, now: true });
  }
  items.sort((a, b) => a.min - b.min);
  tlMinute = isToday ? hmOf(now) : null;

  $('#tl-empty').hidden = events.length > 0;

  // --- 高さを測ってから、重ならないように縦方向へずらす（ずれた分だけ時間軸も伸ばす）
  const wraps = items.map((it) => {
    const w = el('div', it.now ? 'tl-item tl-item-now' : 'tl-item');
    w.style.visibility = 'hidden';
    w.append(it.node);
    axis.append(w);
    return w;
  });
  const heights = wraps.map((w) => w.offsetHeight);
  const segs = [{ from: 0, shift: 0 }];
  let cursor = -Infinity;
  const GAP = 8;
  items.forEach((it, i) => {
    let shift = segs[segs.length - 1].shift;
    const lift = it.now ? 10 : 14; // 時刻の位置からどれだけ上に出すか
    const ideal = it.min * PX_PER_MIN + shift - lift;
    if (ideal < cursor) {
      shift += cursor - ideal;
      segs.push({ from: it.min, shift });
    }
    const top = it.min * PX_PER_MIN + shift - lift;
    wraps[i].style.top = `${top}px`;
    wraps[i].style.visibility = '';
    cursor = top + heights[i] + GAP;
  });
  const yOf = (min) => {
    let shift = 0;
    for (const s of segs) if (s.from <= min) shift = s.shift;
    return min * PX_PER_MIN + shift;
  };
  const total = Math.max(yOf(24 * 60), cursor) + 24;
  axis.style.height = `${total}px`;

  const line = el('div', 'tl-line');
  axis.prepend(line);
  for (let h = 0; h <= 24; h++) {
    const row = el('div', 'tl-hour');
    row.style.top = `${yOf(h * 60)}px`;
    row.append(el('span', null, `${h}:00`), el('i'));
    axis.prepend(row);
  }
  for (const e of events) {
    const muted = SL.eventState(e, tlMaster)[1];
    const dot = el('span', 'tl-dot' + (e.tag === 'lead' ? ' is-lead' : '') + (e.past ? ' is-past' : '')
      + (muted ? ' is-muted' : ''));
    dot.style.top = `${yOf(minOf(e.at))}px`;
    axis.append(dot);
  }

  tlLayout = { yOf };
  if (scroll) scrollTimeline(events.length ? minOf(events[0].at) : 8 * 60);
}

/** 分が変わったら描き直す（現在線の位置と「あと○分」を更新） */
function tickTimeline() {
  if (currentTab !== 'timeline') return;
  renderTlTitle();
  if (tlMinute && tlMinute !== hmOf(new Date())) renderTimeline(false);
}

function scrollTimeline(fallbackMin) {
  if (currentTab !== 'timeline' || !tlLayout) return;
  const now = new Date();
  const min = tlDay === SL.isoDate(now) ? now.getHours() * 60 + now.getMinutes() : fallbackMin;
  const axis = $('#tl-axis');
  const head = $('.tl-head').offsetHeight;
  const top = axis.getBoundingClientRect().top + window.scrollY + tlLayout.yOf(min);
  window.scrollTo({ top: Math.max(0, top - head - 110), behavior: 'auto' });
}

function tlCard(e, leads, sched) {
  const [msg, muted] = SL.eventState(e, tlMaster);
  const b = el('button', 'tl-card' + (e.past ? ' is-past' : '') + (muted ? ' is-muted' : ''));
  b.type = 'button';
  const top = el('div', 'tl-card-top');
  top.append(el('span', 't', hmAt(e.at)), el('span', 'n', e.name));
  if (!e.past && !muted) top.append(el('span', 'rel', SL.fmtCountdown(e.at)));
  b.append(top);
  if (sched) {
    const a = sched.action || {};
    const parts = a.type === 'both' ? [soundLabel(a.sound), `＋「${a.text || ''}」`] : whatParts(a);
    b.append(phrased('span', 'tl-card-sub', parts));
  }
  if (leads.length) {
    const pills = el('div', 'tl-pills');
    for (const l of leads) {
      const lm = SL.eventState(l, tlMaster)[1];
      pills.append(el('span', 'tl-pill-s' + (lm ? ' is-muted' : ''), `${hmAt(l.at)} 予告`));
    }
    b.append(pills);
  }
  if (msg) {
    const st = el('span', 'tl-state');
    if (muted && e.quiet) st.append(icon('moon', 12));
    st.append(el('span', null, msg));
    b.append(st);
  }
  b.setAttribute('aria-label', `${hmAt(e.at)} ${e.name}${msg ? '、' + msg : ''}。タップで編集`);
  b.addEventListener('click', () => sched && openEditor(sched));
  return b;
}

function tlLeadOnly(e, sched) {
  const [msg, muted] = SL.eventState(e, tlMaster);
  const wrap = el('div', 'tl-chimes');
  const b = el('button', 'tl-chime' + (e.past ? ' is-past' : '') + (muted ? ' is-muted' : ''));
  b.type = 'button';
  b.append(icon(muted && e.quiet ? 'moon' : 'bell', 14), el('b', null, `${hmAt(e.at)} 予告`),
    el('span', 'n', `${e.name}（${hmAt(e.main_at)}）`));
  b.setAttribute('aria-label', `${hmAt(e.at)} ${e.name}の予告${msg ? '、' + msg : ''}`);
  if (msg) b.title = msg;
  b.addEventListener('click', () => sched && openEditor(sched));
  wrap.append(b);
  return wrap;
}

function tlChimes(rows, sched) {
  const wrap = el('div', 'tl-chimes');
  const mk = (label, e) => {
    const [msg, muted] = SL.eventState(e, tlMaster);
    const b = el('button', 'tl-chime' + (e.past ? ' is-past' : '') + (muted ? ' is-muted' : ''));
    b.type = 'button';
    b.append(icon(muted && e.quiet ? 'moon' : 'bell', 14), el('b', null, label), el('span', 'n', e.name));
    b.setAttribute('aria-label', `${label} ${e.name}${msg ? '、' + msg : ''}`);
    if (msg) b.title = msg;
    b.addEventListener('click', () => sched && openEditor(sched));
    return b;
  };
  if (rows.length > 4) {
    // 細かい間隔は 1 時間ぶんをまとめる
    const b = mk(`${hmAt(rows[0].at)}〜${hmAt(rows[rows.length - 1].at)}`, rows[rows.length - 1]);
    b.querySelector('.n').textContent = `${rows[0].name} · ${rows.length}回`;
    wrap.append(b);
  } else {
    for (const e of rows) wrap.append(mk(hmAt(e.at), e));
  }
  return wrap;
}

function shiftWeek(delta) {
  tlWeekStart = SL.addDays(tlWeekStart, delta * 7);
  tlDay = SL.isoDate(SL.addDays(SL.parseLocal(tlDay), delta * 7));
  tlEvents = {};
  renderTimeline(false);
  loadTimeline(true);
}

function goToday() {
  tlWeekStart = SL.weekStart(new Date());
  tlDay = SL.isoDate(new Date());
  loadTimeline(true);
}

// ---------------------------------------------------------------- 全体の描画・通信

function renderAll() {
  renderBanner();
  renderSchedules();
  renderSounds();
  renderSettings();
  renderLog();
  renderAbout();
}

function applyNow(data) {
  state.next_events = data.next_events;
  const was = state.playing;
  state.playing = !!data.playing;
  if (was !== state.playing) renderBanner();
}

async function refresh() {
  try {
    const data = await api('GET', '/api/state');
    state = Object.assign(state, data);
    renderAll();
    if (currentTab === 'timeline') await loadTimeline(false);
  } catch (e) {
    fail(e);
  }
}

async function poll() {
  try {
    const data = await api('GET', '/api/now');
    state.next_events = data.next_events;
    state.playing = !!data.playing;
    if ($('#push-log').hidden) state.log = data.log;
    renderBanner();
    if (!$('#push-log').hidden) renderLog();
    if (currentTab === 'timeline') {
      if (Date.now() - tlFetchedAt > 55000) loadTimeline(false);
      else tickTimeline();
    }
  } catch { /* 一時的な失敗は黙って見送る */ }
}

let pollTimer = null;
function startPolling() {
  clearInterval(pollTimer);
  pollTimer = setInterval(poll, 10000);
}

async function stopSound() {
  try {
    await api('POST', '/api/stop');
    state.playing = false;
    playingRef = null;
    renderBanner();
    renderSounds();
    toast('止めました（順番待ちも取り消しました）');
  } catch (e) { fail(e); }
}

async function testSchedule(id, which) {
  try {
    await api('POST', `/api/schedules/${id}/test`, { which: which === 'lead' ? 'lead' : 'main' });
    toast(which === 'lead' ? '予告を鳴らしました' : '鳴らしました');
    watchPlaying();
  } catch (e) { fail(e); }
}

async function toggleSchedule(s, on, sw) {
  try {
    const data = await api('PATCH', `/api/schedules/${s.id}`, { enabled: on });
    Object.assign(s, data.schedule);
    renderSchedules();
    poll();
    if (currentTab === 'timeline') loadTimeline(false);
  } catch (e) {
    setSwitch(sw, !on);
    fail(e);
  }
}

async function removeSchedule(s) {
  if (!(await confirmSheet(`「${s.name}」を削除しますか？`, '予定を削除'))) return false;
  try {
    await api('DELETE', `/api/schedules/${s.id}`);
    toast('削除しました');
    await refresh();
    poll();
    return true;
  } catch (e) { fail(e); return false; }
}

function duplicate(s) {
  const copy = JSON.parse(JSON.stringify(s));
  copy.id = null;
  copy.name = (s.name || '予定') + ' のコピー';
  copy.last_fired = null;
  openEditor(copy, { asNew: true });
}

/** 読み上げを Mac で試聴する（声・速さを変えたとき）。文章が空なら声の言語に合う見本を読む */
function previewSpeech({ voice, rate, volume, text }) {
  const v = state.voices.find((x) => x.name === voice);
  const ja = !v || v.locale.startsWith('ja');
  const sample = (text || '').trim() || (ja ? 'お出かけの時間です。' : "It's time to go.");
  api('POST', '/api/preview', { type: 'speak', text: sample, voice: voice || '', rate, volume })
    .then(() => toast(voice && voice.startsWith('qwen:') ? 'Mac で再生します（声を作るのに数秒かかります）' : 'Mac で再生します'))
    .catch(fail);
}

async function preview(ref) {
  if (!ref) { toast('サウンドを選んでください', true); return; }
  try {
    await api('POST', '/api/preview', { type: 'sound', sound: ref });
    markPlaying(ref);
  } catch (e) { fail(e); }
}

async function deleteSound(s) {
  const name = s.ref.slice('user:'.length);
  if (!(await confirmSheet(`「${s.label}」を削除しますか？`, 'サウンドを削除'))) return;
  try {
    const data = await api('DELETE', `/api/sounds/${encodeURIComponent(name)}`);
    state.sounds = data.sounds;
    renderSounds();
    toast('削除しました');
  } catch (e) { fail(e); }
}

async function uploadFiles(files) {
  for (const file of files) {
    uploadStatus = `${file.name} を追加中…`;
    renderSounds();
    try {
      const buf = await file.arrayBuffer();
      let bin = '';
      const bytes = new Uint8Array(buf);
      for (let i = 0; i < bytes.length; i += 0x8000) {
        bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
      }
      const data = await api('POST', '/api/sounds', { filename: file.name, data: btoa(bin) });
      state.sounds = data.sounds;
      uploadStatus = `${data.sound.label} を追加しました`;
      renderSounds();
    } catch (e) {
      uploadStatus = '';
      renderSounds();
      toast(`${file.name}: ${e.message}`, true);
    }
  }
  $('#upload').value = '';
  setTimeout(() => { uploadStatus = ''; renderSounds(); }, 4000);
}

// ---------------------------------------------------------------- 編集シート

function blank() {
  const now = new Date();
  return {
    id: null, name: '', enabled: true, kind: 'weekly', time: `${SL.pad2(now.getHours())}:00`,
    date: SL.isoDate(now),
    days: [0, 1, 2, 3, 4], every_minutes: 60,
    day_of_month: String(now.getDate()), month: String(now.getMonth() + 1),
    window: { start: '09:00', end: '21:00' }, lead_times: [], note: '', skip: [],
    action: {
      type: 'sound', sound: 'builtin:doorbell',
      volume: state.settings.default_volume ?? 0.6, repeat: 1,
      text: '', voice: state.settings.default_voice || '', rate: state.settings.speak_rate ?? 180,
    },
    lead_action: { sound: 'builtin:melody_notice', speak_remaining: false },
  };
}

/** sched を下書きにしてシートを開く。
    opts.asNew: 新規として保存 / opts.presets: よく使う型を出す / opts.dateISO・opts.kind: 新規の初期値 */
function openEditor(sched, opts = {}) {
  const b = blank();
  draft = sched ? JSON.parse(JSON.stringify(sched)) : b;
  if (!sched && opts.kind) draft.kind = opts.kind;
  if (!sched && opts.dateISO) {
    draft.date = opts.dateISO;
    draft.days = [(SL.parseLocal(opts.dateISO).getDay() + 6) % 7];
  }
  draft.action = Object.assign({}, b.action, draft.action || {});
  draft.lead_action = Object.assign({}, b.lead_action, draft.lead_action || {});
  if (!draft.window) draft.window = b.window;
  if (!draft.date) draft.date = b.date;
  if (!draft.time) draft.time = b.time;
  if (!draft.every_minutes) draft.every_minutes = 60;
  draft.day_of_month = String(draft.day_of_month ?? b.day_of_month);
  draft.month = String(draft.month ?? b.month);
  if (!draft.days || !draft.days.length) draft.days = draft.kind === 'interval' ? [0, 1, 2, 3, 4, 5, 6] : b.days;
  draft.lead_times = draft.lead_times || [];
  draft.skip = draft.skip || [];
  if (!draft.action.sound) draft.action.sound = b.action.sound;
  draft._sound = draft.action.type !== 'speak';
  draft._speak = draft.action.type !== 'sound';
  editing = opts.asNew ? null : (sched ? sched.id : null);

  $('#editor-title').textContent = editing ? '予定を編集' : '予定を追加';
  $('#editor-error').hidden = true;
  $('#preset-strip').hidden = !(opts.presets || !sched);
  $('#editor-danger').hidden = !editing;

  const hist = [];
  if (editing && sched.enabled && sched.next_at) hist.push(`次回 ${SL.fmtWhen(sched.next_at)}`);
  if (editing && sched.last_fired) hist.push(`前回 ${SL.fmtWhen(sched.last_fired)}`);
  $('#f-history').hidden = !hist.length;
  $('#f-history').textContent = hist.join('　·　');

  $$('.sheet-page').forEach((p) => p.classList.remove('is-current', 'is-behind'));
  $('#pg-main').classList.add('is-current');
  $('#pg-main .sheet-scroll').scrollTop = 0;
  fillEditor();

  const d = $('#editor');
  d.classList.remove('is-closing');
  if (!d.open) d.showModal();
  document.documentElement.classList.add('is-locked', 'sheet-open');
  $('#editor-cancel').focus({ preventScroll: true });
}

function fillEditor() {
  $('#f-name').value = draft.name;
  $('#f-time').value = draft.time;
  $('#f-date').value = draft.date;
  $('#f-date2').value = draft.date;
  $('#f-win-start').value = draft.window.start;
  $('#f-win-end').value = draft.window.end;
  $('#f-win-start2').value = draft.window.start;
  $('#f-win-end2').value = draft.window.end;
  fillSelect($('#f-every'), SL.intervalOptions(), String(draft.every_minutes));
  fillSelect($('#f-dom'), SL.domOptions(), draft.day_of_month);
  fillSelect($('#f-month'), SL.monthOptions(), draft.month);
  $('#f-note').value = draft.note || '';
  $('#f-volume').value = draft.action.volume ?? 0.6;
  $('#f-text').value = draft.action.text || '';
  $('#f-rate').value = draft.action.rate || 180;
  setSwitch($('#f-lead-speak'), !!draft.lead_action.speak_remaining);
  renderEditorValues();
}

function renderEditorValues() {
  $$('#editor [data-kind-for]').forEach((e) => {
    e.hidden = !e.dataset.kindFor.split(' ').includes(draft.kind);
  });
  $('#v-repeat').textContent = SL.repeatSummary(draft);
  $('#f-caption').textContent = SL.describeRecurrence(draft);
  const past = SL.pastWarning(draft);
  $('#f-past').hidden = !past;
  $('#f-past').textContent = past;
  $('#v-sound').textContent = draft._sound ? soundLabel(draft.action.sound) : 'なし';
  $('#v-speak').textContent = draft._speak ? (draft.action.text || '（文章を入力）') : 'オフ';
  $('#f-vol-out').textContent = SL.volumePct(draft.action.volume);
  setRangeFill($('#f-volume'));
  $('#f-repeat-out').textContent = `×${draft.action.repeat || 1}`;
  $('#f-repeat-dec').disabled = (draft.action.repeat || 1) <= 1;
  $('#f-repeat-inc').disabled = (draft.action.repeat || 1) >= 10;
  const hasLeads = draft.lead_times.length > 0;
  $('#v-lead').textContent = hasLeads ? SL.leadLabel(draft.lead_times) : 'なし';
  $$('#editor [data-leads-only]').forEach((e) => { e.hidden = !hasLeads; });
  $('#v-lead-sound').textContent = soundLabel(draft.lead_action.sound);
}

function pushPage(id) {
  const cur = $('.sheet-page.is-current');
  const next = $(id);
  if (id === '#pg-repeat') renderRepeatPage();
  if (id === '#pg-speak') renderSpeakPage();
  if (id === '#pg-lead') renderLeadPage();
  next.querySelector('.sheet-scroll').scrollTop = 0;
  cur.classList.remove('is-current');
  cur.classList.add('is-behind');
  next.classList.add('is-current');
  setTimeout(() => next.querySelector('[data-pop]').focus({ preventScroll: true }), 320);
}

function popPage() {
  const cur = $('.sheet-page.is-current');
  if (!cur || cur.id === 'pg-main') return false;
  cur.classList.remove('is-current');
  const main = $('#pg-main');
  main.classList.remove('is-behind');
  main.classList.add('is-current');
  renderEditorValues();
  return true;
}

function closeEditor() {
  const d = $('#editor');
  if (!d.open) return;
  d.classList.add('is-closing');
  setTimeout(() => {
    d.classList.remove('is-closing');
    if (d.open) d.close();
  }, 200);
}

// --- 子画面: 繰り返し
function renderRepeatPage() {
  $$('#f-kind button').forEach((b) => b.setAttribute('aria-checked', b.dataset.kind === draft.kind ? 'true' : 'false'));
  $$('#pg-repeat [data-kind-for]').forEach((e) => {
    e.hidden = !e.dataset.kindFor.split(' ').includes(draft.kind);
  });
  const hint = SL.domHint(draft.day_of_month);
  $('#f-dom-hint').textContent = hint || '「月末」を選ぶと、その月の最後の日に鳴ります。';
  const box = $('#f-days');
  box.textContent = '';
  DAY_NAMES.forEach((label, i) => {
    box.append(checkCell(label, draft.days.includes(i), () => {
      const set = new Set(draft.days);
      set.has(i) ? set.delete(i) : set.add(i);
      draft.days = Array.from(set).sort((x, y) => x - y);
      renderRepeatPage();
    }));
  });
  box.querySelectorAll('.check-cell').forEach((c) => c.setAttribute('role', 'menuitemcheckbox'));
  const cur = draft.days.join(',');
  $$('#f-quick button').forEach((b) => b.classList.toggle('is-on', b.dataset.days === cur));
  renderSkip();
}

// 祝日・学校の休校日を選ぶ（TDSB の小学校と中高はどちらか一方）
function renderSkip() {
  const box = $('#f-skip');
  box.textContent = '';
  const cals = state.calendars || [];
  for (const c of cals) {
    box.append(checkCell(c.label, draft.skip.includes(c.id), () => {
      let set = draft.skip.filter((x) => x !== c.id);
      if (!draft.skip.includes(c.id)) {
        if (c.id.startsWith('tdsb_')) set = set.filter((x) => !x.startsWith('tdsb_'));
        set.push(c.id);
      }
      draft.skip = cals.map((x) => x.id).filter((id) => set.includes(id));
      renderRepeatPage();
      renderEditorValues();
    }));
  }
  box.querySelectorAll('.check-cell').forEach((c) => c.setAttribute('role', 'menuitemcheckbox'));
  const tdsb = cals.find((c) => draft.skip.includes(c.id) && c.known_until);
  $('#f-skip-hint').textContent = tdsb
    ? `TDSB の休校日（PA デー・冬休み・3 月休み・夏休みなど）は ${tdsb.known_until.replace(/^(\d+)-0?(\d+)-0?(\d+)$/, '$1年$2月$3日')}までの分が入っています。その先の日は、新しい年度の予定表を入れるまでは普段どおり鳴ります。`
    : '祝日は元日・ファミリー・デー・グッドフライデー・ビクトリア・デー・カナダ・デー・レイバー・デー・サンクスギビング・クリスマス・ボクシング・デー（土日に重なったら振替休日も）です。';
}

// --- 子画面: サウンド（本番 / 予告）
let soundTarget = 'main';
function renderSoundPage() {
  const box = $('#pg-sound-list');
  box.textContent = '';
  const isMain = soundTarget === 'main';
  $('#pg-sound-title').textContent = isMain ? 'サウンド' : '予告のサウンド';
  const chosen = isMain ? (draft._sound ? draft.action.sound : '') : draft.lead_action.sound;
  const pick = (ref) => {
    if (isMain) {
      if (!ref) draft._sound = false;
      else { draft._sound = true; draft.action.sound = ref; }
    } else {
      draft.lead_action.sound = ref;
    }
    renderSoundPage();
    renderEditorValues();
    if (ref) preview(ref);
  };
  const mkRow = (ref, label) => {
    const row = el('button', 'cell check-cell');
    row.type = 'button';
    row.setAttribute('role', 'menuitemradio');
    row.setAttribute('aria-checked', ref === chosen ? 'true' : 'false');
    if (ref) {
      const dot = el('span', 'play-dot');
      dot.append(icon('play', 12));
      row.append(dot);
    }
    row.append(el('span', 'cell-label', label), icon('check', 20, 'check'));
    row.addEventListener('click', () => pick(ref));
    return row;
  };
  if (isMain) {
    const block = el('div', 'group-block');
    const g = el('div', 'group');
    g.append(mkRow('', 'なし（読み上げだけ）'));
    block.append(g);
    box.append(block);
  }
  let known = !chosen;
  for (const [title, key] of SOUND_GROUPS) {
    const items = state.sounds[key] || [];
    if (!items.length) continue;
    const block = el('div', 'group-block');
    block.append(el('h3', 'group-head', title));
    const g = el('div', 'group');
    for (const s of items) {
      if (s.ref === chosen) known = true;
      g.append(mkRow(s.ref, s.label));
    }
    block.append(g);
    box.append(block);
  }
  if (!known) {
    const block = el('div', 'group-block');
    block.append(el('h3', 'group-head', '見つからないサウンド'));
    const g = el('div', 'group');
    g.append(mkRow(chosen, `${chosen}（見つかりません）`));
    block.append(g);
    box.append(block);
  }
  box.append(el('p', 'footnote', 'タップすると選んで、Mac で試聴します。'));
}

// --- 子画面: 読み上げ
function renderSpeakPage() {
  setSwitch($('#f-speak'), draft._speak);
  $('[data-speak-only]').hidden = !draft._speak;
  $('#f-rate-out').textContent = $('#f-rate').value;
  setRangeFill($('#f-rate'));
  renderVoiceList($('#f-voice-list'), draft.action.voice, (v) => {
    draft.action.voice = v;
    previewSpeech({ voice: v, rate: Number(draft.action.rate), volume: Number(draft.action.volume), text: draft.action.text });
  }, '声');
}

// --- 子画面: 予告
function renderLeadPage() {
  const box = $('#f-leads');
  box.textContent = '';
  box.append(checkCell('なし', !draft.lead_times.length, () => { draft.lead_times = []; renderLeadPage(); }));
  for (const m of LEAD_CHOICES) {
    box.append(checkCell(`${m}分前`, draft.lead_times.includes(m), () => {
      const set = new Set(draft.lead_times);
      if (set.has(m)) set.delete(m);
      else if (set.size >= 8) { toast('予告は 8 つまでです', true); return; }
      else set.add(m);
      draft.lead_times = Array.from(set).sort((x, y) => y - x);
      renderLeadPage();
    }));
  }
  box.querySelectorAll('.check-cell').forEach((c, i) => { if (i) c.setAttribute('role', 'menuitemcheckbox'); });
}

function actionType() {
  if (draft._sound && draft._speak) return 'both';
  if (draft._speak) return 'speak';
  if (draft._sound) return 'sound';
  return null;
}

function domValue() {
  return draft.day_of_month === 'last' ? 'last' : Number(draft.day_of_month);
}

function collect() {
  const type = actionType();
  if (!type) throw new Error('サウンドか読み上げのどちらかを選んでください');
  const kind = draft.kind;
  const a = draft.action;
  const payload = {
    name: $('#f-name').value.trim(),
    enabled: draft.enabled !== false,
    kind, note: $('#f-note').value.trim(),
    lead_times: draft.lead_times,
    skip: kind === 'once' ? [] : draft.skip,
    action: {
      type,
      sound: a.sound,
      text: (a.text || '').trim(),
      voice: a.voice || '',
      rate: Number(a.rate) || 180,
      volume: Number(a.volume),
      repeat: Number(a.repeat) || 1,
    },
    lead_action: {
      sound: draft.lead_action.sound,
      speak_remaining: !!draft.lead_action.speak_remaining,
      volume: Number(a.volume),
    },
  };
  if (kind === 'weekly') { payload.time = draft.time; payload.days = draft.days; }
  if (kind === 'monthly') { payload.time = draft.time; payload.day_of_month = domValue(); }
  if (kind === 'yearly') {
    payload.time = draft.time;
    payload.day_of_month = domValue();
    payload.month = Number(draft.month);
  }
  if (kind === 'once') { payload.time = draft.time; payload.date = draft.date; }
  if (kind === 'interval') {
    payload.every_minutes = Number(draft.every_minutes);
    payload.window = { start: draft.window.start, end: draft.window.end };
    payload.anchor = draft.window.start;
    payload.days = draft.days.length ? draft.days : [0, 1, 2, 3, 4, 5, 6];
  }
  return payload;
}

function showEditorError(msg) {
  const err = $('#editor-error');
  err.textContent = msg;
  err.hidden = false;
  popPage();
  $('#pg-main .sheet-scroll').scrollTo({ top: 0, behavior: 'smooth' });
}

async function save() {
  let payload;
  try { payload = collect(); } catch (e) { showEditorError(e.message); return; }
  try {
    if (editing) await api('PUT', `/api/schedules/${editing}`, payload);
    else await api('POST', '/api/schedules', payload);
    closeEditor();
    toast(editing ? '更新しました' : '追加しました');
    await refresh();
    poll();
  } catch (e) {
    showEditorError(e.message);
  }
}

/** 編集中の内容（保存前の変更も含む）を、新しい予定として開き直す */
function copyCurrent() {
  draft.name = $('#f-name').value;
  draft.note = $('#f-note').value;
  const copy = JSON.parse(JSON.stringify(draft));
  copy.action.type = actionType() || copy.action.type;
  copy.id = null;
  copy.last_fired = null;
  copy.name = (draft.name.trim() || '予定') + ' のコピー';
  openEditor(copy, { asNew: true });
  toast('コピーを作りました（まだ保存していません）');
}

function previewMain() {
  const type = actionType();
  if (!type) { toast('サウンドか読み上げを選んでください', true); return; }
  const a = draft.action;
  api('POST', '/api/preview', {
    type, sound: a.sound, text: (a.text || '').trim() || '読み上げの見本です', voice: a.voice,
    rate: Number(a.rate), volume: Number(a.volume),
  }).then(() => toast('再生しました')).catch(fail);
}

function previewLead() {
  const a = draft.action;
  if (draft.lead_action.speak_remaining) {
    const m = draft.lead_times[draft.lead_times.length - 1] || 5;
    api('POST', '/api/preview', {
      type: 'both', sound: draft.lead_action.sound,
      text: `${$('#f-name').value.trim() || 'その予定'}まで、あと${m}分です。`,
      voice: a.voice, volume: Number(a.volume),
    }).then(() => toast('予告を再生しました')).catch(fail);
  } else {
    api('POST', '/api/preview', { type: 'sound', sound: draft.lead_action.sound, volume: Number(a.volume) })
      .then(() => toast('予告を再生しました')).catch(fail);
  }
}

// ---------------------------------------------------------------- よく使う型

function preset(fields, action, leadAction) {
  const d = Object.assign(blank(), fields);
  d.action = Object.assign(d.action, action);
  if (leadAction) d.lead_action = leadAction;
  return d;
}

const PRESETS = [
  {
    title: 'お出かけの合図', desc: '平日 8:15 にピンポーン。10分前と5分前に予告',
    make: () => preset({ name: 'お出かけの時間', kind: 'weekly', time: '08:15', days: [0, 1, 2, 3, 4], lead_times: [10, 5] },
      { type: 'both', sound: 'builtin:doorbell', text: 'お出かけの時間です。', volume: 0.7 },
      { sound: 'builtin:melody_notice', speak_remaining: true }),
  },
  {
    title: '朝のメロディー', desc: '毎日 7:00 にやさしいメロディー',
    make: () => preset({ name: '朝のメロディー', kind: 'weekly', time: '07:00', days: [0, 1, 2, 3, 4, 5, 6] },
      { type: 'sound', sound: 'builtin:melody_morning', volume: 0.55 }),
  },
  {
    title: '時報', desc: '9:00〜21:00 の毎正時にウェストミンスター',
    make: () => preset({ name: '時報', kind: 'interval', every_minutes: 60,
      window: { start: '09:00', end: '21:00' }, days: [0, 1, 2, 3, 4, 5, 6] },
    { type: 'sound', sound: 'builtin:westminster', volume: 0.45 }),
  },
  {
    title: '休憩のうながし', desc: '平日 10:00〜18:00 の 90分ごと',
    make: () => preset({ name: '休憩しよう', kind: 'interval', every_minutes: 90,
      window: { start: '10:00', end: '18:00' }, days: [0, 1, 2, 3, 4] },
    { type: 'sound', sound: 'builtin:melody_notice', volume: 0.5 }),
  },
  {
    title: 'ゴミ出し', desc: '火・金 7:30 に読み上げ付き。15分前に予告',
    make: () => preset({ name: 'ゴミ出し', kind: 'weekly', time: '07:30', days: [1, 4], lead_times: [15] },
      { type: 'both', sound: 'builtin:chime_up', text: 'ゴミ出しの日です。', volume: 0.6 },
      { sound: 'builtin:ding', speak_remaining: false }),
  },
  {
    title: '毎月の支払い', desc: '毎月25日 10:00 に読み上げで知らせる',
    make: () => preset({ name: '支払い日', kind: 'monthly', day_of_month: '25', time: '10:00' },
      { type: 'both', sound: 'builtin:chime_up', text: '今日は支払いの日です。', volume: 0.6 }),
  },
  {
    title: '記念日', desc: '毎年 決まった日に鳴らす',
    make: () => preset({ name: '記念日', kind: 'yearly', month: '1', day_of_month: '1', time: '09:00' },
      { type: 'sound', sound: 'builtin:melody_morning', volume: 0.6 }),
  },
  {
    title: 'おやすみの合図', desc: '毎日 22:30 にやさしいメロディー',
    make: () => preset({ name: 'おやすみ', kind: 'weekly', time: '22:30', days: [0, 1, 2, 3, 4, 5, 6] },
      { type: 'sound', sound: 'builtin:melody_relax', volume: 0.4 }),
  },
];

function renderPresets() {
  const box = $('#presets');
  box.textContent = '';
  for (const p of PRESETS) {
    const b = el('button', 'preset');
    b.type = 'button';
    b.append(el('b', null, p.title), el('span', null, p.desc));
    b.addEventListener('click', () => {
      openEditor(p.make(), { asNew: true, presets: true });
      toast(`「${p.title}」を読み込みました`);
    });
    box.append(b);
  }
  renderEmptyPresets();
}

// ---------------------------------------------------------------- タブ（ハッシュで切り替える。戻る操作が効く）

function viewFromHash() {
  const h = (location.hash || '').replace(/^#/, '');
  const name = OLD_HASH[h] || h || 'schedules';
  return VIEWS.includes(name) ? name : 'schedules';
}

function showTab(name, fromHash) {
  if (!fromHash) {
    const hash = name === 'schedules' ? '' : name;
    if ((location.hash || '').replace(/^#/, '') !== hash) {
      location.hash = hash;   // hashchange から改めて呼ばれる
      return;
    }
  }
  $$('.push').forEach((p) => { p.hidden = true; });
  document.documentElement.classList.remove('is-locked');
  if (name === currentTab) {
    window.scrollTo({ top: 0, behavior: 'smooth' });
    return;
  }
  if (currentTab) scrollByTab[currentTab] = window.scrollY;
  currentTab = name;
  $$('.tab').forEach((t) => t.setAttribute('aria-selected', t.dataset.tab === name ? 'true' : 'false'));
  $$('.view').forEach((v) => { v.hidden = v.id !== 'view-' + name; });
  if (name === 'timeline') {
    renderTimeline(false);
    loadTimeline(true);
  } else {
    window.scrollTo(0, scrollByTab[name] || 0);
  }
}

function watchLargeTitles() {
  if (!('IntersectionObserver' in window)) return;
  for (const view of $$('.view')) {
    const h1 = view.querySelector('.large-title');
    const bar = view.querySelector('.navbar');
    if (!h1 || !bar) continue;
    new IntersectionObserver(([entry]) => {
      if (view.hidden) return;
      bar.classList.toggle('is-scrolled', !entry.isIntersecting);
    }, { rootMargin: '-52px 0px 0px 0px', threshold: 0 }).observe(h1);
  }
}

// ---------------------------------------------------------------- 配線

function wire() {
  $$('.tab').forEach((tab) => tab.addEventListener('click', () => showTab(tab.dataset.tab)));
  $('.tabbar').addEventListener('keydown', (e) => {
    if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
    const tabs = $$('.tab');
    const i = tabs.findIndex((t) => t.dataset.tab === currentTab);
    const next = tabs[(i + (e.key === 'ArrowRight' ? 1 : tabs.length - 1)) % tabs.length];
    showTab(next.dataset.tab);
    next.focus();
  });
  window.addEventListener('hashchange', () => showTab(viewFromHash(), true));

  // 予定
  $('#new-btn').addEventListener('click', () => openEditor(null));
  $('#edit-btn').addEventListener('click', () => setEditMode(!editMode));
  $('#empty-new').addEventListener('click', () => openEditor(null));

  // タイムライン
  $('#tl-prev').addEventListener('click', () => shiftWeek(-1));
  $('#tl-next').addEventListener('click', () => shiftWeek(1));
  $('#tl-today').addEventListener('click', goToday);
  $('#tl-add').addEventListener('click', () => openEditor(null, { dateISO: tlDay, kind: 'once' }));
  let sx = null, sy = null;
  const head = $('.tl-head');
  head.addEventListener('touchstart', (e) => { sx = e.touches[0].clientX; sy = e.touches[0].clientY; }, { passive: true });
  head.addEventListener('touchend', (e) => {
    if (sx === null) return;
    const dx = e.changedTouches[0].clientX - sx;
    const dy = e.changedTouches[0].clientY - sy;
    sx = null;
    if (Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy) * 1.5) shiftWeek(dx < 0 ? 1 : -1);
  }, { passive: true });

  // サウンド
  $('#sound-edit-btn').addEventListener('click', () => setSoundEdit(!soundEdit));
  $('#upload').addEventListener('change', (e) => uploadFiles(Array.from(e.target.files || [])));

  // 設定（即時保存）
  $('#master').addEventListener('click', () => {
    const on = !isOn($('#master'));
    setSwitch($('#master'), on);
    setMaster(on);
  });
  $('#stop-btn').addEventListener('click', stopSound);
  $('#set-volume').addEventListener('input', (e) => {
    $('#vol-out').textContent = SL.volumePct(e.target.value);
    setRangeFill(e.target);
  });
  $('#set-volume').addEventListener('change', (e) => {
    saveSettings({ default_volume: Number(e.target.value) }, '音量を保存しました');
    api('POST', '/api/preview', { type: 'sound', sound: 'builtin:ding', volume: Number(e.target.value) }).catch(fail);
  });
  $('#vol-test').addEventListener('click', () => {
    api('POST', '/api/preview', { type: 'sound', sound: 'builtin:ding', volume: Number($('#set-volume').value) })
      .then(() => toast('再生しました')).catch(fail);
  });
  $('#set-rate').addEventListener('input', (e) => { $('#rate-out').textContent = e.target.value; setRangeFill(e.target); });
  $('#set-rate').addEventListener('change', (e) => {
    saveSettings({ speak_rate: Number(e.target.value) }, '速さを保存しました');
    previewSpeech({ voice: state.settings.default_voice, rate: Number(e.target.value), volume: Number($('#set-volume').value) });
  });
  $('#voice-test').addEventListener('click', () => {
    api('POST', '/api/preview', {
      type: 'speak', text: 'お出かけの時間です。', voice: state.settings.default_voice || '',
      rate: Number($('#set-rate').value), volume: Number($('#set-volume').value),
    }).then(() => toast('再生しました')).catch(fail);
  });
  $('#quiet-enabled').addEventListener('click', () => {
    const on = !isOn($('#quiet-enabled'));
    setSwitch($('#quiet-enabled'), on);
    saveSettings({ quiet_hours: { enabled: on } }, on ? '禁止時間をオンにしました' : '禁止時間をオフにしました');
  });
  const saveQuiet = () => {
    const start = $('#quiet-start').value, end = $('#quiet-end').value;
    if (!start || !end) return;
    saveSettings({ quiet_hours: { enabled: isOn($('#quiet-enabled')), start, end } }, '禁止時間を保存しました');
  };
  $('#quiet-start').addEventListener('change', saveQuiet);
  $('#quiet-end').addEventListener('change', saveQuiet);
  $('#set-voice-btn').addEventListener('click', () => {
    renderVoiceList($('#set-voice-list'), state.settings.default_voice, (v) => { saveSettings({ default_voice: v }, '声を保存しました'); previewSpeech({ voice: v, rate: Number($('#set-rate').value), volume: Number($('#set-volume').value) }); });
    openPush('#push-voice');
  });
  $('#log-btn').addEventListener('click', () => { renderLog(); openPush('#push-log'); loadLog(); });
  $('#reload-log').addEventListener('click', loadLog);
  $$('[data-back]').forEach((b) => b.addEventListener('click', () => closePush(b.closest('.push'))));

  // 編集シート
  const ed = $('#editor');
  $('#editor-save').addEventListener('click', save);
  $('#editor-cancel').addEventListener('click', closeEditor);
  ed.addEventListener('cancel', (e) => {
    e.preventDefault();
    if (!popPage()) closeEditor();
  });
  ed.addEventListener('close', () => {
    document.documentElement.classList.remove('is-locked', 'sheet-open');
    const t = $('#toast');
    if (t.parentElement !== document.body) document.body.append(t);
  });
  $$('[data-push]').forEach((b) => b.addEventListener('click', () => {
    if (b.dataset.soundTarget) { soundTarget = b.dataset.soundTarget; renderSoundPage(); }
    pushPage('#' + b.dataset.push);
  }));
  $$('[data-pop]').forEach((b) => b.addEventListener('click', popPage));
  $('#editor-dup').addEventListener('click', copyCurrent);
  $('#editor-del').addEventListener('click', async () => {
    const src = state.schedules.find((s) => s.id === editing);
    if (src && await removeSchedule(src)) closeEditor();
  });

  // 入力 → 下書き
  $('#f-name').addEventListener('input', (e) => { draft.name = e.target.value; });
  $('#f-note').addEventListener('input', (e) => { draft.note = e.target.value; });
  $('#f-time').addEventListener('change', (e) => {
    if (e.target.value) draft.time = e.target.value;
    renderEditorValues();
  });
  for (const id of ['#f-date', '#f-date2']) {
    $(id).addEventListener('change', (e) => {
      if (!e.target.value) return;
      draft.date = e.target.value;
      $('#f-date').value = $('#f-date2').value = draft.date;
      renderEditorValues();
    });
  }
  for (const [a, b, key] of [['#f-win-start', '#f-win-start2', 'start'], ['#f-win-end', '#f-win-end2', 'end']]) {
    for (const id of [a, b]) {
      $(id).addEventListener('change', (e) => {
        if (!e.target.value) return;
        draft.window[key] = e.target.value;
        $(a).value = $(b).value = e.target.value;
        renderEditorValues();
      });
    }
  }
  $('#f-every').addEventListener('change', (e) => { draft.every_minutes = Number(e.target.value); renderEditorValues(); });
  $('#f-dom').addEventListener('change', (e) => { draft.day_of_month = e.target.value; renderRepeatPage(); renderEditorValues(); });
  $('#f-month').addEventListener('change', (e) => { draft.month = e.target.value; renderEditorValues(); });
  $$('#f-kind button').forEach((b) => b.addEventListener('click', () => {
    draft.kind = b.dataset.kind;
    if (draft.kind === 'interval' && !draft.days.length) draft.days = [0, 1, 2, 3, 4, 5, 6];
    renderRepeatPage();
    renderEditorValues();
  }));
  $$('#f-quick button').forEach((b) => b.addEventListener('click', () => {
    draft.days = b.dataset.days.split(',').map(Number);
    renderRepeatPage();
  }));
  $('#f-volume').addEventListener('input', (e) => { draft.action.volume = Number(e.target.value); renderEditorValues(); });
  $('#f-volume').addEventListener('change', () => { if (actionType()) previewMain(); });
  $('#f-test').addEventListener('click', previewMain);
  $('#f-repeat-dec').addEventListener('click', () => { draft.action.repeat = Math.max(1, (draft.action.repeat || 1) - 1); renderEditorValues(); });
  $('#f-repeat-inc').addEventListener('click', () => { draft.action.repeat = Math.min(10, (draft.action.repeat || 1) + 1); renderEditorValues(); });
  $('#f-lead-speak').addEventListener('click', () => {
    draft.lead_action.speak_remaining = !draft.lead_action.speak_remaining;
    setSwitch($('#f-lead-speak'), draft.lead_action.speak_remaining);
    if (draft.lead_action.speak_remaining) previewLead();
  });
  $('#f-lead-test').addEventListener('click', previewLead);
  $('#f-speak').addEventListener('click', () => {
    draft._speak = !draft._speak;
    renderSpeakPage();
    renderEditorValues();
    if (draft._speak) setTimeout(() => $('#f-text').focus(), 50);
  });
  $('#f-text').addEventListener('input', (e) => { draft.action.text = e.target.value; });
  $('#f-rate').addEventListener('input', (e) => {
    draft.action.rate = Number(e.target.value);
    $('#f-rate-out').textContent = e.target.value;
    setRangeFill(e.target);
  });
  $('#f-rate').addEventListener('change', () => {
    const a = draft.action;
    previewSpeech({ voice: a.voice, rate: Number(a.rate), volume: Number(a.volume), text: a.text });
  });

  // デスクトップ Chrome ではアイコンを隠しているので、タップでピッカーを出す
  $$('input[type="time"], input[type="date"]').forEach((i) => i.addEventListener('click', () => {
    try { i.showPicker(); } catch { /* iOS などはネイティブの動作に任せる */ }
  }));

  // 確認シート
  $('#as-ok').addEventListener('click', () => closeSheet(true));
  $('#as-cancel').addEventListener('click', () => closeSheet(false));
  $('#actionsheet').addEventListener('cancel', (e) => { e.preventDefault(); closeSheet(false); });
  $('#actionsheet').addEventListener('click', (e) => { if (e.target === e.currentTarget) closeSheet(false); });

  // キーボード（PC 用）: n で新規、タイムラインでは ← → で前後の週
  document.addEventListener('keydown', (e) => {
    const typing = ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName);
    if (typing || e.metaKey || e.ctrlKey || e.altKey || $('dialog[open]')) return;
    if (e.key === 'n') { e.preventDefault(); openEditor(null); }
    if (currentTab === 'timeline' && e.key === 'ArrowLeft') shiftWeek(-1);
    if (currentTab === 'timeline' && e.key === 'ArrowRight') shiftWeek(1);
  });

  // 画面が見えていない間はポーリングを止める
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) { clearInterval(pollTimer); pollTimer = null; }
    else { poll(); startPolling(); }
  });
}

wire();
watchLargeTitles();
renderPresets();
showTab(viewFromHash(), true);
refresh();
startPolling();
// 「あと○分」と現在線は、通信を待たずに 15 秒ごとに描き直す
setInterval(() => { renderBanner(); tickTimeline(); }, 15000);
