/* Real unpacked-extension smoke test. Pass the path to an installed playwright package. */
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const http = require('node:http');
const path = require('node:path');
const os = require('node:os');
const {chromium} = require(process.argv[2] || 'playwright');

async function main() {
  const root = path.resolve(__dirname, '..');
  const output = path.resolve(process.env.DAW_SMOKE_OUTPUT || path.join(root, 'output', 'playwright'));
  await fs.mkdir(output, {recursive:true});
  const profile = await fs.mkdtemp(path.join(os.tmpdir(), 'video-extension-'));
  const releaseIndex = process.argv.indexOf('--release');
  const extension = releaseIndex >= 0 ? path.resolve(process.argv[releaseIndex + 1], 'chrome-extension')
    : path.join(root, ...(process.argv.includes('--dist') ? ['dist', 'chrome-extension'] : ['chrome-extension']));
  const context = await chromium.launchPersistentContext(profile, {
    channel:'chromium', headless:true, viewport:{width:1280,height:900},
    args:[`--disable-extensions-except=${extension}`,`--load-extension=${extension}`]
  });
  let server;
  try {
    const page = context.pages()[0];
    const worker = context.serviceWorkers()[0] || await context.waitForEvent('serviceworker');
    // A test profile still sees HKCU native hosts. Never send fixtures to the user's running downloader.
    await worker.evaluate(()=>{chrome.runtime.connectNative=()=>{throw new Error('Native transport disabled in UI smoke test');};});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const bytes = await page.evaluate(async () => {
      const canvas = document.createElement('canvas');
      canvas.width=640; canvas.height=360;
      const ctx=canvas.getContext('2d');
      const draw = frame => {
        ctx.fillStyle='#dce9ef';ctx.fillRect(0,0,640,360);
        ctx.fillStyle='#186956';ctx.fillRect(32,32,576,296);
        ctx.fillStyle='#f0c952';ctx.fillRect(62 + frame*3,180,110,100);
        ctx.fillStyle='#e9f4f0';ctx.font='bold 38px sans-serif';ctx.fillText('VIDEO SAMPLE',62,110);
        ctx.font='18px sans-serif';ctx.fillText('640 x 360 / WebM',62,146);
      };
      draw(0);
      const stream=canvas.captureStream(12);
      const recorder=new MediaRecorder(stream,{mimeType:'video/webm;codecs=vp8'});
      const chunks=[];
      recorder.ondataavailable=event=>chunks.push(event.data);
      const done=new Promise(resolve=>recorder.onstop=resolve);
      recorder.start();
      for(let i=0;i<18;i++){draw(i);await new Promise(resolve=>setTimeout(resolve,85));}
      recorder.stop();await done;stream.getTracks().forEach(track=>track.stop());
      return Array.from(new Uint8Array(await new Blob(chunks).arrayBuffer()));
    });
    const video=Buffer.from(bytes);
    assert(video.length>1000);
    await fs.writeFile(path.join(output,'sample.webm'),video);
    const html=await fs.readFile(path.join(root,'tests','fixtures','video_page.html'));
    server=http.createServer((req,res)=>{
      const pathname=new URL(req.url,'http://localhost').pathname;
      if(pathname.endsWith('.webm') || pathname==='/videoplayback') {
        const range=/bytes=(\d+)-(\d*)/.exec(req.headers.range||'');
        const start=range?Number(range[1]):0;
        const end=range&&range[2]?Math.min(Number(range[2]),video.length-1):video.length-1;
        if(start> end){res.writeHead(416);res.end();return;}
        const headers={'Content-Type':'video/webm','Content-Length':end-start+1,'Accept-Ranges':'bytes'};
        if(range) headers['Content-Range']=`bytes ${start}-${end}/${video.length}`;
        res.writeHead(range?206:200,headers);res.end(video.subarray(start,end+1));
      } else if(pathname==='/ump') {
        res.writeHead(200,{'Content-Type':'application/vnd.yt-ump'});res.end('stream fixture');
      } else if(pathname==='/blob-only') {
        res.writeHead(200,{'Content-Type':'text/html; charset=utf-8'});
        res.end('<video controls style="display:block;width:90%;aspect-ratio:16/9;margin:30px auto"></video>');
      } else if(req.url==='/iframe') {
        res.writeHead(200,{'Content-Type':'text/html; charset=utf-8'});
        res.end('<iframe src="/embed" style="width:800px;height:550px"></iframe>');
      } else if(req.url==='/master.m3u8') {
        res.writeHead(200,{'Content-Type':'application/vnd.apple.mpegurl'});res.end('#EXTM3U\n#EXT-X-ENDLIST\n');
      } else {res.writeHead(200,{'Content-Type':'text/html; charset=utf-8'});res.end(html);}
    });
    await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
    const base=`http://127.0.0.1:${server.address().port}`;
    await page.goto(base);
    await page.locator('video').first().evaluate(video=>video.play());
    await page.locator('video').first().evaluate(video=>video.pause());
    await page.getByRole('button',{name:'下载视频',exact:true}).first().waitFor();
    await page.getByRole('button',{name:'下载视频',exact:true}).first().click();
    await page.getByRole('button',{name:'下载',exact:true}).waitFor();
    assert.equal(await page.locator('laowang-video-assistant .row').count(),1);
    await page.screenshot({path:path.join(output,'video-desktop.png')});
    await page.getByRole('button',{name:'下载',exact:true}).click();
    await page.getByText('本地桥接不可用',{exact:true}).waitFor();
    console.log('PASS: real video detection, player association, safely simulated unavailable native transport');
    await page.setViewportSize({width:390,height:844});
    await page.waitForTimeout(300);
    const bounds=await page.locator('laowang-video-assistant .panel').first().boundingBox();
    assert(bounds.x>=0 && bounds.x+bounds.width<=391,JSON.stringify(bounds));
    await page.screenshot({path:path.join(output,'video-mobile.png')});
    await worker.evaluate(()=>chrome.storage.local.set({enabled:false}));
    await page.waitForFunction(()=>[...document.querySelectorAll('laowang-video-assistant')].every(el=>getComputedStyle(el).display==='none'));
    await worker.evaluate(()=>chrome.storage.local.set({enabled:true}));
    await page.getByRole('button',{name:'下载视频',exact:true}).first().waitFor();
    console.log('PASS: narrow viewport bounds and enable/disable');
    await page.evaluate(()=>fetch('/master.m3u8'));
    await page.waitForTimeout(300);
    const tabId=await worker.evaluate(async base=>(await chrome.tabs.query({})).find(tab=>tab.url===base+'/').id,base);
    let items=await worker.evaluate(async id=>(await chrome.storage.session.get(`media:${id}`))[`media:${id}`],tabId);
    assert(items.some(item=>item.kind==='hls'));
    const popup=await context.newPage();
    await popup.goto(worker.url().replace('background.js','popup.html'));
    await popup.waitForFunction(()=>document.getElementById('connection').textContent !== '正在检查下载器…');
    await popup.evaluate(async id=>{tabId=id;await refresh();},tabId);
    assert(await popup.getByText('HLS · 最高 720p · MP4 视频',{exact:true}).isVisible());
    assert(await popup.locator('.row').filter({hasText:'master.m3u8'}).getByRole('button',{name:'下载',exact:true}).isEnabled());
    await popup.screenshot({path:path.join(output,'video-popup.png')});
    await popup.close();
    await page.evaluate(()=>history.pushState({},'', '/next'));
    await page.waitForTimeout(300);
    items=await worker.evaluate(async id=>(await chrome.storage.session.get(`media:${id}`))[`media:${id}`],tabId);
    assert.equal(items.length,0);
    console.log('PASS: HLS recognition with enabled action, SPA cleanup, real popup');
    await page.goto(base+'/blob-only');
    await page.locator('video').evaluate((video,bytes)=>{
      video.src=URL.createObjectURL(new Blob([new Uint8Array(bytes)],{type:'video/webm'}));
    },bytes);
    await page.getByRole('button',{name:'下载视频',exact:true}).click();
    await page.getByText('检测到浏览器内视频源（blob），尚未获取可下载直链',{exact:true}).waitFor();
    await page.evaluate(async()=>{await fetch('/videoplayback?itag=137&range=0-99');await fetch('/ump',{method:'POST'});});
    await page.getByRole('button',{name:'关闭资源列表',exact:true}).click();
    await page.getByRole('button',{name:'下载视频',exact:true}).click();
    await page.waitForFunction(()=>{
      const host=document.querySelector('laowang-video-assistant');
      return host?.shadowRoot.querySelectorAll('.row').length===2;
    });
    for(const button of await page.getByRole('button',{name:'下载',exact:true}).all()) assert(await button.isDisabled());
    await page.screenshot({path:path.join(output,'video-stream-detected.png')});
    const streamPopup=await context.newPage();
    await streamPopup.goto(worker.url().replace('background.js','popup.html'));
    await streamPopup.waitForFunction(()=>document.getElementById('connection').textContent !== '正在检查下载器…');
    await streamPopup.evaluate(async id=>{tabId=id;await refresh();},tabId);
    assert.equal(await streamPopup.getByText('媒体流 · 分片或媒体流，请选择对应的 HLS/DASH 资源',{exact:true}).count(),2);
    await streamPopup.close();
    await page.evaluate(()=>history.pushState({},'', '/after-blob'));
    await page.waitForTimeout(300);
    const state=await worker.evaluate(id=>chrome.storage.session.get([`media:${id}`,`blob:${id}`]),tabId);
    assert.equal(state[`media:${tabId}`].length,0);
    assert.equal(state[`blob:${tabId}`].length,0);
    console.log('PASS: blob diagnostics, GET/XHR and POST stream discovery, disabled actions, popup and cleanup');
    await page.setViewportSize({width:1280,height:900});
    await page.goto(base+'/iframe');
    await page.frameLocator('iframe').getByRole('button',{name:'下载视频',exact:true}).first().click();
    await page.frameLocator('iframe').getByRole('button',{name:'下载',exact:true}).waitFor();
    assert.equal(errors.length,0,JSON.stringify(errors));
    console.log('PASS: iframe player and no page exceptions');
    // Exercise YouTube's page-based path without using a real account or user native host.
    await context.route('https://www.youtube.com/**', route => route.fulfill({
      contentType:'text/html; charset=utf-8', body:'<!doctype html><title>YouTube fixture - YouTube</title><video controls style="display:block;width:90%;aspect-ratio:16/9;margin:30px auto"></video>'
    }));
    await worker.evaluate(() => {
      globalThis.nativeMessages = [];
      chrome.runtime.connectNative = () => {
        const listeners = [];
        return {onMessage:{addListener:fn=>listeners.push(fn)}, onDisconnect:{addListener:()=>{}},
          disconnect:()=>{}, postMessage:message=>{
            globalThis.nativeMessages.push(message);
            const response=message.action==='ping' ? {ok:false,installed:true,capabilities:globalThis.testCapabilities || ['youtube', 'x', 'bilibili']}
              : {ok:true,task_id:'fixture',filename:message.filename+'.mp4'};
            setTimeout(()=>listeners.forEach(fn=>fn(response)),0);
          }};
      };
    });
    await page.goto('https://www.youtube.com/watch?v=ayl6TcSsre8&t=1s');
    await page.locator('video').evaluate((video,bytes)=>{
      video.src=URL.createObjectURL(new Blob([new Uint8Array(bytes)],{type:'video/webm'}));
    },bytes);
    await page.getByRole('button',{name:'下载视频',exact:true}).click();
    await page.getByText('YOUTUBE · 最高 720p · MP4 音视频',{exact:true}).waitFor();
    await page.screenshot({path:path.join(output,'youtube-desktop.png')});
    await page.getByRole('button',{name:'下载',exact:true}).click();
    await page.getByRole('button',{name:'已发送',exact:true}).waitFor();
    let messages=await worker.evaluate(()=>globalThis.nativeMessages.filter(m=>m.action==='download'));
    assert.equal(messages.length,1);
    assert.equal(messages[0].url,'https://www.youtube.com/watch?v=ayl6TcSsre8');
    assert.equal(messages[0].kind,'youtube');
    assert.equal(messages[0].height,720);
    assert.equal(messages[0].headers,undefined);
    const youtubePopup=await context.newPage();
    await youtubePopup.goto(worker.url().replace('background.js','popup.html'));
    await youtubePopup.waitForFunction(()=>document.getElementById('connection').textContent !== '正在检查下载器…');
    await youtubePopup.evaluate(async id=>{tabId=id;await refresh();},tabId);
    await youtubePopup.locator('#quality').selectOption('480');
    await youtubePopup.getByText('YOUTUBE · 最高 480p · MP4 音视频',{exact:true}).waitFor();
    await youtubePopup.screenshot({path:path.join(output,'youtube-popup.png')});
    await youtubePopup.getByRole('button',{name:'下载',exact:true}).click();
    await youtubePopup.getByRole('button',{name:'已发送',exact:true}).waitFor();
    messages=await worker.evaluate(()=>globalThis.nativeMessages.filter(m=>m.action==='download'));
    assert.equal(messages[1].height,480);
    assert.equal(messages[1].url,messages[0].url);
    assert.notEqual(messages[1].request_id,messages[0].request_id);
    await youtubePopup.close();
    await page.setViewportSize({width:390,height:844});
    await page.evaluate(()=>history.pushState({},'', '/watch?v=abcdefghijk'));
    await page.waitForFunction(()=>document.querySelector('laowang-video-assistant')?.shadowRoot.querySelector('.panel').hidden);
    await page.getByRole('button',{name:'下载视频',exact:true}).click();
    await page.getByText('YOUTUBE · 最高 480p · MP4 音视频',{exact:true}).waitFor();
    const youtubeBounds=await page.locator('laowang-video-assistant .panel').boundingBox();
    assert(youtubeBounds.x>=0 && youtubeBounds.x+youtubeBounds.width<=391,JSON.stringify(youtubeBounds));
    await page.screenshot({path:path.join(output,'youtube-mobile.png')});
    await page.getByRole('button',{name:'下载',exact:true}).click();
    await page.getByRole('button',{name:'已发送',exact:true}).waitFor();
    messages=await worker.evaluate(()=>globalThis.nativeMessages.filter(m=>m.action==='download'));
    assert.equal(messages[2].url,'https://www.youtube.com/watch?v=abcdefghijk');
    assert.equal(messages[2].height,480);
    assert.notEqual(messages[2].request_id,messages[1].request_id);
    await page.getByRole('button',{name:'关闭资源列表',exact:true}).click();
    await page.locator('video').evaluate(video=>video.dispatchEvent(new Event('encrypted')));
    await page.getByRole('button',{name:'下载视频',exact:true}).click();
    await page.getByText('YOUTUBE · 受保护视频，不支持下载',{exact:true}).waitFor();
    assert(await page.getByRole('button',{name:'下载',exact:true}).isDisabled());
    assert.equal(errors.length,0,JSON.stringify(errors));
    console.log('PASS: YouTube page handoff, cold-start capabilities, quality persistence, SPA identity, narrow viewport and DRM guard');
    // Two feed posts plus unrelated HLS evidence must never share an actionable list.
    await context.route('https://x.com/**', route => route.fulfill({contentType:'text/html; charset=utf-8', body:`
      <!doctype html><title>X feed fixture</title><style>body{margin:16px;font:16px sans-serif}article{max-width:760px;margin:auto}video{display:block;width:100%;height:240px;background:#182329}a{display:block;margin:12px 0}</style>
      <article><a href="/kyliaspeijcken/status/2106950444442595371"><time>First post</time></a><video controls></video></article>
      <article><a href="/other/status/123456789"><time>Second post</time></a><video controls></video></article>`}));
    await page.setViewportSize({width:1280,height:900});
    await page.goto('https://x.com/home');
    await page.locator('video').evaluateAll((videos, bytes)=>videos.forEach(video=>{
      video.src=URL.createObjectURL(new Blob([new Uint8Array(bytes)],{type:'video/webm'}));
    }),bytes);
    await worker.evaluate(async id=>{
      await chrome.storage.local.set({videoHeight:720});
      const key=`media:${id}`, items=(await chrome.storage.session.get(key))[key]||[];
      items.push({id:'unrelated',url:'https://video.twimg.com/unrelated.m3u8',kind:'hls',frameId:0,seen:Date.now()});
      await chrome.storage.session.set({[key]:items});
      globalThis.nativeMessages=[];
    },tabId);
    await page.getByRole('button',{name:'下载视频',exact:true}).first().click();
    await page.getByText('X-2106950444442595371-video-1',{exact:true}).waitFor();
    assert.equal(await page.locator('laowang-video-assistant .row').count(),1);
    await page.screenshot({path:path.join(output,'x-desktop.png')});
    await page.getByRole('button',{name:'下载',exact:true}).click();
    await page.getByRole('button',{name:'已发送',exact:true}).waitFor();
    messages=await worker.evaluate(()=>globalThis.nativeMessages.filter(m=>m.action==='download'));
    assert.equal(messages[0].url,'https://x.com/kyliaspeijcken/status/2106950444442595371/video/1');
    assert.equal(messages[0].kind,'x');
    assert.equal(messages[0].headers,undefined);
    await page.getByRole('button',{name:'关闭资源列表',exact:true}).first().click();
    await page.getByRole('button',{name:'下载视频',exact:true}).nth(1).click();
    await page.getByText('X-123456789-video-1',{exact:true}).waitFor();
    await page.getByRole('button',{name:'下载',exact:true}).click();
    await page.getByRole('button',{name:'已发送',exact:true}).waitFor();
    messages=await worker.evaluate(()=>globalThis.nativeMessages.filter(m=>m.action==='download'));
    assert.equal(messages[1].url,'https://x.com/other/status/123456789/video/1');
    const xPopup=await context.newPage();
    await xPopup.goto(worker.url().replace('background.js','popup.html'));
    await xPopup.waitForFunction(()=>document.getElementById('connection').textContent !== '正在检查下载器…');
    await xPopup.evaluate(async id=>{tabId=id;await refresh();},tabId);
    assert.equal(await xPopup.locator('.row').count(),2);
    await xPopup.locator('#quality').selectOption('480');
    await xPopup.getByRole('button',{name:'下载',exact:true}).first().click();
    await xPopup.getByRole('button',{name:'已发送',exact:true}).waitFor();
    messages=await worker.evaluate(()=>globalThis.nativeMessages.filter(m=>m.action==='download'));
    assert.equal(messages[2].height,480);
    assert.notEqual(messages[2].request_id,messages[0].request_id);
    await xPopup.screenshot({path:path.join(output,'x-popup.png')});
    await xPopup.close();
    await page.getByRole('button',{name:'关闭资源列表',exact:true}).click();
    await page.setViewportSize({width:390,height:844});
    await page.getByRole('button',{name:'下载视频',exact:true}).first().click();
    await page.getByText('X · 最高 480p · MP4 视频',{exact:true}).waitFor();
    const xBounds=await page.locator('laowang-video-assistant .panel').first().boundingBox();
    assert(xBounds.x>=0 && xBounds.x+xBounds.width<=391,JSON.stringify(xBounds));
    await page.screenshot({path:path.join(output,'x-mobile.png')});
    // Old desktop builds must not receive an unsupported X task.
    await worker.evaluate(()=>{globalThis.testCapabilities=['youtube'];globalThis.nativeMessages=[];});
    await page.getByRole('button',{name:'下载',exact:true}).click();
    await page.getByText('请更新并重启桌面下载器，视频模块尚未就绪',{exact:true}).waitFor();
    assert.equal(await worker.evaluate(()=>globalThis.nativeMessages.filter(m=>m.action==='download').length),0);
    // Feed virtualization can reuse the same video node for a different post.
    await page.locator('article a').first().evaluate(a=>a.href='/new/status/987654321');
    await page.getByRole('button',{name:'重试',exact:true}).click();
    await page.getByText('X-987654321-video-1',{exact:true}).waitFor();
    assert.equal(await worker.evaluate(()=>globalThis.nativeMessages.filter(m=>m.action==='download').length),0);
    await page.getByRole('button',{name:'关闭资源列表',exact:true}).first().click();
    await page.locator('article a').first().evaluate(a=>a.remove());
    await page.getByRole('button',{name:'下载视频',exact:true}).first().click();
    await page.getByText('未确认视频所属帖子，请打开该视频的帖子详情后重试',{exact:true}).waitFor();
    assert.equal(await page.getByRole('button',{name:'下载',exact:true}).count(),0);
    assert.equal(errors.length,0,JSON.stringify(errors));
    console.log('PASS: X feed per-player identity, popup, quality, narrow viewport, old desktop guard and recycled/unknown posts');
    await context.route('https://www.bilibili.com/**', route => route.fulfill({
      contentType:'text/html; charset=utf-8', body:'<!doctype html><title>Bilibili 分P测试</title><video controls style="display:block;width:90%;aspect-ratio:16/9;margin:30px auto"></video>'
    }));
    await worker.evaluate(async()=>{
      globalThis.testCapabilities=['youtube','x','bilibili']; globalThis.nativeMessages=[];
      await chrome.storage.local.set({videoHeight:720});
    });
    await page.setViewportSize({width:1280,height:900});
    await page.goto('https://www.bilibili.com/video/BV1VbYk6FE6c/?trackid=ignore');
    await page.locator('video').evaluate((video,bytes)=>{
      video.src=URL.createObjectURL(new Blob([new Uint8Array(bytes)],{type:'video/webm'}));
    },bytes);
    await page.getByRole('button',{name:'下载视频',exact:true}).click();
    await page.getByText('B站 · 最高 720p · MP4 音视频',{exact:true}).waitFor();
    assert.equal(await page.locator('laowang-video-assistant .row').count(),1);
    await page.screenshot({path:path.join(output,'bilibili-desktop.png')});
    await page.getByRole('button',{name:'下载',exact:true}).click();
    await page.getByRole('button',{name:'已发送',exact:true}).waitFor();
    messages=await worker.evaluate(()=>globalThis.nativeMessages.filter(m=>m.action==='download'));
    assert.equal(messages[0].url,'https://www.bilibili.com/video/BV1VbYk6FE6c/?p=1');
    assert.equal(messages[0].kind,'bilibili');
    assert.equal(messages[0].headers,undefined);
    assert(messages[0].filename.endsWith(' - P1'));
    await page.evaluate(()=>history.pushState({},'', '/video/BV1VbYk6FE6c/?p=2&spm_id_from=ignore'));
    await page.waitForFunction(()=>document.querySelector('laowang-video-assistant')?.shadowRoot.querySelector('.panel').hidden);
    await page.getByRole('button',{name:'下载视频',exact:true}).click();
    await page.getByText('Bilibili 分P测试 - P2',{exact:true}).waitFor();
    await page.getByRole('button',{name:'下载',exact:true}).click();
    await page.getByRole('button',{name:'已发送',exact:true}).waitFor();
    messages=await worker.evaluate(()=>globalThis.nativeMessages.filter(m=>m.action==='download'));
    assert.equal(messages[1].url,'https://www.bilibili.com/video/BV1VbYk6FE6c/?p=2');
    assert.notEqual(messages[0].request_id,messages[1].request_id);
    const biliPopup=await context.newPage();
    await biliPopup.goto(worker.url().replace('background.js','popup.html'));
    await biliPopup.waitForFunction(()=>document.getElementById('connection').textContent !== '正在检查下载器…');
    await biliPopup.evaluate(async id=>{tabId=id;await refresh();},tabId);
    await biliPopup.locator('#quality').selectOption('480');
    await biliPopup.getByText('B站 · 最高 480p · MP4 音视频',{exact:true}).waitFor();
    assert.equal(await biliPopup.locator('.row').count(),1);
    await biliPopup.screenshot({path:path.join(output,'bilibili-popup.png')});
    await biliPopup.getByRole('button',{name:'下载',exact:true}).click();
    await biliPopup.getByRole('button',{name:'已发送',exact:true}).waitFor();
    messages=await worker.evaluate(()=>globalThis.nativeMessages.filter(m=>m.action==='download'));
    assert.equal(messages[2].height,480);
    assert.equal(messages[2].url,messages[1].url);
    assert.notEqual(messages[2].request_id,messages[1].request_id);
    await biliPopup.close();
    await page.getByRole('button',{name:'关闭资源列表',exact:true}).click();
    await page.setViewportSize({width:390,height:844});
    await page.getByRole('button',{name:'下载视频',exact:true}).click();
    await page.getByText('B站 · 最高 480p · MP4 音视频',{exact:true}).waitFor();
    const biliBounds=await page.locator('laowang-video-assistant .panel').boundingBox();
    assert(biliBounds.x>=0 && biliBounds.x+biliBounds.width<=391,JSON.stringify(biliBounds));
    await page.screenshot({path:path.join(output,'bilibili-mobile.png')});
    await worker.evaluate(()=>{globalThis.testCapabilities=['youtube','x'];globalThis.nativeMessages=[];});
    await page.getByRole('button',{name:'下载',exact:true}).click();
    await page.getByText('请更新并重启桌面下载器，视频模块尚未就绪',{exact:true}).waitFor();
    assert.equal(await worker.evaluate(()=>globalThis.nativeMessages.filter(m=>m.action==='download').length),0);
    await page.getByRole('button',{name:'关闭资源列表',exact:true}).click();
    await page.locator('video').evaluate(video=>video.dispatchEvent(new Event('encrypted')));
    await page.getByRole('button',{name:'下载视频',exact:true}).click();
    await page.getByText('B站 · 受保护视频，不支持下载',{exact:true}).waitFor();
    assert(await page.getByRole('button',{name:'下载',exact:true}).isDisabled());
    assert.equal(errors.length,0,JSON.stringify(errors));
    console.log('PASS: Bilibili blob page handoff, part switching, popup quality, narrow viewport, old desktop and DRM guards');
    // Douyin uses separate MP4 tracks; only the selected video page is sent for extraction.
    const douyinUrl='https://www.douyin.com/video/7686532267488840975';
    let douyinApiMode='complete';
    let douyinPageMode='detail';
    let douyinDetailRequests=0;
    let douyinPlayRequests=0;
    const douyinDetail=id=>({aweme_detail:{aweme_id:douyinApiMode==='mismatch'?'999':id,video:{duration:5000,bit_rate:[
      {format:'mp4',is_h265:0,play_addr:{width:640,height:360,url_list:['https://v26-web.douyinvod.com/movie/?signature=keep',
        'https://www.douyin.com/aweme/v1/play/?video_id=ignore','https://douyinvod.com.evil.test/ignore']}},
      {format:'dash',is_h265:0,play_addr:{width:640,height:360,url_list:['https://v26-web.douyinvod.com/media-video-avc1/']}}
    ]}}});
    await context.route('https://www.douyin.com/**', async route => {
      const url=new URL(route.request().url());
      if(url.pathname==='/aweme/v1/play/' && url.searchParams.get('video_id')==='feed-a') {
        douyinPlayRequests++;
        assert.equal(route.request().headers().range,'bytes=0-0');
        assert(route.request().headers().cookie?.includes('api_session=fixture-only'));
        return route.fulfill({status:302,headers:{Location:'https://v26-web.douyinvod.com/issued/feed-a/?signature=fresh'}});
      }
      if(url.pathname==='/aweme/v1/web/aweme/detail/') {
        douyinDetailRequests++;
        if(douyinApiMode==='protect') await worker.evaluate(async id=>{
          const key=`media:${id}`;
          const items=(await chrome.storage.session.get(key))[key] || [];
          await chrome.storage.session.set({[key]:items.map(item=>({...item,protected:true}))});
        },douyinTab);
        return route.fulfill({status:douyinApiMode==='error'?503:200, contentType:'application/json',
          body:JSON.stringify(douyinDetail(url.searchParams.get('aweme_id')))
        });
      }
      if(douyinPageMode==='feed') return route.fulfill({contentType:'text/html; charset=utf-8',
        body:'<!doctype html><title>Douyin feed fixture</title><section><video controls style="display:block;width:90%;aspect-ratio:16/9;margin:30px auto"></video></section><video id="preloaded" style="display:none"></video><script>window.originalPageFetch=fetch;window.originalPageXHR=XMLHttpRequest;</script>'});
      const id=/\/video\/(\d+)/.exec(url.pathname)?.[1] || '7686532267488840975';
      return route.fulfill({contentType:'text/html; charset=utf-8',
        body:`<!doctype html><title>Douyin fixture</title><section><video controls style="display:block;width:90%;aspect-ratio:16/9;margin:30px auto"></video><a href="${douyinUrl}">当前视频</a></section><script>fetch('/aweme/v1/web/aweme/detail/?aweme_id=${id}').then(r=>r.json())</script>`
      });
    });
    // Playwright route handlers only see the first URL of a redirect chain. Intercept the
    // redirected CDN request with CDP as well, so this fixture never contacts a real CDN.
    const douyinFetch=await context.newCDPSession(page);
    douyinFetch.on('Fetch.requestPaused',async event=>{
      try {
        const headers=Object.fromEntries(Object.entries(event.request.headers).map(([key,value])=>[key.toLowerCase(),value]));
        assert.equal(headers.range,'bytes=0-0');
        assert(!headers.cookie?.includes('api_session'));
        await douyinFetch.send('Fetch.fulfillRequest',{requestId:event.requestId,responseCode:206,responseHeaders:[
          {name:'Content-Type',value:'video/mp4'},{name:'Content-Range',value:'bytes 0-0/1000000'},
          {name:'Access-Control-Allow-Origin',value:'*'}],body:'AA=='});
      } catch(error) {
        errors.push(error.message);
        await douyinFetch.send('Fetch.failRequest',{requestId:event.requestId,errorReason:'Failed'}).catch(()=>{});
      }
    });
    await douyinFetch.send('Fetch.enable',{patterns:[{urlPattern:'https://v26-web.douyinvod.com/issued/*',requestStage:'Request'}]});
    await worker.evaluate(()=>{
      globalThis.testCapabilities=['youtube','x','bilibili','douyin','hls','dash'];
      globalThis.nativeMessages=[];
      return chrome.storage.local.set({videoHeight:720});
    });
    await page.setViewportSize({width:1280,height:900});
    await page.goto(douyinUrl+'?recommend=1');
    await page.locator('video').evaluate((video,bytes)=>{
      video.src=URL.createObjectURL(new Blob([new Uint8Array(bytes)],{type:'video/webm'}));
    },bytes);
    await page.getByRole('button',{name:'下载视频',exact:true}).click();
    await page.getByText('抖音 · 最高 720p · MP4 音视频',{exact:true}).waitFor();
    assert.equal(await page.locator('laowang-video-assistant .row').count(),1);
    await page.screenshot({path:path.join(output,'douyin-desktop.png')});
    const douyinTab=await worker.evaluate(async()=>
      (await chrome.tabs.query({})).find(tab=>tab.url?.includes('douyin.com/video/')).id);
    // Simulate ordinary/incognito cookie stores without reading any user profile.
    await worker.evaluate(id=>{
      globalThis.originalCookieStores=chrome.cookies.getAllCookieStores;
      globalThis.originalGetCookies=chrome.cookies.getAll;
      globalThis.cookieCalls=[];
      chrome.cookies.getAllCookieStores=async()=>[{id:'unrelated',tabIds:[id+10000]},{id:'selected',tabIds:[id]}];
      chrome.cookies.getAll=async query=>{
        globalThis.cookieCalls.push(query);
        if(query.storeId!=='selected') throw new Error('Wrong cookie store');
        return [{name:'nonce',value:'test-authorization',domain:'.douyin.com',path:'/',secure:true,hostOnly:false},
          {name:'',value:'ignore-nameless-cookie',domain:'www.douyin.com',path:'/video',secure:true,hostOnly:true},
          {name:'partitioned',value:'never-forward',domain:'.douyin.com',path:'/',secure:true,hostOnly:false,
            partitionKey:{topLevelSite:'https://www.douyin.com'}}];
      };
    },douyinTab);
    await page.getByRole('button',{name:'下载',exact:true}).click();
    await page.getByRole('button',{name:'已发送',exact:true}).waitFor();
    messages=await worker.evaluate(()=>globalThis.nativeMessages.filter(m=>m.action==='download'));
    assert.equal(messages.length,1);
    assert.equal(messages[0].kind,'douyin');
    assert.equal(messages[0].url,douyinUrl);
    assert.equal(messages[0].height,720);
    assert.equal(messages[0].headers.Cookie,undefined);
    assert(messages[0].headers.Referer.startsWith('https://www.douyin.com/'));
    assert.deepEqual(messages[0].video,{id:'7686532267488840975',duration:5,formats:[
      {url:'https://v26-web.douyinvod.com/movie/?signature=keep',width:640,height:360,vcodec:'h264'}]});
    assert.deepEqual(messages[0].cookies,[{name:'nonce',value:'test-authorization',domain:'.douyin.com',path:'/',secure:true,hostOnly:false}]);
    assert.deepEqual(await worker.evaluate(()=>globalThis.cookieCalls),[{url:douyinUrl,storeId:'selected'}]);
    const douyinPopup=await context.newPage();
    await douyinPopup.goto(worker.url().replace('background.js','popup.html'));
    await douyinPopup.waitForFunction(()=>document.getElementById('connection').textContent !== '正在检查下载器…');
    await douyinPopup.evaluate(async id=>{tabId=id;await refresh();},douyinTab);
    await douyinPopup.locator('#quality').selectOption('480');
    await douyinPopup.getByText('抖音 · 最高 480p · MP4 音视频',{exact:true}).waitFor();
    await douyinPopup.getByRole('button',{name:'下载',exact:true}).click();
    await douyinPopup.getByRole('button',{name:'已发送',exact:true}).waitFor();
    messages=await worker.evaluate(()=>globalThis.nativeMessages.filter(m=>m.action==='download'));
    assert.equal(messages[1].height,480);
    assert.notEqual(messages[0].request_id,messages[1].request_id);
    await douyinPopup.screenshot({path:path.join(output,'douyin-popup.png')});
    await worker.evaluate(()=>{chrome.cookies.getAllCookieStores=async()=>[];});
    await douyinPopup.locator('#refresh').click();
    await douyinPopup.getByRole('button',{name:'下载',exact:true}).click();
    await douyinPopup.getByText('无法确认当前标签页的授权环境，请刷新视频页后重试',{exact:true}).waitFor();
    assert.equal(await worker.evaluate(()=>globalThis.nativeMessages.filter(m=>m.action==='download').length),2);
    await worker.evaluate(()=>{
      globalThis.testCapabilities=['youtube','x','bilibili','hls','dash'];
      chrome.cookies.getAllCookieStores=globalThis.originalCookieStores;
      chrome.cookies.getAll=globalThis.originalGetCookies;
    });
    await douyinPopup.locator('#refresh').click();
    await douyinPopup.getByRole('button',{name:'下载',exact:true}).click();
    await douyinPopup.getByText('请更新并重启桌面下载器，抖音模块尚未就绪',{exact:true}).waitFor();
    assert.equal(await worker.evaluate(()=>globalThis.nativeMessages.filter(m=>m.action==='download').length),2);
    await worker.evaluate(()=>{globalThis.testCapabilities=['douyin'];});
    douyinApiMode='mismatch';
    await douyinPopup.locator('#refresh').click();
    await douyinPopup.getByRole('button',{name:'下载',exact:true}).click();
    await douyinPopup.getByText('抖音视频已切换，请刷新资源列表',{exact:true}).waitFor();
    douyinApiMode='error';
    await douyinPopup.locator('#refresh').click();
    await douyinPopup.getByRole('button',{name:'下载',exact:true}).click();
    await douyinPopup.getByText('抖音视频信息获取失败，请刷新视频页后重试',{exact:true}).waitFor();
    assert.equal(await worker.evaluate(()=>globalThis.nativeMessages.filter(m=>m.action==='download').length),2);
    douyinApiMode='protect';
    await douyinPopup.locator('#refresh').click();
    await douyinPopup.getByRole('button',{name:'下载',exact:true}).click();
    await douyinPopup.getByText('受保护视频不支持下载',{exact:true}).waitFor();
    assert.equal(await worker.evaluate(()=>globalThis.nativeMessages.filter(m=>m.action==='download').length),2);
    douyinApiMode='complete';
    await douyinPopup.close();
    await page.locator('video').evaluate(video=>video.dispatchEvent(new Event('encrypted')));
    await page.getByRole('button',{name:'关闭资源列表',exact:true}).click();
    await page.getByRole('button',{name:'下载视频',exact:true}).click();
    await page.getByText('抖音 · 受保护视频，不支持下载',{exact:true}).waitFor();
    assert(await page.getByRole('button',{name:'下载',exact:true}).isDisabled());
    // A reused player in a feed must refresh its selected post before sending.
    await worker.evaluate(()=>{globalThis.testCapabilities=['douyin'];globalThis.nativeMessages=[];});
    await page.goto('https://www.douyin.com/?recommend=1');
    await page.getByRole('button',{name:'下载视频',exact:true}).click();
    await page.getByText('抖音-7686532267488840975',{exact:true}).waitFor();
    await page.locator('section a').evaluate(async link=>{
      link.href='https://www.douyin.com/video/7686532267488840976';
      await (await fetch('/aweme/v1/web/aweme/detail/?aweme_id=7686532267488840976')).json();
    });
    await page.getByRole('button',{name:'下载',exact:true}).click();
    await page.getByText('抖音-7686532267488840976',{exact:true}).waitFor();
    assert.equal(await worker.evaluate(()=>globalThis.nativeMessages.filter(m=>m.action==='download').length),0);
    await page.getByRole('button',{name:'下载',exact:true}).click();
    await page.getByRole('button',{name:'已发送',exact:true}).waitFor();
    messages=await worker.evaluate(()=>globalThis.nativeMessages.filter(m=>m.action==='download'));
    assert.equal(messages[0].url,'https://www.douyin.com/video/7686532267488840976');
    await page.getByRole('button',{name:'关闭资源列表',exact:true}).click();
    await page.locator('section a').evaluate(link=>link.remove());
    await page.getByRole('button',{name:'下载视频',exact:true}).click();
    await page.getByText('未确认当前视频，请先播放视频并完整刷新页面后重试',{exact:true}).waitFor();
    assert.equal(await page.getByRole('button',{name:'下载',exact:true}).count(),0);
    // Matching duration/height alone must never identify a video, even with an observed detail request.
    await worker.evaluate(()=>{globalThis.nativeMessages=[];});
    await page.goto('https://www.douyin.com/?recommend=1');
    await page.locator('section a').evaluate(link=>link.remove());
    await page.locator('video').evaluate((video,bytes)=>{
      video.src=URL.createObjectURL(new Blob([new Uint8Array(bytes)],{type:'video/webm'}));
      Object.defineProperty(video,'duration',{value:5});
      Object.defineProperty(video,'videoHeight',{value:360});
    },bytes);
    await page.getByRole('button',{name:'下载视频',exact:true}).click();
    await page.getByText('未确认当前视频，请先播放视频并完整刷新页面后重试',{exact:true}).waitFor();
    assert.equal(await page.getByRole('button',{name:'下载',exact:true}).count(),0);
    // Actual feed shape: stable URL, blob players, no post links and no detail API at all.
    douyinPageMode='feed';
    douyinDetailRequests=0;
    await context.addCookies([{name:'api_session',value:'fixture-only',url:'https://www.douyin.com/',httpOnly:true,secure:true,sameSite:'Lax'}]);
    await page.goto('https://www.douyin.com/?recommend=1');
    await page.locator('section video').evaluate((video,bytes)=>{
      video.src=URL.createObjectURL(new Blob([new Uint8Array(bytes)],{type:'video/webm'}));
    },bytes);
    await page.waitForFunction(()=>Number.isFinite(document.querySelector('section video').duration));
    await page.evaluate(()=>{
      const video=document.querySelector('section video');
      const awemeInfo={awemeId:'7686532267488840975',video:{duration:video.duration*1000,width:640,height:360,bitRateList:[
        {format:'mp4',isH265:0,width:640,height:360,playApi:'//www.douyin.com/aweme/v1/play/?video_id=feed-a',playAddr:[
          {src:'https://v26-web-prime.douyinvod.com/feed-a/?signature=stale'},
          {src:'https://v26-web.douyinvod.com/media-video-avc1/'},
          {src:'https://v26-web.douyinvod.com/media-audio-und-mp4a/'},
          {src:'https://v26-web.douyinvod.com/partial/?range=0-999'}]},
        {format:'mp4',width:640,height:360,playAddr:[{src:'https://douyinvod.com.evil.test/full/'},
          {src:'http://v26-web.douyinvod.com/full/'},{src:'https://u:p@v26-web.douyinvod.com/full/'},
          {src:'https://v26-web.douyinvod.com:444/full/'}]},
        {format:'mp4',width:640,height:360,playAddr:[{src:'https://v26-web.douyinvod.com/partial/?range=0-999'}]},
        {format:'dash',width:640,height:360,playAddr:[{src:'https://v26-web.douyinvod.com/dash/'}]}
      ]}};
      window.player={video,config:{awemeInfo}};
    });
    await page.getByRole('button',{name:'下载视频',exact:true}).click();
    await page.getByText('抖音-7686532267488840975',{exact:true}).waitFor();
    assert.equal(await page.locator('.notice').filter({hasText:'诊断'}).count(),0);
    await page.getByRole('button',{name:'下载',exact:true}).click();
    await page.getByRole('button',{name:'已发送',exact:true}).waitFor();
    messages=await worker.evaluate(()=>globalThis.nativeMessages.filter(m=>m.action==='download'));
    assert.equal(messages.length,1);
    assert.equal(messages[0].url,douyinUrl);
    assert.equal(messages[0].kind,'douyin');
    assert.equal(messages[0].video.id,'7686532267488840975');
    assert.deepEqual(messages[0].video.formats,[{url:'https://v26-web.douyinvod.com/issued/feed-a/?signature=fresh',width:640,height:360,vcodec:'h264'}]);
    assert.equal(douyinPlayRequests,1,'Use the authenticated play endpoint, not the raw prime CDN candidate');
    assert(Math.abs(messages[0].video.duration-await page.locator('section video').evaluate(video=>video.duration))<0.001);
    await page.screenshot({path:path.join(output,process.argv.includes('--dist') || releaseIndex>=0 ? 'douyin-feed-dist.png' : 'douyin-feed-source.png')});
    // Open the popup before switching. Its old row must not send the previously selected video.
    const feedPopup=await context.newPage();
    await feedPopup.goto(worker.url().replace('background.js','popup.html'));
    await feedPopup.waitForFunction(()=>document.getElementById('connection').textContent !== '正在检查下载器…');
    await feedPopup.evaluate(async id=>{tabId=id;await refresh();},douyinTab);
    await feedPopup.getByText('抖音-7686532267488840975',{exact:true}).waitFor();
    await page.getByRole('button',{name:'关闭资源列表',exact:true}).click();
    await page.getByRole('button',{name:'下载视频',exact:true}).click();
    await page.getByText('抖音-7686532267488840975',{exact:true}).waitFor();
    // New player generation, same DOM video, same blob, duration and resolution, but a different aweme.
    await page.evaluate(()=>{
      const video=document.querySelector('section video');
      const awemeInfo={awemeId:'7686532267488840976',video:{duration:video.duration,width:640,height:360,bitRateList:[
        {format:'mp4',isH265:1,width:640,height:360,playAddr:[{src:'https://v26-web.douyinvod.com/feed-b/?signature=other'}]}
      ]}};
      window.player={_core:{_media:video},_config:{_config:{pluginsConfig:{WatchLaterPlugin:{awemeInfo},DanmakuPlugin:{awemeInfo}}}}};
    });
    await feedPopup.getByRole('button',{name:'下载',exact:true}).click();
    await feedPopup.getByText('抖音视频已切换，请刷新资源列表',{exact:true}).waitFor();
    assert.equal(await worker.evaluate(()=>globalThis.nativeMessages.filter(m=>m.action==='download').length),1);
    await page.getByRole('button',{name:'下载',exact:true}).click();
    await page.getByText('抖音-7686532267488840976',{exact:true}).waitFor();
    assert.equal(await worker.evaluate(()=>globalThis.nativeMessages.filter(m=>m.action==='download').length),1);
    await page.getByRole('button',{name:'下载',exact:true}).click();
    await page.getByRole('button',{name:'已发送',exact:true}).waitFor();
    messages=await worker.evaluate(()=>globalThis.nativeMessages.filter(m=>m.action==='download'));
    assert.equal(messages.length,2);
    assert.equal(messages[1].url,'https://www.douyin.com/video/7686532267488840976');
    assert.deepEqual(messages[1].video.formats,[{url:'https://v26-web.douyinvod.com/feed-b/?signature=other',width:640,height:360,vcodec:'h265'}]);
    await feedPopup.locator('#refresh').click();
    await feedPopup.getByText('抖音-7686532267488840976',{exact:true}).waitFor();
    assert.equal(await feedPopup.getByText('抖音-7686532267488840975',{exact:true}).count(),0);
    // A switch while browser credentials are being read must also stop the pending handoff.
    await worker.evaluate(()=>{
      globalThis.releaseFeedCookies=null;
      chrome.cookies.getAll=async query=>{
        await new Promise(resolve=>{globalThis.releaseFeedCookies=resolve;});
        return globalThis.originalGetCookies(query);
      };
    });
    await feedPopup.getByRole('button',{name:'下载',exact:true}).click();
    for(let attempt=0;attempt<100;attempt++) {
      if(await worker.evaluate(()=>typeof globalThis.releaseFeedCookies==='function')) break;
      await new Promise(resolve=>setTimeout(resolve,25));
    }
    assert(await worker.evaluate(()=>typeof globalThis.releaseFeedCookies==='function'),'Download did not reach the held cookie request');
    await page.evaluate(()=>{
      const plugins=window.player._config._config.pluginsConfig;
      const awemeInfo={...plugins.WatchLaterPlugin.awemeInfo,awemeId:'7686532267488840999'};
      plugins.WatchLaterPlugin={awemeInfo};
      plugins.DanmakuPlugin={awemeInfo};
    });
    await worker.evaluate(()=>{chrome.cookies.getAll=globalThis.originalGetCookies;globalThis.releaseFeedCookies();});
    await feedPopup.getByText('抖音视频已切换，请刷新资源列表',{exact:true}).waitFor();
    assert.equal(await worker.evaluate(()=>globalThis.nativeMessages.filter(m=>m.action==='download').length),2);
    // Conflicting plugin metadata during a transition fails closed, even for equal-duration videos.
    await page.evaluate(()=>{
      const plugins=window.player._config._config.pluginsConfig;
      plugins.DanmakuPlugin={awemeInfo:{...plugins.DanmakuPlugin.awemeInfo,awemeId:'7686532267488840977'}};
    });
    await page.getByRole('button',{name:'关闭资源列表',exact:true}).click();
    await page.getByRole('button',{name:'下载视频',exact:true}).click();
    await page.getByText('未确认当前视频，请先播放视频并完整刷新页面后重试',{exact:true}).waitFor();
    assert.equal(await page.getByRole('button',{name:'下载',exact:true}).count(),0);
    // A player referring to a hidden preload is not the visible player's identity or a popup fallback.
    await page.evaluate(()=>{
      const plugins=window.player._config._config.pluginsConfig;
      plugins.DanmakuPlugin=plugins.WatchLaterPlugin;
      const preloaded=document.getElementById('preloaded');
      Object.defineProperty(preloaded,'duration',{value:document.querySelector('section video').duration});
      window.player._core._media=preloaded;
    });
    await feedPopup.locator('#refresh').click();
    await feedPopup.getByText('未确认当前视频，请先播放视频并完整刷新页面后重试',{exact:true}).waitFor();
    assert.equal(await feedPopup.getByRole('button',{name:'下载',exact:true}).count(),0);
    assert.equal(douyinDetailRequests,0,'Feed identification must not replay or require a detail API');
    assert(await page.evaluate(()=>window.fetch===window.originalPageFetch && window.XMLHttpRequest===window.originalPageXHR));
    await feedPopup.close();
    assert.equal(errors.length,0,JSON.stringify(errors));
    console.log('PASS: Douyin detail/modal fallback, authenticated play redirects, scoped cookies, DRM, old/new feed players without detail requests, exact player binding, stale popup/overlay rejection and split-track filtering');
    console.log('Screenshots:',output);
  } catch(error) {
    await context.pages()[0]?.screenshot({path:path.join(output,'video-failure.png')});
    throw error;
  } finally {
    await context.close();
    if(server) await new Promise(resolve=>server.close(resolve));
  }
}
main().catch(error=>{console.error(error);process.exitCode=1;});
