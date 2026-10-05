// 3D Printing explainer — deterministic motion graphics. render(t) draws frame at time t (seconds).
// Cue times are aligned to pauses in the Jordanian voiceover (vo.wav, 156s).
const C = document.getElementById('c'), X = C.getContext('2d');
const W = 1920, H = 1080, TAU = Math.PI * 2, DUR = 156;
const FONT = '"Avenir Next","Helvetica Neue",Arial,sans-serif';
const P = { org: '#FF7A1A', pink: '#FF3D7F', cyan: '#2EE6D6', yel: '#FFD23F', pur: '#7B5CFF', wht: '#F4F1FF',
  mut: '#8B8FB8', grn: '#3DDC84', red: '#FF4D5E', navy: '#0B0F2A', ink: '#151238', blue: '#3D7BFF' };

// ---------- math / easing ----------
const clamp = (v, a = 0, b = 1) => Math.max(a, Math.min(b, v));
const lerp = (a, b, t) => a + (b - a) * t;
const pr = (t, a, b) => clamp((t - a) / (b - a));
const vis = (t, a, b, f = .3) => clamp(Math.min((t - a) / f, (b - t) / f));
const E = {
  out: t => 1 - Math.pow(1 - t, 3), in: t => t * t * t,
  io: t => t < .5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2,
  back: t => { const c1 = 1.70158, c3 = c1 + 1; return 1 + c3 * Math.pow(t - 1, 3) + c1 * Math.pow(t - 1, 2); },
  el: t => t === 0 ? 0 : t === 1 ? 1 : Math.pow(2, -10 * t) * Math.sin((t * 10 - .75) * TAU / 3) + 1,
};
const pop = (t, a, d = .55) => E.back(pr(t, a, a + d));
const amp = t => { const i = Math.floor(t * 30); return AMP[Math.max(0, Math.min(AMP.length - 1, i))] || 0; };
function rng(seed) { return () => { seed |= 0; seed = seed + 0x6D2B79F5 | 0; let t = Math.imul(seed ^ seed >>> 15, 1 | seed); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }

// ---------- color ----------
const hx = h => { const n = parseInt(h.slice(1), 16); return [n >> 16 & 255, n >> 8 & 255, n & 255]; };
const mixc = (a, b, t) => { a = typeof a === 'string' ? hx(a) : a; b = typeof b === 'string' ? hx(b) : b; return [lerp(a[0], b[0], t), lerp(a[1], b[1], t), lerp(a[2], b[2], t)]; };
const rgb = (c, al = 1) => `rgba(${c[0] | 0},${c[1] | 0},${c[2] | 0},${al})`;
const mix = (a, b, t, al = 1) => rgb(mixc(a, b, t), al);
const hexA = (h, a) => rgb(hx(h), a);
const hsl = (h, s, l, a = 1) => `hsla(${((h % 360) + 360) % 360},${s}%,${l}%,${a})`;

// ---------- drawing primitives ----------
function rr(x, y, w, h, r) { X.beginPath(); X.roundRect(x, y, w, h, r); }
function circ(x, y, r) { X.beginPath(); X.arc(x, y, Math.max(0, r), 0, TAU); }
function ell(x, y, rx, ry) { X.beginPath(); X.ellipse(x, y, Math.max(0, rx), Math.max(0, ry), 0, 0, TAU); }
function fillC(c) { X.fillStyle = c; X.fill(); }
function strokeC(c, w) { X.strokeStyle = c; X.lineWidth = w; X.stroke(); }
function G(x, y, s, r, a, fn) { if (s <= .001 || a <= .001) return; X.save(); X.translate(x, y); if (r) X.rotate(r); X.scale(s, s); X.globalAlpha *= clamp(a); fn(); X.restore(); }
function txt(s, x, y, size, col, weight = 800, align = 'center', ls = 0) {
  X.font = `${weight} ${size}px ${FONT}`; X.letterSpacing = ls + 'px'; X.textAlign = align; X.textBaseline = 'middle'; X.fillStyle = col; X.fillText(s, x, y); X.letterSpacing = '0px';
}
function poly(pts, close = true) { X.beginPath(); pts.forEach((p, i) => i ? X.lineTo(p[0], p[1]) : X.moveTo(p[0], p[1])); if (close) X.closePath(); }
function arrow(x1, y1, x2, y2, col, w = 8, head = 26) {
  const a = Math.atan2(y2 - y1, x2 - x1); X.save(); X.strokeStyle = col; X.fillStyle = col; X.lineWidth = w; X.lineCap = 'round';
  X.beginPath(); X.moveTo(x1, y1); X.lineTo(x2 - Math.cos(a) * head * .6, y2 - Math.sin(a) * head * .6); X.stroke();
  X.beginPath(); X.moveTo(x2, y2); X.lineTo(x2 - Math.cos(a - .45) * head, y2 - Math.sin(a - .45) * head); X.lineTo(x2 - Math.cos(a + .45) * head, y2 - Math.sin(a + .45) * head); X.closePath(); X.fill(); X.restore();
}
function chip(x, y, label, col, k = 1, size = 34) {
  if (k <= 0) return; X.save(); X.translate(x, y); const s = E.back(clamp(k)); X.scale(s, s); X.globalAlpha *= clamp(k * 2);
  X.font = `800 ${size}px ${FONT}`; X.letterSpacing = (size * .12) + 'px';
  const w = X.measureText(label).width + size * 1.4, h = size * 1.75;
  X.shadowColor = hexA(col, .5); X.shadowBlur = 24; rr(-w / 2, -h / 2, w, h, h / 2); fillC('rgba(14,16,46,0.92)'); X.shadowBlur = 0; strokeC(col, 3);
  X.fillStyle = col; X.textAlign = 'center'; X.textBaseline = 'middle'; X.fillText(label, size * .06, 2); X.letterSpacing = '0px'; X.restore();
}
function callout(t, t0, px, py, lx, ly, label, col, size = 34) {
  const k = pr(t, t0, t0 + .45); if (k <= 0) return; const e = E.out(k);
  X.save(); X.strokeStyle = col; X.lineWidth = 3; X.setLineDash([]); X.beginPath(); X.moveTo(px, py); X.lineTo(lerp(px, lx, e), lerp(py, ly, e)); X.stroke();
  circ(px, py, 9 * E.back(clamp(k * 2))); fillC(col); circ(px, py, 18 * E.out(k)); strokeC(hexA(col, .5 * (1 - k) + .2), 2); X.restore();
  chip(lx, ly, label, col, pr(t, t0 + .2, t0 + .65), size);
}
function burst(x, y, t, t0, col, R = 160, n = 14, dur = .7) {
  const k = pr(t, t0, t0 + dur); if (k <= 0 || k >= 1) return; X.save(); X.translate(x, y); X.strokeStyle = col; X.lineCap = 'round'; X.lineWidth = 7 * (1 - k) + 1;
  for (let i = 0; i < n; i++) { const a = i / n * TAU + .2, r0 = R * (.35 + .7 * E.out(k)), r1 = R * (.5 + 1.0 * E.out(k)); X.beginPath(); X.moveTo(Math.cos(a) * r0, Math.sin(a) * r0); X.lineTo(Math.cos(a) * r1, Math.sin(a) * r1); X.stroke(); }
  X.globalAlpha *= 1 - k; circ(0, 0, R * 1.25 * E.out(k)); X.lineWidth = 4; X.stroke(); X.restore();
}
function star4(x, y, r, col) { if (r <= 0) return; X.beginPath(); X.moveTo(x, y - r); X.quadraticCurveTo(x, y, x + r, y); X.quadraticCurveTo(x, y, x, y + r); X.quadraticCurveTo(x, y, x - r, y); X.quadraticCurveTo(x, y, x, y - r); fillC(col); }
function sparkles(cx, cy, t, t0, seed, n = 10, R = 300, col = P.yel, life = 1.3) {
  const rnd = rng(seed);
  for (let i = 0; i < n; i++) {
    const a = rnd() * TAU, d = R * (.35 + .65 * rnd()), ph = rnd(), sz = rnd();
    const k = pr(t, t0 + ph * .5, t0 + ph * .5 + life); if (k <= 0 || k >= 1) continue;
    star4(cx + Math.cos(a) * d * (.8 + .3 * k), cy + Math.sin(a) * d * (.8 + .3 * k) - k * 30, (8 + 22 * sz) * Math.sin(k * Math.PI), col);
  }
}
function confetti(cx, cy, t, t0, seed, n = 60, spread = 900) {
  const rnd = rng(seed), cols = [P.org, P.pink, P.cyan, P.yel, P.pur, P.grn]; const dt = t - t0; if (dt < 0 || dt > 3.2) return;
  for (let i = 0; i < n; i++) {
    const vx = (rnd() - .5) * spread, vy = -400 - rnd() * 700, rot = rnd() * TAU, vr = (rnd() - .5) * 14, c = cols[i % 6], w = 10 + rnd() * 14;
    const x = cx + vx * dt, y = cy + vy * dt + 900 * dt * dt; X.save(); X.translate(x, y); X.rotate(rot + vr * dt); X.scale(1, Math.cos(dt * 9 + i));
    X.globalAlpha *= clamp(3.2 - dt); X.fillStyle = c; X.fillRect(-w / 2, -w / 4, w, w / 2); X.restore();
  }
}
function gear(x, y, r, teeth, rot, col, hole = .35) {
  X.save(); X.translate(x, y); X.rotate(rot); X.beginPath();
  for (let i = 0; i < teeth * 2; i++) { const a0 = i / (teeth * 2) * TAU, a1 = (i + 1) / (teeth * 2) * TAU, rr2 = i % 2 ? r * .82 : r; X.arc(0, 0, rr2, a0, a1); }
  X.closePath(); X.moveTo(r * hole, 0); X.arc(0, 0, r * hole, 0, TAU, true); X.fillStyle = col; X.fill('evenodd'); X.restore();
}
function bubble(x, y, r, k, dir = -1) {
  if (k <= 0) return; G(x, y, E.back(clamp(k)), 0, clamp(k * 3), () => {
    X.shadowColor = 'rgba(0,0,0,.35)'; X.shadowBlur = 30; circ(0, 0, r); fillC('#F4F1FF'); X.shadowBlur = 0;
    circ(dir * r * .75, r * 1.05, r * .2); fillC('#F4F1FF'); circ(dir * r * 1.0, r * 1.45, r * .1); fillC('#F4F1FF');
  });
}
function qmark(x, y, size, col, k = 1, rot = 0) { if (k <= 0) return; G(x, y, E.back(clamp(k)), rot, clamp(k * 2), () => txt('?', 0, size * .04, size, col, 900)); }

// ---------- 3D layer-stack engine (the visual language of the video) ----------
const OC = document.createElement('canvas'); OC.width = W; OC.height = H; const O = OC.getContext('2d');
function proj(x, y, z, c) {
  const cy = Math.cos(c.yaw), sy = Math.sin(c.yaw), X1 = x * cy - y * sy, Y1 = x * sy + y * cy;
  const cp = Math.cos(c.pitch), sp = Math.sin(c.pitch), d = Y1 * cp - z * sp, D = c.dist || 1800, f = D / (D + d);
  return [c.cx + X1 * f * c.s, c.cy + (-z * cp - Y1 * sp) * f * c.s];
}
function circC(r, N = 72) { const o = []; for (let k = 0; k < N; k++) { const a = k / N * TAU; o.push([Math.cos(a) * r, Math.sin(a) * r]); } return o; }
const heroC = (u) => { const N = 96, R = 92 * (.8 + .42 * Math.sin(u * Math.PI * .95 + .35)), tw = u * 2.4, o = []; for (let k = 0; k < N; k++) { const a = k / N * TAU, r = R * (1 + .13 * Math.cos(6 * a + tw)); o.push([Math.cos(a) * r, Math.sin(a) * r]); } return o; };
const bulbR = z => { let r; if (z < 70) r = 46 + ((Math.floor(z / 10) % 2) ? 4 : 0); else { const neck = lerp(46, 76, E.io(clamp((z - 70) / 50))); const g = Math.sqrt(Math.max(0, 105 * 105 - (z - 195) ** 2)); r = z < 120 ? neck : g; } return Math.max(r, 3); };
const bulbC = (u) => circC(bulbR(u * 300), 72);
const heroColor = (u, i, top) => hsl(lerp(24, -28, u), 95, (top ? 70 : 57) + (i % 2 ? -6 : 0));
function stack(o) {
  const c = { cx: o.cx, cy: o.cy, s: o.s, yaw: o.yaw || 0, pitch: o.pitch ?? .5, dist: o.dist || 1800 };
  const n = o.n, full = clamp(o.prog ?? 1) * n, nf = Math.floor(full + 1e-6), fr = full - nf, gap = o.gap || 0, step = o.step || 1;
  O.setTransform(1, 0, 0, 1, 0, 0); O.clearRect(0, 0, W, H); O.setTransform(X.getTransform());
  let partial = null; const lastFull = Math.min(nf, n) - 1;
  for (let i = 0; i < n; i++) {
    if (i > nf || (i === nf && fr <= .001)) break;
    const z = i * (o.lh + gap), pts = o.contour(i / n, i * o.lh, i);
    if (i === nf) { const m = Math.max(2, Math.ceil(fr * pts.length)); partial = pts.slice(0, m).map(p => proj(p[0], p[1], z, c)); break; }
    if (step > 1 && (i % step) && i !== lastFull) continue;
    const q = pts.map(p => proj(p[0], p[1], z, c));
    O.beginPath(); O.moveTo(q[0][0], q[0][1]); for (let k = 1; k < q.length; k++) O.lineTo(q[k][0], q[k][1]); O.closePath();
    O.fillStyle = o.color(i / n, i, i === lastFull); O.fill();
    O.strokeStyle = o.edgeCol || 'rgba(0,0,0,0.22)'; O.lineWidth = o.edge ?? 1; O.stroke();
  }
  if (o.shade !== false && nf > 0) {
    O.globalCompositeOperation = 'source-atop'; const R = (o.R || 120) * c.s;
    const g = O.createLinearGradient(c.cx - R, 0, c.cx + R, 0);
    g.addColorStop(0, 'rgba(10,6,40,.55)'); g.addColorStop(.3, 'rgba(255,255,255,.05)'); g.addColorStop(.42, 'rgba(255,255,255,.22)'); g.addColorStop(.6, 'rgba(0,0,0,0)'); g.addColorStop(1, 'rgba(10,6,40,.6)');
    O.fillStyle = g; O.fillRect(c.cx - R - 400, -5000, 2 * R + 800, 10000); O.globalCompositeOperation = 'source-over';
  }
  X.save(); X.setTransform(1, 0, 0, 1, 0, 0); X.drawImage(OC, 0, 0); X.restore();
  let tip;
  if (partial) {
    X.save(); X.lineJoin = 'round'; X.lineCap = 'round'; X.strokeStyle = o.bead || '#FFE7C2'; X.shadowColor = o.beadGlow || P.org; X.shadowBlur = 22;
    X.lineWidth = Math.max(3, o.lh * c.s * .95); poly(partial, false); X.stroke(); X.restore(); tip = partial[partial.length - 1];
  } else { const i = Math.min(nf, n - 1), pts = o.contour(i / n, i * o.lh, i); tip = proj(pts[0][0], pts[0][1], Math.min(nf, n) * (o.lh + gap), c); }
  return { tip, cam: c };
}
function plate(c, R, col = '#1B1F4A', grid = P.cyan, a = 1) {
  const corners = [[-R, -R], [R, -R], [R, R], [-R, R]].map(p => proj(p[0], p[1], 0, c));
  X.save(); X.globalAlpha *= a; poly(corners); X.shadowColor = hexA(P.cyan, .35); X.shadowBlur = 40; fillC(col); X.shadowBlur = 0; strokeC(hexA(grid, .7), 3);
  X.strokeStyle = hexA(grid, .16); X.lineWidth = 1.5;
  for (let v = -R + R / 7; v < R; v += R / 7) { let a1 = proj(v, -R, 0, c), b1 = proj(v, R, 0, c); X.beginPath(); X.moveTo(...a1); X.lineTo(...b1); X.stroke(); a1 = proj(-R, v, 0, c); b1 = proj(R, v, 0, c); X.beginPath(); X.moveTo(...a1); X.lineTo(...b1); X.stroke(); }
  X.restore();
}

// ---------- illustrated objects ----------
function hotend(x, y, s = 1, heat = 0, fil = P.org, filLen = 400) {
  X.save(); X.translate(x, y); X.scale(s, s);
  if (fil) { X.strokeStyle = fil; X.lineWidth = 9; X.lineCap = 'round'; X.beginPath(); X.moveTo(0, -150); X.lineTo(0, -filLen); X.stroke(); }
  for (let i = 0; i < 5; i++) { rr(-40, -168 + i * 14, 80, 9, 3); fillC(i % 2 ? '#8E97C0' : '#A7B0D6'); }
  rr(-11, -100, 22, 24, 3); fillC('#C9CFE8');
  if (heat > 0) { X.shadowColor = '#FF4A1F'; X.shadowBlur = 50 * heat; }
  rr(-46, -80, 92, 48, 8); fillC(mix('#B9C1DD', '#FF5A2A', heat)); X.shadowBlur = 0;
  rr(-46, -80, 92, 10, 6); fillC('rgba(255,255,255,.28)'); circ(26, -56, 7); fillC('rgba(0,0,0,.25)');
  rr(-24, -36, 48, 10, 3); fillC('#D99A2B');
  X.beginPath(); X.moveTo(-18, -27); X.lineTo(18, -27); X.lineTo(13, -14); X.lineTo(5, 0); X.lineTo(-5, 0); X.lineTo(-13, -14); X.closePath(); fillC('#F5B942');
  X.beginPath(); X.moveTo(-6, -26); X.lineTo(-2, -26); X.lineTo(-1, -4); X.lineTo(-4, -4); X.closePath(); fillC('rgba(255,255,255,.5)');
  if (heat > .3) { circ(0, 2, 6 * heat); X.shadowColor = P.org; X.shadowBlur = 20; fillC('#FFD9A0'); X.shadowBlur = 0; }
  X.restore();
}
function spool(x, y, r, rot, col = P.org) {
  X.save(); X.translate(x, y); X.rotate(rot);
  circ(0, 0, r); fillC('#2A2F66'); circ(0, 0, r * .9); fillC(col);
  X.strokeStyle = 'rgba(0,0,0,.18)'; X.lineWidth = 2; for (let i = 1; i < 9; i++) { circ(0, 0, r * (.42 + i * .055)); X.stroke(); }
  circ(0, 0, r * .4); fillC('#3A418A'); circ(0, 0, r * .16); fillC('#0B0F2A');
  for (let i = 0; i < 3; i++) { X.save(); X.rotate(i * TAU / 3); rr(-r * .05, -r * .4, r * .1, r * .26, 4); fillC('#0B0F2A'); X.restore(); }
  X.beginPath(); X.arc(0, 0, r * .78, -2.2, -1.4); X.strokeStyle = 'rgba(255,255,255,.35)'; X.lineWidth = r * .07; X.lineCap = 'round'; X.stroke();
  X.restore();
}
function printer(x, y, s, o = {}) {
  X.save(); X.translate(x, y); X.scale(s, s);
  const gy = o.gy ?? -330, hx = o.hx ?? 0;
  ell(0, 6, 320, 28); fillC('rgba(0,0,0,.35)');
  rr(-252, -610, 38, 610, 10); fillC('#2E3470'); rr(214, -610, 38, 610, 10); fillC('#2E3470');
  rr(-252, -610, 10, 610, 6); fillC('rgba(255,255,255,.08)');
  rr(-270, -640, 540, 44, 14); fillC('#3A418A'); rr(-270, -640, 540, 10, 8); fillC('rgba(255,255,255,.15)');
  rr(-285, -64, 570, 64, 16); fillC('#3A418A'); rr(-285, -64, 570, 12, 8); fillC('rgba(255,255,255,.14)');
  rr(-240, -50, 130, 34, 7); fillC('#0B0F2A'); rr(-233, -43, 116, 20, 4); fillC(hexA(P.cyan, o.led ?? .85));
  circ(-80, -33, 12); fillC('#5A62B0'); circ(220, -33, 7); fillC(o.ledOn === false ? '#334' : P.grn);
  rr(-190, -106, 380, 20, 6); fillC('#20244F'); rr(-205, -124, 410, 20, 6); fillC('#C9CFE8'); rr(-205, -124, 410, 6, 4); fillC('#FFFFFF');
  if (o.obj) o.obj(-124);
  // spool + filament
  const sx = 150, sy = -700; rr(sx - 8, -640, 16, -30, 4); fillC('#2E3470');
  X.strokeStyle = o.fil || P.org; X.lineWidth = 7; X.lineCap = 'round'; X.beginPath(); X.moveTo(sx - 60, sy + 10); X.bezierCurveTo(sx - 160, sy + 40, hx + 30, gy - 160, hx, gy - 40); X.stroke();
  spool(sx, sy, 72, o.spoolRot || 0, o.fil || P.org);
  rr(-236, gy - 15, 472, 30, 10); fillC('#4B53A8'); rr(-236, gy - 15, 472, 8, 6); fillC('rgba(255,255,255,.2)');
  hotend(hx, gy + 118, .58, o.heat || 0, null);
  rr(hx - 52, gy - 42, 104, 66, 14); fillC(o.carriage || P.org); rr(hx - 52, gy - 42, 104, 14, 8); fillC('rgba(255,255,255,.28)');
  circ(hx + 30, gy - 10, 6); fillC(o.ledOn === false ? '#552' : '#FFF3C4');
  X.restore();
  return [x + hx * s, y + (gy + 118) * s];
}
function bulb(x, y, s, on = 1, col = P.yel) {
  X.save(); X.translate(x, y); X.scale(s, s);
  if (on > 0) { const g = X.createRadialGradient(0, -25, 0, 0, -25, 220); g.addColorStop(0, hexA(col, .6 * on)); g.addColorStop(1, hexA(col, 0)); X.fillStyle = g; X.fillRect(-230, -250, 460, 460); }
  X.beginPath(); X.arc(0, -30, 62, Math.PI * .8, Math.PI * 2.2); X.lineTo(26, 40); X.lineTo(-26, 40); X.closePath();
  if (on > .3) { X.shadowColor = col; X.shadowBlur = 50 * on; } fillC(mix('#3A3F6B', '#FFF4B8', on)); X.shadowBlur = 0; strokeC(mix('#6870A8', '#FFFFFF', on), 4);
  rr(-28, 42, 56, 14, 5); fillC('#A7AECF'); rr(-25, 58, 50, 12, 5); fillC('#8A91B5'); rr(-15, 72, 30, 10, 5); fillC('#6E7599');
  X.strokeStyle = on > .5 ? mix(P.org, '#FFFFFF', on) : '#8088B8'; X.lineWidth = 4; X.lineJoin = 'round';
  X.beginPath(); X.moveTo(-14, 38); X.lineTo(-14, 2); X.lineTo(-7, -14); X.lineTo(0, 2); X.lineTo(7, -14); X.lineTo(14, 2); X.lineTo(14, 38); X.stroke();
  X.beginPath(); X.arc(0, -30, 46, -2.6, -1.9); X.strokeStyle = 'rgba(255,255,255,.7)'; X.lineWidth = 7; X.lineCap = 'round'; X.stroke();
  X.restore();
}
function glassIcon() {
  X.beginPath(); X.moveTo(-44, -62); X.lineTo(44, -62); X.lineTo(35, 62); X.lineTo(-35, 62); X.closePath(); fillC('rgba(170,220,255,0.16)');
  X.beginPath(); X.moveTo(-40, -18); X.quadraticCurveTo(0, -10, 40, -18); X.lineTo(35, 58); X.lineTo(-35, 58); X.closePath(); fillC(hexA(P.org, .9));
  ell(0, -18, 40, 8); fillC('#FFB066');
  X.beginPath(); X.moveTo(-44, -62); X.lineTo(44, -62); X.lineTo(35, 62); X.lineTo(-35, 62); X.closePath(); strokeC('rgba(225,240,255,.95)', 5);
  ell(0, -62, 44, 9); strokeC('rgba(225,240,255,.95)', 4);
  rr(-32, -48, 9, 92, 5); fillC('rgba(255,255,255,.4)');
}
function caseIcon() {
  X.save(); rr(-38, -68, 76, 136, 18); fillC(P.pink); X.clip(); X.strokeStyle = 'rgba(255,255,255,.18)'; X.lineWidth = 8;
  for (let i = -6; i < 9; i++) { X.beginPath(); X.moveTo(-42, i * 22); X.lineTo(42, i * 22 - 44); X.stroke(); } X.restore();
  rr(-38, -68, 76, 136, 18); strokeC('#FF9CC0', 4);
  rr(-29, -59, 32, 36, 10); fillC('#B8204F'); circ(-20, -49, 6.5); fillC('#2A1030'); circ(-7, -33, 6.5); fillC('#2A1030');
}
function chargerIcon() {
  rr(-13, -82, 7, 24, 2); fillC('#C9CFE8'); rr(6, -82, 7, 24, 2); fillC('#C9CFE8');
  rr(-42, -62, 84, 78, 18); fillC('#F4F1FF'); rr(-42, -8, 84, 24, 12); fillC('#D5D8EE');
  X.beginPath(); X.moveTo(4, -50); X.lineTo(-12, -24); X.lineTo(0, -24); X.lineTo(-6, 0); X.lineTo(12, -30); X.lineTo(0, -30); X.closePath(); fillC(P.cyan);
  X.strokeStyle = '#F4F1FF'; X.lineWidth = 10; X.lineCap = 'round'; X.beginPath(); X.moveTo(0, 16); X.bezierCurveTo(0, 64, -56, 44, -44, 92); X.stroke();
}
function drawCyl(cx, cy, rx, ry, h, top, side) {
  side = typeof side === 'string' ? hx(side) : side; top = typeof top === 'string' ? top : rgb(top);
  X.beginPath(); X.moveTo(cx - rx, cy - h); X.lineTo(cx - rx, cy); X.ellipse(cx, cy, rx, ry, 0, Math.PI, 0, true); X.lineTo(cx + rx, cy - h); X.closePath();
  const g = X.createLinearGradient(cx - rx, 0, cx + rx, 0); g.addColorStop(0, mix(side, '#000000', .18)); g.addColorStop(.4, rgb(side)); g.addColorStop(.55, mix(side, '#FFFFFF', .18)); g.addColorStop(1, mix(side, '#000000', .22)); X.fillStyle = g; X.fill();
  ell(cx, cy - h, rx, ry); fillC(top);
}
function avatar(x, y, s, skin, shirt, hair, i) {
  G(x, y, s, 0, 1, () => {
    X.beginPath(); X.moveTo(-90, 120); X.bezierCurveTo(-90, 20, 90, 20, 90, 120); X.closePath(); fillC(shirt);
    rr(-18, -10, 36, 40, 10); fillC(mix(skin, '#000000', .15));
    circ(0, -50, 52); fillC(skin);
    X.beginPath(); if (i % 3 === 0) { X.arc(0, -58, 54, Math.PI * 1.02, TAU * .99); X.closePath(); } else if (i % 3 === 1) { X.arc(0, -50, 56, Math.PI * .9, Math.PI * 2.1); X.lineTo(40, -60); X.quadraticCurveTo(0, -80, -40, -60); X.closePath(); } else { X.ellipse(0, -96, 40, 22, 0, 0, TAU); X.moveTo(-54, -62); X.arc(0, -56, 54, Math.PI, TAU); X.closePath(); }
    fillC(hair); circ(-18, -48, 5); fillC('#1A1030'); circ(18, -48, 5); fillC('#1A1030');
    X.beginPath(); X.arc(0, -34, 14, .2, Math.PI - .2); strokeC('#1A1030', 4);
  });
}

// ---------- background ----------
const BGP = (() => { const r = rng(7), a = []; for (let i = 0; i < 46; i++) a.push({ x: r() * W, y: r() * H, s: 2 + r() * 5, v: 6 + r() * 22, c: [P.org, P.pink, P.cyan, P.pur, P.yel][i % 5], k: r() < .5 ? 0 : 1, ph: r() * TAU }); return a; })();
function background(t, tint) {
  const g = X.createLinearGradient(0, 0, W * .3, H); g.addColorStop(0, '#0C1030'); g.addColorStop(1, '#170E3C'); X.fillStyle = g; X.fillRect(0, 0, W, H);
  const a = .2 + .14 * amp(t), rg = X.createRadialGradient(W / 2, H * .46, 0, W / 2, H * .46, 1150);
  rg.addColorStop(0, rgb(tint, a)); rg.addColorStop(1, rgb(tint, 0)); X.fillStyle = rg; X.fillRect(0, 0, W, H);
  const sp = 54, ox = (t * 9) % sp, oy = (t * 5) % sp; X.fillStyle = 'rgba(255,255,255,0.05)';
  for (let x = -sp; x < W + sp; x += sp) for (let y = -sp; y < H + sp; y += sp) X.fillRect(x + ox, y + oy, 3, 3);
  for (const p of BGP) {
    const y = ((p.y - t * p.v) % (H + 40) + H + 40) % (H + 40) - 20, x = p.x + Math.sin(t * .5 + p.ph) * 20;
    X.globalAlpha = .22; X.fillStyle = p.c; if (p.k) X.fillRect(x, y, p.s, p.s); else { X.fillRect(x - p.s, y - 1, p.s * 2, 2); X.fillRect(x - 1, y - p.s, 2, p.s * 2); }
  }
  X.globalAlpha = 1;
}
function vignette() { const g = X.createRadialGradient(W / 2, H / 2, H * .45, W / 2, H / 2, H * 1.05); g.addColorStop(0, 'rgba(0,0,0,0)'); g.addColorStop(1, 'rgba(0,0,0,.55)'); X.fillStyle = g; X.fillRect(0, 0, W, H); }

// =====================================================================
// SCENES
// =====================================================================

// 1 — Hook: "شوف حواليك هيك… الكاسة، كفر الموبايل، الشاحن" (0 – 5.55)
function sHook(t) {
  const cx = W / 2, cy = H / 2, ra = 1 - pr(t, 1.4, 2.3);
  if (ra > 0) {
    X.save(); X.globalAlpha *= ra;
    for (let i = 0; i < 4; i++) { const ph = (t * .8 + i / 4) % 1; circ(cx, cy, 60 + ph * 760); strokeC(hexA(P.cyan, .45 * (1 - ph)), 3); }
    const cg = X.createConicGradient(t * 3.4, cx, cy); cg.addColorStop(0, hexA(P.cyan, 0)); cg.addColorStop(.86, hexA(P.cyan, 0)); cg.addColorStop(1, hexA(P.cyan, .32));
    X.fillStyle = cg; circ(cx, cy, 820); X.fill();
    G(cx, cy, 1 + .06 * Math.sin(t * 6), 0, 1, () => { ell(0, 0, 120, 70); fillC('#F4F1FF'); circ(Math.sin(t * 2.4) * 46, 0, 44); fillC('#1A1440'); circ(Math.sin(t * 2.4) * 46 + 14, -14, 12); fillC('#FFF'); });
    X.restore();
  }
  const items = [[520, 560, 1.81, glassIcon, P.org], [960, 560, 3.34, caseIcon, P.pink], [1400, 560, 4.50, chargerIcon, P.cyan]];
  items.forEach(([x, y, t0, fn, col], i) => {
    const k = pop(t, t0, .6); if (k <= 0) return; const bob = Math.sin(t * 2.2 + i) * 10;
    G(x, y + 210, 1, 0, clamp(k), () => { ell(0, 0, 170 * k, 40 * k); const g = X.createRadialGradient(0, 0, 0, 0, 0, 170); g.addColorStop(0, hexA(col, .55)); g.addColorStop(1, hexA(col, 0)); X.fillStyle = g; X.fill(); ell(0, 0, 150 * k, 30 * k); strokeC(hexA(col, .8), 3); });
    G(x, y + bob, 2.3 * k, Math.sin(t * 1.6 + i) * .06, 1, fn);
    burst(x, y, t, t0, col, 200, 16);
  });
}

// 2 — Factory: "بمصنع كبير، بمكنات غالية، وآلاف القطع" (5.55 – 12.4)
function sFactory(t) {
  const zo = E.io(pr(t, 10.15, 11.3)), sc = lerp(1, .5, zo);
  X.save(); X.translate(W / 2, H / 2); X.scale(sc, sc); X.translate(-W / 2, -H / 2); X.globalAlpha *= 1 - .8 * zo;
  // skyline
  X.fillStyle = '#1D1652';
  for (let i = 0; i < 6; i++) { const bx = -40 + i * 340; X.beginPath(); X.moveTo(bx, 760); X.lineTo(bx, 470); for (let k = 0; k < 3; k++) { X.lineTo(bx + k * 110 + 80, 400); X.lineTo(bx + k * 110 + 110, 470); } X.lineTo(bx + 330, 760); X.closePath(); X.fill(); }
  [[300, 250], [780, 210], [1500, 240]].forEach(([x, top], i) => {
    rr(x, top, 54, 300, 6); fillC('#241B63'); rr(x - 6, top, 66, 20, 4); fillC('#2E2478');
    for (let k = 0; k < 6; k++) { const ph = ((t * .5 + k / 6 + i * .3) % 1); circ(x + 27 + ph * 80 + Math.sin(ph * 6) * 12, top - 20 - ph * 220, 26 + ph * 40); fillC(`rgba(150,140,220,${.22 * (1 - ph)})`); }
  });
  gear(170, 200, 95, 12, t * 1.1, '#2C2470'); gear(300, 300, 62, 9, -t * 1.7 - .2, '#3A2F8F'); gear(1760, 230, 110, 14, -t * .9, '#2C2470');
  // conveyor
  const by = 800; rr(-60, by, W + 120, 44, 22); fillC('#2B2F66');
  X.save(); X.setLineDash([36, 22]); X.lineDashOffset = -(t * 230); X.strokeStyle = '#4B53A8'; X.lineWidth = 6; X.beginPath(); X.moveTo(-60, by + 4); X.lineTo(W + 60, by + 4); X.stroke(); X.restore();
  for (let x = 20; x < W; x += 120) { G(x, by + 22, 1, t * 5, 1, () => { circ(0, 0, 16); fillC('#3A418A'); rr(-2, -14, 4, 28, 2); fillC('#1B1F4A'); }); }
  rr(-60, by + 44, W + 120, 200, 0); fillC('#120E33');
  // items
  const sp = 240, speed = 230, fns = [glassIcon, caseIcon, chargerIcon]; let dmin = 999;
  for (let i = 0; i < 11; i++) {
    const x = ((i * sp + (t - 5.55) * speed) % (sp * 11)) - 260; dmin = Math.min(dmin, Math.abs(x - 960));
    if (x < 960) { G(x, by - 52, 1, 0, 1, () => { rr(-46, -46, 92, 92, 18); fillC('#4A4F86'); rr(-46, -46, 92, 20, 12); fillC('#5D63A0'); }); }
    else G(x, by - 80, .95, 0, 1, fns[i % 3]);
  }
  // press machine
  const down = 70 * clamp(1 - dmin / 46);
  rr(1016 - 160, 410, 210, 230, 26); fillC('#3A418A'); rr(856, 410, 210, 30, 16); fillC('#4B53A8');
  for (let k = 0; k < 3; k++) { circ(900 + k * 40, 470, 10); fillC([P.red, P.yel, P.grn][k]); if ((Math.floor(t * 3) + k) % 3 === 0) { circ(900 + k * 40, 470, 18); fillC(hexA([P.red, P.yel, P.grn][k], .3)); } }
  rr(935, 640, 50, 30 + down, 6); fillC('#8E97C0'); rr(900, 664 + down, 120, 26, 8); fillC('#C9CFE8');
  if (down > 60) { burst(960, by - 40, t, t - .05, P.yel, 90, 10, .5); }
  // expensive machines
  const pk = pop(t, 8.86, .6);
  if (pk > 0) {
    G(1180, 470, pk, Math.sin((t - 8.86) * 5) * .25 * Math.exp(-(t - 8.86) * 1.5), 1, () => {
      X.strokeStyle = '#C9CFE8'; X.lineWidth = 3; X.beginPath(); X.moveTo(-60, -30); X.lineTo(-10, 0); X.stroke();
      X.beginPath(); X.moveTo(0, -40); X.lineTo(160, -40); X.lineTo(200, 0); X.lineTo(160, 40); X.lineTo(0, 40); X.closePath(); X.shadowColor = hexA(P.grn, .6); X.shadowBlur = 30; fillC(P.grn); X.shadowBlur = 0;
      circ(20, 0, 8); fillC('#14112F'); txt('$$$', 100, 2, 46, '#0B2A18', 900);
    });
    for (let i = 0; i < 6; i++) {
      const dt = t - 9.0 - i * .12; if (dt < 0 || dt > 1.6) continue; const vx = (i - 2.5) * 110, vy = -620;
      G(960 + vx * dt, 420 + vy * dt + 900 * dt * dt, 1, 0, clamp(1.6 - dt), () => { X.scale(Math.cos(dt * 10 + i), 1); circ(0, 0, 28); fillC(P.yel); circ(0, 0, 20); strokeC('#C9971A', 4); txt('$', 0, 2, 26, '#9A6F00', 900); });
    }
  }
  X.restore();
  // thousands of copies
  const tk = pr(t, 10.15, 11.6);
  if (tk > 0) {
    const cols = 30, rows = 16, gx = W / cols, gy = H / rows, cl = [P.org, P.pink, '#F4F1FF'];
    for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) {
      const x = gx * (c + .5), y = gy * (r + .5), d = Math.hypot(x - W / 2, y - H / 2) / 1100, k = E.back(pr(t, 10.15 + d * 1.1, 10.15 + d * 1.1 + .35)); if (k <= 0) continue;
      const ty = (r * 7 + c * 3) % 3; G(x, y, k * .5, 0, 1, () => { if (ty === 0) { X.beginPath(); X.moveTo(-26, -34); X.lineTo(26, -34); X.lineTo(20, 34); X.lineTo(-20, 34); X.closePath(); fillC(cl[0]); } else if (ty === 1) { rr(-20, -36, 40, 72, 10); fillC(cl[1]); } else { rr(-26, -26, 52, 52, 12); fillC(cl[2]); } });
    }
    const n = Math.floor(E.out(pr(t, 10.6, 11.8)) * 10000);
    chip(W / 2, H / 2, '× ' + n.toLocaleString('en-US'), P.yel, pr(t, 10.5, 10.9), 64);
  }
}

