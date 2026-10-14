import test from 'node:test';
import assert from 'node:assert/strict';
import {
  createLinkDownloader, validateLinkUrl, refererFor, cookieHeader,
  MENU_ID, FILE_FEATURE, FILE_STATUS_KEY,
} from '../chrome-extension/link-download.js';

const PAGE = 'https://files.test/downloads?view=1';
const FILE = 'https://files.test/report.pdf?sig=a%2Bb+c&part=1&part=2';
const event = () => ({listeners: [], addListener(fn) { this.listeners.push(fn); }});
const deferred = () => {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return {promise, resolve};
};

function fixture(options = {}) {
  const messages = [], storesRead = [], cookiesRead = [], notifications = [], state = {};
  let frameReads = 0;
  const api = {
    runtime: {lastError: undefined, onInstalled: event(), onStartup: event()},
    contextMenus: {onClicked: event(), created: [], create(value, callback) {
      this.created.push(value);
      callback?.();
    }},
    storage: {local: {async set(value) { Object.assign(state, structuredClone(value)); }}},
    notifications: {create(id, value, callback) {
      if (options.noNotifications) throw new Error('Notifications disabled');
      notifications.push({id, ...value});
      callback?.();
    }},
    webNavigation: {async getFrame(query) {
      assert.deepEqual(query, {tabId: 7, frameId: options.frameId ?? 0});
      const frames = options.frames || [{url: PAGE, documentId: 'document-1'}];
      const result = frames[Math.min(frameReads++, frames.length - 1)];
      if (result instanceof Error) throw result;
      return result;
    }},
    cookies: {
      async getAllCookieStores() {
        storesRead.push(true);
        return options.stores ?? [{id: 'incognito-2', tabIds: [7]}, {id: 'normal-0', tabIds: [9]}];
      },
      async getAll(query) {
        cookiesRead.push(query);
        if (options.cookieGate) await options.cookieGate.promise;
        return options.cookies || [{name: 'session', value: 'fixture-secret', path: '/'}];
      },
    },
  };
  const native = async message => {
    messages.push(structuredClone(message));
    if (message.action === 'ping') return options.ping ?? {ok: true, features: [FILE_FEATURE]};
    if (options.sendGate) await options.sendGate.promise;
    return options.response ?? {ok: true, task_id: 'created-task', filename: 'report.pdf'};
  };
  return {
    api, messages, storesRead, cookiesRead, notifications, state,
    downloader: createLinkDownloader(native, api, 'FixtureBrowser/1.0'),
    info: {menuItemId: MENU_ID, linkUrl: FILE, pageUrl: PAGE, frameId: options.frameId ?? 0,
      ...(options.frameId ? {frameUrl: PAGE} : {})},
    tab: {id: 7, url: 'https://top.test/'},
  };
}

test('HTTP links retain their signed query and reject unsafe schemes, credentials and controls', () => {
  assert.equal(validateLinkUrl(FILE), true);
  for (const url of ['blob:https://files.test/id', 'file:///C:/x', 'javascript:alert(1)',
    'https://user:password@files.test/a', 'https://files.test/a\r\nCookie: x', 'https://files.test:99999/a']) {
    assert.equal(validateLinkUrl(url), false, url);
  }
});

test('referer trims cross-origin URLs, removes fragments and omits HTTPS downgrades', () => {
  assert.equal(refererFor(PAGE + '#part', FILE), PAGE);
  assert.equal(refererFor(PAGE, 'https://cdn.test/a'), 'https://files.test/');
  assert.equal(refererFor(PAGE, 'http://files.test/a'), '');
});

test('cookies exclude partitions and empty names while preserving path precedence', () => {
  assert.equal(cookieHeader([
    {name: 'id', value: 'root', path: '/'}, {name: 'id', value: 'specific', path: '/files'},
    {name: 'partition', value: 'no', path: '/', partitionKey: {}}, {name: '', value: 'no'},
  ]), 'id=specific; id=root');
});

test('unrelated menu commands and absent browser tabs never contact the native host', async () => {
  const f = fixture();
  await f.downloader.submit({...f.info, menuItemId: 'another-menu'}, f.tab);
  await f.downloader.submit(f.info, undefined);
  assert.equal(f.messages.length, 0);
  assert.equal(f.cookiesRead.length, 0);
});

test('a direct file uses the clicked frame store and original URL without video messages', async () => {
  const f = fixture({frameId: 3});
  const result = await f.downloader.submit(f.info, f.tab);
  assert.equal(result.ok, true);
  assert.equal(f.messages.length, 2);
  const request = f.messages[1];
  assert.equal(request.action, 'download_file');
  assert.equal(request.url, FILE);
  assert.match(request.request_id, /^file-[a-f0-9-]{36}$/);
  assert.deepEqual(request.headers, {'User-Agent': 'FixtureBrowser/1.0', Referer: PAGE, Cookie: 'session=fixture-secret'});
  assert.deepEqual(f.cookiesRead, [{url: FILE, storeId: 'incognito-2'}]);
  assert.equal('kind' in request, false);
  const saved = JSON.stringify(f.state);
  assert.equal(saved.includes('fixture-secret'), false);
  assert.equal(saved.includes('files.test'), false);
  assert.equal(saved.includes('sig='), false);
});

test('an installed but stopped desktop with the file feature may be cold-started', async () => {
  const f = fixture({ping: {ok: false, installed: true, capabilities: [], features: [FILE_FEATURE]}});
  assert.equal((await f.downloader.submit(f.info, f.tab)).ok, true);
  assert.equal(f.messages[1].action, 'download_file');
});

