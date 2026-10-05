/* Real unpacked-extension smoke test. Pass the path to an installed playwright package. */
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const http = require('node:http');
const path = require('node:path');
const os = require('node:os');
const {chromium} = require(process.argv[2] || 'playwright');

async function main() {
  const root = path.resolve(__dirname, '..');
  const output = path.join(root, 'output', 'playwright');
  await fs.mkdir(output, {recursive:true});
  const profile = await fs.mkdtemp(path.join(os.tmpdir(), 'video-extension-'));
  const extension = path.join(root, ...(process.argv.includes('--dist') ? ['dist', 'chrome-extension'] : ['chrome-extension']));
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
    assert(await popup.getByText('HLS · 分段视频暂不支持',{exact:true}).isVisible());
    await popup.screenshot({path:path.join(output,'video-popup.png')});
    await popup.close();
    await page.evaluate(()=>history.pushState({},'', '/next'));
    await page.waitForTimeout(300);
    items=await worker.evaluate(async id=>(await chrome.storage.session.get(`media:${id}`))[`media:${id}`],tabId);
    assert.equal(items.length,0);
    console.log('PASS: HLS recognition with disabled action, SPA cleanup, real popup');
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
    assert.equal(await streamPopup.getByText('媒体流 · 流式媒体暂不支持下载',{exact:true}).count(),2);
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
            const response=message.action==='ping' ? {ok:false,installed:true,capabilities:['youtube']}
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
