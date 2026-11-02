// Which incoming WhatsApp messages may command Jarvis. Kept separate so it can be tested on its own.
import { isJidGroup, isJidStatusBroadcast, jidNormalizedUser } from '@whiskeysockets/baileys';

export function digits(s) { return String(s || '').split('@')[0].split(':')[0].replace(/\D/g, ''); }

// key: the message key; me: {pn, lid} of the linked account; allowed: Set of phone numbers (digits only)
export function fromYou(key, me, allowed, selfChat) {
  const chat = key.remoteJid || '';
  if (!chat || isJidGroup(chat) || isJidStatusBroadcast(chat) || chat.endsWith('@newsletter')) return false;
  const ids = [chat, key.remoteJidAlt].filter(Boolean).map(j => jidNormalizedUser(j));
  if (key.fromMe) {   // something you typed: only the chat with yourself counts
    return selfChat && ids.some(j => (me.pn && j === me.pn) || (me.lid && j === me.lid));
  }
  return ids.some(j => j.endsWith('@s.whatsapp.net') && allowed.has(digits(j)));
}
