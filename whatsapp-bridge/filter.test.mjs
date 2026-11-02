import assert from 'node:assert/strict';
import test from 'node:test';

import { fromYou } from './filter.mjs';

const me = { pn: '923001112222@s.whatsapp.net', lid: '55511122233@lid' };
const allowed = new Set(['923334445555']);

test('your own "Message yourself" chat is accepted (phone-number or hidden id)', () => {
  assert.equal(fromYou({ remoteJid: '923001112222@s.whatsapp.net', fromMe: true }, me, allowed, true), true);
  assert.equal(fromYou({ remoteJid: '55511122233@lid', fromMe: true }, me, allowed, true), true);
  assert.equal(fromYou({ remoteJid: '923001112222:7@s.whatsapp.net', fromMe: true }, me, allowed, true), true);
});

test('what you type in other chats is never a command', () => {
  assert.equal(fromYou({ remoteJid: '923009998888@s.whatsapp.net', fromMe: true }, me, allowed, true), false);
});

test('self chat can be switched off', () => {
  assert.equal(fromYou({ remoteJid: '923001112222@s.whatsapp.net', fromMe: true }, me, allowed, false), false);
});

test('an allowed number is accepted, also when WhatsApp hides it behind a LID', () => {
  assert.equal(fromYou({ remoteJid: '923334445555@s.whatsapp.net' }, me, allowed, true), true);
  assert.equal(fromYou({ remoteJid: '99988877766@lid', remoteJidAlt: '923334445555@s.whatsapp.net' }, me, allowed, true), true);
});

test('strangers, groups, status and channels are ignored', () => {
  assert.equal(fromYou({ remoteJid: '923007776666@s.whatsapp.net' }, me, allowed, true), false);
  assert.equal(fromYou({ remoteJid: '923334445555@lid' }, me, allowed, true), false);  // a LID that only looks like the number
  assert.equal(fromYou({ remoteJid: '1203630@g.us', participant: '923334445555@s.whatsapp.net' }, me, allowed, true), false);
  assert.equal(fromYou({ remoteJid: 'status@broadcast', participant: '923334445555@s.whatsapp.net' }, me, allowed, true), false);
  assert.equal(fromYou({ remoteJid: '120363@newsletter' }, me, allowed, true), false);
});

test('before linking finishes (me unknown) nothing of yours is accepted', () => {
  assert.equal(fromYou({ remoteJid: '923001112222@s.whatsapp.net', fromMe: true }, { pn: '', lid: '' }, allowed, true), false);
});
