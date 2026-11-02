// Overlay logic. Three modes:
//   dock - idle: a small orb in the corner, waiting for "Hey Jarvis"
//   full - a call: full screen, keeps listening until you say "bye" or press X / Esc
//   mini - the same call shrunk to a corner pill, so you can see what Jarvis just opened
import { Orb } from './orb.js';

const qs = new URLSearchParams(location.search);  // dev preview in a browser: ?port=&token=
const host = window.host || { port: qs.get('port') || '8765', token: qs.get('token') || '', hotkey: 'Ctrl+Alt+J',
  setInteractive() {}, focus() {}, blur() {}, reportMuted() {}, setMode() {}, on() {} };
const $ = (id) => document.getElementById(id);
const body = document.body;
if (!window.host) body.style.background = 'radial-gradient(circle at 30% 20%, #2b3445, #0d1117 70%)'; // fake desktop
const orb = new Orb($('orb'));
new ResizeObserver(() => orb.resize()).observe($('orb'));

const PALETTE = {
  booting:   ['#1e293b', '#475569', 0.3, 0.6],
  idle:      ['#0B2A6B', '#2563EB', 0.0, 0.8],
  listening: ['#06B6D4', '#3B82F6', 0.0, 1.0],
  thinking:  ['#7C3AED', '#D946EF', 1.0, 1.0],
  acting:    ['#0D9488', '#2DD4BF', 0.6, 1.0],
  speaking:  ['#F59E0B', '#F97316', 0.2, 1.0],
  waiting:   ['#0891B2', '#6366F1', 0.1, 0.9],
  error:     ['#B91C1C', '#EF4444', 0.0, 1.0],
  notify:    ['#E11D48', '#FB7185', 0.2, 1.0],
  muted:     ['#1e293b', '#64748B', 0.0, 0.55],
  offline:   ['#450a0a', '#7f1d1d', 0.0, 0.5],
};
const LABEL = { listening: 'Listening', thinking: 'Thinking', acting: 'Working on it', speaking: 'Speaking',
  waiting: 'Your turn', error: 'Something went wrong', idle: 'Ready', muted: 'Microphone off', offline: 'Reconnecting',
  booting: 'Starting up' };
const CHIP = { app: 'Apps', youtube: 'YouTube', spotify: 'Spotify', liked: 'Spotify', email: 'Mail', folder: 'Files',
  file: 'Files', website: 'Web', search: 'Web', volume: 'Volume', control: 'Media', screen: 'System', time: 'Clock',
  calculate: 'Maths', message: 'WhatsApp', whatsapp: 'WhatsApp', projects: 'Claude Code', status: 'Claude Code', task: 'Claude Code', code: 'Claude Code' };

let ws = null, state = 'booting', muted = false, connected = false, lostAt = 0;
let mode = 'dock', inCall = false, callStart = 0, lastReply = '';
let curJarvis = null;   // the Jarvis bubble currently being written

// ---------------------------------------------------------------- connection
function connect() {
  ws = new WebSocket(`ws://127.0.0.1:${host.port}/?token=${encodeURIComponent(host.token)}`);
  ws.onopen = () => { connected = true; lostAt = 0; };
  ws.onmessage = (e) => handle(JSON.parse(e.data));
  ws.onclose = () => {
    if (connected) { setState('offline'); lostAt = Date.now(); }
    connected = false;
    // the backend owns this window; if it's gone for good, close (the supervisor restarts both)
    if (lostAt && Date.now() - lostAt > 5000 && host.quit) { host.quit(); return; }
    setTimeout(connect, 1000);
  };
}
const send = (msg) => ws && ws.readyState === 1 && ws.send(JSON.stringify(msg));

// ---------------------------------------------------------------- modes
function setMode(next) {
  if (next === mode) return;
  body.classList.add('switching');
  setTimeout(() => {
    host.setMode(next);                    // main process resizes the window
    body.classList.remove('dock', 'full', 'mini');
    body.classList.add(next);
    mode = next;
    interactive = null;
    orb.fps = next === 'dock' && state === 'idle' ? 20 : next === 'mini' ? 30 : 60;
    if (next === 'full') setTimeout(() => $('input').focus(), 80);
    setTimeout(() => { orb.resize(); body.classList.remove('switching'); }, 70);
  }, 140);
}

