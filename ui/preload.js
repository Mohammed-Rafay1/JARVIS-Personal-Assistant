const { contextBridge, ipcRenderer } = require('electron');

const arg = (name, def) => {
  const hit = process.argv.find((a) => a.startsWith(`--jarvis-${name}=`));
  return hit ? hit.split('=').slice(1).join('=') : def;
};

contextBridge.exposeInMainWorld('host', {
  port: arg('port', '8765'),
  token: arg('token', ''),
  hotkey: arg('hotkey', 'Control+Alt+J').replace('Control', 'Ctrl'),
  spot: (() => { const [x, y] = arg('spot', '').split(',').map(Number); return Number.isFinite(x) && Number.isFinite(y) ? { x, y } : null; })(),
  drag: (phase, grab) => ipcRenderer.send('drag', { phase, grab }),
  setInteractive: (on) => ipcRenderer.send('interactive', !!on),
  focus: () => ipcRenderer.send('focus'),
  blur: () => ipcRenderer.send('blur'),
  reportMuted: (v) => ipcRenderer.send('muted', !!v),
  snapshot: () => ipcRenderer.send('snapshot'),
  setMode: (m) => ipcRenderer.send('mode', m),
  quit: () => ipcRenderer.send('quit'),
  on: (channel, cb) => {
    if (['hotkey', 'mute', 'quit', 'spot'].includes(channel)) ipcRenderer.on(channel, (_e, v) => cb(v));
  },
});
