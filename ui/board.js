// The board window: renders what the backend sends ({type:'board'}, {type:'board_vitals'}) and sends your
// clicks back as {type:'board_action'}. Opens in a browser too (dev): board.html?port=&token=&demo=1
const qs = new URLSearchParams(location.search);
const host = window.host || { port: qs.get('port') || '8765', token: qs.get('token') || '',
  hide() {}, openUrl(u) { window.open(u, '_blank'); }, onView() {}, onLeave() {} };
const $ = (id) => document.getElementById(id);
const body = document.body;
const SVGNS = 'http://www.w3.org/2000/svg';

let ws = null, view = 'all', vitals = null, focusTimer = null, saidTimer = null, lastJSON = '', firstRender = true;

// ---------------------------------------------------------------- helpers
function el(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === 'class') n.className = v;
    else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) n.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  return n;
}
function svgEl(tag, attrs = {}) {
  const n = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  return n;
}
const check = () => { const s = svgEl('svg', { viewBox: '0 0 24 24' }); s.append(svgEl('path', { d: 'M5 12.5l4.2 4.2L19 7' })); return s; };
const initials = (s) => (s || '?').replace(/[^\p{L}\p{N} ]/gu, '').split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join('').toUpperCase() || '?';
const send = (msg) => ws && ws.readyState === 1 && ws.send(JSON.stringify(msg));
const act = (action, extra = {}) => send({ type: 'board_action', action, ...extra });

// segmented arcs (mission ring, gauges): n short strokes around a circle, the first `on` lit
function segments(group, { n, r, cx, cy, from = -90, sweep = 360, len = 8, cls = 'tick' }) {
  group.replaceChildren();
  const out = [];
  for (let i = 0; i < n; i++) {
    const a = ((from + (sweep * (i + 0.5)) / n) * Math.PI) / 180;
    const line = svgEl('line', { x1: cx + Math.cos(a) * (r - len), y1: cy + Math.sin(a) * (r - len),
      x2: cx + Math.cos(a) * r, y2: cy + Math.sin(a) * r, class: cls });
    group.append(line); out.push(line);
  }
  return out;
}
const light = (segs, fraction) => segs.forEach((s, i) => s.classList.toggle('on', i < Math.round(fraction * segs.length)));

// ---------------------------------------------------------------- connection
function connect() {
  ws = new WebSocket(`ws://127.0.0.1:${host.port}/?token=${encodeURIComponent(host.token)}`);
  ws.onopen = () => act('ready');
  ws.onmessage = (e) => handle(JSON.parse(e.data));
  ws.onclose = () => setTimeout(connect, 1200);
}

function handle(m) {
  if (m.type === 'board') {
    if (m.action === 'hide') { leave(); return; }
    if (m.view) setView(m.view);
    if (m.data) render(m.data);
    if (m.action === 'show') enter();
  } else if (m.type === 'board_vitals') {
    vitals = m; renderVitals();
  } else if (m.type === 'reply' && m.text) {
    caption(m.text, 'jarvis');
  } else if (m.type === 'transcript' && m.text) {
    caption(m.text.replace(/^📱\s*/, ''), 'you');
  }
}

const HINT = 'Say “Hey Jarvis” · “I\'ve called Ali” · “Remind me at 5 to…” · “Start a focus session”';
function caption(text, who) {
  const s = $('said'), w = $('who');
  s.textContent = who === 'you' ? `“${text}”` : text;
  s.className = who; w.className = `who ${who}`; w.textContent = who === 'you' ? 'YOU' : 'JARVIS';
  clearTimeout(saidTimer);
  saidTimer = setTimeout(() => { s.className = ''; w.className = 'who'; w.textContent = 'SYS'; s.textContent = HINT; }, 20000);
}