// 3 — Room: "إنك بتقدر تعمل أي إشي… وإنت قاعد بغرفتك؟" (12.4 – 18.6)
const STARS = (() => { const r = rng(21), a = []; for (let i = 0; i < 40; i++) a.push([230 + r() * 400, 170 + r() * 380, r() * TAU, 1 + r() * 2.5]); return a; })();
function sRoom(t) {
  const sc = lerp(1.25, 1, E.out(pr(t, 12.2, 13.8))) * (1 + .05 * pr(t, 13.8, 18.6));
  X.save(); X.translate(W / 2, H / 2); X.scale(sc, sc); X.translate(-W / 2, -H / 2);
  X.fillStyle = '#17113F'; X.fillRect(-200, -200, W + 400, 1020); X.fillStyle = '#100B2E'; X.fillRect(-200, 820, W + 400, 500);
  // window
  rr(210, 150, 440, 420, 18); fillC('#2A2160');
  const sk = X.createLinearGradient(0, 170, 0, 550); sk.addColorStop(0, '#0A0C2E'); sk.addColorStop(1, '#2B1D6B'); rr(230, 170, 400, 380, 10); X.fillStyle = sk; X.fill();
  X.save(); rr(230, 170, 400, 380, 10); X.clip();
  for (const [x, y, ph, s] of STARS) { X.globalAlpha = .4 + .6 * Math.abs(Math.sin(t * 1.5 + ph)); star4(x, y, s * 2, '#FFFFFF'); }
  X.globalAlpha = 1; circ(520, 270, 50); X.shadowColor = '#FFF3C4'; X.shadowBlur = 60; fillC('#FFF3C4'); X.shadowBlur = 0; circ(542, 256, 44); fillC('#151445');
  X.fillStyle = '#120E33'; for (let i = 0; i < 8; i++) X.fillRect(230 + i * 52, 470 - (i * 37 % 70), 44, 120);
  X.restore();
  X.fillStyle = '#2A2160'; X.fillRect(425, 170, 10, 380); X.fillRect(230, 355, 400, 10);
  // shelf + books + plant
  rr(1300, 300, 420, 18, 6); fillC('#3B2F7A');
  [[1320, 70, P.org], [1360, 90, P.pur], [1405, 80, P.cyan], [1446, 96, P.pink], [1492, 64, P.yel]].forEach(([x, h, c]) => { rr(x, 300 - h, 36, h, 5); fillC(c); X.globalAlpha = .9; });
  X.globalAlpha = 1; rr(1590, 240, 60, 60, 10); fillC('#C2245A'); [[-.6, 0], [0, -.2], [.6, 0]].forEach(([a]) => { G(1620, 240, 1, a, 1, () => { ell(0, -50, 16, 50); fillC(P.grn); }); });
  // poster
  rr(780, 210, 260, 330, 14); fillC('#221A5C'); G(910, 380, .55, 0, 1, () => { X.globalAlpha *= .9; bulb(0, 0, 1, .4); });
  // desk
  rr(660, 820 - 30, 1240, 34, 8); fillC('#3D2C78'); rr(700, 824, 30, 260, 6); fillC('#2C2160'); rr(1820, 824, 30, 260, 6); fillC('#2C2160');
  // lamp
  const lampOn = .75 + .25 * Math.sin(t * 9) * (t < 13 ? 1 : 0);
  X.save(); X.globalAlpha *= .35 * lampOn; X.beginPath(); X.moveTo(1640, 470); X.lineTo(1470, 790); X.lineTo(1830, 790); X.closePath(); const lg = X.createLinearGradient(0, 470, 0, 790); lg.addColorStop(0, hexA(P.yel, .9)); lg.addColorStop(1, hexA(P.yel, 0)); X.fillStyle = lg; X.fill(); X.restore();
  rr(1680, 770, 120, 20, 8); fillC('#4B53A8'); X.strokeStyle = '#4B53A8'; X.lineWidth = 12; X.lineCap = 'round'; X.beginPath(); X.moveTo(1740, 775); X.lineTo(1700, 580); X.lineTo(1650, 470); X.stroke();
  G(1650, 460, 1, -.5, 1, () => { X.beginPath(); X.moveTo(-50, 20); X.lineTo(50, 20); X.lineTo(26, -40); X.lineTo(-26, -40); X.closePath(); fillC(P.org); ell(0, 20, 50, 10); fillC('#FFF3C4'); });
  // printer on desk
  const on = pr(t, 17.6, 17.9);
  printer(1150, 790, .62, { led: .2 + .7 * on, ledOn: on > .5, hx: -60, spoolRot: 0 });
  // imagined objects (ghost outlines)
  const gk = pr(t, 14.2, 14.9) * (1 - pr(t, 18.2, 18.6));
  if (gk > 0) {
    [[980, 360, glassIcon], [1150, 300, caseIcon], [1320, 360, chargerIcon]].forEach(([x, y, fn], i) => {
      const k = E.back(pr(t, 14.2 + i * .2, 14.8 + i * .2)); G(x, y + Math.sin(t * 2 + i) * 8, .85 * k, 0, .55 * gk, () => { X.setLineDash([10, 10]); X.lineDashOffset = -t * 30; fn(); X.setLineDash([]); });
    });
  }
  // question bubble
  bubble(800, 330, 90, pr(t, 16.2, 16.7), 1); qmark(800, 330, 120, P.pink, pr(t, 16.35, 16.85), Math.sin(t * 3) * .1);
  X.restore();
}

