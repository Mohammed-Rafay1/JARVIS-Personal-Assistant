// JARVIS overlay: a transparent, always-on-top window in the bottom-right corner.
// Launched by the Python backend with --jarvis-port, --jarvis-token and --jarvis-hotkey.
const { app, BrowserWindow, globalShortcut, ipcMain, Menu, Tray, nativeImage, screen, shell } = require('electron');
const path = require('path');

const arg = (name, def) => {
  const hit = process.argv.find((a) => a.startsWith(`--jarvis-${name}=`));
  return hit ? hit.split('=').slice(1).join('=') : def;
};
const PORT = arg('port', '8765');
const TOKEN = arg('token', '');
const HOTKEY = arg('hotkey', 'Control+Alt+J');
const ROOT = path.resolve(__dirname, '..');
const W = 440, H = 640, MARGIN = 8;

let win, tray, muted = false, mode = 'dock';
// No single-instance lock here: the backend's mutex already allows one Jarvis, and after a crash
// the new overlay must be able to start while the old one is still closing itself.

function place() {
  if (mode === 'full') {  // a call covers the whole screen the cursor is on
    win.setBounds(screen.getDisplayNearestPoint(screen.getCursorScreenPoint()).bounds);
    return;
  }
  const { workArea } = screen.getPrimaryDisplay();
  win.setBounds({ x: workArea.x + workArea.width - W - MARGIN, y: workArea.y + workArea.height - H - MARGIN, width: W, height: H });
}

function createWindow() {
  win = new BrowserWindow({
    width: W, height: H, show: false, frame: false, transparent: true, resizable: false,
    movable: false, minimizable: false, maximizable: false, fullscreenable: false,
    skipTaskbar: true, hasShadow: false, alwaysOnTop: true, backgroundColor: '#00000000',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'), contextIsolation: true, nodeIntegration: false,
      backgroundThrottling: false,
      additionalArguments: [`--jarvis-port=${PORT}`, `--jarvis-token=${TOKEN}`, `--jarvis-hotkey=${HOTKEY}`],
    },
  });
  win.setAlwaysOnTop(true, 'screen-saver');
  win.setVisibleOnAllWorkspaces(true);
  win.setIgnoreMouseEvents(true, { forward: true });
  place();
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
ipcMain.on('mode', (_e, next) => {
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
