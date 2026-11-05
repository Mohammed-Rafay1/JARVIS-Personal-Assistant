// Where the docked orb sits on screen. Pure maths, so it can be tested without Electron.
//
// The overlay window (W x H, transparent, click-through) carries the orb. The orb's top-left corner
// on screen is the "anchor"; "offset" is where the orb sits inside the window. When you drag the orb
// the window moves with it; at a screen edge the window stops and the orb slides inside it instead,
// so the orb can reach every corner and the window never hangs off the screen.
const W = 440, H = 640;
const ORB = 108;   // the docked orb's size (matches style.css)
const PAD = 8;     // the orb never gets closer than this to the screen edge (room for its glow and hover zoom)
const EDGE = 16;   // gap to the edge when it snaps there (also the default corner)
const SNAP = 28;   // let go this close to an edge and it snaps to it

const clamp = (v, lo, hi) => Math.round(Math.max(lo, Math.min(hi, v)));
const num = (v) => typeof v === 'number' && Number.isFinite(v);

// bottom-right corner of the work area, as it always was
function home(wa) {
  const anchor = { x: wa.x + wa.width - EDGE - ORB, y: wa.y + wa.height - EDGE - ORB };
  return { anchor, offset: { x: W - 10 - ORB, y: H - 10 - ORB } };
}

// keep the orb on this screen and put the window around it
function fit(anchor, offset, wa) {
  const a = {
    x: clamp(anchor.x, wa.x + PAD, wa.x + wa.width - ORB - PAD),
    y: clamp(anchor.y, wa.y + PAD, wa.y + wa.height - ORB - PAD),
  };
  const o = { x: clamp(offset.x, PAD, W - ORB - PAD), y: clamp(offset.y, PAD, H - ORB - PAD) };
  const win = {
    x: clamp(a.x - o.x, wa.x, Math.max(wa.x, wa.x + wa.width - W)),
    y: clamp(a.y - o.y, wa.y, Math.max(wa.y, wa.y + wa.height - H)),
    width: W, height: H,
  };
  return { anchor: a, offset: { x: a.x - win.x, y: a.y - win.y }, win };
}

// let go near an edge -> tidy gap to that edge
function snap(anchor, wa) {
  const a = { ...anchor };
  const right = wa.x + wa.width - ORB, bottom = wa.y + wa.height - ORB;
  if (a.x - wa.x < SNAP) a.x = wa.x + EDGE;
  else if (right - a.x < SNAP) a.x = right - EDGE;
  if (a.y - wa.y < SNAP) a.y = wa.y + EDGE;
  else if (bottom - a.y < SNAP) a.y = bottom - EDGE;
  return a;
}

// after a snap: keep the window still and slide the orb inside it (smooth), moving the window only
// if the orb would leave it
function settle(anchor, win, wa) {
  return fit(anchor, { x: anchor.x - win.x, y: anchor.y - win.y }, wa);
}

const contains = (wa, p) => p.x >= wa.x && p.x < wa.x + wa.width && p.y >= wa.y && p.y < wa.y + wa.height;
const centre = (anchor) => ({ x: anchor.x + ORB / 2, y: anchor.y + ORB / 2 });

// a saved spot is used only if it is still on a connected screen
function restore(saved, workAreas, primary) {
  const ok = saved && saved.anchor && saved.offset && [saved.anchor.x, saved.anchor.y, saved.offset.x, saved.offset.y].every(num);
  const wa = ok && workAreas.find((w) => contains(w, centre(saved.anchor)));
  if (!wa) { const h = home(primary); return fit(h.anchor, h.offset, primary); }
  return fit(saved.anchor, saved.offset, wa);
}

module.exports = { W, H, ORB, PAD, EDGE, SNAP, home, fit, snap, settle, restore, centre, contains };
