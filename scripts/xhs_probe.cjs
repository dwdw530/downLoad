/* XHS note diagnostic with manual login window.
 * Usage: XHS_PROFILE=<dir> node scripts/xhs_probe.cjs <noteUrl>
 * A Chrome window opens: log in / solve the captcha manually (up to 5 minutes).
 * After the note page renders, the probe dumps page state, video elements and
 * the MAIN-world parse result. Read-only: never downloads, never calls native host.
 */
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const {chromium} = require('playwright-core');

const NOTE_URL = process.argv[2] || '';
if (!/^https:\/\/www\.xiaohongshu\.com\/(?:explore|discovery\/item)\/[0-9a-f]{24}/.test(NOTE_URL)) {
  console.error('Usage: XHS_PROFILE=<dir> node scripts/xhs_probe.cjs https://www.xiaohongshu.com/explore/<id>');
  process.exit(2);
}
const NOTE_ID = NOTE_URL.match(/[0-9a-f]{24}/)[0];
const extension = path.resolve(__dirname, '..', 'chrome-extension');

async function dump(page) {
  return page.evaluate(async noteId => {
    const out = {url: location.href, noteId};
    const m = /^\/(?:explore|discovery\/item)\/([0-9a-f]{24})\/?$/.exec(location.pathname);
    out.noteIdInUrl = m?.[1] || null;
    const s = window.__INITIAL_STATE__;
    out.hasState = !!s;
    if (s) {
      const map = s.note?.noteDetailMap;
      out.mapKeys = map ? Object.keys(map).slice(0, 6) : null;
      const note = out.noteIdInUrl && map?.[out.noteIdInUrl]?.note;
      if (note) {
        out.noteType = note.type;
        out.capaDuration = note.video?.capa?.duration;
        const st = note.video?.media?.stream;
        out.stream = null;
        if (st) {
          out.stream = {};
          for (const [k, v] of Object.entries(st)) {
            out.stream[k] = Array.isArray(v) ? v.map(i => ({
              codec: i?.videoCodec, st: i?.streamType, h: i?.height, w: i?.width,
              dur: i?.duration, url: (i?.masterUrl || '').slice(0, 50)})) : typeof v;
          }
        }
        out.videoKeys = Object.keys(note.video || {});
      } else out.noteFound = false;
      out.h5 = !!s.noteData?.data?.noteData;
    }
    out.videos = [...document.querySelectorAll('video')].map((v, i) => {
      const r = v.getBoundingClientRect();
      return {i, w: Math.round(r.width), h: Math.round(r.height), dur: v.duration,
              visible: r.width > 0 && r.height > 0, src: (v.currentSrc || v.src || '').slice(0, 60)};
    });
    if (out.videos.length) {
      const video = document.querySelector('video');
      const nonce = crypto.randomUUID();
      const resp = await new Promise(resolve => {
        const receive = e => { if (e.target === video) { video.removeEventListener('daw-xhs-data', receive); resolve(e.detail); } };
        video.addEventListener('daw-xhs-data', receive);
        video.dispatchEvent(new CustomEvent('daw-xhs-read', {bubbles: true, detail: nonce}));
        setTimeout(() => resolve(null), 3000);
      });
      const parsed = resp ? JSON.parse(resp) : null;
      out.mainWorldDetail = parsed?.detail
        ? {id: parsed.detail.id, duration: parsed.detail.duration, formats: parsed.detail.formats.length}
        : (resp ? 'null-detail' : 'no-response');
    }
    return out;
  }, NOTE_ID);
}

async function main() {
  const profile = process.env.XHS_PROFILE ? path.resolve(process.env.XHS_PROFILE) : await fs.mkdtemp(path.join(os.tmpdir(), 'xhs-probe-'));
  const output = path.resolve(__dirname, '..', 'output', 'playwright');
  await fs.mkdir(output, {recursive: true});
  const context = await chromium.launchPersistentContext(profile, {
    executablePath: 'C:/Users/lenovo/AppData/Local/ms-playwright/chromium-1212/chrome-win64/chrome.exe',
    headless: false, viewport: {width: 1440, height: 900},
    args: [`--disable-extensions-except=${extension}`, `--load-extension=${extension}`]
  });
  try {
    const page = context.pages()[0] || await context.newPage();
    await page.goto(NOTE_URL, {waitUntil: 'domcontentloaded', timeout: 45000}).catch(() => {});
    console.log('>>请在弹出的窗口中登录/完成安全验证（最多 5 分钟），到达笔记页后自动继续...');
    const deadline = Date.now() + 300000;
    let ok = false;
    while (Date.now() < deadline) {
      await page.waitForTimeout(3000);
      if (/\/(?:explore|discovery\/item)\/[0-9a-f]{24}/.test(new URL(page.url()).pathname)) { ok = true; break; }
    }
    if (!ok) { console.log('超时，当前页面:', page.url().slice(0, 120)); }
    await page.waitForTimeout(6000);
    const info = await dump(page);
    console.log(JSON.stringify(info, null, 1));
    await page.screenshot({path: path.join(output, 'xhs-probe.png')});
    console.log('Screenshot:', path.join(output, 'xhs-probe.png'));
    await page.waitForTimeout(60000); // keep window open briefly for manual re-check
  } finally { await context.close(); }
}
main().catch(e => {console.error(e); process.exit(1);});
