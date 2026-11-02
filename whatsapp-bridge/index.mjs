// Jarvis <-> WhatsApp bridge (Baileys). Started and supervised by the Jarvis backend.
//
// Talks to Jarvis over stdin/stdout, one JSON object per line; logs go to stderr. No network port is
// opened. Only messages from you reach Jarvis: your "Message yourself" chat on the linked account, and
// the numbers in `allowed`. Groups, status updates, other people and old messages are ignored here,
// before anything is passed on.
//
// out: {type:"qr", path} {type:"pairing_code", code} {type:"ready", me} {type:"logged_out"}
//      {type:"message", id, chat, text} {type:"message", id, chat, voice, seconds}
// in:  {type:"send", chat, text} {type:"send_voice", chat, path, seconds}
//      {type:"presence", chat, state} {type:"react", chat, id, emoji}
import fs from 'node:fs';
import path from 'node:path';
import readline from 'node:readline';

import makeWASocket, {
  DisconnectReason, downloadMediaMessage, fetchLatestBaileysVersion, jidNormalizedUser, useMultiFileAuthState,
} from '@whiskeysockets/baileys';
import pino from 'pino';
import QRCode from 'qrcode';

import { digits, fromYou } from './filter.mjs';

const cfg = JSON.parse(process.env.JARVIS_WA_CONFIG || '{}');
const allowed = new Set((cfg.allowed || []).map(digits).filter(Boolean));
const selfChat = cfg.selfChat !== false;
const logger = pino({ level: 'warn' }, pino.destination(2));
fs.mkdirSync(cfg.mediaDir, { recursive: true });

const sentByUs = new Set();   // our own replies show up in the self chat too; never treat them as commands
let sock = null;
let me = { pn: '', lid: '' };

function emit(obj) { process.stdout.write(JSON.stringify(obj) + '\n'); }
function log(...a) { process.stderr.write(a.join(' ') + '\n'); }

function remember(id) {
  sentByUs.add(id);
  if (sentByUs.size > 500) sentByUs.delete(sentByUs.values().next().value);
}

function unwrap(m) {
  let msg = m.message || {};
  for (const k of ['ephemeralMessage', 'viewOnceMessage', 'viewOnceMessageV2', 'documentWithCaptionMessage']) {
    if (msg[k]?.message) msg = msg[k].message;
  }
  return msg;
}

async function onMessage(m) {
  const key = m.key || {};
  if (!key.id || sentByUs.has(key.id) || !fromYou(key, me, allowed, selfChat)) return;
  const age = Date.now() / 1000 - Number(m.messageTimestamp || 0);
  if (age > 120) return;   // backlog delivered after a reconnect: too old to act on
  const msg = unwrap(m);
  const text = msg.conversation || msg.extendedTextMessage?.text || '';
  if (text.trim()) {
    emit({ type: 'message', id: key.id, chat: key.remoteJid, text: text.trim() });
    return;
  }
  if (msg.audioMessage) {
    try {
      const buf = await downloadMediaMessage(m, 'buffer', {}, { logger, reuploadRequest: sock.updateMediaMessage });
      const file = path.join(cfg.mediaDir, `${key.id}.ogg`);
      fs.writeFileSync(file, buf);
      emit({ type: 'message', id: key.id, chat: key.remoteJid, voice: file,
             seconds: Number(msg.audioMessage.seconds || 0) });
    } catch (e) {
      log('voice note download failed:', e?.message || e);
      emit({ type: 'message', id: key.id, chat: key.remoteJid, error: 'download' });
    }
  }
}

async function handle(cmd) {
  if (!sock) return;
  try {
    if (cmd.type === 'send') {
      const r = await sock.sendMessage(cmd.chat, { text: cmd.text });
      if (r?.key?.id) remember(r.key.id);
      log('reply delivered to WhatsApp');
    } else if (cmd.type === 'send_voice') {
      const r = await sock.sendMessage(cmd.chat, {
        audio: fs.readFileSync(cmd.path), mimetype: 'audio/ogg; codecs=opus', ptt: true,
        seconds: Math.max(1, Math.round(cmd.seconds || 1)),
      });
      if (r?.key?.id) remember(r.key.id);
      fs.rm(cmd.path, { force: true }, () => {});
    } else if (cmd.type === 'presence') {
      await sock.sendPresenceUpdate(cmd.state, cmd.chat);
    } else if (cmd.type === 'react') {
      const r = await sock.sendMessage(cmd.chat, { react: { text: cmd.emoji, key: { remoteJid: cmd.chat, id: cmd.id, fromMe: !!cmd.fromMe } } });
      if (r?.key?.id) remember(r.key.id);
    }
  } catch (e) {
    log(`${cmd.type} failed:`, e?.message || e);
  }
}

async function connect() {
  const { state, saveCreds } = await useMultiFileAuthState(cfg.authDir);
  let version;
  try { ({ version } = await fetchLatestBaileysVersion()); } catch { /* the built-in version is fine */ }
  sock = makeWASocket({
    auth: state, version, logger, browser: ['Jarvis', 'Desktop', '1.0'],
    markOnlineOnConnect: false,   // keep notifications coming to your phone
    syncFullHistory: false,
  });
  sock.ev.on('creds.update', saveCreds);
  let askedCode = false;
  sock.ev.on('connection.update', async (u) => {
    if (u.qr) {
      if (cfg.phone && !askedCode) {
        askedCode = true;
        try { emit({ type: 'pairing_code', code: await sock.requestPairingCode(digits(cfg.phone)) }); return; }
        catch (e) { log('pairing code failed, showing a QR code instead:', e?.message || e); }
      }
      await QRCode.toFile(cfg.qrPath, u.qr, { width: 420, margin: 2 });
      emit({ type: 'qr', path: cfg.qrPath });
    }
    if (u.connection === 'open') {
      me = { pn: jidNormalizedUser(sock.user?.id || ''), lid: jidNormalizedUser(sock.user?.lid || '') };
      fs.rm(cfg.qrPath, { force: true }, () => {});
      emit({ type: 'ready', me: digits(me.pn) });
    }
    if (u.connection === 'close') {
      const code = u.lastDisconnect?.error?.output?.statusCode;
      if (code === DisconnectReason.loggedOut) {
        emit({ type: 'logged_out' });
        process.exit(0);
      }
      log('connection closed', code, '- reconnecting');
      setTimeout(connect, code === DisconnectReason.restartRequired ? 0 : 3000);
    }
  });
  sock.ev.on('messages.upsert', ({ messages, type }) => {
    if (type !== 'notify') return;
    for (const m of messages) onMessage(m).catch(e => log('message failed:', e?.message || e));
  });
}

// Jarvis closing our stdin means it has exited: leave too, so two bridges never share one session
readline.createInterface({ input: process.stdin })
  .on('line', (line) => { try { handle(JSON.parse(line)); } catch { log('bad command:', line); } })
  .on('close', () => process.exit(0));

connect().catch((e) => { log('bridge failed:', e?.stack || e); process.exit(1); });