// 4 — Title: "أهلاً فيك بعالم الـ 3D printing… الطباعة ثلاثية الأبعاد" (18.6 – 25.2)
function extText(str, x, y, size, L, dy, prog, cBot, cTop, ls = 0) {
  X.save(); X.font = `900 ${size}px ${FONT}`; X.letterSpacing = ls + 'px'; X.textAlign = 'center'; X.textBaseline = 'alphabetic';
  const w = X.measureText(str).width, left = x - w / 2, full = prog * L, nf = Math.floor(full), fr = full - nf; let tip = null;
  for (let i = 0; i <= Math.min(nf, L - 1); i++) {
    const yy = y - i * dy, c = mixc(cBot, cTop, i / L), top = i === L - 1;
    if (i === nf) { if (fr <= 0) break; X.save(); X.beginPath(); X.rect(left - 30, yy - size * 1.3, (w + 60) * fr, size * 1.7); X.clip(); X.fillStyle = rgb(mixc(c, '#FFFFFF', .5)); X.fillText(str, x, yy); X.restore(); tip = [left - 30 + (w + 60) * fr, yy - size * .74]; break; }
    X.fillStyle = top ? '#FFF5DE' : rgb(mixc(c, '#000000', i % 2 ? .12 : 0)); if (top) { X.shadowColor = hexA(P.yel, .6); X.shadowBlur = 40; }
    X.fillText(str, x, yy); X.shadowBlur = 0;
  }
  X.restore(); return { tip, w, left };
}
function sTitle(t) {
  // rays
  X.save(); X.translate(W / 2, H / 2); X.rotate(t * .08); X.globalAlpha *= .5 * pr(t, 18.6, 19.4);
  for (let i = 0; i < 18; i++) { X.rotate(TAU / 18); X.beginPath(); X.moveTo(0, 0); X.lineTo(1400, -120); X.lineTo(1400, 120); X.closePath(); fillC(i % 2 ? hexA(P.org, .07) : hexA(P.pur, .06)); }
  X.restore();
  const fl = 1 - pr(t, 18.6, 18.95); if (fl > 0) { X.fillStyle = `rgba(255,240,220,${fl * .8})`; X.fillRect(0, 0, W, H); }
  const fy = Math.sin(t * 1.4) * 8 * pr(t, 22.5, 23.5);
  const a = extText('3D', W / 2, 560 + fy, 360, 30, 2.6, pr(t, 19.3, 20.9), '#B8320A', '#FFB020');
  const b = extText('PRINTING', W / 2, 760 + fy, 128, 18, 1.9, pr(t, 20.7, 21.9), '#7B2BD6', '#FF5FA8', 22);
  const tip = a.tip || b.tip;
  if (tip) hotend(tip[0], tip[1], .7, 1, P.org, 600);
  const hk = pr(t, 21.9, 22.4); if (hk > 0 && hk < 1) { const tx = b.left + b.w + 30; hotend(tx + hk * 300, 760 - 18 * 1.9 - 128 * .74 - hk * 400, .7, 1 - hk, P.org, 600); }
  // XYZ gizmo — "three dimensions"
  const gx = 1440, gyy = 560;
  [[0, 22.0, '#FF4D5E', 'X', [1, 0]], [1, 22.35, '#3DDC84', 'Y', [.62, -.48]], [2, 22.7, '#3D9BFF', 'Z', [0, -1]]].forEach(([i, t0, col, l, d]) => {
    const k = E.out(pr(t, t0, t0 + .45)); if (k <= 0) return; const L = 190 * k;
    X.save(); X.shadowColor = col; X.shadowBlur = 20; arrow(gx, gyy, gx + d[0] * L, gyy + d[1] * L, col, 10, 34); X.restore();
    chip(gx + d[0] * (L + 50), gyy + d[1] * (L + 50), l, col, pr(t, t0 + .2, t0 + .6), 30);
  });
  if (pr(t, 22.0, 22.3) > 0) { circ(gx, gyy, 12 * pop(t, 22, .4)); fillC('#FFFFFF'); }
  sparkles(W / 2, 520, t, 21.9, 3, 14, 520, P.yel);
}

