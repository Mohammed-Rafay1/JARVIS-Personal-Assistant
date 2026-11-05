// JARVIS overlay: a transparent, always-on-top window carrying the orb. The orb starts in the
// bottom-right corner and can be dragged anywhere; the spot is remembered in data/overlay.json.
// Launched by the Python backend with --jarvis-port, --jarvis-token and --jarvis-hotkey.
const { app, BrowserWindow, globalShortcut, ipcMain, Menu, Tray, nativeImage, screen, shell } = require('electron');
const fs = require('fs');
const path = require('path');
const pos = require('./position');

const arg = (name, def) => {
  const hit = process.argv.find((a) => a.startsWith(`--jarvis-${name}=`));
  return hit ? hit.split('=').slice(1).join('=') : def;
};
const PORT = arg('port', '8765');
const TOKEN = arg('token', '');
const HOTKEY = arg('hotkey', 'Control+Alt+J');
const ROOT = path.resolve(__dirname, '..');
const SPOT_FILE = path.join(ROOT, 'data', 'overlay.json');
const LOG_FILE = path.join(ROOT, 'data', 'logs', 'overlay.log');

// the backend doesn't capture this process's output, so problems go to data/logs/overlay.log
function log(...parts) {
  try {
    if (fs.existsSync(LOG_FILE) && fs.statSync(LOG_FILE).size > 512 * 1024) fs.renameSync(LOG_FILE, `${LOG_FILE}.1`);
    fs.appendFileSync(LOG_FILE, `${new Date().toTimeString().slice(0, 8)} ${parts.join(' ')}\n`);
  } catch { /* logging must never break the overlay */ }
}

let win, tray, muted = false, mode = 'dock';
let spot = null;   // { anchor, offset, win } from position.js
let drag = null;   // { grab, timer, until } while the orb is being dragged
// No single-instance lock here: the backend's mutex already allows one Jarvis, and after a crash
// the new overlay must be able to start while the old one is still closing itself.

// ---------------------------------------------------------------- where the orb is
const workAreaOf = (anchor) => screen.getDisplayNearestPoint(pos.centre(anchor)).workArea;

function loadSpot() {
  let saved = null;
  try { saved = JSON.parse(fs.readFileSync(SPOT_FILE, 'utf8')); } catch { /* first run: the corner */ }
  return pos.restore(saved, screen.getAllDisplays().map((d) => d.workArea), screen.getPrimaryDisplay().workArea);
}

function saveSpot() {
  try {
    fs.mkdirSync(path.dirname(SPOT_FILE), { recursive: true });
    fs.writeFileSync(SPOT_FILE, JSON.stringify({ anchor: spot.anchor, offset: spot.offset }));
  } catch (e) { log('could not save the orb position:', e.message); }
}

const sameRect = (a, b) => a && b && a.x === b.x && a.y === b.y && a.width === b.width && a.height === b.height;

// move the window and/or tell the page where the orb sits inside it
function applySpot(next, { smooth = false, force = false } = {}) {
  const prev = spot;
  spot = next;
  if (!win || mode === 'full') return;
  if (force || !sameRect(win.getBounds(), next.win)) win.setBounds(next.win);
  if (force || !prev || prev.offset.x !== next.offset.x || prev.offset.y !== next.offset.y) {
    win.webContents.send('spot', { x: next.offset.x, y: next.offset.y, smooth });
  }
}

function dragTick() {
  if (Date.now() > drag.until) { endDrag(); return; }   // a lost mouse-up never leaves it stuck
  const p = screen.getCursorScreenPoint();
  const wa = screen.getDisplayNearestPoint(p).workArea;  // follows the cursor onto other monitors
  applySpot(pos.fit({ x: p.x - drag.grab.x, y: p.y - drag.grab.y }, spot.offset, wa));
}

function endDrag(keep = true) {
  if (!drag) return;
  clearInterval(drag.timer);
  drag = null;
  if (keep) {
    const wa = workAreaOf(spot.anchor);
    applySpot(pos.settle(pos.snap(spot.anchor, wa), spot.win, wa), { smooth: true });
    saveSpot();
    log('orb moved to', spot.anchor.x, spot.anchor.y);
  }
  // clicks pass through the window again, except right over the orb (the cursor is usually still on it)
  if (win && mode !== 'full') {
    const p = screen.getCursorScreenPoint(), a = spot.anchor;
    const onOrb = p.x >= a.x && p.x < a.x + pos.ORB && p.y >= a.y && p.y < a.y + pos.ORB;
    win.setIgnoreMouseEvents(!onOrb, { forward: true });
  }
}

function resetSpot() {
  endDrag(false);
  const wa = screen.getPrimaryDisplay().workArea;
  const h = pos.home(wa);
  applySpot(pos.fit(h.anchor, h.offset, wa), { force: true });
  saveSpot();
}

function place() {
  if (mode === 'full') {  // a call covers the whole screen the cursor is on
    win.setBounds(screen.getDisplayNearestPoint(screen.getCursorScreenPoint()).bounds);
    return;
  }
  // back from a call, or the screens changed: same spot, kept on a screen that is still there
  applySpot(pos.fit(spot.anchor, spot.offset, workAreaOf(spot.anchor)), { force: true });
}