test('old components produce an upgrade message before any cookies or download request', async () => {
  const f = fixture({ping: {ok: true, capabilities: ['mp4']}});
  const result = await f.downloader.submit(f.info, f.tab);
  assert.equal(result.code, 'unsupported');
  assert.match(result.message, /更新/);
  assert.equal(f.cookiesRead.length, 0);
  assert.deepEqual(f.messages.map(x => x.action), ['ping']);
});

test('known video pages and encoded stream URLs do not collect account cookies or submit files', async () => {
  const links = [
    'https://www.youtube.com/watch?v=ayl6TcSsre8',
    'https://x.com/example/status/12345',
    'https://www.bilibili.com/video/BV1xx411c7mD/?p=2',
    'https://www.douyin.com/?recommend=1',
    'https://www.douyin.com/video/123456',
    'https://v.example.douyinvod.com/a.mp4',
    'https://files.test/master%2Em3u8',
    'https://files.test/manifest.%6Dpd',
  ];
  for (const linkUrl of links) {
    const f = fixture();
    const result = await f.downloader.submit({...f.info, linkUrl}, f.tab);
    assert.equal(result.ok, false, linkUrl);
    assert.match(result.message, /视频入口/);
    assert.equal(f.messages.length, 0, linkUrl);
    assert.equal(f.cookiesRead.length, 0, linkUrl);
  }
});

test('a missing initial frame fails closed even if a different frame appears later', async () => {
  const f = fixture({frames: [null, {url: PAGE, documentId: 'new-document'}]});
  const result = await f.downloader.submit(f.info, f.tab);
  assert.equal(result.ok, false);
  assert.equal(f.cookiesRead.length, 0);
  assert.equal(f.messages.some(x => x.action === 'download_file'), false);
});

test('navigation before the first frame lookup cannot replace the clicked-page context', async () => {
  const f = fixture({frames: [{url: 'https://other.test/account', documentId: 'new-document'}]});
  const result = await f.downloader.submit(f.info, f.tab);
  assert.equal(result.ok, false);
  assert.equal(f.cookiesRead.length, 0);
  assert.equal(f.messages.some(x => x.action === 'download_file'), false);
});

test('navigation while cookies are read prevents submitting the obsolete click', async () => {
  const f = fixture({frames: [{url: PAGE, documentId: 'old-document'}, {url: PAGE, documentId: 'new-document'}]});
  const result = await f.downloader.submit(f.info, f.tab);
  assert.equal(result.code, 'navigated');
  assert.equal(f.messages.some(x => x.action === 'download_file'), false);
});

test('an unknown cookie store never falls back to the default profile', async () => {
  const f = fixture({stores: [{id: 'other', tabIds: [99]}]});
  assert.equal((await f.downloader.submit(f.info, f.tab)).ok, false);
  assert.equal(f.cookiesRead.length, 0);
  assert.equal(f.messages.some(x => x.action === 'download_file'), false);
});

test('rapid repeat clicks share one in-flight native request', async () => {
  const gate = deferred(), f = fixture({sendGate: gate});
  const a = f.downloader.submit(f.info, f.tab);
  const b = f.downloader.submit(f.info, f.tab);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(f.messages.filter(x => x.action === 'download_file').length, 1);
  gate.resolve();
  assert.deepEqual(await a, await b);
});

test('progress remains visible in local status when OS notifications are unavailable', async () => {
  const gate = deferred(), f = fixture({sendGate: gate, noNotifications: true});
  const request = f.downloader.submit(f.info, f.tab);
  await new Promise(resolve => setImmediate(resolve));
  const pending = f.state[FILE_STATUS_KEY];
  gate.resolve();
  await request;
  assert.equal(pending?.code, 'sending');
  assert.match(pending.message, /正在发送/);
  assert.equal(f.state[FILE_STATUS_KEY].ok, true);
});

test('native timeout stays unknown and never causes an automatic retry', async () => {
  const f = fixture({response: {ok: false, error: '下载器响应超时'}});
  const result = await f.downloader.submit(f.info, f.tab);
  assert.equal(result.code, 'unknown');
  assert.match(result.message, /任务列表/);
  assert.equal(f.messages.filter(x => x.action === 'download_file').length, 1);
});

test('losing the reply after dispatch is also an unknown result', async () => {
  const f = fixture({response: {ok: false, error: '本地桥接已断开'}});
  assert.equal((await f.downloader.submit(f.info, f.tab)).code, 'unknown');
  assert.equal(f.messages.filter(x => x.action === 'download_file').length, 1);
});

test('unexpected native errors cannot persist or notify signed URLs or cookie contents', async () => {
  const f = fixture({response: {ok: false, error: `403 ${FILE} Cookie: session=fixture-secret`}});
  await f.downloader.submit(f.info, f.tab);
  const feedback = JSON.stringify([f.state, f.notifications]);
  assert.equal(feedback.includes('fixture-secret'), false);
  assert.equal(feedback.includes('sig='), false);
  assert.equal(feedback.includes('files.test'), false);
});

test('menu registration is idempotent for all lifecycle listeners', () => {
  const f = fixture();
  f.downloader.install();
  f.downloader.install();
  assert.equal(f.api.contextMenus.onClicked.listeners.length, 1);
  assert.equal(f.api.runtime.onInstalled.listeners.length, 1);
  assert.equal(f.api.runtime.onStartup.listeners.length, 1);
  assert.deepEqual(f.api.contextMenus.created[0].contexts, ['link']);
  assert.deepEqual(f.api.contextMenus.created[0].targetUrlPatterns, ['http://*/*', 'https://*/*']);
});

test('menu API initialization failure does not abort the existing worker', () => {
  const f = fixture();
  f.api.runtime.onInstalled.addListener = () => { throw new Error('Extension reloading'); };
  assert.doesNotThrow(() => f.downloader.install());
});