// 5 — Simple idea + cake analogy (25.2 – 37.7)
const cylC = circC(150, 72);
function sCake(t) {
  // "الفكرة أبسط بكتير"
  const bk = pop(t, 25.95, .6) * (1 - E.in(pr(t, 27.3, 27.9)));
  if (bk > 0) { G(W / 2, 500, 1.7 * bk, Math.sin(t * 3) * .05, 1, () => bulb(0, 0, 1, pr(t, 26.3, 26.7))); burst(W / 2, 470, t, 26.4, P.yel, 230, 16); }
  const pk = pop(t, 28.43, .6); if (pk <= 0) return;
  const cx = lerp(W / 2, 560, E.io(pr(t, 33.35, 34.2))), by = 830;
  // morph to plastic colors "بس بالبلاستيك"
  const mk = E.io(pr(t, 35.6, 36.4));
  G(cx, by, pk, 0, 1, () => {
    ell(0, 30, 360, 90); fillC('rgba(0,0,0,.3)'); ell(0, 14, 350, 86); fillC('#D8DBF0'); ell(0, 8, 330, 78); fillC('#F4F1FF'); ell(0, 8, 250, 56); strokeC('rgba(0,0,0,.06)', 3);
    const L = [[29.97, 90, '#8B4A2B', '#A65E36', P.org], [31.0, 36, '#FFF1E0', '#FFFFFF', P.yel], [32.08, 90, '#8B4A2B', '#A65E36', P.pink], [32.6, 30, '#FFF1E0', '#FFFFFF', P.pur]];
    let base = 0;
    L.forEach(([t0, h, side, top, plc], i) => {
      const k = pr(t, t0, t0 + .5); if (k <= 0) return; const drop = -650 * (1 - E.out(k)), sq = 1 + .12 * Math.sin(pr(t, t0 + .35, t0 + .8) * Math.PI);
      const sd = mixc(side, plc, mk), tp = mix(top, mixc(plc, '#FFFFFF', .3), mk);
      X.save(); X.translate(0, drop - base); X.scale(sq, 1 / sq); drawCyl(0, 0, 250, 60, h, tp, sd);
      if ((i === 1 || i === 3) && mk < 1) { X.globalAlpha *= 1 - mk; for (let d = 0; d < 8; d++) { const ax = -220 + d * 62; ell(ax, -h + 50 + Math.sin(d) * 8, 16, 26 + (d % 3) * 8); fillC(rgb(sd)); } }
      if (mk > 0) { X.globalAlpha = mk * .5; for (let s2 = 1; s2 < h / 9; s2++) { X.beginPath(); X.ellipse(0, -s2 * 9, 250, 60, 0, .05, Math.PI - .05); strokeC('rgba(0,0,0,.25)', 2); } }
      X.restore(); base += h;
    });
    const ck = pop(t, 32.9, .5); if (ck > 0) G(30, -base - 26, ck, 0, 1, () => { X.strokeStyle = '#3DDC84'; X.lineWidth = 5; X.beginPath(); X.moveTo(0, -20); X.quadraticCurveTo(10, -50, 30, -60); X.stroke(); circ(0, 0, 30); X.shadowColor = P.red; X.shadowBlur = 20; fillC(mix('#E0243C', P.cyan, mk)); X.shadowBlur = 0; circ(-10, -10, 8); fillC('rgba(255,255,255,.6)'); });
  });
  // printer twin
  const rk = pr(t, 33.5, 34.1);
  if (rk > 0) {
    const ek = E.back(rk);
    G(W / 2 + 40, 640, pop(t, 34.0, .5), 0, 1, () => { rr(-60, -26, 120, 18, 9); fillC(P.wht); rr(-60, 8, 120, 18, 9); fillC(P.wht); });
    X.save(); X.globalAlpha *= clamp(rk * 2);
    const yOff = (1 - ek) * 300;
    const c = { cx: 1360, cy: by + yOff, s: 1.25, yaw: t * .3, pitch: .33 };
    plate(c, 220, '#1B1F4A', P.cyan);
    const res = stack({ ...c, n: 30, lh: 7.2, prog: E.io(pr(t, 33.9, 37.4)), contour: () => cylC, color: (u, i, top) => hsl(lerp(24, -30, u), 95, (top ? 70 : 57) + (i % 2 ? -7 : 0)), R: 150 });
    hotend(res.tip[0], res.tip[1], .75, 1);
    X.restore();
    chip(1360, 230, 'PLASTIC', P.org, pr(t, 35.7, 36.2), 40);
  }
}

// 6 — Printer anatomy: filament → extruder → heater → nozzle (37.7 – 48.4)
const FIL = new Path2D('M 560 430 C 780 430 900 170 1150 170 L 1150 760'), FIL_LEN = 1340;
function sAnatomy(t) {
  const tip = [1150, 760], zk = E.io(pr(t, 45.0, 46.1)), zs = lerp(1, 2.3, zk);
  X.save();
  const tx = lerp(0, 960 - tip[0] * zs, zk), ty = lerp(0, 520 - tip[1] * zs, zk);
  X.translate(tx, ty); X.scale(zs, zs);
  // bed
  const bedK = pr(t, 38.4, 38.9); if (bedK > 0) { rr(700, 800, 900, 24, 8); fillC(hexA('#C9CFE8', bedK)); rr(700, 824, 900, 30, 8); fillC(hexA('#20244F', bedK)); }
  // spool
  const feed = Math.max(0, t - 42.3) * 1.8 + E.out(pr(t, 38.6, 40.5)) * 2;
  const sk = pop(t, 38.05, .6); if (sk > 0) G(380, 430, sk, 0, 1, () => spool(0, 0, 190, -feed, P.org));
  // filament path (reveal then flow)
  const fk = E.io(pr(t, 38.5, 40.6));
  if (fk > 0) {
    X.save(); X.lineCap = 'round'; X.strokeStyle = P.org; X.lineWidth = 12; X.shadowColor = P.org; X.shadowBlur = 16; X.setLineDash([FIL_LEN * fk, 9999]); X.stroke(FIL); X.restore();
    if (t > 42.3) { X.save(); X.strokeStyle = 'rgba(255,240,210,.8)'; X.lineWidth = 4; X.setLineDash([14, 40]); X.lineDashOffset = -(t - 42.3) * 160; X.stroke(FIL); X.restore(); }
  }
  // extruder gears
  const ek = pop(t, 42.31, .55);
  if (ek > 0) G(1150, 300, ek, 0, 1, () => {
    rr(-150, -70, 300, 140, 24); fillC('#2E3470'); rr(-150, -70, 300, 26, 16); fillC('#3A418A');
    gear(-46, 0, 42, 14, (t - 42.3) * 4, '#C9CFE8', .3); gear(46, 0, 42, 14, -(t - 42.3) * 4, '#C9CFE8', .3);
    rr(-6, -90, 12, 180, 6); fillC(P.org);
  });
  // hotend
  const hk = E.out(pr(t, 38.6, 39.4)), heat = E.io(pr(t, 43.2, 44.8));
  if (hk > 0) {
    X.save(); X.globalAlpha *= hk; hotend(1150, 760 - (1 - hk) * 120, 1.45, heat, null); X.restore();
    if (heat > 0) for (let i = 0; i < 3; i++) { X.save(); X.globalAlpha *= heat * .6; X.strokeStyle = '#FF9A5A'; X.lineWidth = 4; X.beginPath(); for (let y = 0; y < 90; y += 6) { const xx = 1090 + i * 60 + Math.sin(y * .12 - t * 8 + i) * 8; y ? X.lineTo(xx, 640 - y) : X.moveTo(xx, 640); } X.stroke(); X.restore(); }
  }
  // extrusion bead under nozzle
  const bk = pr(t, 45.4, 47.9);
  if (bk > 0) {
    X.save(); X.strokeStyle = '#FFB060'; X.lineWidth = 12; X.lineCap = 'round'; X.shadowColor = P.org; X.shadowBlur = 24;
    const dropK = E.out(clamp(bk * 3)); X.beginPath(); X.moveTo(1150, 762); X.lineTo(1150, 762 + 34 * dropK);
    if (bk > .33) { const L2 = (bk - .33) / .67 * 260; X.quadraticCurveTo(1150, 798, 1150 - 20, 798); X.lineTo(1150 - 20 - L2, 798); } X.stroke(); X.restore();
  }
  X.restore();
  // callouts in screen space (positions follow the zoom)
  const S = (x, y) => [tx + x * zs, ty + y * zs];
  const zf = 1 - zk;
  if (zf > .01) {
    X.save(); X.globalAlpha *= zf;
    let p = S(380 + 140, 430 + 130); callout(t, 41.03, p[0], p[1], 420, 820, 'FILAMENT', P.org, 40);
    chip(420, 905, 'PLA  •  1.75 mm', P.wht, pr(t, 41.5, 41.9), 26);
    p = S(1150, 230); callout(t, 42.5, p[0] + 150, p[1] - 30, 1560, 170, 'EXTRUDER', P.cyan, 34);
    p = S(1150 + 66, 760 - 80); callout(t, 43.25, p[0], p[1], 1580, 620, 'HEATER', P.red, 34);
    const temp = Math.round(lerp(25, 210, E.io(pr(t, 43.3, 44.8))));
    chip(1580, 700, temp + '°C', mix(P.yel, P.red, heat), pr(t, 43.5, 43.9), 40);
    X.restore();
  }
  callout(t, 46.05, 960, 520, 1350, 300, 'NOZZLE', P.yel, 42);
  chip(1350, 390, 'Ø 0.4 mm', P.wht, pr(t, 46.5, 46.9), 28);
}

// 7 — Layer by layer (48.4 – 65.0)
function heroProg(t) {
  let L = pr(t, 48.85, 51.1) + pr(t, 56.74, 57.9);
  L += 4 * E.out(pr(t, 58.45, 58.85)) + 5 * E.out(pr(t, 59.25, 59.65)) + 5 * E.out(pr(t, 60.3, 60.7));
  L += 44 * E.io(pr(t, 61.8, 64.0)); return L / 60;
}
function sLayers(t) {
  const build = E.io(pr(t, 61.8, 65)), zoomIn = E.io(pr(t, 48.4, 49.3));
  const c = { cx: 960, cy: lerp(760, 800, build), s: lerp(2.2, 1.55, Math.max(E.io(pr(t, 57.6, 58.6)), 0)) * lerp(1.15, 1, zoomIn), yaw: .5 + (t - 48.4) * .05 + build * 1.8, pitch: lerp(.62, .48, build) };
  plate(c, 230);
  // beat rings
  [58.45, 59.25, 60.3, 64.0].forEach((b, i) => { const k = pr(t, b, b + .6); if (k <= 0 || k >= 1) return; X.save(); X.globalAlpha *= 1 - k; const R = 120 + 200 * E.out(k); poly(circC(R, 60).map(p => proj(p[0], p[1], 0, c))); strokeC(i === 3 ? P.yel : P.cyan, 5); X.restore(); });
  const prog = heroProg(t);
  const res = stack({ ...c, n: 60, lh: 4.5, prog, contour: heroC, color: heroColor, R: 125 });
  // nozzle
  const lift = 60 * E.out(pr(t, 54.55, 55.2)) * (1 - E.io(pr(t, 55.9, 56.6))) + 400 * E.in(pr(t, 64.2, 65.0));
  const ntip = [res.tip[0], res.tip[1] - lift];
  hotend(ntip[0], ntip[1], .78, 1, P.org, 700);
  if (pr(t, 54.55, 54.9) > 0 && t < 56.7) { const k = vis(t, 54.6, 56.6, .25); X.save(); X.globalAlpha *= k; arrow(ntip[0] + 110, ntip[1] - 40, ntip[0] + 110, ntip[1] - 200, P.cyan, 10, 34); chip(ntip[0] + 200, ntip[1] - 130, 'Z +', P.cyan, 1, 40); X.restore(); }
  // magnifier
  const mk = vis(t, 51.5, 54.4, .35);
  if (mk > 0) {
    const lx = 1450, ly = 360, R = 210, s0 = E.back(clamp(pr(t, 51.5, 52.0)));
    const q = proj(heroC(0)[24][0], heroC(0)[24][1], 0, c);
    X.save(); X.globalAlpha *= mk; X.strokeStyle = P.wht; X.lineWidth = 3; X.setLineDash([8, 8]); X.beginPath(); X.moveTo(q[0], q[1]); X.lineTo(lx - R * .7, ly + R * .7); X.stroke(); X.setLineDash([]);
    G(lx, ly, s0, 0, 1, () => {
      circ(0, 0, R); X.shadowColor = 'rgba(0,0,0,.5)'; X.shadowBlur = 40; fillC('#0E1236'); X.shadowBlur = 0;
      X.save(); circ(0, 0, R); X.clip();
      rr(-R, 70, 2 * R, R, 0); fillC('#C9CFE8');
      const bead = (y, k) => { if (k <= 0) return; X.save(); X.beginPath(); X.rect(-R, y - 40, 2 * R * k, 80); X.clip(); for (let i = -3; i <= 3; i++) { ell(i * 64, y, 34, 22); const g = X.createLinearGradient(0, y - 22, 0, y + 22); g.addColorStop(0, '#FFC27A'); g.addColorStop(1, '#D9570C'); X.fillStyle = g; X.fill(); } X.restore(); };
      bead(48, E.io(pr(t, 51.7, 52.4)));
      X.restore(); circ(0, 0, R); strokeC(P.wht, 8);
      const dk = E.out(pr(t, 52.5, 53.0)); if (dk > 0) { X.save(); X.globalAlpha *= dk; X.strokeStyle = P.yel; X.lineWidth = 4; X.beginPath(); X.moveTo(90, 26); X.lineTo(150, 26); X.moveTo(90, 70); X.lineTo(150, 70); X.moveTo(130, 26); X.lineTo(130, 70); X.stroke(); X.restore(); }
    });
    chip(lx, ly + R + 60, '0.2 mm', P.yel, pr(t, 52.6, 53.1) * mk, 46);
    X.restore();
  }
  if (t > 64) { burst(c.cx, res.tip[1], t, 64.0, P.yel, 260, 18, .9); sparkles(c.cx, c.cy - 260, t, 64.0, 9, 16, 380, P.yel); }
}

// 8 — Design on computer / download (65.0 – 73.2)
function wire(c, n, lh, contour, col, a = 1, rings = 9, spokes = 16) {
  X.save(); X.globalAlpha *= a; X.strokeStyle = col; X.lineWidth = 2;
  for (let r = 0; r <= rings; r++) { const i = Math.round(r / rings * (n - 1)), pts = contour(i / n); poly(pts.map(p => proj(p[0], p[1], i * lh, c))); X.stroke(); }
  for (let s = 0; s < spokes; s++) { X.beginPath(); for (let r = 0; r <= rings * 2; r++) { const i = Math.round(r / (rings * 2) * (n - 1)), pts = contour(i / n), p = pts[Math.floor(s / spokes * pts.length)], q = proj(p[0], p[1], i * lh, c); r ? X.lineTo(...q) : X.moveTo(...q); } X.stroke(); }
  X.restore();
}
function sCAD(t) {
  const mv = E.io(pr(t, 68.5, 69.3));
  const px = lerp(W / 2, 330, mv), ps = lerp(.95, .62, mv);
  const wob = Math.sin(t * 5) * 60 * vis(t, 66.0, 68.6, .3);
  printer(px, 960 - mv * 40, ps, { hx: wob, obj: (bed) => { X.save(); const c = { cx: 0, cy: bed, s: .55, yaw: t * .6, pitch: .45 }; stack({ ...c, n: 60, lh: 4.5, contour: heroC, color: heroColor, R: 125, shade: true }); X.restore(); }, gy: -420 });
  bubble(px + 260 * ps + 90, 960 - 720 * ps, 90 * lerp(1, .8, mv), pr(t, 65.9, 66.4) * (1 - pr(t, 68.4, 68.7)), -1);
  qmark(px + 260 * ps + 90, 960 - 720 * ps, 120, P.pink, pr(t, 66.05, 66.5) * (1 - pr(t, 68.4, 68.7)), Math.sin(t * 4) * .15);
  // monitor
  const mk = pr(t, 68.79, 69.4); if (mk <= 0) return;
  const my = lerp(1400, 500, E.out(mk));
  X.save(); X.translate(1180, my);
  rr(-40, 330, 80, 90, 6); fillC('#2E3470'); rr(-160, 410, 320, 22, 10); fillC('#3A418A');
  rr(-520, -330, 1040, 670, 30); X.shadowColor = 'rgba(0,0,0,.5)'; X.shadowBlur = 50; fillC('#2E3470'); X.shadowBlur = 0;
  rr(-496, -306, 992, 622, 16); fillC('#0D1135');
  rr(-496, -306, 992, 52, [16, 16, 0, 0]); fillC('#1A1F52'); [P.red, P.yel, P.grn].forEach((cl, i) => { circ(-466 + i * 28, -280, 8); fillC(cl); });
  for (let i = 0; i < 4; i++) { rr(-360 + i * 110, -290, 90, 20, 8); fillC('rgba(255,255,255,.08)'); }
  rr(-496, -254, 90, 570, 0); fillC('#141945'); for (let i = 0; i < 6; i++) { rr(-475, -230 + i * 76, 48, 48, 10); fillC(i === 1 ? hexA(P.cyan, .6) : 'rgba(255,255,255,.1)'); }
  rr(330, -254, 166, 570, 0); fillC('#141945'); for (let i = 0; i < 7; i++) { rr(350, -230 + i * 50, 126 - (i % 3) * 30, 14, 7); fillC('rgba(255,255,255,.12)'); }
  X.save(); rr(-406, -254, 736, 570, 0); X.clip();
  const c = { cx: -40, cy: 210, s: 1.0, yaw: t * .7, pitch: .45 };
  X.strokeStyle = 'rgba(46,230,214,.14)'; X.lineWidth = 1.5; for (let v = -300; v <= 300; v += 50) { let a = proj(v, -300, 0, c), b = proj(v, 300, 0, c); X.beginPath(); X.moveTo(...a); X.lineTo(...b); X.stroke(); a = proj(-300, v, 0, c); b = proj(300, v, 0, c); X.beginPath(); X.moveTo(...a); X.lineTo(...b); X.stroke(); }
  const solid = E.io(pr(t, 72.1, 72.9));
  if (solid < 1) wire(c, 60, 4.5, heroC, P.cyan, 1 - solid);
  if (solid > 0) { X.save(); X.globalAlpha *= solid; stack({ ...c, n: 60, lh: 4.5, contour: heroC, color: heroColor, R: 125 }); X.restore(); }
  // cursor
  const cxp = -40 + Math.sin(t * 1.3) * 160, cyp = 20 + Math.cos(t * 1.7) * 60;
  G(cxp, cyp, 1, 0, 1 - solid, () => { X.beginPath(); X.moveTo(0, 0); X.lineTo(0, 40); X.lineTo(11, 30); X.lineTo(20, 48); X.lineTo(28, 44); X.lineTo(19, 27); X.lineTo(33, 27); X.closePath(); fillC('#FFFFFF'); strokeC('#0D1135', 2); });
  X.restore(); X.restore();
  // cloud + downloaded file
  const ck = pop(t, 70.8, .55);
  if (ck > 0) {
    G(1640, 180, ck, 0, 1, () => {
      X.shadowColor = hexA(P.cyan, .6); X.shadowBlur = 30; X.beginPath(); X.arc(-50, 10, 50, Math.PI * .5, Math.PI * 1.5); X.arc(0, -30, 62, Math.PI, 0); X.arc(60, 5, 46, -Math.PI * .5, Math.PI * .5); X.closePath(); fillC('#F4F1FF'); X.shadowBlur = 0;
      arrow(0, -40, 0, 40 + Math.sin(t * 8) * 6, P.cyan, 12, 34);
    });
    const fk = pr(t, 71.3, 72.2);
    if (fk > 0 && fk < 1) {
      const fx = lerp(1640, 1140, E.io(fk)), fy = lerp(220, 640, E.in(fk));
      G(fx, fy, 1, (1 - fk) * .5, 1 - pr(fk, .85, 1), () => { X.beginPath(); X.moveTo(-50, -64); X.lineTo(26, -64); X.lineTo(50, -40); X.lineTo(50, 64); X.lineTo(-50, 64); X.closePath(); fillC('#F4F1FF'); X.beginPath(); X.moveTo(26, -64); X.lineTo(26, -40); X.lineTo(50, -40); fillC('#C9CFE8'); rr(-40, 4, 80, 34, 8); fillC(P.org); txt('STL', 0, 22, 26, '#FFFFFF', 900); });
    }
    burst(1140, 640, t, 72.15, P.cyan, 150, 14);
  }
}