function createWindow() {
  spot = loadSpot();
  win = new BrowserWindow({
    ...spot.win, show: false, frame: false, transparent: true, resizable: false,
    movable: false, minimizable: false, maximizable: false, fullscreenable: false,
    skipTaskbar: true, hasShadow: false, alwaysOnTop: true, backgroundColor: '#00000000',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'), contextIsolation: true, nodeIntegration: false,
      backgroundThrottling: false,
      // the orb's spot goes in up front so the first frame already has it in the right place
      additionalArguments: [`--jarvis-port=${PORT}`, `--jarvis-token=${TOKEN}`, `--jarvis-hotkey=${HOTKEY}`,
        `--jarvis-spot=${spot.offset.x},${spot.offset.y}`],
    },
  });
  win.setAlwaysOnTop(true, 'screen-saver');
  win.setVisibleOnAllWorkspaces(true);
  win.setIgnoreMouseEvents(true, { forward: true });
  win.setBounds(spot.win);
  win.webContents.on('console-message', (_e, level, message, line, source) => {
    if (level >= 2) log('page:', message, `(${path.basename(source || '')}:${line})`);   // warnings and errors
  });
  win.webContents.on('render-process-gone', (_e, d) => log('page crashed:', d.reason));
  win.loadFile(path.join(__dirname, 'index.html'));
  win.once('ready-to-show', () => win.showInactive());
  screen.on('display-metrics-changed', place);
  screen.on('display-added', place);
  screen.on('display-removed', place);
}

function trayIcon(color) {
  // 16x16 dot drawn as SVG so there is no binary asset to ship
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32"><defs><radialGradient id="g"><stop offset="0" stop-color="#fff"/><stop offset=".45" stop-color="${color}"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></radialGradient></defs><circle cx="16" cy="16" r="15" fill="url(#g)"/></svg>`;
  return nativeImage.createFromDataURL('data:image/svg+xml;base64,' + Buffer.from(svg).toString('base64')).resize({ width: 16, height: 16 });
}

function buildTray() {
  tray = new Tray(trayIcon('#22D3EE'));
  tray.setToolTip('JARVIS');
  const menu = () => Menu.buildFromTemplate([
    { label: `Talk or type   (${HOTKEY.replace('Control', 'Ctrl')})`, click: () => win.webContents.send('hotkey') },
    { label: 'Mute microphone', type: 'checkbox', checked: muted, click: (i) => setMute(i.checked) },
    { label: 'Put the orb back in the corner', click: resetSpot },
    { type: 'separator' },
    { label: 'Settings (config.yaml)', click: () => shell.openPath(path.join(ROOT, 'config.yaml')) },
    { label: 'Contacts (contacts.yaml)', click: () => shell.openPath(path.join(ROOT, 'contacts.yaml')) },
    { label: 'Open log', click: () => shell.openPath(path.join(ROOT, 'data', 'logs', 'jarvis.log')) },
    { type: 'separator' },
    { label: 'Quit JARVIS', click: () => { win.webContents.send('quit'); setTimeout(() => app.quit(), 400); } },
  ]);
  tray.setContextMenu(menu());
  tray.on('click', () => win.webContents.send('hotkey'));
  tray.rebuild = () => tray.setContextMenu(menu());
}

function setMute(value) {
  muted = value;
  win.webContents.send('mute', value);
  tray.setImage(trayIcon(value ? '#64748B' : '#22D3EE'));
  tray.rebuild();
}

ipcMain.on('interactive', (_e, on) => {
  if (!win || mode === 'full') return;
  win.setIgnoreMouseEvents(!on, { forward: true });
});
ipcMain.on('drag', (_e, m) => {
  if (!win || !m) return;
  if (m.phase === 'start' && (mode === 'full' || drag)) log('drag ignored:', mode, drag ? 'already dragging' : '');
  if (m.phase === 'start' && mode !== 'full' && !drag && m.grab && Number.isFinite(m.grab.x) && Number.isFinite(m.grab.y)) {
    win.setIgnoreMouseEvents(false);   // keep the mouse while dragging, even past the orb's edge
    drag = { grab: { x: Math.round(m.grab.x), y: Math.round(m.grab.y) }, timer: setInterval(dragTick, 8), until: Date.now() + 120000 };
  } else if (m.phase === 'end') {
    endDrag();
  }
});
ipcMain.on('mode', (_e, next) => {
  endDrag();   // a call can start mid-drag ("Hey Jarvis")
  mode = next;
  place();
  if (next === 'full') {
    win.setIgnoreMouseEvents(false);
    win.setAlwaysOnTop(true, 'screen-saver');
    win.show();
    win.focus();  // so Esc and typing work straight away
  } else {
    win.setIgnoreMouseEvents(true, { forward: true });
    if (next === 'dock') win.blur();
  }
});
ipcMain.on('focus', () => { win.setFocusable(true); win.focus(); });
ipcMain.on('blur', () => { win.blur(); });
ipcMain.on('muted', (_e, value) => {
  if (value !== muted) { muted = value; tray.setImage(trayIcon(value ? '#64748B' : '#22D3EE')); tray.rebuild(); }
});
ipcMain.on('quit', () => app.quit());
ipcMain.on('snapshot', async () => {  // debug aid: what the overlay looks like right now
  const img = await win.webContents.capturePage();
  require('fs').writeFileSync(path.join(ROOT, 'data', 'snapshot.png'), img.toPNG());
});
ipcMain.on('open', (_e, what) => {
  if (what === 'config') shell.openPath(path.join(ROOT, 'config.yaml'));
});

app.whenReady().then(() => {
  createWindow();
  buildTray();
  const ok = globalShortcut.register(HOTKEY, () => {
    win.showInactive();
    win.webContents.send('hotkey');
  });
  if (!ok) console.error(`hotkey ${HOTKEY} is taken by another app`);
});

app.on('will-quit', () => globalShortcut.unregisterAll());
app.on('window-all-closed', () => app.quit());