// ---------------------------------------------------------------- show / hide / views
function enter() {
  body.classList.remove('leave', 'booting', 'enter');
  void body.offsetWidth;  // restart the entrance animation
  body.classList.add('enter');
  setTimeout(() => body.classList.remove('enter'), 1100);
}
function leave() {
  body.classList.add('leave');
  setTimeout(() => { host.hide(); body.classList.remove('leave'); }, 200);
}
function close() { act('closed'); leave(); }

function setView(v) {
  view = v || 'all';
  body.classList.toggle('single', view !== 'all');
  document.querySelectorAll('.panel').forEach((p) => p.classList.toggle('only', p.dataset.view === view));
  document.querySelectorAll('#tabs button').forEach((b) => b.classList.toggle('on', b.dataset.view === view));
  $('tagline').textContent = { all: 'Tactical daily overview', todos: "Today's mission", emails: 'Inbox · last 24 hours',
    projects: 'Claude Code projects' }[view] || 'Tactical daily overview';
}

// ---------------------------------------------------------------- rendering
function render(d) {
  const json = JSON.stringify(d);
  if (json === lastJSON) return;   // nothing changed: leave the page alone
  lastJSON = json;
  $('greeting').textContent = d.greeting || '';
  renderWeather(d.weather);
  renderInbox(d.emails || {}, d.replies || []);
  renderMission(d.items || []);
  renderProjects(d.projects || []);
  renderTimeline(d);
  firstRender = false;
}

function renderWeather(w) {
  const box = $('weather');
  box.replaceChildren();
  if (!w) return;
  box.append(el('b', {}, `${w.temp}°C`), el('span', {}, `${w.city} · ${w.sky}`), el('span', {}, `H${w.hi} L${w.lo}`),
    w.rain != null ? el('span', {}, `Rain ${w.rain}%`) : null);
}

// Replies Jarvis drafted for the emails waiting for yours: edit the text if you like, then Send. Your edits
// survive the board refreshing (it re-renders when anything changes).
const edits = {};
function renderReplies(box, replies) {
  const open = replies.filter((r) => r.status !== 'sent');
  box.append(el('div', { class: 'rh' }, el('span', {}, 'Needs your reply'),
    el('span', { class: 'rc' }, open.length ? `${open.length} waiting` : 'all answered')));
  for (const r of replies) {
    const kids = [
      el('div', { class: 'rtop' }, el('div', { class: 'avatar' }, initials(r.from)),
        el('div', {}, el('div', { class: 'from' }, r.from), el('div', { class: 'subj' }, r.subject)),
        el('div', { class: 'when' }, r.time)),
      r.preview ? el('div', { class: 'preview', title: r.preview }, el('b', {}, 'THEY WROTE'), r.preview) : null,
    ];
    if (r.status === 'drafting') {
      kids.push(el('div', { class: 'drafting' }, el('span', { class: 'spin' }), 'Reading the email and drafting a reply…'));
    } else if (r.status === 'sent') {
      kids.push(el('div', { class: 'sentnote' }, '✓ Reply sent'));
    } else if (r.status === 'sending') {
      kids.push(el('div', { class: 'drafting' }, el('span', { class: 'spin' }), 'Sending…'));
    } else {
      const ta = el('textarea', { class: 'draft', rows: 5, 'data-id': r.id, spellcheck: 'true',
        oninput: (e) => { edits[r.id] = e.target.value; } });
      ta.value = edits[r.id] ?? r.body ?? '';
      kids.push(
        r.status === 'failed' ? el('div', { class: 'warnnote' }, "Couldn't draft this one. Write it here, or try Redraft.") : null,
        el('div', { class: 'rlabel' }, 'DRAFT REPLY'), ta,
        el('div', { class: 'ractions' },
          el('button', { class: 'btn send', title: 'Send this reply now', onclick: () => {
            const body = ta.value.trim();
            if (!body) return;
            act('reply_send', { id: r.id, body }); delete edits[r.id]; } }, 'Send'),
          el('button', { class: 'btn', title: 'Have Jarvis write it again', onclick: () => {
            delete edits[r.id]; act('reply_redraft', { id: r.id }); } }, 'Redraft'),
          el('button', { class: 'btn ghost', title: "Don't reply to this one", onclick: () => act('reply_skip', { id: r.id }) }, 'Skip')));
    }
    box.append(el('div', { class: `reply ${r.status}` }, ...kids));
  }
  box.append(el('div', { class: 'rh' }, el('span', {}, 'Inbox · last 24 hours')));
}