// 9 — Slicer (73.2 – 84.6)
let INFILL = null;
function buildInfill() {
  const pts = heroC(.5).map(p => [p[0] * 3.3 + W / 2, p[1] * 3.3 + 540]), path = new Path2D();
  pts.forEach((p, i) => i ? path.lineTo(p[0], p[1]) : path.moveTo(p[0], p[1])); path.closePath();
  const inner = heroC(.5).map(p => [p[0] * 3.0 + W / 2, p[1] * 3.0 + 540]); const ipath = new Path2D(); inner.forEach((p, i) => i ? ipath.lineTo(p[0], p[1]) : ipath.moveTo(p[0], p[1])); ipath.closePath();
  const route = []; let dir = 1; const sp = 30, cs = Math.SQRT1_2;
  for (let d = -560; d <= 560; d += sp) {
    const seg = []; let inside = false, a = null;
    for (let s = -600; s <= 600; s += 3) { const x = W / 2 + (d * cs - s * cs), y = 540 + (d * cs + s * cs); const ins = X.isPointInPath(ipath, x, y); if (ins && !inside) a = [x, y]; if (!ins && inside) seg.push([a, [x, y]]); inside = ins; }
    if (!seg.length) continue; let longest = seg.reduce((m, s2) => Math.hypot(s2[1][0] - s2[0][0], s2[1][1] - s2[0][1]) > Math.hypot(m[1][0] - m[0][0], m[1][1] - m[0][1]) ? s2 : m);
    if (dir < 0) longest = [longest[1], longest[0]]; route.push(longest[0], longest[1]); dir = -dir;
  }
  INFILL = { outer: pts, inner, route };
}
function pathAlong(pts, k) { let L = 0; const seg = []; for (let i = 1; i < pts.length; i++) { const d = Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]); seg.push(d); L += d; } let target = L * k, out = [pts[0]]; for (let i = 1; i < pts.length; i++) { if (target >= seg[i - 1]) { out.push(pts[i]); target -= seg[i - 1]; } else { const f = target / seg[i - 1]; out.push([lerp(pts[i - 1][0], pts[i][0], f), lerp(pts[i - 1][1], pts[i][1], f)]); break; } } return out; }
const TOAST = new Path2D('M -110 120 L -110 -40 C -150 -60 -150 -140 -80 -140 C -50 -170 50 -170 80 -140 C 150 -140 150 -60 110 -40 L 110 120 Z');
const ex0 = t => E.io(pr(t, 75.8, 76.8)) * (1 - E.io(pr(t, 79.2, 80.4)));
function sSlicer(t) {
  const topK = E.io(pr(t, 80.5, 81.2));
  if (topK < 1) {
    X.save(); X.globalAlpha *= 1 - topK;
    const loaf = E.io(pr(t, 77.2, 77.9)) * (1 - E.io(pr(t, 79.9, 80.6)));
    const c = { cx: lerp(W / 2, 760, loaf), cy: lerp(860, 900, ex0(t)), s: lerp(1.45, 1.05, ex0(t)), yaw: .4 + t * .25, pitch: .42 };
    const ex = E.io(pr(t, 75.8, 76.8)) * (1 - E.io(pr(t, 79.2, 80.4)));
    plate(c, 230, '#1B1F4A', P.cyan, 1 - ex);
    const many = pr(t, 79.2, 80.2);
    stack({ ...c, n: 60, lh: 4.5, gap: ex * 4, step: ex > .05 ? 3 : 1, contour: heroC, R: 125, edge: ex > .05 ? 2 : 1, edgeCol: ex > .05 ? 'rgba(255,255,255,.55)' : undefined,
      color: (u, i, top) => many > 0 && many < 1 && ((Math.floor(i / 3) + Math.floor(t * 6)) % 2) ? hsl(180, 80, 60) : heroColor(u, i, top) });
    // laser plane sweep
    const lk = pr(t, 73.7, 75.6);
    if (lk > 0 && lk < 1) { const z = lerp(290, 0, E.io(lk)), q = [[-230, -230], [230, -230], [230, 230], [-230, 230]].map(p => proj(p[0], p[1], z, c)); X.save(); poly(q); fillC(hexA(P.cyan, .18)); X.shadowColor = P.cyan; X.shadowBlur = 30; strokeC(P.cyan, 4); X.restore(); }
    // toast analogy
    if (loaf > 0) {
      G(1450, 620, E.back(clamp(loaf)), 0, clamp(loaf * 2), () => {
        for (let i = 6; i >= 0; i--) { const fan = E.out(pr(t, 77.5 + i * .06, 78.3 + i * .06)); X.save(); X.translate(i * 34 * (1 + fan * .9), -i * 8 - fan * Math.sin(i) * 10); X.rotate(fan * (i - 3) * .03); X.fillStyle = '#B5652A'; X.fill(TOAST); X.scale(.86, .86); X.translate(0, 8); X.fillStyle = i === 0 ? '#FFE2A8' : '#F2C77F'; X.fill(TOAST); X.restore(); }
      });
      G(1140, 620, pop(t, 77.6, .5), 0, 1, () => { rr(-50, -22, 100, 16, 8); fillC(P.wht); rr(-50, 8, 100, 16, 8); fillC(P.wht); });
    }
    const n = Math.round(E.out(pr(t, 79.2, 80.4)) * 300);
    chip(c.cx, 170, n + ' LAYERS', P.cyan, pr(t, 79.1, 79.5) * (1 - pr(t, 80.4, 80.7)), 48);
    X.restore();
  }
  if (topK > 0) {
    if (!INFILL) buildInfill();
    X.save(); X.globalAlpha *= topK;
    const s0 = lerp(.6, 1, E.out(topK)); X.translate(W / 2, 540); X.scale(s0, s0); X.rotate((1 - topK) * .4); X.translate(-W / 2, -540);
    poly(INFILL.outer); fillC('rgba(46,230,214,.06)');
    X.strokeStyle = 'rgba(255,255,255,.12)'; X.lineWidth = 1.5; for (let x = 360; x < 1600; x += 60) { X.beginPath(); X.moveTo(x, 60); X.lineTo(x, 1020); X.stroke(); }
    const pk = pr(t, 81.0, 81.9), ik = pr(t, 82.0, 83.6);
    X.lineJoin = 'round'; X.lineCap = 'round';
    const outer = [...INFILL.outer, INFILL.outer[0]], inner = [...INFILL.inner, INFILL.inner[0]];
    let head = null;
    if (pk > 0) { const a = pathAlong(outer, Math.min(1, pk * 2)); X.save(); X.shadowColor = P.org; X.shadowBlur = 18; poly(a, false); strokeC(P.org, 14); X.restore(); head = a[a.length - 1]; if (pk > .5) { const b = pathAlong(inner, (pk - .5) * 2); X.save(); X.shadowColor = P.org; X.shadowBlur = 18; poly(b, false); strokeC('#FF9A4A', 12); X.restore(); head = b[b.length - 1]; } }
    if (ik > 0) { const a = pathAlong(INFILL.route, ik); X.save(); poly(a, false); X.shadowColor = P.pink; X.shadowBlur = 12; strokeC(P.pink, 9); X.restore(); head = a[a.length - 1]; }
    if (head) { circ(head[0], head[1], 26); fillC(hexA(P.yel, .3)); circ(head[0], head[1], 14); X.shadowColor = P.yel; X.shadowBlur = 30; fillC('#FFF3C4'); X.shadowBlur = 0; }
    X.restore();
    chip(330, 200, 'PERIMETER', P.org, pr(t, 81.1, 81.5), 30); chip(330, 280, 'INFILL', P.pink, pr(t, 82.1, 82.5), 30);
  }
}

// 10 — "People do what with it? Mind-blowing stuff" (84.6 – 90.0)
function sWow(t) {
  const wk = pr(t, 87.3, 87.6);
  if (wk > 0) { X.save(); X.translate(W / 2, 520); X.rotate(t * .25); X.globalAlpha *= wk; for (let i = 0; i < 24; i++) { X.rotate(TAU / 24); X.beginPath(); X.moveTo(0, 0); X.lineTo(1500, -90); X.lineTo(1500, 90); X.closePath(); fillC(i % 2 ? hexA(P.yel, .12) : hexA(P.pink, .08)); } X.restore(); }
  const skins = ['#F2C29B', '#8D5A3B', '#E8B48A', '#5C3A21', '#F5D3B5', '#B07850', '#D9A07A'], shirts = [P.org, P.pur, P.cyan, P.pink, P.yel, P.grn, P.blue], hair = ['#2A1A12', '#111', '#6B3E1F', '#111', '#C98A3A', '#2A1A12', '#3A2012'];
  for (let i = 0; i < 7; i++) {
    const x = 330 + i * 210, k = pop(t, 84.7 + i * .07, .5), jump = wk > 0 ? Math.abs(Math.sin((t - 87.4) * 7 + i)) * 70 * Math.exp(-(t - 87.4) * .6) : 0;
    avatar(x, 830 - jump, 1.05 * k, skins[i], shirts[i], hair[i], i);
    if (i % 2 === 1) { const qk = pr(t, 85.5 + i * .08, 85.9 + i * .08) * (1 - wk); qmark(x + 50, 640 + Math.sin(t * 3 + i) * 8, 80, P.wht, qk, .2); }
    if (wk > 0 && i % 2 === 0) { G(x + 50, 640 - jump, pop(t, 87.45 + i * .05, .4), .15, 1, () => txt('!', 0, 0, 100, P.yel, 900)); }
  }
  burst(W / 2, 420, t, 87.39, P.yel, 260, 20, .9); confetti(W / 2, 450, t, 87.45, 4, 90, 1600);
}

