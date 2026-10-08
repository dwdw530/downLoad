/* Real Douyin page -> unpacked extension -> isolated source/packaged download.
 * Usage: node scripts/smoke_douyin.cjs <playwright package> <python.exe> [--exe]
 * Native transport is captured; the user's registered downloader is never invoked.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const {spawn} = require('node:child_process');
const {chromium} = require(process.argv[2] || 'playwright');

async function main() {
  const root = path.resolve(__dirname, '..');
  const packaged = process.argv.includes('--exe');
  const extension = path.join(root, ...(packaged ? ['dist', 'chrome-extension'] : ['chrome-extension']));
  const profile = await fs.mkdtemp(path.join(os.tmpdir(), 'douyin-extension-'));
  const output = path.join(root, 'output', 'playwright');
  await fs.mkdir(output, {recursive:true});
  const context = await chromium.launchPersistentContext(profile, {
    channel:'chromium', headless:true, viewport:{width:1440,height:900},
    args:[`--disable-extensions-except=${extension}`, `--load-extension=${extension}`]
  });
  try {
    const worker = context.serviceWorkers()[0] || await context.waitForEvent('serviceworker');
    await worker.evaluate(()=>{
      globalThis.downloadMessages=[];
      chrome.runtime.connectNative=()=>{
        const listeners=[];
        return {onMessage:{addListener:fn=>listeners.push(fn)}, onDisconnect:{addListener:()=>{}},
          disconnect:()=>{}, postMessage:message=>{
            if(message.action==='download') globalThis.downloadMessages.push(message);
            const response=message.action==='ping' ? {ok:true,capabilities:['douyin']}
              : {ok:true,filename:'Douyin-7686532267488840975.mp4'};
            setTimeout(()=>listeners.forEach(fn=>fn(response)),0);
          }};
      };
      return chrome.storage.local.set({enabled:true,videoHeight:720});
    });
    const page = context.pages()[0];
    const url = 'https://www.douyin.com/video/7686532267488840975';
    const metadata = page.waitForResponse(response => response.url().includes('/aweme/v1/web/aweme/detail/') && response.ok(), {timeout:45000});
    await page.goto(url, {waitUntil:'domcontentloaded', timeout:45000});
    const detail = await (await metadata).json();
    assert(detail.aweme_detail?.video, 'Douyin did not provide video metadata');
    await page.getByRole('button',{name:'下载视频',exact:true}).first().click();
    await page.getByText('抖音 · 最高 720p · MP4 音视频',{exact:true}).first().waitFor();
    await page.screenshot({path:path.join(output, packaged ? 'douyin-live-dist.png' : 'douyin-live-source.png')});
    await page.locator('laowang-video-assistant').filter({has:page.getByText('抖音 · 最高 720p · MP4 音视频',{exact:true})})
      .getByRole('button',{name:'下载',exact:true}).first().click();
    await page.getByRole('button',{name:'已发送',exact:true}).first().waitFor();
    const messages = await worker.evaluate(()=>globalThis.downloadMessages);
    assert.equal(messages.length,1);
    const message = messages[0];
    assert.equal(message.kind,'douyin');
    assert.equal(message.url,url);
    assert(message.cookies.length>0);
    assert(message.cookies.every(cookie=>cookie.domain.replace(/^\./,'').endsWith('douyin.com')));
    assert.equal(message.headers.Cookie,undefined);
    assert.equal(message.video.id,'7686532267488840975');
    assert(message.video.formats.length>0);
    console.log('PASS: real Douyin page and extension select a complete video with scoped browser context');
    const args = ['-B', 'scripts/smoke_youtube.py', url, '--height', String(message.height), '--browser-context-stdin'];
    if(packaged) args.push('--exe');
    const child = spawn(process.argv[3] || 'python', args, {
      cwd:root, stdio:['pipe','inherit','inherit'], windowsHide:true,
      env:{...process.env, PYTHONUTF8:'1', PYTHONIOENCODING:'utf-8'}
    });
    child.stdin.end(JSON.stringify({headers:message.headers,cookies:message.cookies,video:message.video}));
    const code = await new Promise((resolve,reject)=>{child.on('error',reject);child.on('close',resolve);});
    assert.equal(code,0,'Isolated download or full decode failed');
  } finally {
    await context.close();
  }
}
main().catch(error=>{console.error(error);process.exitCode=1;});
