// Renders index.html frame-by-frame in headless Chrome, then muxes with vo.wav.
// Usage:
//   node render.mjs                       -> full video (out.mp4)
//   node render.mjs --stills 1.9,20,45    -> PNG stills into ./stills
import { spawn, execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';

const DIR = path.dirname(new URL(import.meta.url).pathname);
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const FPS = 30, DUR = 156, TOTAL = FPS * DUR;
const args = process.argv.slice(2);
const stillsArg = args.includes('--stills') ? args[args.indexOf('--stills') + 1] : null;
const WORKERS = Number(process.env.WORKERS || 6);
const FRAMES = process.env.FRAMES_DIR || path.join(os.tmpdir(), 'printing3d-frames');

const sleep = ms => new Promise(r => setTimeout(r, ms));

async function launch(port) {
  const udd = fs.mkdtempSync(path.join(os.tmpdir(), 'chr-'));
  const proc = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${port}`, `--user-data-dir=${udd}`, '--no-first-run',
    '--no-default-browser-check', '--hide-scrollbars', '--allow-file-access-from-files', '--window-size=1920,1080', 'about:blank'], { stdio: 'ignore' });
  let target;
  for (let i = 0; i < 100 && !target; i++) {
    await sleep(150);
    try { const list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json(); target = list.find(t => t.type === 'page'); } catch { }
  }
  if (!target) throw new Error('chrome did not start');
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  let id = 0; const pending = new Map();
  ws.onmessage = ev => { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)) { const p = pending.get(m.id); pending.delete(m.id); m.error ? p.rej(new Error(JSON.stringify(m.error))) : p.res(m.result); } };
  const send = (method, params = {}) => new Promise((res, rej) => { const i = ++id; pending.set(i, { res, rej }); ws.send(JSON.stringify({ id: i, method, params })); });
  await send('Page.enable'); await send('Runtime.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: 1920, height: 1080, deviceScaleFactor: 1, mobile: false });
  await send('Page.addScriptToEvaluateOnNewDocument', { source: 'window.__RENDER__=true;' });
  await send('Page.navigate', { url: 'file://' + path.join(DIR, 'index.html') });
  for (let i = 0; i < 100; i++) { await sleep(100); const r = await send('Runtime.evaluate', { expression: 'typeof render==="function" && document.readyState==="complete"', returnByValue: true }); if (r.result.value) break; }
  await send('Runtime.evaluate', { expression: 'document.fonts.ready.then(()=>1)', awaitPromise: true });
  const frame = async (t, type = 'image/jpeg', q = 0.95) => {
    const r = await send('Runtime.evaluate', { expression: `render(${t}); C.toDataURL('${type}', ${q})`, returnByValue: true });
    if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails));
    return Buffer.from(r.result.value.split(',')[1], 'base64');
  };
  const logs = async () => (await send('Runtime.evaluate', { expression: '1' }));
  return { frame, close: () => { ws.close(); proc.kill(); }, logs };
}

if (stillsArg) {
  const out = path.join(DIR, 'stills'); fs.mkdirSync(out, { recursive: true });
  const b = await launch(9400);
  for (const s of stillsArg.split(',').map(Number)) { const t0 = Date.now(); fs.writeFileSync(path.join(out, `t${s.toFixed(2)}.png`), await b.frame(s, 'image/png')); console.log('still', s, Date.now() - t0, 'ms'); }
  b.close(); process.exit(0);
}

fs.rmSync(FRAMES, { recursive: true, force: true }); fs.mkdirSync(FRAMES, { recursive: true });
const start = Date.now(); let done = 0;
const chunk = Math.ceil(TOTAL / WORKERS);
await Promise.all(Array.from({ length: WORKERS }, async (_, w) => {
  const b = await launch(9500 + w);
  for (let f = w * chunk; f < Math.min(TOTAL, (w + 1) * chunk); f++) {
    fs.writeFileSync(path.join(FRAMES, `f${String(f).padStart(5, '0')}.jpg`), await b.frame(f / FPS));
    if (++done % 150 === 0) console.log(`${done}/${TOTAL} frames  ${((Date.now() - start) / 1000).toFixed(0)}s`);
  }
  b.close();
}));
console.log('frames done, encoding…');
execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-framerate', String(FPS), '-i', path.join(FRAMES, 'f%05d.jpg'), '-i', path.join(DIR, 'vo.wav'),
  '-c:v', 'libx264', '-preset', 'slow', '-crf', '17', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-movflags', '+faststart', '-shortest',
  path.join(DIR, '3d-printing-explainer.mp4')], { stdio: 'inherit' });
console.log('done in', ((Date.now() - start) / 1000).toFixed(0), 's');