// 11 — Real-world uses: 5 cards (90.0 – 107.9)
const CARDS = [
  { t: 90.3, col: P.cyan, label: 'MEDICINE', draw: cardHand },
  { t: 94.22, col: P.pink, label: 'FOOTWEAR', draw: cardShoe },
  { t: 97.31, col: '#C9CFE8', label: 'CONSTRUCTION', draw: cardHouse },
  { t: 99.96, col: P.yel, label: 'FOOD', draw: cardFood },
  { t: 102.1, col: P.pur, label: 'GAMING & COSPLAY', draw: cardGame },
];
function cardFrame(col, label, lt) {
  rr(-470, -330, 940, 660, 44); const g = X.createLinearGradient(0, -330, 0, 330); g.addColorStop(0, '#221E5A'); g.addColorStop(1, '#141139'); X.fillStyle = g; X.shadowColor = 'rgba(0,0,0,.5)'; X.shadowBlur = 60; X.fill(); X.shadowBlur = 0; strokeC(hexA(col, .9), 4);
  X.save(); rr(-470, -330, 940, 660, 44); X.clip(); X.globalAlpha *= .12; X.strokeStyle = col; X.lineWidth = 1.5; for (let x = -470; x < 470; x += 40) { X.beginPath(); X.moveTo(x, -330); X.lineTo(x, 330); X.stroke(); } for (let y = -330; y < 330; y += 40) { X.beginPath(); X.moveTo(-470, y); X.lineTo(470, y); X.stroke(); } X.restore();
  chip(0, -330, label, col, pr(lt, .25, .6), 34);
}
function cardHand(lt) {
  const curl = .5 + .5 * Math.sin(lt * 2.2 - 1);
  X.save(); X.translate(0, 40); X.scale(1.45, 1.45);
  const col = P.cyan, dark = '#1A8F86';
  rr(-80, 120, 160, 150, 30); fillC('#2E3470'); rr(-80, 120, 160, 26, 14); fillC('#4B53A8');
  [-70, -24, 22, 68].forEach((fx, i) => {
    const len = [110, 135, 128, 100][i] * (1 - curl * .35);
    X.save(); X.translate(fx, -60); rr(-19, -len, 38, len * .55, 14); fillC(col); rr(-19, -len * .45 - 4, 38, len * .45 + 10, 14); fillC(dark); circ(0, -len * .45, 11); fillC('#F4F1FF'); circ(0, 0, 12); fillC('#F4F1FF'); X.restore();
  });
  X.save(); X.translate(-100, 30); X.rotate(-.7 - curl * .3); rr(-18, -100, 36, 100, 14); fillC(col); circ(0, -50, 10); fillC('#F4F1FF'); X.restore();
  rr(-100, -70, 200, 200, 46); fillC(col); rr(-100, -70, 200, 40, 30); fillC('rgba(255,255,255,.25)');
  X.save(); X.globalAlpha *= .35; for (let i = 0; i < 3; i++) { rr(-70 + i * 50, -30, 36, 110, 12); fillC(dark); } X.restore();
  ell(0, 150, 150 * 1.0, 40); X.setLineDash([12, 10]); X.lineDashOffset = -lt * 40; strokeC(P.yel, 4); X.setLineDash([]);
  const sy = -230 + ((lt * 220) % 460); X.save(); X.shadowColor = P.cyan; X.shadowBlur = 20; rr(-200, sy, 400, 4, 2); fillC(hexA('#FFFFFF', .8)); X.restore();
  X.restore();
  G(330, -190, pop(lt, .4, .5), 0, 1, () => { circ(0, 0, 54); fillC(P.red); rr(-12, -34, 24, 68, 4); fillC('#FFF'); rr(-34, -12, 68, 24, 4); fillC('#FFF'); });
}
const SHOE_UP = new Path2D('M -320 70 L -310 -40 C -300 -100 -250 -110 -220 -90 C -180 -60 -120 -60 -90 -130 L -40 -140 C 40 -60 120 -30 230 -10 C 300 5 330 40 320 70 Z');
const SHOE_MID = new Path2D('M -330 60 L 330 60 C 345 90 335 130 300 135 L -300 135 C -335 130 -345 90 -330 60 Z');
function cardShoe(lt) {
  X.save(); X.translate(0, 50); X.scale(1.22, 1.22);
  X.fillStyle = P.pink; X.fill(SHOE_UP); X.save(); X.clip(SHOE_UP); X.globalAlpha = .2; X.strokeStyle = '#FFF'; X.lineWidth = 8; for (let i = -10; i < 12; i++) { X.beginPath(); X.moveTo(i * 40, -160); X.lineTo(i * 40 - 80, 80); X.stroke(); } X.restore();
  X.strokeStyle = '#FFFFFF'; X.lineWidth = 6; X.lineCap = 'round'; for (let i = 0; i < 4; i++) { X.beginPath(); X.moveTo(-60 + i * 36, -110 + i * 22); X.lineTo(-20 + i * 36, -90 + i * 22); X.stroke(); }
  X.fillStyle = '#2B2F66'; X.fill(SHOE_MID);
  const k = E.io(pr(lt, .3, 2.6)), edge = -340 + 690 * k;
  X.save(); X.clip(SHOE_MID); X.beginPath(); X.rect(-340, 50, 690 * k, 100); X.clip();
  X.fillStyle = '#F4F1FF'; X.fill(SHOE_MID); X.strokeStyle = P.cyan; X.lineWidth = 4;
  const r = 16; for (let row = 0; row < 6; row++) for (let col = 0; col < 26; col++) { const cx = -340 + col * r * 1.75 + (row % 2) * r * .87, cy = 50 + row * r * 1.5; X.beginPath(); for (let a = 0; a < 6; a++) { const an = a / 6 * TAU + Math.PI / 6; a ? X.lineTo(cx + Math.cos(an) * r, cy + Math.sin(an) * r) : X.moveTo(cx + Math.cos(an) * r, cy + Math.sin(an) * r); } X.closePath(); X.stroke(); }
  X.restore();
  if (k > 0 && k < 1) { X.save(); X.shadowColor = P.cyan; X.shadowBlur = 24; rr(edge - 3, 40, 6, 110, 3); fillC('#FFFFFF'); X.restore(); hotend(edge, 40, .5, 1, P.cyan, 300); }
  rr(-300, 135, 600, 18, 9); fillC('#14112F');
  X.restore();
}
function cardHouse(lt) {
  const c = { cx: 0, cy: 230, s: 1.0, yaw: .55 + lt * .08, pitch: .6 };
  plate(c, 300, '#2A2E5E', '#8E97C0');
  const n = 40, lh = 5.5, k = E.io(pr(lt, .2, 2.5)), full = k * n, nf = Math.floor(full), fr = full - nf;
  const Wd = 190, Dp = 130; const corners = [[-Wd, -Dp], [Wd, -Dp], [Wd, Dp], [-Wd, Dp]];
  const sides = [0, 1, 2, 3].map(i => { const a = corners[i], b = corners[(i + 1) % 4], m = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]; const yaw = c.yaw; return { a, b, depth: m[0] * Math.sin(yaw) + m[1] * Math.cos(yaw), i }; }).sort((p, q) => q.depth - p.depth);
  for (const sd of sides) for (let i = 0; i < Math.min(nf, n); i++) {
    const z = i * lh, door = sd.i === 0 && i < 26; const A = proj(sd.a[0], sd.a[1], z, c), B = proj(sd.b[0], sd.b[1], z, c);
    X.strokeStyle = i % 2 ? '#9AA0BE' : '#B3B8D2'; X.lineWidth = 16; X.lineCap = 'butt';
    if (door) { const m1 = proj(-40, sd.a[1], z, c), m2 = proj(40, sd.a[1], z, c); X.beginPath(); X.moveTo(...A); X.lineTo(...m1); X.moveTo(...m2); X.lineTo(...B); X.stroke(); } else { X.beginPath(); X.moveTo(...A); X.lineTo(...B); X.stroke(); }
  }
  let tip = null;
  if (nf < n) { const per = 4 * fr; const si = Math.floor(per), f = per - si; const a = corners[si], b = corners[(si + 1) % 4]; const z = nf * lh; X.save(); X.strokeStyle = '#E8EAF8'; X.shadowColor = '#FFF'; X.shadowBlur = 14; X.lineWidth = 16; X.beginPath(); X.moveTo(...proj(corners[0][0], corners[0][1], z, c)); for (let s = 1; s <= si; s++) X.lineTo(...proj(corners[s % 4][0], corners[s % 4][1], z, c)); tip = proj(lerp(a[0], b[0], f), lerp(a[1], b[1], f), z, c); X.lineTo(...tip); X.stroke(); X.restore(); }
  // gantry
  const gz = Math.min(nf, n) * lh + 70; const g1 = proj(-260, -200, 0, c), g1t = proj(-260, -200, gz + 80, c), g2 = proj(260, -200, 0, c), g2t = proj(260, -200, gz + 80, c);
  X.strokeStyle = P.yel; X.lineWidth = 14; X.lineCap = 'round'; X.beginPath(); X.moveTo(...g1); X.lineTo(...g1t); X.moveTo(...g2); X.lineTo(...g2t); X.stroke();
  X.lineWidth = 18; X.beginPath(); X.moveTo(...g1t); X.lineTo(...g2t); X.stroke();
  if (tip) { X.strokeStyle = '#8E97C0'; X.lineWidth = 6; X.beginPath(); X.moveTo(tip[0], g1t[1] + (g2t[1] - g1t[1]) * .5); X.lineTo(tip[0], tip[1] - 30); X.stroke(); rr(tip[0] - 16, tip[1] - 34, 32, 30, 6); fillC(P.yel); }
}
function cardFood(lt) {
  X.save(); X.translate(0, 120);
  ell(0, 30, 330, 100); fillC('rgba(0,0,0,.3)'); ell(0, 14, 320, 96); fillC('#D8DBF0'); ell(0, 8, 300, 88); fillC('#F4F1FF');
  const pts = []; for (let a = 0; a < TAU * 4.2; a += .08) { const r = 16 + a * 9.5; pts.push([Math.cos(a) * r, Math.sin(a) * r * .3 - 10]); }
  const k = E.io(pr(lt, .2, 2.4)); const path = pathAlong(pts.slice().reverse(), k);
  X.lineJoin = 'round'; X.lineCap = 'round'; poly(path, false); strokeC('#3A1C0E', 26); poly(path, false); strokeC('#6B3A1F', 18); poly(path, false); X.save(); X.translate(-3, -4); strokeC('rgba(255,200,160,.35)', 5); X.restore();
  const tip = path[path.length - 1]; if (k < 1) hotend(tip[0], tip[1] - 6, .6, 0, '#6B3A1F', 400);
  if (k >= 1) { const rnd = rng(5); for (let i = 0; i < 26; i++) { const a = rnd() * TAU, r = rnd() * 230, cl = [P.pink, P.yel, P.cyan, '#FFF'][i % 4], sk = pop(lt, 2.45 + i * .02, .3); G(Math.cos(a) * r, Math.sin(a) * r * .3 - 14, sk, a, 1, () => { rr(-8, -3, 16, 6, 3); fillC(cl); }); } }
  X.restore();
}
function sword(len) {
  X.beginPath(); X.moveTo(0, -len); X.lineTo(18, -len + 40); X.lineTo(18, 0); X.lineTo(-18, 0); X.lineTo(-18, -len + 40); X.closePath();
  const g = X.createLinearGradient(-18, 0, 18, 0); g.addColorStop(0, '#9AA3C7'); g.addColorStop(.5, '#FFFFFF'); g.addColorStop(1, '#7A83A8'); X.fillStyle = g; X.fill();
  rr(-70, 0, 140, 22, 10); fillC(P.yel); rr(-12, 22, 24, 90, 8); fillC('#5A2E8A'); circ(0, 122, 18); fillC(P.yel);
}
function cardGame(lt) {
  const wig = Math.sin(lt * 9) * .04 * Math.exp(-lt * 1.5);
  X.save(); X.rotate(wig);
  [-.6, .6].forEach((a, i) => G(i ? 120 : -120, 120, 1, a, pop(lt, .3 + i * .15, .5), () => sword(330)));
  G(0, -10, pop(lt, .1, .6), 0, 1, () => {
    X.beginPath(); X.moveTo(-150, 120); X.bezierCurveTo(-170, -80, -100, -180, 0, -180); X.bezierCurveTo(100, -180, 170, -80, 150, 120); X.lineTo(80, 150); X.lineTo(-80, 150); X.closePath();
    const g = X.createLinearGradient(-150, 0, 150, 0); g.addColorStop(0, '#5B3BD6'); g.addColorStop(.45, '#9C83FF'); g.addColorStop(1, '#3A2499'); X.fillStyle = g; X.fill();
    X.beginPath(); X.moveTo(-120, -40); X.lineTo(120, -40); X.lineTo(100, 10); X.lineTo(22, 10); X.lineTo(22, 110); X.lineTo(-22, 110); X.lineTo(-22, 10); X.lineTo(-100, 10); X.closePath(); X.shadowColor = P.cyan; X.shadowBlur = 30 + 20 * Math.sin(lt * 5); fillC(P.cyan); X.shadowBlur = 0;
    X.save(); X.globalAlpha *= .25; X.strokeStyle = '#000'; X.lineWidth = 2; for (let i = 0; i < 40; i++) { X.beginPath(); X.moveTo(-160, 140 - i * 8); X.lineTo(160, 140 - i * 8); X.stroke(); } X.restore();
  });
  G(0, 250, pop(lt, .7, .5), 0, 1, () => { rr(-130, -50, 260, 100, 50); fillC('#2E3470'); circ(-70, 0, 20); fillC('#4B53A8'); rr(-82, -6, 24, 12, 3); fillC('#9AA3C7'); rr(-76, -12, 12, 24, 3); fillC('#9AA3C7'); [[70, -16, P.pink], [96, 4, P.cyan], [70, 22, P.yel], [44, 4, P.grn]].forEach(([x, y, c]) => { circ(x, y, 10); fillC(c); }); });
  X.restore();
  sparkles(0, 0, lt, .3, 12, 12, 380, P.yel, 1.5); sparkles(0, 0, lt, 2.0, 13, 12, 380, P.cyan, 1.5);
}
function sUses(t) {
  const gridT = 106.2, gk = E.io(pr(t, gridT, gridT + .8));
  CARDS.forEach((cd, i) => {
    const lt = t - cd.t; if (lt < -.2) return; const next = CARDS[i + 1]?.t ?? gridT;
    const ek = E.out(pr(t, cd.t - .15, cd.t + .45)), ok = E.io(pr(t, next - .15, next + .45));
    let x = lerp(W + 700, W / 2, ek), y = 500, s = 1, r = (1 - ek) * .25;
    const slotX = W / 2 + (i - 2) * 300, slotY = 950;
    x = lerp(x, slotX, ok); y = lerp(y, slotY, ok); s = lerp(s, .27, ok);
    if (gk > 0) { x = lerp(x, W / 2 + (i - 2) * 360, gk); y = lerp(y, 540, gk); s = lerp(s, .36, gk); }
    G(x, y, s, r, 1, () => { cardFrame(cd.col, cd.label, lt); X.save(); rr(-470, -330, 940, 660, 44); X.clip(); cd.draw(lt); X.restore(); });
  });
  if (gk > 0) sparkles(W / 2, 540, t, gridT + .6, 31, 20, 800, P.yel);
}

// 12 — "The most important part: try your idea today, no factory" (107.9 – 114.3)
function factoryIcon(s) { X.save(); X.scale(s, s); X.beginPath(); X.moveTo(-120, 80); X.lineTo(-120, -20); X.lineTo(-60, -60); X.lineTo(-60, -20); X.lineTo(0, -60); X.lineTo(0, -20); X.lineTo(60, -60); X.lineTo(60, -120); X.lineTo(100, -120); X.lineTo(100, 80); X.closePath(); fillC('#4B53A8'); for (let i = 0; i < 3; i++) { rr(-100 + i * 60, 10, 36, 36, 6); fillC('#2B2F66'); } X.restore(); }
function sImportant(t) {
  const sk = pr(t, 108.6, 109.1) * (1 - pr(t, 109.9, 110.4));
  if (sk > 0) {
    X.save(); X.globalAlpha *= sk; X.beginPath(); X.moveTo(W / 2 - 60, -20); X.lineTo(W / 2 + 60, -20); X.lineTo(W / 2 + 330, 880); X.lineTo(W / 2 - 330, 880); X.closePath(); const g = X.createLinearGradient(0, 0, 0, 880); g.addColorStop(0, hexA(P.yel, .5)); g.addColorStop(1, hexA(P.yel, .05)); X.fillStyle = g; X.fill(); ell(W / 2, 880, 330, 60); fillC(hexA(P.yel, .2)); X.restore();
    G(W / 2, 640, pop(t, 108.8, .5), 0, sk, () => extText('!', 0, 120, 380, 16, 3, pr(t, 108.8, 109.4), '#B8320A', '#FFD23F'));
  }
  const ik = pop(t, 110.14, .55);
  if (ik > 0) {
    G(480, 500, 1.35 * ik, 0, 1, () => bulb(0, 0, 1, 1)); burst(480, 470, t, 110.2, P.yel, 200, 16);
    const ak = E.io(pr(t, 110.7, 111.3)); if (ak > 0) { X.save(); X.setLineDash([22, 16]); X.lineDashOffset = -t * 60; X.strokeStyle = P.wht; X.lineWidth = 8; X.beginPath(); X.moveTo(650, 500); X.lineTo(650 + 420 * ak, 500); X.stroke(); X.restore(); if (ak > .9) arrow(1050, 500, 1100, 500, P.wht, 8, 40); }
    G(1300, 720, pop(t, 110.9, .55) * .6, 0, 1, () => printer(0, 0, 1, { hx: Math.sin(t * 4) * 120, obj: (b) => { stack({ cx: 0, cy: b, s: .7, yaw: t, pitch: .45, n: 60, lh: 4.5, prog: E.io(pr(t, 111.0, 114.3)), contour: bulbC, color: heroColor, R: 110 }); } }));
    G(860, 260, pop(t, 111.3, .5), -.08, 1, () => { rr(-110, -100, 220, 200, 22); fillC('#F4F1FF'); rr(-110, -100, 220, 64, [22, 22, 0, 0]); fillC(P.red); txt('TODAY', 0, -60, 34, '#FFFFFF', 900, 'center', 3); txt('✓', 0, 36, 100, P.grn, 900); rr(-90, -122, 16, 34, 8); fillC('#2E3470'); rr(74, -122, 16, 34, 8); fillC('#2E3470'); });
  }
  const fk = pop(t, 112.3, .5);
  if (fk > 0) {
    G(1690, 300, fk * .75, 0, 1, () => {
      factoryIcon(1); G(110, -110, .5, (t - 112.3) * 2, 1, () => { X.beginPath(); X.moveTo(-40, -50); X.lineTo(40, -50); X.lineTo(0, 0); X.lineTo(40, 50); X.lineTo(-40, 50); X.lineTo(0, 0); X.closePath(); fillC(P.yel); });
      const xk = E.out(pr(t, 112.95, 113.35)); if (xk > 0) { X.save(); X.strokeStyle = P.red; X.lineWidth = 22; X.lineCap = 'round'; X.shadowColor = P.red; X.shadowBlur = 20; circ(0, -10, 170); X.globalAlpha *= xk; X.stroke(); X.beginPath(); X.moveTo(-120, 110); X.lineTo(-120 + 240 * xk, 110 - 240 * xk); X.stroke(); X.restore(); }
    });
  }
}