function renderInbox(em, replies = []) {
  const box = $('inbox');
  // keep your place while typing in a draft: the board re-renders when anything changes
  const focused = document.activeElement && document.activeElement.classList.contains('draft')
    ? { id: document.activeElement.dataset.id, s: document.activeElement.selectionStart, e: document.activeElement.selectionEnd } : null;
  box.replaceChildren();
  const items = em.items || [];
  const unread = items.filter((m) => m.unread).length;
  const waiting = replies.filter((r) => r.status !== 'sent').length;
  $('inbox-badge').textContent = waiting ? `${waiting} to reply` : unread ? `${unread} new` : '';
  if (replies.length) renderReplies(box, replies);
  if (focused) {
    const ta = box.querySelector(`textarea.draft[data-id="${CSS.escape(focused.id)}"]`);
    if (ta) { ta.focus(); ta.setSelectionRange(focused.s, focused.e); }
  }
  if (!em.connected) {
    box.append(el('div', { class: 'empty' },
      el('div', {}, em.error ? 'Gmail unreachable right now.' : 'Email is not connected.'),
      el('div', { class: 'sub' }, em.error ? 'Retrying in the background.' : 'Run setup_email to connect it.')));
    return;
  }
  if (!items.length) {
    box.append(el('div', { class: 'empty' }, el('div', {}, em.loading ? 'Syncing inbox…' : 'Inbox clear for the last 24 hours.')));
    return;
  }
  for (const m of items) {
    box.append(el('div', { class: `mail${m.unread ? ' unread' : ''}`, title: 'Click: Jarvis summarises it',
      onclick: () => act('email', { id: m.id }) },
      el('div', { class: 'avatar' }, initials(m.from)),
      el('div', { class: 'from' }, m.from),
      el('div', { class: 'when' }, m.time,
        m.thread ? el('button', { class: 'open', title: 'Open in Gmail', onclick: (e) => { e.stopPropagation();
          host.openUrl(`https://mail.google.com/mail/u/0/#all/${m.thread}`); } }, 'OPEN') : null),
      el('div', { class: 'subj' }, m.subject)));
  }
}

