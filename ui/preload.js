const { contextBridge, ipcRenderer } = require('electron');

const arg = (name, def) => {
  const hit = process.argv.find((a) => a.startsWith(`--jarvis-${name}=`));
  return hit ? hit.split('=').slice(1).join('=') : def;
};

contextBridge.exposeInMainWorld('host', {
  port: arg('port', '8765'),
  token: arg('token', ''),
  hotkey: arg('hotkey', 'Control+Alt+J').replace('Control', 'Ctrl'),
  setInteractive: (on) => ipcRenderer.send('interactive', !!on),
  focus: () => ipcRenderer.send('focus'),
  blur: () => ipcRenderer.send('blur'),
  reportMuted: (v) => ipcRenderer.send('muted', !!v),
  snapshot: () => ipcRenderer.send('snapshot'),
  setMode: (m) => ipcRenderer.send('mode', m),
  quit: () => ipcRenderer.send('quit'),
  on: (channel, cb) => {
    if (['hotkey', 'mute', 'quit', 'cursor'].includes(channel)) ipcRenderer.on(channel, (_e, v) => cb(v));
  },
});
