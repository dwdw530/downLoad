/* Real XHS note page -> unpacked extension -> isolated download.
 * Usage: node scripts/smoke_xhs.cjs <playwright package> <python.exe>
 * Mirrors smoke_douyin.cjs: temporary profile, native transport captured,
 * the user's registered downloader is never invoked and history stays untouched.
 * Only run against a note URL the user provides and can access (login required).
 */
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const {spawn} = require('node:child_process');
const {chromium} = require(process.argv[2] || 'playwright');

const NOTE_URL = process.env.XHS_NOTE_URL || '';
if (!/^https:\/\/(?:www\.)?xiaohongshu\.com\/(?:explore|discovery\/item)\/[0-9a-f]{24}/.test(NOTE_URL)) {
  console.error('Set XHS_NOTE_URL to a note detail page the user can open, then rerun.');
  process.exit(2);
}
const NOTE_ID = NOTE_URL.match(/[0-9a-f]{24}/)[0];

async function main() {
  const root = path.resolve(__dirname, '..');
  const extension = path.join(root, 'chrome-extension');
  const profile = await fs.mkdtemp(path.join(os.tmpdir(), 'xhs-extension-'));
  const output = path.join(root, 'output', 'playwright');
  await fs.mkdir(output, {recursive: true});
  const context = await chromium.launchPersistentContext(profile, {
    channel: 'chromium', headless: false, viewport: {width: 1440, height: 900},
    args: [`--disable-extensions-except=${extension}`, `--load-extension=${extension}`]
  });
  try {
    const worker = context.serviceWorkers()[0] || await context.waitForEvent('serviceworker');
    await worker.evaluate(() => {
      globalThis.downloadMessages = [];
      chrome.runtime.connectNative = () => {
        const listeners = [];
        return {onMessage: {addListener: fn => listeners.push(fn)}, onDisconnect: {addListener: () => {}},
          disconnect: () => {}, postMessage: message => {
            if (message.action === 'download') globalThis.downloadMessages.push(message);
            const response = message.action === 'ping' ? {ok: true, capabilities: ['xhs']}
              : {ok: true, filename: `XHS-${message.video?.id || 'unknown'}.mp4`};
            setTimeout(() => listeners.forEach(fn => fn(response)), 0);
          }};
      };
      return chrome.storage.local.set({enabled: true, videoHeight: 720});
    });
    const page = context.pages()[0];
    await page.goto(NOTE_URL, {waitUntil: 'domcontentloaded', timeout: 45000});
    await page.getByRole('button', {name: '下载视频', exact: true}).first().click();
    await page.getByText('小红书 · 最高 720p · MP4 音视频', {exact: true}).first().waitFor();
    await page.screenshot({path: path.join(output, 'xhs-live-source.png')});
    await page.locator('laowang-video-assistant')
      .filter({has: page.getByText('小红书 · 最高 720p · MP4 音视频', {exact: true})})
      .getByRole('button', {name: '下载', exact: true}).first().click();
    await page.getByRole('button', {name: '已发送', exact: true}).first().waitFor();
    const messages = await worker.evaluate(() => globalThis.downloadMessages);
    assert.equal(messages.length, 1);
    const message = messages[0];
    assert.equal(message.kind, 'xhs');
    assert.equal(message.url, `https://www.xiaohongshu.com/explore/${NOTE_ID}`);
    assert.equal(message.video.id, NOTE_ID);
    assert(message.video.duration > 0);
    assert(message.video.formats.length > 0);
    assert(message.video.formats.every(format => new URL(format.url).hostname.endsWith('.xhscdn.com')));
    assert(message.video.formats.every(format => new URL(format.url).protocol === 'https:'));
    console.log('PASS: real XHS note page and extension select official signed streams');
    const args = ['-B', 'scripts/smoke_youtube.py', message.url, '--height', String(message.height), '--browser-context-stdin'];
    const child = spawn(process.argv[3] || 'python', args, {
      cwd: root, stdio: ['pipe', 'inherit', 'inherit'], windowsHide: true,
      env: {...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8'}
    });
    child.stdin.end(JSON.stringify({headers: message.headers, cookies: message.cookies, video: message.video}));
    const code = await new Promise((resolve, reject) => {child.on('error', reject); child.on('close', resolve);});
    assert.equal(code, 0, 'Isolated download or full decode failed');
  } finally {
    await context.close();
  }
}
main().catch(error => {console.error(error); process.exitCode = 1;});
