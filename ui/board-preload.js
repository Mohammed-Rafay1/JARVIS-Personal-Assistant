// The board window's bridge: where the backend is, and the few things the page may ask the main process.
const { contextBridge, ipcRenderer } = require('electron');

const arg = (name, def) => {
  const hit = process.argv.find((a) => a.startsWith(`--jarvis-${name}=`));
  return hit ? hit.split('=').slice(1).join('=') : def;
};

contextBridge.exposeInMainWorld('host', {
  port: arg('port', '8765'),
  token: arg('token', ''),
  hide: () => ipcRenderer.send('board-hide'),
  openUrl: (url) => ipcRenderer.send('board-open-url', String(url || '')),
  onView: (cb) => ipcRenderer.on('board-view', (_e, v) => cb(v)),
  onLeave: (cb) => ipcRenderer.on('board-leave', () => cb()),
});