let ringSegs = null;
function renderMission(items) {
  if (!ringSegs) ringSegs = segments($('ring-ticks'), { n: 60, r: 66, cx: 70, cy: 70, len: 11 });
  const done = items.filter((i) => i.status === 'done').length;
  const total = items.length;
  const frac = total ? done / total : 0;
  light(ringSegs, frac);
  $('ring-ticks').parentElement.classList.toggle('complete', total > 0 && done === total);
  $('ring-pct').textContent = `${Math.round(frac * 100)}%`;
  $('ring-num').textContent = `${done}/${total} DONE`;
  const mine = items.filter((i) => i.owner === 'jarvis');
  const yours = items.filter((i) => i.owner !== 'jarvis');
  const left = total - done;
  $('mission-sub').textContent = !total ? 'Nothing planned yet' : !left ? 'Mission complete' :
    `${left} objective${left === 1 ? '' : 's'} remaining`;
  $('n-you').textContent = yours.filter((i) => i.status !== 'done').length;
  $('n-me').textContent = mine.filter((i) => i.status !== 'done').length;
  $('run').classList.toggle('hidden', !mine.some((i) => ['todo', 'failed'].includes(i.status)));
  $('mission-empty').classList.toggle('hidden', total > 0);
  $('g-you').classList.toggle('hidden', !yours.length);
  $('g-me').classList.toggle('hidden', !mine.length);

  const order = (a, b) => (a.status === 'done') - (b.status === 'done');
  const before = new Set([...document.querySelectorAll('.item.done')].map((n) => n.dataset.id));
  $('you').replaceChildren(...[...yours].sort(order).map((i) => {
    const li = el('li', { class: `item${i.status === 'done' ? ' done' : ''}`, 'data-id': i.id },
      el('button', { class: 'check', title: i.status === 'done' ? 'Mark as not done' : 'Mark as done',
        onclick: () => act('toggle', { id: i.id }) }, check()),
      el('div', { class: 'txt' }, i.text, i.carried ? el('span', { class: 'tagc', title: 'Carried over from yesterday' }, 'Carried') : null),
      el('button', { class: 'x', title: 'Remove', onclick: () => act('remove', { id: i.id }) }, '×'));
    if (i.status === 'done' && !before.has(i.id) && !firstRender) li.classList.add('pop');
    return li;
  }));
  const label = { todo: 'Standing by', queued: 'Queued', working: 'Running', done: 'Complete', failed: 'Needs a retry' };
  $('me').replaceChildren(...[...mine].sort(order).map((i) => el('li', { class: `item jt ${i.status}`, 'data-id': i.id },
    el('div', { class: 'state' }, i.status === 'done' ? '✓' : i.status === 'failed' ? '!' : i.status === 'working' ? '' :
      i.status === 'scheduled' ? '◷' : '›'),
    el('div', { class: 'txt' }, i.text, el('div', { class: 'chip2' },
      i.status === 'scheduled' ? `Scheduled · ${i.when}` : (label[i.status] || i.status))),
    el('button', { class: 'x', title: 'Remove', onclick: () => act('remove', { id: i.id }) }, '×'),
    i.status === 'working' ? el('div', { class: 'bar' }) : null,
    i.result ? el('div', { class: 'result', title: 'Click to expand', onclick: (e) => e.currentTarget.classList.toggle('open') }, i.result) : null)));
}

function renderProjects(ps) {
  const box = $('projects');
  box.replaceChildren();
  const running = ps.filter((p) => p.running).length;
  $('proj-badge').textContent = running ? `${running} live` : '';
  if (!ps.length) { box.append(el('div', { class: 'empty' }, el('div', {}, 'No Claude Code projects found.'))); return; }
  for (const p of ps) {
    box.append(el('div', { class: `proj${p.running ? ' running' : ''}`, title: 'Click: Jarvis briefs you on it',
      onclick: () => act('project', { cwd: p.cwd }) },
      el('div', { class: 'top' }, el('div', { class: 'pn' }, p.name.replace(/[-_]+/g, ' ')), el('div', { class: 'ago' }, p.ago)),
      p.title ? el('div', { class: 'pt' }, p.title) : null,
      p.running ? el('div', { class: 'live' }, `Claude Code running${p.task ? ' · ' + p.task : ''}`) :
        p.last ? el('div', { class: 'pl' }, el('b', {}, 'LAST'), p.last) : null));
  }
}

function renderTimeline(d) {
  const ahead = (d.ahead || []).flatMap((g) => g.items.map((i, n) => el('li', {},
    el('span', { class: 't' }, n === 0 ? g.day.slice(0, 9) : ''), el('span', {}, i.text,
      el('button', { class: 'x', title: 'Remove', onclick: () => act('remove', { id: i.id }) }, '×')))));
  $('ahead').replaceChildren(...(ahead.length ? ahead :
    [el('li', { class: 'none' }, 'Nothing planned yet. Try “add call the bank for tomorrow”.')]));
  $('reminders').replaceChildren(...(d.reminders && d.reminders.length ? d.reminders.map((r) =>
    el('li', {}, el('span', { class: 't' }, `${r.day ? r.day + ' ' : ''}${r.at}`), el('span', {}, r.text))) :
    [el('li', { class: 'none' }, 'None set. Try “remind me at 5 to call Ali”.')]));
  $('activity').replaceChildren(...(d.log && d.log.length ? d.log.map(([t, text]) =>
    el('li', {}, el('span', { class: 't' }, t), el('span', {}, text))) :
    [el('li', { class: 'none' }, 'Quiet so far today.')]));
  renderFocus(d.focus);
}

