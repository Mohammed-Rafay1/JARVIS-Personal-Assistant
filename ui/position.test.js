// node --test ui   (checks the orb placement maths; no Electron needed)
const test = require('node:test');
const assert = require('node:assert/strict');
const pos = require('./position');

const WA = { x: 0, y: 0, width: 1920, height: 1040 };          // a 1080p screen above the taskbar
const LEFT = { x: -1280, y: 0, width: 1280, height: 984 };     // a second monitor on the left

const inside = (r, wa) => r.x >= wa.x && r.y >= wa.y && r.x + r.width <= wa.x + wa.width && r.y + r.height <= wa.y + wa.height;
const orbOnScreen = (s) => s.anchor.x === s.win.x + s.offset.x && s.anchor.y === s.win.y + s.offset.y;

test('starts in the bottom-right corner, where it always was', () => {
  const s = pos.restore(null, [WA], WA);
  assert.deepEqual(s.anchor, { x: 1920 - pos.EDGE - pos.ORB, y: 1040 - pos.EDGE - pos.ORB });
  assert.deepEqual(s.offset, { x: pos.W - 10 - pos.ORB, y: pos.H - 10 - pos.ORB });
  assert.ok(inside(s.win, WA) && orbOnScreen(s));
});

test('dragging moves the window with the orb', () => {
  const s0 = pos.restore(null, [WA], WA);
  const s = pos.fit({ x: 1200, y: 700 }, s0.offset, WA);
  assert.deepEqual(s.anchor, { x: 1200, y: 700 });
  assert.deepEqual(s.offset, s0.offset);              // the window simply followed
  assert.ok(inside(s.win, WA) && orbOnScreen(s));
});

test('at the top-left the window stops at the edge and the orb slides inside it', () => {
  const s = pos.fit({ x: 30, y: 40 }, { x: 354, y: 554 }, WA);
  assert.deepEqual(s.win, { x: 0, y: 0, width: 440, height: 640 });
  assert.deepEqual(s.anchor, { x: 30, y: 40 });
  assert.deepEqual(s.offset, { x: 30, y: 40 });
});

test('the orb can never be pushed off the screen', () => {
  for (const a of [{ x: -500, y: -500 }, { x: 5000, y: 5000 }, { x: -3, y: 2000 }]) {
    const s = pos.fit(a, { x: 354, y: 554 }, WA);
    assert.ok(inside(s.win, WA) && orbOnScreen(s));
    assert.ok(s.anchor.x >= pos.PAD && s.anchor.x + pos.ORB <= WA.width - pos.PAD);
    assert.ok(s.anchor.y >= pos.PAD && s.anchor.y + pos.ORB <= WA.height - pos.PAD);
  }
});

test('let go near an edge and it snaps to a tidy gap; elsewhere it stays put', () => {
  assert.deepEqual(pos.snap({ x: 10, y: 500 }, WA), { x: 16, y: 500 });
  assert.deepEqual(pos.snap({ x: 1920 - pos.ORB - 9, y: 1040 - pos.ORB - 20 }, WA), { x: 1920 - pos.ORB - 16, y: 1040 - pos.ORB - 16 });
  assert.deepEqual(pos.snap({ x: 900, y: 400 }, WA), { x: 900, y: 400 });
});

test('a snap slides the orb inside the window instead of moving the window', () => {
  const dropped = pos.fit({ x: 10, y: 10 }, { x: 354, y: 554 }, WA);    // window pinned at 0,0
  const s = pos.settle(pos.snap(dropped.anchor, WA), dropped.win, WA);
  assert.deepEqual(s.win, dropped.win);
  assert.deepEqual(s.anchor, { x: 16, y: 16 });
});

test('works on a second monitor to the left (negative coordinates)', () => {
  const s = pos.fit({ x: -1250, y: 30 }, { x: 354, y: 554 }, LEFT);
  assert.ok(inside(s.win, LEFT) && orbOnScreen(s));
  assert.deepEqual(s.anchor, { x: -1250, y: 30 });
});

test('a saved spot comes back after a restart', () => {
  const saved = { anchor: { x: 100, y: 120 }, offset: { x: 40, y: 50 } };
  const s = pos.restore(saved, [WA], WA);
  assert.deepEqual(s.anchor, saved.anchor);
  assert.deepEqual(s.offset, saved.offset);
});

test('a spot on an unplugged monitor, or a broken file, goes back to the corner', () => {
  const home = pos.restore(null, [WA], WA);
  assert.deepEqual(pos.restore({ anchor: { x: -900, y: 100 }, offset: { x: 40, y: 50 } }, [WA], WA), home);
  assert.deepEqual(pos.restore({ anchor: { x: 'a' }, offset: {} }, [WA], WA), home);
  assert.deepEqual(pos.restore({ anchor: { x: -900, y: 100 }, offset: { x: 40, y: 50 } }, [WA, LEFT], WA).anchor, { x: -900, y: 100 });
});