// 13 — Prototyping loop (114.3 – 124.9)
const lumpC = (u) => { const N = 72, o = []; for (let k = 0; k < N; k++) { const a = k / N * TAU, r = 70 * (1 + .25 * Math.sin(a * 3 + u * 9) + .12 * Math.sin(a * 7 - u * 15)) * (1 - u * .4); o.push([Math.cos(a) * r, Math.sin(a) * r]); } return o; };
function sLoop(t) {
  const split = E.io(pr(t, 123.1, 123.9));
  const cx = lerp(W / 2, 1360, split), cy = 530, R = lerp(300, 250, split), sc = lerp(1, .85, split);
  // dreamer vs doer
  if (split > 0) {
    const dim = 1 - .6 * pr(t, 124.0, 124.5);
    X.save(); X.globalAlpha *= split * dim;
    avatar(470, 800, 1.3, '#E8B48A', P.pur, '#2A1A12', 1);
    bubble(560, 420, 130, pr(t, 123.4, 123.9), -1); G(560, 420, 1, 0, pr(t, 123.6, 124), () => { txt('z', -40, 10, 60, P.mut, 900); txt('z', 0, -20, 80, P.mut, 900); txt('z', 50, -60, 100, P.mut, 900); });
    X.restore();
    const lit = pr(t, 124.0, 124.5); if (lit > 0) { X.save(); X.globalAlpha *= lit; const g = X.createRadialGradient(cx, cy, 0, cx, cy, 520); g.addColorStop(0, hexA(P.yel, .25)); g.addColorStop(1, hexA(P.yel, 0)); X.fillStyle = g; X.fillRect(0, 0, W, H); X.restore(); }
  }
  X.save(); X.translate(cx, cy); X.scale(sc, sc);
  const nodes = [{ a: -Math.PI / 2, t: 114.64, col: P.yel, icon: () => bulb(0, 6, .62, 1) }, { a: 0, t: 116.07, col: P.org, icon: () => printer(0, 92, .26, { hx: Math.sin(t * 5) * 100 }) },
    { a: Math.PI / 2, t: 117.01, col: P.cyan, icon: () => { circ(-8, -8, 40); strokeC('#F4F1FF', 12); X.lineCap = 'round'; X.beginPath(); X.moveTo(22, 22); X.lineTo(54, 54); strokeC('#F4F1FF', 16); } },
    { a: Math.PI, t: 118.97, col: P.pink, icon: () => { X.rotate(.75 + Math.sin((t - 118.97) * 6) * .3 * Math.exp(-(t - 118.97))); rr(-11, -20, 22, 84, 11); fillC('#F4F1FF'); circ(0, -34, 32); fillC('#F4F1FF'); rr(-11, -72, 22, 40, 4); fillC('#1C1A4F'); circ(0, 50, 5); fillC('#1C1A4F'); } }];
  // arcs
  const arcs = [[0, 115.55, 116.05], [1, 116.5, 117.0], [2, 118.45, 118.95], [3, 119.5, 120.5]];
  arcs.forEach(([i, a0, a1]) => { const k = E.io(pr(t, a0, a1)); if (k <= 0) return; const s0 = nodes[i].a + .3, s1 = s0 + (Math.PI / 2 - .6) * k; X.save(); X.strokeStyle = hexA(P.wht, .85); X.lineWidth = 10; X.lineCap = 'round'; X.beginPath(); X.arc(0, 0, R, s0, s1); X.stroke(); if (k > .2) { const ex = Math.cos(s1) * R, ey = Math.sin(s1) * R, tg = s1 + Math.PI / 2; arrow(ex - Math.cos(tg) * 2, ey - Math.sin(tg) * 2, ex + Math.cos(tg) * 20, ey + Math.sin(tg) * 20, P.wht, 10, 34); } X.restore(); });
  // spinning flow after full loop
  const fl = pr(t, 120.5, 121.0); if (fl > 0) { X.save(); X.globalAlpha *= fl; X.setLineDash([30, 30]); X.lineDashOffset = -t * 120; X.strokeStyle = P.cyan; X.lineWidth = 5; circ(0, 0, R + 34); X.stroke(); X.restore(); }
  nodes.forEach((nd, i) => {
    const k = pop(t, nd.t, .55); if (k <= 0) return; const x = Math.cos(nd.a) * R, y = Math.sin(nd.a) * R;
    const shake = i === 2 ? Math.sin(t * 50) * 12 * vis(t, 117.9, 118.6, .1) : 0;
    G(x + shake, y, k, 0, 1, () => { circ(0, 0, 100); X.shadowColor = hexA(nd.col, .7); X.shadowBlur = 40; fillC('#1C1A4F'); X.shadowBlur = 0; strokeC(nd.col, 6); nd.icon(); });
    burst(x, y, t, nd.t, nd.col, 140, 12);
  });
  // fail badge
  const fk = pop(t, 117.89, .4) * (1 - pr(t, 119.0, 119.4));
  if (fk > 0) G(80, R - 70, fk, 0, 1, () => { circ(0, 0, 40); fillC(P.red); X.strokeStyle = '#FFF'; X.lineWidth = 10; X.lineCap = 'round'; X.beginPath(); X.moveTo(-14, -14); X.lineTo(14, 14); X.moveTo(14, -14); X.lineTo(-14, 14); X.stroke(); });
  // center: versions improving
  const v = t < 117.9 ? 0 : t < 120.97 ? 1 : 2, ver = t > 121.5 ? Math.min(2, 1 + Math.floor((t - 120.97) / .9)) : v;
  const ck = pop(t, 116.2, .5);
  if (ck > 0) {
    G(0, 0, ck, 0, 1, () => {
      const c = { cx: 0, cy: 90, s: .62, yaw: t * .8, pitch: .45 };
      const vv = t < 118.97 ? 0 : t < 120.97 ? 1 : 2;
      if (vv === 0) stack({ ...c, n: 30, lh: 7, prog: E.io(pr(t, 116.2, 117.0)), contour: lumpC, color: (u, i, top) => hsl(230, 15, (top ? 62 : 48) + (i % 2 ? -5 : 0)), R: 90 });
      else if (vv === 1) stack({ ...c, n: 40, lh: 5.5, prog: E.io(pr(t, 119.0, 119.9)), contour: (u) => circC(70 + 20 * Math.sin(u * 3), 64), color: (u, i, top) => hsl(30, 70, (top ? 66 : 54) + (i % 2 ? -6 : 0)), R: 90 });
      else stack({ ...c, n: 60, lh: 4.5, prog: E.io(pr(t, 120.97, 121.9)), contour: heroC, color: heroColor, R: 125 });
      chip(0, 170, 'v' + (vv + 1), [P.mut, P.org, P.grn][vv], pop(t, [116.4, 119.1, 121.1][vv], .4), 32);
    });
    if (t > 121.9) sparkles(0 + cx, cy, t, 121.9, 41, 10, 160, P.yel);
  }
  X.restore();
  chip(cx, cy - R * sc - 120 + 30 * split, 'PROTOTYPING', P.cyan, pr(t, 120.97, 121.4), 46);
}

// 14 — Where to start: road → Tinkercad → model libraries → no printer needed → map (124.9 – 138.95)
function isoBox(x, y, w, d, h, col) {
  const c = hx(col); const top = [[x, y - h], [x + w, y - h - w * .5], [x + w - d, y - h - w * .5 - d * .5], [x - d, y - h - d * .5]];
  poly([[x, y], [x + w, y - w * .5], [x + w, y - w * .5 - h], [x, y - h]]); fillC(rgb(mixc(c, '#000000', .15)));
  poly([[x, y], [x - d, y - d * .5], [x - d, y - d * .5 - h], [x, y - h]]); fillC(rgb(mixc(c, '#000000', .35)));
  poly(top); fillC(rgb(mixc(c, '#FFFFFF', .15)));
}
function browser(x, y, url, tab, fn) {
  X.save(); X.translate(x, y);
  rr(-560, -360, 1120, 720, 28); X.shadowColor = 'rgba(0,0,0,.5)'; X.shadowBlur = 60; fillC('#F4F1FF'); X.shadowBlur = 0;
  rr(-560, -360, 1120, 96, [28, 28, 0, 0]); fillC('#DCDDF0'); [P.red, P.yel, P.grn].forEach((c, i) => { circ(-520 + i * 30, -330, 9); fillC(c); });
  tab.forEach((tb, i) => { rr(-400 + i * 230, -350, 220, 40, [12, 12, 0, 0]); fillC(tb.on ? '#F4F1FF' : '#C9CBE3'); txt(tb.name, -290 + i * 230, -330, 20, '#2B2F66', 700); });
  rr(-500, -300, 1000, 30, 15); fillC('#FFFFFF'); txt('🔒', -478, -285, 14, '#2B2F66', 400); txt(url, -455, -284, 20, '#2B2F66', 600, 'left');
  X.save(); rr(-560, -264, 1120, 624, [0, 0, 28, 28]); X.clip(); fn(); X.restore();
  X.restore();
}
function sStart(t) {
  // A: road
  const ak = vis(t, 124.6, 127.4, .35);
  if (ak > 0) {
    const fz = E.in(pr(t, 126.6, 127.4)); X.save(); X.globalAlpha *= ak; X.translate(W / 2, 420); X.scale(1 + fz * 1.5, 1 + fz * 1.5); X.translate(-W / 2, -420);
    const sky = X.createLinearGradient(0, 0, 0, 420); sky.addColorStop(0, 'rgba(123,92,255,0)'); sky.addColorStop(1, 'rgba(255,61,127,.25)'); X.fillStyle = sky; X.fillRect(0, 0, W, 420);
    X.fillStyle = '#1D1652'; X.fillRect(0, 420, W, 700);
    poly([[W / 2 - 40, 420], [W / 2 + 40, 420], [W / 2 + 700, H], [W / 2 - 700, H]]); fillC('#2B2F66');
    for (let i = 0; i < 12; i++) { const ph = ((i / 12 + t * .5) % 1), z = ph * ph; const y0 = 420 + z * 660, y1 = 420 + Math.min(1, (ph + .04) ** 2) * 660; X.fillStyle = hexA(P.yel, .9); poly([[W / 2 - 3 - z * 12, y0], [W / 2 + 3 + z * 12, y0], [W / 2 + 3 + z * 14, y1], [W / 2 - 3 - z * 14, y1]]); X.fill(); }
    G(560, 900, pop(t, 125.15, .5), 0, 1, () => { rr(-6, -260, 12, 260, 6); fillC('#C9CFE8'); X.beginPath(); X.moveTo(6, -258); X.quadraticCurveTo(80, -230 + Math.sin(t * 6) * 10, 150, -250); X.lineTo(150, -170); X.quadraticCurveTo(80, -150 + Math.sin(t * 6 + 1) * 10, 6, -178); X.closePath(); fillC(P.grn); txt('START', 78, -212, 30, '#0B2A18', 900); });
    G(W / 2 + 150, 420, pop(t, 125.5, .5), 0, 1, () => { rr(-5, -120, 10, 120, 5); fillC('#C9CFE8'); rr(-70, -150, 140, 70, 14); fillC(P.org); txt('?', 0, -114, 56, '#FFF', 900); });
    X.restore();
  }
  // B: browser — Tinkercad then model libraries
  const bk = vis(t, 127.0, 133.3, .3);
  if (bk > 0) {
    const enter = E.out(pr(t, 127.0, 127.5)), lib = t > 130.97;
    X.save(); X.globalAlpha *= bk;
    const tabs = [{ name: 'Tinkercad', on: !lib }, { name: 'Printables', on: lib && t < 131.9 }, { name: 'Thingiverse', on: lib && t >= 131.9 }];
    browser(W / 2, lerp(900, 540, enter), lib ? (t < 131.9 ? 'printables.com' : 'thingiverse.com') : 'tinkercad.com', tabs, () => {
      if (!lib) {
        rr(-560, -264, 150, 624, 0); fillC('#E6E7F5');
        const pal = [[P.blue, 'box'], [P.org, 'cyl'], [P.yel, 'cone'], [P.pink, 'roof']];
        pal.forEach(([c], i) => { rr(-530, -230 + i * 120, 90, 90, 16); fillC('#FFFFFF'); G(-485, -170 + i * 120, .5, 0, 1, () => isoBox(0, 30, 60, 60, 60, c)); });
        X.strokeStyle = 'rgba(61,123,255,.25)'; X.lineWidth = 1.5;
        for (let i = -8; i <= 8; i++) { X.beginPath(); X.moveTo(80 + i * 40 - 320, 250 + i * 20 - 160); X.lineTo(80 + i * 40 + 320, 250 + i * 20 + 160 - 320); X.stroke(); X.beginPath(); X.moveTo(80 + i * 40 - 320, 250 - i * 20 - 160 + 320 - 320 + 0); X.lineTo(80 + i * 40 + 320, 250 - i * 20 - 160); X.stroke(); }
        const parts = [[127.6, P.blue, (s) => isoBox(0, 200, 160, 160, 130, P.blue)], [128.1, P.pink, () => { poly([[0, 70], [160, -10], [80, -110], [-80, -30]]); fillC(P.pink); poly([[0, 70], [-160, -10], [-80, -110], [-80 + 0, -30]]); fillC(mix(P.pink, '#000000', .25)); poly([[0, 70], [160, -10], [160 - 160, -10 - 80 + 160 - 80 - 80 + 160], [0, 70]]); }],
          [128.6, P.org, () => { isoBox(60, -10, 40, 40, 90, P.org); }], [129.1, P.yel, () => { isoBox(-40, 180, 50, 10, 80, P.yel); }]];
        X.save(); X.translate(80, 40);
        parts.forEach(([t0, c, fn], i) => { const k = E.out(pr(t, t0, t0 + .45)); if (k <= 0) return; X.save(); X.translate(lerp(-565 + 0, 0, k), lerp(-210 + i * 120 - 40, 0, k)); X.globalAlpha *= clamp(k * 3); fn(); X.restore(); if (k >= 1) burst(0, 100, t, t0 + .45, c, 140, 10, .5); });
        X.restore();
        G(400, -170, pop(t, 128.9, .5), -.25 + Math.sin(t * 3) * .05, 1, () => { X.beginPath(); for (let i = 0; i < 24; i++) { const a = i / 24 * TAU, r = i % 2 ? 80 : 96; i ? X.lineTo(Math.cos(a) * r, Math.sin(a) * r) : X.moveTo(Math.cos(a) * r, Math.sin(a) * r); } X.closePath(); X.shadowColor = hexA(P.grn, .7); X.shadowBlur = 30; fillC(P.grn); X.shadowBlur = 0; txt('FREE', 0, 4, 40, '#FFFFFF', 900); });
      } else {
        const scroll = (t - 130.97) * 60, icons = [() => bulb(0, 0, .55, 1), () => G(0, 0, .8, 0, 1, glassIcon), () => G(0, 0, .55, 0, 1, () => cardGame(10)), () => G(0, 0, .7, 0, 1, caseIcon), () => G(0, 0, .3, 0, 1, () => sword(330)), () => G(0, 0, .8, 0, 1, chargerIcon), () => G(0, 0, .28, 0, 1, () => cardHand(1)), () => bulb(0, 0, .55, .3)];
        for (let i = 0; i < 8; i++) {
          const col = i % 4, row = Math.floor(i / 4), x = -400 + col * 265, y = -120 + row * 300 - scroll, k = pop(t, 131.0 + i * .06, .45);
          G(x, y, k, 0, 1, () => { rr(-115, -125, 230, 250, 22); fillC('#FFFFFF'); X.save(); rr(-115, -125, 230, 180, [22, 22, 0, 0]); X.clip(); fillC(['#2B2F66', '#3A2F8F', '#1B1F4A', '#4B2A7A'][i % 4]); icons[i](); X.restore(); rr(-95, 72, 140, 14, 7); fillC('#C9CBE3'); rr(-95, 96, 90, 12, 6); fillC('#E0E1F0'); circ(80, 92, 22); fillC(P.org); arrow(80, 78, 80, 104, '#FFF', 5, 14); });
          if (i === 2) burst(x, y, t, 131.9, P.org, 160, 12);
        }
      }
    });
    X.restore();
  }
  // C: you don't need to buy a printer
  const ck = vis(t, 133.1, 135.2, .3);
  if (ck > 0) {
    X.save(); X.globalAlpha *= ck;
    G(W / 2 - 120, 900, .75 * pop(t, 133.15, .5), 0, 1, () => printer(0, 0, 1, { ledOn: false, led: .2 }));
    G(W / 2 + 330, 380, pop(t, 133.4, .5), Math.sin((t - 133.4) * 4) * .2 * Math.exp(-(t - 133.4)), 1, () => {
      X.beginPath(); X.moveTo(0, -60); X.lineTo(220, -60); X.lineTo(260, 0); X.lineTo(220, 60); X.lineTo(0, 60); X.closePath(); fillC(P.yel); circ(28, 0, 12); fillC('#14112F'); txt('$$$', 130, 3, 60, '#5A3D00', 900);
    });
    const xk = E.out(pr(t, 133.85, 134.25)); if (xk > 0) { X.save(); X.translate(W / 2 + 460, 380); X.strokeStyle = P.red; X.lineWidth = 24; X.lineCap = 'round'; X.shadowColor = P.red; X.shadowBlur = 20; X.beginPath(); X.moveTo(-150, -90); X.lineTo(-150 + 300 * xk, -90 + 180 * xk); X.stroke(); X.restore(); }
    X.restore();
  }
  // D: map with fab labs / makerspaces / university
  const dk = pr(t, 134.9, 135.4);
  if (dk > 0) {
    const zs = 1 + .08 * pr(t, 135, 139); X.save(); X.globalAlpha *= dk; X.translate(W / 2, H / 2); X.scale(zs, zs); X.translate(-W / 2, -H / 2);
    rr(100, 80, W - 200, H - 160, 40); fillC('#141A44');
    X.save(); rr(100, 80, W - 200, H - 160, 40); X.clip();
    X.strokeStyle = '#2B3577'; X.lineWidth = 60; X.beginPath(); X.moveTo(0, 820); X.bezierCurveTo(500, 700, 900, 980, 1400, 760); X.lineTo(2000, 640); X.stroke();
    for (let gx = 140; gx < W - 100; gx += 170) for (let gy = 120; gy < H - 100; gy += 150) { rr(gx, gy, 140, 118, 14); fillC((gx + gy) % 340 === 0 ? '#1D2A5E' : '#1A2052'); }
    X.strokeStyle = 'rgba(255,255,255,.07)'; X.lineWidth = 30; for (let gx = 125; gx < W; gx += 170) { X.beginPath(); X.moveTo(gx, 0); X.lineTo(gx, H); X.stroke(); } for (let gy = 105; gy < H; gy += 150) { X.beginPath(); X.moveTo(0, gy); X.lineTo(W, gy); X.stroke(); }
    X.restore();
    const pins = [[640, 420, 135.1, P.org, 'FAB LAB'], [1260, 330, 135.85, P.cyan, 'MAKERSPACE'], [990, 690, 137.08, P.yel, 'UNIVERSITY']];
    pins.forEach(([x, y, t0, col, label], i) => {
      const k = pr(t, t0, t0 + .5); if (k <= 0) return; const drop = (1 - E.out(k)) * -300;
      for (let r = 0; r < 2; r++) { const ph = ((t - t0) * .8 + r * .5) % 1; X.save(); X.globalAlpha *= (1 - ph) * .7; ell(x, y, 30 + ph * 120, (30 + ph * 120) * .4); strokeC(col, 4); X.restore(); }
      G(x, y + drop, 1, 0, clamp(k * 3), () => { X.beginPath(); X.moveTo(0, 0); X.bezierCurveTo(-20, -40, -50, -60, -50, -100); X.arc(0, -100, 50, Math.PI, 0); X.bezierCurveTo(50, -60, 20, -40, 0, 0); X.shadowColor = hexA(col, .7); X.shadowBlur = 30; fillC(col); X.shadowBlur = 0; circ(0, -100, 22); fillC('#141A44');
        if (i === 2) { poly([[-18, -102], [0, -112], [18, -102], [0, -92]]); fillC(col); } });
      chip(x, y - 200, label, col, pr(t, t0 + .3, t0 + .7), 32);
    });
    const fk = pop(t, 137.9, .5); if (fk > 0) { G(990, 690 - 340, fk, 0, 1, () => stack({ cx: 0, cy: 60, s: .45, yaw: t, pitch: .45, n: 60, lh: 4.5, contour: heroC, color: heroColor, R: 125 })); chip(1110, 260, '#1', P.yel, pr(t, 138.1, 138.5), 40); sparkles(990, 330, t, 137.9, 51, 10, 160, P.yel); }
    X.restore();
  }
}