function renderFocus(f) {
  const box = $('focus');
  clearInterval(focusTimer);
  box.replaceChildren();
  if (f && f.until > Date.now()) {
    const count = el('div', { class: 'count' });
    const tick = () => {
      const s = Math.max(0, Math.round((f.until - Date.now()) / 1000));
      count.textContent = `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
      if (!s) clearInterval(focusTimer);
    };
    tick(); focusTimer = setInterval(tick, 1000);
    box.append(count, el('div', {}, el('div', { class: 'fl' }, `Focus · ${f.minutes} min`), el('div', { class: 'sub' }, 'Alerts on hold')),
      el('button', { class: 'btn', onclick: () => act('focus_stop') }, 'End'));
  } else {
    box.append(el('div', {}, el('div', { class: 'fl' }, 'Focus mode'), el('div', { class: 'sub' }, 'Holds email alerts')),
      el('button', { class: 'btn', onclick: () => act('focus', { minutes: 25 }) }, '25 min'),
      el('button', { class: 'btn', onclick: () => act('focus', { minutes: 50 }) }, '50 min'));
  }
}

// ---------------------------------------------------------------- live system gauges
const GAUGES = [['cpu', 'CPU'], ['mem', 'Memory'], ['battery', 'Power'], ['disk', 'Disk']];
const gaugeSegs = {};
function buildGauges() {
  $('gauges').replaceChildren(...GAUGES.map(([k, label]) => {
    const s = svgEl('svg', { viewBox: '0 0 100 100' });
    const g = svgEl('g');
    s.append(g);
    gaugeSegs[k] = segments(g, { n: 28, r: 46, cx: 50, cy: 50, from: 135, sweep: 270, len: 9, cls: 'seg' });
    return el('div', { class: 'gauge', id: `g-${k}` }, s, el('div', { class: 'val' }, '--'), el('div', { class: 'lbl' }, label));
  }));
}
function renderVitals() {
  if (!vitals) return;
  for (const [k] of GAUGES) {
    const g = $(`g-${k}`), v = vitals[k];
    if (!g) continue;
    if (v == null) { g.querySelector('.val').textContent = '—'; light(gaugeSegs[k], 0); continue; }
    light(gaugeSegs[k], v / 100);
    const warn = k === 'battery' ? (!vitals.plugged && v < 30) : v > 75;
    const hot = k === 'battery' ? (!vitals.plugged && v < 15) : v > 90;
    g.classList.toggle('warn', warn && !hot); g.classList.toggle('hot', hot);
    g.querySelector('.val').replaceChildren(String(v), el('small', {}, k === 'battery' && vitals.plugged ? '%+' : '%'));
  }
  const kb = (n) => (n >= 1024 ? `${(n / 1024).toFixed(1)}MB/s` : `${n}KB/s`);
  $('net').textContent = `DN ${kb(vitals.down || 0)} · UP ${kb(vitals.up || 0)}`;
}

// ---------------------------------------------------------------- clock
function clock() {
  const now = new Date();
  $('time').firstChild.nodeValue = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
  $('sec').textContent = String(now.getSeconds()).padStart(2, '0');
  $('date').textContent = now.toLocaleDateString([], { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
}

// ---------------------------------------------------------------- input
$('close').addEventListener('click', close);
$('run').addEventListener('click', () => act('run'));
document.querySelectorAll('#tabs button').forEach((b) => b.addEventListener('click', () => setView(b.dataset.view)));
$('add').addEventListener('submit', (e) => {
  e.preventDefault();
  const t = $('add-text').value.trim();
  if (t) { act('add', { text: t }); $('add-text').value = ''; }
});
window.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') close();
  else if (e.target.tagName !== 'INPUT' && /^[1-4]$/.test(e.key)) setView(['all', 'todos', 'emails', 'projects'][+e.key - 1]);
});
window.addEventListener('beforeunload', () => act('closed'));
host.onView((v) => { if (v) setView(v); enter(); });
host.onLeave(() => leave());

buildGauges();
clock();
setInterval(clock, 1000);
setView('all');
$('said').textContent = HINT;

if (qs.get('demo')) {  // a look at the board without the backend
  render({ greeting: 'Good morning, Sir', weather: { temp: 22, sky: 'clear skies', city: 'Lahore', hi: 27, lo: 14, rain: 10 },
    emails: { connected: true, items: [
      { id: '1', from: 'Murtaza Khalid', subject: 'Re: Availability for a meeting', unread: true, time: '9:12 AM', thread: 'x' },
      { id: '2', from: 'LinkedIn', subject: 'You appeared in 14 searches this week', unread: true, time: '8:40 AM' },
      { id: '3', from: 'Workable', subject: 'Your application to Senior AI Engineer', unread: false, time: '7:05 AM' },
      { id: '4', from: 'AGCP Recruiting Team', subject: 'Next steps for your interview', unread: false, time: 'Thu' }] },
    replies: [
      { id: '1', from: 'Murtaza Khalid', subject: 'Re: Availability for a meeting', time: '9:12 AM', status: 'ready',
        preview: 'Hi Rafay, are you free this week to go over the Solvix rollout? Thursday afternoon works for us.',
        body: 'Hi Murtaza,\n\nThanks for reaching out. Thursday afternoon should work; I\'ll confirm the exact time shortly.\n\nBest regards' },
      { id: '4', from: 'AGCP Recruiting Team', subject: 'Re: Next steps for your interview', time: 'Thu', status: 'drafting',
        preview: 'Thank you for your time. We would like to invite you to the next round.', body: '' },
      { id: '5', from: 'Sara Ahmed', subject: 'Re: Invoice for October', time: 'Wed', status: 'sent', preview: '', body: '' }],
    items: [
      { id: 'a', text: 'Call Ali about the contract', owner: 'you', status: 'done' },
      { id: 'b', text: 'Finish the Agreeofy summary', owner: 'you', status: 'todo', carried: true },
      { id: 'c', text: 'Gym at 7', owner: 'you', status: 'todo' },
      { id: 'd', text: 'Research AI agents on ChatGPT', owner: 'jarvis', status: 'working' },
      { id: 'e', text: 'Email Sara about the meeting', owner: 'jarvis', status: 'todo' },
      { id: 'f', text: 'Check the status of Solvix', owner: 'jarvis', status: 'done', result: 'Solvix, last active 2 h ago. You are on the login page; the API tests pass.' }],
    projects: [
      { name: 'jarvis', cwd: 'x', title: 'Board and morning briefing', ago: '3 min ago', running: true, task: 'Add the command center' },
      { name: 'Solvix', cwd: 'y', title: 'Login page', ago: '2 h ago', last: 'make the sign-in button match the brand colour' },
      { name: 'portfolio-site', cwd: 'z', title: '', ago: '1 d ago', last: 'deploy to vercel' }],
    reminders: [{ id: 'r', text: 'Call Ali', at: '5:00 PM', day: '' }],
    focus: null, log: [['09:02', 'Plan: 6 items added'], ['09:05', 'Done: Check the status of Solvix']] });
  vitals = { cpu: 23, mem: 61, battery: 84, plugged: true, disk: 71, down: 320, up: 45 }; renderVitals();
  enter();
} else {
  connect();
}