function startCall() {
  if (!inCall) {
    inCall = true; callStart = Date.now(); $('log').textContent = ''; curJarvis = null; lastReply = '';
    $('you').textContent = ''; typeTo(''); clearCards();
  }
  setMode('full');
}
function endCall() {
  inCall = false;
  clearCards();
  setMode('dock');
}
setInterval(() => {
  if (!inCall) return;
  const s = Math.floor((Date.now() - callStart) / 1000);
  $('timer-text').textContent = `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
}, 500);

// ---------------------------------------------------------------- state & orb
function setState(value, extra = {}) {
  state = value;
  const key = muted ? 'muted' : (extra.notify ? 'notify' : value);
  const [a, b, swirl, dim] = PALETTE[key] || PALETTE.idle;
  orb.setPalette(a, b, swirl, dim);
  document.documentElement.style.setProperty('--accent', b);
  body.className = body.className.replace(/\bstate-\S+/g, '').trim() + ` state-${value}`;
  if (value === 'error') orb.doShake();
  let label = LABEL[key] || value;
  if (value === 'acting' && extra.icon && CHIP[extra.icon]) label = `Working · ${CHIP[extra.icon]}`;
  $('status-text').textContent = label;
  $('pill-status').textContent = label;
  $('t-status').textContent = value === 'offline' ? 'OFFLINE' : 'ONLINE';
  orb.fps = mode === 'dock' && value === 'idle' ? 20 : 60;
}

// ---------------------------------------------------------------- conversation log
const hhmmss = () => new Date().toLocaleTimeString('en-GB', { hour12: false });
function addMsg(who, text) {
  const n = el('div', { class: `msg ${who}` },
    el('div', { class: 'meta' }, el('span', { class: 'who' }, who === 'you' ? 'You' : 'Jarvis'), el('span', {}, hhmmss())),
    el('div', { class: 't' }, text));
  $('log').append(n);
  while ($('log').children.length > 60) $('log').firstChild.remove();
  $('log').scrollTop = $('log').scrollHeight;
  return n;
}

function onTranscript(text) {
  $('you').textContent = text;
  typeTo('');
  lastReply = '';
  curJarvis = null;
  addMsg('you', text);
}

function onReply(text, append) {
  if (!append) { curJarvis = null; lastReply = ''; if (!text) return; }   // a new reply starts fresh
  if (!text) return;
  lastReply = lastReply ? `${lastReply} ${text}` : text;
  typeTo(lastReply);
  $('pill-line').textContent = lastReply;
  if (!curJarvis) curJarvis = addMsg('jarvis', text);
  else curJarvis.querySelector('.t').textContent = lastReply;
  $('log').scrollTop = $('log').scrollHeight;
}

// ---------------------------------------------------------------- typewriter caption
let typeTarget = '', typeShown = '';
function typeTo(text) {
  typeTarget = text;
  if (!text.startsWith(typeShown)) typeShown = '';   // a different line: start over
  if (!text) $('live').textContent = '';
}
setInterval(() => {
  if (typeShown.length >= typeTarget.length) return;
  typeShown = typeTarget.slice(0, typeShown.length + 2);
  $('live').textContent = typeShown;
  $('live').classList.toggle('long', typeTarget.length > 100);
}, 16);

// ---------------------------------------------------------------- spectrum ring (full screen only)
const spec = $('spectrum'), sctx = spec.getContext('2d');
const BARS = 120;
function drawSpectrum() {
  requestAnimationFrame(drawSpectrum);
  if (mode !== 'full') return;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const w = spec.clientWidth * dpr, h = spec.clientHeight * dpr;
  if (spec.width !== w || spec.height !== h) { spec.width = w; spec.height = h; }
  sctx.clearRect(0, 0, w, h);
  const cx = w / 2, cy = h / 2, r0 = (w / 1.55) * 0.5 * 1.04, maxLen = w * 0.085;
  const color = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim() || '#38BDF8';
  sctx.strokeStyle = color; sctx.shadowColor = color; sctx.shadowBlur = 8 * dpr; sctx.lineCap = 'round';
  const t = performance.now() / 1000, lvl = orb.level || 0, bands = orb.bands || [];
  for (let i = 0; i < BARS; i++) {
    const k = i % 60, b = bands[Math.floor((k < 30 ? k : 59 - k) * 32 / 30)] || 0;   // mirrored left/right
    const idle = 0.05 + 0.04 * Math.sin(t * 2 + i * 0.35);
    const len = maxLen * Math.max(idle, b * (0.35 + lvl * 1.4));
    const a = (i / BARS) * Math.PI * 2 - Math.PI / 2;
    sctx.globalAlpha = 0.35 + Math.min(0.6, b * lvl * 2);
    sctx.lineWidth = Math.max(1.2, w / 260);
    sctx.beginPath();
    sctx.moveTo(cx + Math.cos(a) * r0, cy + Math.sin(a) * r0);
    sctx.lineTo(cx + Math.cos(a) * (r0 + len), cy + Math.sin(a) * (r0 + len));
    sctx.stroke();
  }
  sctx.globalAlpha = 1;
}
requestAnimationFrame(drawSpectrum);

// ---------------------------------------------------------------- telemetry (real numbers from the backend)
function onStats(m) {
  $('t-cpu').textContent = `${m.cpu}%`; $('t-cpu-bar').style.width = `${m.cpu}%`;
  $('t-mem').textContent = `${m.mem}%`; $('t-mem-bar').style.width = `${m.mem}%`;
  $('t-stt').textContent = m.stt ? `${m.stt.toFixed(1)} s` : '--';
  $('t-llm').textContent = m.llm || 'offline';
  $('t-voice').textContent = (m.voice || '').replace(/^piper \((.+)\)$/, '$1').replace('en_GB-', '').replace('-medium', '');
}
setInterval(() => {
  if (mode !== 'full') return;
  const d = new Date();
  $('t-clock').textContent = d.toLocaleTimeString('en-GB', { hour12: false });
  $('t-date').textContent = d.toLocaleDateString('en-GB', { weekday: 'short', day: '2-digit', month: 'short', year: 'numeric' });
  $('t-mic').textContent = muted ? 'MUTED' : 'LIVE';
}, 500);

// ---------------------------------------------------------------- cards
function el(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') n.className = v; else if (k.startsWith('on')) n.addEventListener(k.slice(2), v); else n.setAttribute(k, v);
  }
  for (const kid of kids.flat()) if (kid != null) n.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  return n;
}

function addCard(node, { sticky = false, ttl = 25000, into = $('cards'), replace = true } = {}) {
  if (replace) clearCards(into);
  node.classList.add('card', 'interactive');
  node.prepend(el('button', { class: 'close', title: 'Dismiss', onclick: () => { removeCard(node); send({ type: 'dismiss' }); } }, '×'));
  into.prepend(node);
  if (!sticky) node._timer = setTimeout(() => removeCard(node), ttl);
  return node;
}
function removeCard(node) { clearTimeout(node._timer); node.remove(); }
function clearCards(into = $('cards')) { [...into.children].forEach(removeCard); }

function showCard(c) {
  // cards that need an answer bring a minimised call back to full screen
  if (['email_draft', 'choices', 'email_list', 'project', 'prompt_draft', 'capabilities'].includes(c.kind) && mode === 'mini') setMode('full');
  switch (c.kind) {
    case 'clear': clearCards(); break;
    case 'email_draft': {
      const to = el('input', { value: c.to || '', placeholder: 'name@company.com' });
      const subject = el('input', { value: c.subject || '' });
      const text = el('textarea', {}, c.body || '');
      const card = addCard(el('div', {},
        el('h4', {}, `Email draft${c.to_name ? ' · ' + c.to_name : ''}`),
        c.note ? el('div', { class: 'note' }, c.note) : null,
        el('div', { class: 'row' }, el('label', {}, 'To'), to),
        el('div', { class: 'row' }, el('label', {}, 'Subject'), subject),
        text,
        el('div', { class: 'actions' },
          el('button', { class: 'btn', type: 'button', onclick: () => { send({ type: 'confirm', id: c.id, action: 'keep' }); removeCard(card); } }, 'Keep as draft'),
          el('button', { class: 'btn primary', type: 'button', onclick: () => {
            send({ type: 'confirm', id: c.id, action: 'send', edited: { to: to.value, subject: subject.value, body: text.value } });
          } }, 'Send'))), { sticky: true });
      break;
    }
    case 'choices': {
      const items = c.items.map((it, i) => el('button', { type: 'button', onclick: () => send({ type: 'choose', index: i }) },
        el('span', { class: 'n' }, i + 1),
        el('span', { style: 'min-width:0' }, el('div', {}, it), c.subs && c.subs[i] ? el('div', { class: 'sub' }, c.subs[i]) : null)));
      addCard(el('div', {}, el('h4', {}, c.title || 'Which one?'), el('div', { class: 'list' }, items)), { ttl: 45000 });
      break;
    }
    case 'email_list': {
      const items = c.items.map((m, i) => el('button', { type: 'button', onclick: () => send({ type: 'choose', index: i }) },
        el('span', { class: 'n' }, i + 1),
        el('span', { style: 'min-width:0' }, el('div', {}, m.from), el('div', { class: 'sub' }, m.subject))));
      addCard(el('div', {}, el('h4', {}, 'Inbox'), el('div', { class: 'list' }, items)), { ttl: 45000 });
      break;
    }
    case 'email':
      addCard(el('div', {}, el('h4', {}, c.from), el('div', { style: 'font-weight:600;margin-bottom:6px' }, c.subject),
        el('div', { class: 'body' }, c.body)), { ttl: 60000 });
      break;
    case 'media':
      $('pill-line').textContent = `${c.service === 'youtube' ? '▶' : '♫'} ${c.title}`;
      addCard(el('div', { class: 'media' },
        el('div', { class: `logo ${c.service}` }, c.service === 'youtube' ? '▶' : '♫'),
        el('div', { style: 'min-width:0' }, el('div', { class: 't' }, c.title), el('div', { class: 'sub' }, c.subtitle || ''))), { ttl: 15000 });
      break;
    case 'meter':
      addCard(el('div', {}, el('h4', {}, `${c.label} · ${c.value}%`),
        el('div', { class: 'meter' }, el('div', { style: `width:${c.value}%` }))), { ttl: 5000 });
      break;
    case 'whatsapp_draft': {  // the message is already typed in WhatsApp (not sent); stay small so it's visible
      $('pill-line').textContent = `WhatsApp → ${c.chat}: ${c.text}`;
      const card = addCard(el('div', {}, el('h4', {}, `WhatsApp · ${c.chat}`), el('div', { class: 'body' }, c.text),
        el('div', { class: 'actions' },
          el('button', { class: 'btn', type: 'button', onclick: () => { send({ type: 'confirm', id: c.id, action: 'cancel' }); removeCard(card); } }, 'Cancel'),
          el('button', { class: 'btn primary', type: 'button', onclick: () => { send({ type: 'confirm', id: c.id, action: 'send' }); removeCard(card); } }, 'Send'))),
        { sticky: true });
      break;
    }
    case 'capabilities':
      addCard(el('div', {}, el('h4', {}, 'What I can do'),
        el('div', { class: 'caps' }, c.groups.map((g) => el('div', { class: 'cap' },
          el('div', { class: 'cap-title' }, g.title),
          g.items.map((it) => el('div', { class: 'cap-item' }, `“${it}”`)))))), { ttl: 90000 });
      break;
    case 'project':
      addCard(el('div', {}, el('h4', {}, `Project · ${c.name}`),
        el('div', { style: 'font-weight:600;margin-bottom:4px' }, c.title || ''),
        el('div', { class: 'sub', style: 'margin-bottom:8px' }, `Last active ${c.ago}`),
        el('div', { class: 'body' }, c.summary),
        c.recent && c.recent.length ? el('div', { class: 'cap-title', style: 'margin-top:12px' }, 'Latest updates') : null,
        (c.recent || []).map((r) => el('div', { class: 'cap-item' }, r)),
        c.last_prompt ? el('div', { class: 'sub', style: 'margin-top:8px;white-space:normal' }, `Your last request: “${c.last_prompt}”`) : null),
        { sticky: true });
      break;
    case 'prompt_draft': {
      if (mode === 'mini') setMode('full');
      const text = el('textarea', {}, c.text || '');
      const card = addCard(el('div', {},
        el('h4', {}, `Task for Claude Code · ${c.project}`),
        text,
        el('div', { class: 'actions' },
          el('button', { class: 'btn', type: 'button', onclick: () => { send({ type: 'confirm', id: c.id, action: 'cancel' }); removeCard(card); } }, 'Cancel'),
          el('button', { class: 'btn primary', type: 'button', onclick: () => {
            send({ type: 'confirm', id: c.id, action: 'send', edited: { text: text.value } }); removeCard(card);
          } }, 'Send to Claude Code'))), { sticky: true });
      break;
    }
    case 'sent':
      addCard(el('div', {}, el('h4', {}, 'Sent ✓'), el('div', {}, `${c.subject} → ${c.to}`)), { ttl: 6000 });
      break;
    default: break;
  }
}

function toast(n) {
  const into = mode === 'full' ? $('cards') : $('toasts');
  addCard(el('div', {}, el('h4', {}, 'New email'), el('div', { style: 'font-weight:600' }, n.from),
    el('div', { class: 'sub' }, n.subject)), { into, replace: false, ttl: 15000 });
}

// ---------------------------------------------------------------- backend events
function handle(m) {
  switch (m.type) {
    case 'state': setState(m.value, m); break;
    case 'status':
      if (m.ready) { body.classList.remove('booting'); if (state === 'booting') setState('idle'); }
      else if (m.error) setState('error');
      if (typeof m.muted === 'boolean') setMuted(m.muted, false);
      break;
    case 'call':
      if (m.value === 'start') startCall();
      else if (m.value === 'end') endCall();
      else if (m.value === 'mini' && inCall) setMode('mini');
      else if (m.value === 'full' && inCall) setMode('full');
      break;
    case 'level': orb.setLevel(m.rms, m.bands); break;
    case 'stats': onStats(m); break;
    case 'transcript': onTranscript(m.text); break;
    case 'reply': onReply(m.text, m.append); break;
    case 'card': showCard(m); break;
    case 'notify': toast(m); break;
    case 'snapshot': host.snapshot && host.snapshot(); break;
    case 'focus_input':  // Jarvis is asking for something easier to type (an email address)
      if (inCall && mode !== 'full') setMode('full');
      $('input').placeholder = m.placeholder || 'Type a command…';
      host.focus();
      setTimeout(() => $('input').focus(), 200);
      break;
    default: break;
  }
}

// ---------------------------------------------------------------- input
function setMuted(v, tell = true) {
  muted = v;
  body.classList.toggle('muted', v);
  host.reportMuted(v);
  if (tell) send({ type: 'mute', value: v });
  setState(state);
}

const endIt = () => { send({ type: 'end_call' }); endCall(); };
$('btn-close').addEventListener('click', endIt);
$('btn-end').addEventListener('click', endIt);
$('pill-end').addEventListener('click', endIt);
$('btn-min').addEventListener('click', () => setMode('mini'));
$('pill-expand').addEventListener('click', () => setMode('full'));
$('btn-mic').addEventListener('click', () => setMuted(!muted));

$('bar').addEventListener('submit', (e) => {
  e.preventDefault();
  const text = $('input').value.trim();
  if (!text) return;
  $('input').value = '';
  send({ type: 'text', text });
});

$('orb-wrap').addEventListener('click', () => {
  if (!inCall) { send({ type: 'wake' }); return; }           // start a call
  if (mode === 'mini') { setMode('full'); return; }
  send({ type: state === 'speaking' || state === 'thinking' ? 'stop' : 'wake' });  // cut in / listen now
});
$('orb-wrap').addEventListener('contextmenu', (e) => { e.preventDefault(); setMuted(!muted); });

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && inCall) { e.preventDefault(); endIt(); }
});

host.on('hotkey', () => {
  if (!inCall) { send({ type: 'wake' }); return; }   // backend answers with call:start -> full screen
  setMode(mode === 'full' ? 'mini' : 'full');
});
host.on('mute', (v) => setMuted(v));
host.on('quit', () => send({ type: 'quit' }));

// click-through in the corner modes, except over the orb / pill / toasts
let interactive = null;
document.addEventListener('mousemove', (e) => {
  if (mode === 'full') return;
  const on = !!(e.target.closest && e.target.closest('.interactive'));
  if (on !== interactive) { interactive = on; host.setInteractive(on); }
});
document.addEventListener('mouseleave', () => { if (mode !== 'full' && interactive) { interactive = false; host.setInteractive(false); } });

$('hk').textContent = host.hotkey;
setState('booting');
connect();