// 15 — "Everything around you started as an idea… now it can be in your hand in a few hours" (138.95 – 152.5)
const HEAD = new Path2D('M -120 220 L -110 120 C -200 70 -210 -70 -150 -150 C -95 -230 50 -245 115 -170 C 155 -125 165 -80 152 -40 L 192 32 C 198 44 186 50 170 52 L 166 82 C 170 96 160 106 150 108 L 154 126 C 152 150 122 162 92 152 L 72 150 L 64 220 Z');
const HAND = new Path2D('M -260 140 L -120 60 C -90 40 -40 30 20 34 L 170 26 C 200 26 204 56 176 60 L 70 64 L 190 66 C 216 68 214 96 188 98 L 70 98 L 176 104 C 200 108 196 134 172 134 L 60 132 L 150 140 C 170 144 166 166 146 166 L 10 168 C -60 172 -100 190 -200 230 Z M -40 40 C -60 0 -50 -40 -20 -50 C 0 -54 10 -40 4 -20 L -10 34 Z');
const LINE_ICONS = [
  () => { X.beginPath(); X.moveTo(-36, -50); X.lineTo(36, -50); X.lineTo(28, 50); X.lineTo(-28, 50); X.closePath(); X.stroke(); },
  () => { rr(-30, -56, 60, 112, 14); X.stroke(); X.beginPath(); X.moveTo(-10, 42); X.lineTo(10, 42); X.stroke(); },
  () => { rr(-30, -60, 60, 52, 10); X.stroke(); rr(-38, -6, 76, 16, 6); X.stroke(); X.beginPath(); X.moveTo(-30, 10); X.lineTo(-34, 56); X.moveTo(30, 10); X.lineTo(34, 56); X.stroke(); },
  () => { X.beginPath(); X.arc(0, -14, 34, Math.PI * .8, Math.PI * 2.2); X.lineTo(14, 30); X.lineTo(-14, 30); X.closePath(); X.stroke(); X.beginPath(); X.moveTo(-12, 42); X.lineTo(12, 42); X.stroke(); },
  () => { X.beginPath(); X.moveTo(-60, 20); X.lineTo(-50, -10); X.lineTo(-24, -30); X.lineTo(30, -30); X.lineTo(50, -6); X.lineTo(64, 0); X.lineTo(64, 20); X.closePath(); X.stroke(); circ(-30, 24, 13); X.stroke(); circ(36, 24, 13); X.stroke(); },
  () => { X.beginPath(); X.arc(0, 0, 46, Math.PI, 0); X.stroke(); rr(-56, -4, 22, 44, 8); X.stroke(); rr(34, -4, 22, 44, 8); X.stroke(); },
  () => { X.beginPath(); X.moveTo(-50, 50); X.lineTo(-50, -10); X.lineTo(0, -50); X.lineTo(50, -10); X.lineTo(50, 50); X.closePath(); X.stroke(); rr(-14, 14, 28, 36, 4); X.stroke(); },
  () => { rr(-56, -36, 112, 70, 8); X.stroke(); X.beginPath(); X.moveTo(-70, 46); X.lineTo(70, 46); X.stroke(); },
];
function sInspire(t) {
  const conv = E.io(pr(t, 142.1, 143.5)), headK = E.out(pr(t, 141.9, 142.6)), slide = E.io(pr(t, 145.5, 146.4));
  const hx0 = lerp(W / 2, 560, slide), hy0 = 520;
  // orbiting everyday objects
  if (conv < 1) {
    LINE_ICONS.forEach((fn, i) => {
      const k = pop(t, 139.3 + i * .14, .5); if (k <= 0) return; const a = i / 8 * TAU + t * .25, rad = lerp(1, .05, conv);
      const x = W / 2 + Math.cos(a) * 560 * rad, y = 520 + Math.sin(a) * 300 * rad;
      G(x, y, k * lerp(1.3, .3, conv), 0, 1 - conv, () => { X.strokeStyle = [P.org, P.pink, P.cyan, P.yel, P.pur, P.grn, P.wht, P.org][i]; X.lineWidth = 6; X.lineJoin = 'round'; X.lineCap = 'round'; fn(); });
    });
  }
  // head
  if (headK > 0) {
    G(hx0, hy0, headK * 1.25, 0, headK, () => { X.shadowColor = hexA(P.cyan, .6); X.shadowBlur = 40; X.fillStyle = '#251F66'; X.fill(HEAD); X.shadowBlur = 0; X.strokeStyle = P.cyan; X.lineWidth = 5; X.stroke(HEAD);
      X.save(); X.clip(HEAD); X.globalAlpha *= .25; X.strokeStyle = P.cyan; X.lineWidth = 2; for (let i = 0; i < 9; i++) { X.beginPath(); X.arc(-10, -60, 30 + i * 18, 0, TAU); X.stroke(); } X.restore(); });
  }
  // the idea bulb
  const bk = pop(t, 143.2, .5);
  if (bk > 0) {
    const fly = E.io(pr(t, 145.9, 147.2)), lx = 1430, ly = 560;
    const sx = hx0 - 10 * 1.25, sy = hy0 - 70 * 1.25, x = lerp(sx, lx, fly), y = lerp(sy, ly, fly) - Math.sin(fly * Math.PI) * 260;
    const shrink = 1 - pr(t, 147.3, 147.7);
    if (shrink > 0) { G(x, y, .9 * bk * shrink, Math.sin(fly * Math.PI) * .4, 1, () => bulb(0, 0, 1, 1)); if (fly > 0 && fly < 1) { X.save(); for (let i = 1; i < 8; i++) { const f2 = Math.max(0, fly - i * .025); circ(lerp(sx, lx, f2), lerp(sy, ly, f2) - Math.sin(f2 * Math.PI) * 260, 14 - i * 1.5); fillC(hexA(P.yel, .5 - i * .06)); } X.restore(); } }
    burst(sx, sy, t, 143.25, P.yel, 160, 14);
  }
  // hand + printed idea
  const hk = E.out(pr(t, 147.0, 147.6));
  if (hk > 0) {
    G(1470, 730 + (1 - hk) * 400, 1.3, 0, hk, () => { X.shadowColor = hexA(P.cyan, .6); X.shadowBlur = 40; X.fillStyle = '#251F66'; X.fill(HAND, 'nonzero'); X.shadowBlur = 0; X.strokeStyle = P.cyan; X.lineWidth = 4; X.stroke(HAND); });
    const pk = E.io(pr(t, 147.4, 149.0));
    X.save(); stack({ cx: 1470 + 40, cy: 730 + 40, s: .72, yaw: t * .6, pitch: .4, n: 60, lh: 5, prog: pk, contour: bulbC, color: heroColor, R: 110 }); X.restore();
    if (pk >= 1) { const g = X.createRadialGradient(1510, 560, 0, 1510, 560, 300); g.addColorStop(0, hexA(P.yel, .35)); g.addColorStop(1, hexA(P.yel, 0)); X.fillStyle = g; X.fillRect(1100, 300, 800, 700); }
    burst(1510, 580, t, 149.0, P.yel, 200, 16);
  }
  // clock: "in just a few hours"
  const ck = pop(t, 149.2, .5) * (1 + .15 * Math.sin(pr(t, 150.68, 151.0) * Math.PI));
  if (ck > 0) {
    G(1060, 250, ck, 0, 1, () => {
      circ(0, 0, 120); X.shadowColor = 'rgba(0,0,0,.5)'; X.shadowBlur = 30; fillC('#F4F1FF'); X.shadowBlur = 0; strokeC(P.org, 10);
      const hk2 = E.io(pr(t, 149.4, 150.6)) * 3;
      X.save(); X.globalAlpha *= .35; X.beginPath(); X.moveTo(0, 0); X.arc(0, 0, 104, -Math.PI / 2, -Math.PI / 2 + hk2 / 12 * TAU); X.closePath(); fillC(P.org); X.restore();
      for (let i = 0; i < 12; i++) { X.save(); X.rotate(i / 12 * TAU); rr(-3, -104, 6, i % 3 ? 12 : 22, 3); fillC('#2B2F66'); X.restore(); }
      X.save(); X.rotate(hk2 / 12 * TAU); rr(-6, -60, 12, 66, 6); fillC('#2B2F66'); X.restore();
      X.save(); X.rotate(hk2 * TAU); rr(-4, -90, 8, 96, 4); fillC(P.org); X.restore(); circ(0, 0, 10); fillC('#2B2F66');
    });
    chip(1060, 420, '~ 3 h', P.org, pr(t, 150.6, 151.0), 40);
  }
}

// 16 — Finale: "يلا… اطبع فكرتك" (152.5 – 156)
function sFinale(t) {
  X.save(); X.translate(W / 2, 520); X.rotate(t * .15); X.globalAlpha *= pr(t, 152.5, 153.0);
  for (let i = 0; i < 20; i++) { X.rotate(TAU / 20); X.beginPath(); X.moveTo(0, 0); X.lineTo(1500, -110); X.lineTo(1500, 110); X.closePath(); fillC(i % 2 ? hexA(P.org, .1) : hexA(P.pink, .06)); } X.restore();
  const c = { cx: W / 2, cy: 860, s: 1.55, yaw: .4 + t * .35, pitch: .42 };
  plate(c, 220, '#1B1F4A', P.cyan, pop(t, 152.6, .5));
  const prog = .22 * E.io(pr(t, 152.9, 154.4)) + .78 * E.io(pr(t, 154.51, 155.35));
  const res = stack({ ...c, n: 60, lh: 5, prog, contour: bulbC, color: heroColor, R: 110 });
  const done = pr(t, 155.35, 155.6);
  const sw = E.out(pr(t, 152.81, 153.2)); const hx2 = lerp(-200, res.tip[0], sw), hy2 = lerp(-200, res.tip[1], sw) - 900 * E.in(done);
  if (sw > 0) hotend(hx2, hy2, .85, 1, P.org, 900);
  if (done > 0) {
    const g = X.createRadialGradient(W / 2, 520, 0, W / 2, 520, 700); g.addColorStop(0, hexA(P.yel, .55 * done)); g.addColorStop(1, hexA(P.yel, 0)); X.fillStyle = g; X.fillRect(0, 0, W, H);
    burst(W / 2, 520, t, 155.35, '#FFF3C4', 340, 24, .7); sparkles(W / 2, 520, t, 155.35, 77, 22, 520, P.yel, 1);
  }
  const end = pr(t, 155.75, 156); if (end > 0) { X.fillStyle = `rgba(5,6,20,${end})`; X.fillRect(0, 0, W, H); }
}

// =====================================================================
// timeline
// =====================================================================
const SCN = [
  { a: 0, f: sHook, tint: P.cyan },
  { a: 5.55, f: sFactory, tint: P.pur, tr: 'wipe', c: [P.org, P.pink] },
  { a: 12.4, f: sRoom, tint: P.pur, tr: 'fade' },
  { a: 18.6, f: sTitle, tint: P.org, tr: 'wipe', c: [P.yel, P.org] },
  { a: 25.2, f: sCake, tint: P.pink, tr: 'fade' },
  { a: 37.7, f: sAnatomy, tint: P.org, tr: 'wipe', c: [P.cyan, P.pur] },
  { a: 48.4, f: sLayers, tint: P.pink, tr: 'fade' },
  { a: 65.0, f: sCAD, tint: P.cyan, tr: 'wipe', c: [P.pink, P.org] },
  { a: 73.2, f: sSlicer, tint: P.cyan, tr: 'fade' },
  { a: 84.6, f: sWow, tint: P.yel, tr: 'wipe', c: [P.yel, P.pink] },
  { a: 90.0, f: sUses, tint: P.pur, tr: 'fade' },
  { a: 107.9, f: sImportant, tint: P.yel, tr: 'wipe', c: [P.cyan, P.org] },
  { a: 114.3, f: sLoop, tint: P.cyan, tr: 'fade' },
  { a: 124.9, f: sStart, tint: P.pink, tr: 'wipe', c: [P.org, P.yel] },
  { a: 138.95, f: sInspire, tint: P.pur, tr: 'wipe', c: [P.pur, P.cyan] },
  { a: 152.5, f: sFinale, tint: P.org, tr: 'wipe', c: [P.yel, P.pink] },
];
function tintAt(t) { let i = 0; while (i + 1 < SCN.length && t >= SCN[i + 1].a) i++; const nx = SCN[i + 1]; const k = nx ? pr(t, nx.a - .5, nx.a + .5) : 0; return mixc(SCN[i].tint, nx ? nx.tint : SCN[i].tint, k); }
function wipe(t, tb, cols) {
  const p = pr(t, tb - .38, tb + .38); if (p <= 0 || p >= 1) return; const sk = 420;
  const lead = lerp(-sk, W + sk + 200, E.io(Math.min(1, p * 2))), trail = lerp(-sk - 200, W + sk, E.io(Math.max(0, p * 2 - 1)));
  [[cols[1], 90], [cols[0], 0]].forEach(([c, off]) => { const l = lead + off * (p < .5 ? 1 : 0), tr = trail - off * (p > .5 ? 1 : 0) + off; X.beginPath(); X.moveTo(tr, 0); X.lineTo(l, 0); X.lineTo(l - sk, H); X.lineTo(tr - sk, H); X.closePath(); fillC(c); });
}
function render(t) {
  X.setTransform(1, 0, 0, 1, 0, 0); X.globalAlpha = 1; X.shadowBlur = 0; X.setLineDash([]);
  background(t, tintAt(t));
  for (let k = 0; k < SCN.length; k++) {
    const sc = SCN[k], a = sc.a, nx = SCN[k + 1], b = nx ? nx.a : 1e9; let al = 1;
    if (sc.tr === 'fade') { if (t < a - .3) continue; al *= pr(t, a - .3, a + .3); } else if (t < a) continue;
    if (nx) { if (nx.tr === 'fade') { if (t > b + .3) continue; al *= 1 - pr(t, b - .3, b + .3); } else if (t >= b) continue; }
    X.save(); X.globalAlpha = al; try { sc.f(t); } catch (e) { console.error(sc.f.name, e); } X.restore();
    X.setTransform(1, 0, 0, 1, 0, 0); X.globalAlpha = 1; X.shadowBlur = 0; X.setLineDash([]);
  }
  vignette();
  for (const sc of SCN) if (sc.tr === 'wipe') wipe(t, sc.a, sc.c);
}
window.render = render; window.C = C;
