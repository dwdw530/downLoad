import {classify, classifyResponse, displayName, upsert, publicItem, youtubeUrl, xUrl, isXPage, bilibiliUrl, douyinUrl, isDouyinPage} from "./media.js";

const HOST = "com.laowang.downloader";
const tabLocks = new Map();
const sending = new Map();
const keyFor = tabId => `media:${tabId}`;
const setting = async () => (await chrome.storage.local.get({enabled: true})).enabled;

function serial(tabId, work) {
  const next = (tabLocks.get(tabId) || Promise.resolve()).catch(() => {}).then(work);
  tabLocks.set(tabId, next);
  next.finally(() => { if (tabLocks.get(tabId) === next) tabLocks.delete(tabId); }).catch(() => {});
  return next;
}

async function getItems(tabId) {
  return (await chrome.storage.session.get(keyFor(tabId)))[keyFor(tabId)] || [];
}

async function saveItems(tabId, items) {
  await chrome.storage.session.set({[keyFor(tabId)]: items});
  await chrome.action.setBadgeText({tabId, text: items.length ? String(items.length) : ""}).catch(() => {});
  await chrome.action.setBadgeBackgroundColor({tabId, color: "#13795b"}).catch(() => {});
}

function record(tabId, candidate) {
  return serial(tabId, async () => {
    if (!(await setting())) return;
    if (candidate.documentId) {
      const frame = await chrome.webNavigation.getFrame({tabId, frameId: candidate.frameId}).catch(() => null);
      if (!frame || frame.documentId !== candidate.documentId) return;
    }
    const kind = candidate.requestType ? classifyResponse(candidate.url, candidate.mime, candidate.requestType, candidate.method)
      : classify(candidate.url, candidate.mime);
    if (!kind) return;
    const protectedFrames = (await chrome.storage.session.get(`protected:${tabId}`))[`protected:${tabId}`] || [];
    await saveItems(tabId, upsert(await getItems(tabId), {
      ...candidate, kind, id: crypto.randomUUID(),
      name: kind === "stream" ? `流式媒体 · ${new URL(candidate.url).hostname}` : displayName(candidate.url, kind),
      protected: protectedFrames.includes(candidate.frameId)
    }));
  });
}

chrome.webRequest.onHeadersReceived.addListener(details => {
  if (details.tabId < 0 || !["GET", "POST"].includes(details.method) || ![200, 206].includes(details.statusCode)) return;
  const header = name => details.responseHeaders?.find(h => h.name.toLowerCase() === name)?.value || "";
  const kind = classifyResponse(details.url, header("content-type"), details.type, details.method);
  if (!kind) return;
  const rangeSize = /\/(\d+)$/.exec(header("content-range"));
  record(details.tabId, {url: details.url, mime: header("content-type"), frameId: details.frameId,
    documentId: details.documentId, requestType: details.type, method: details.method,
    pageUrl: details.documentUrl || details.initiator || "",
    size: Number(rangeSize?.[1] || header("content-length")) || 0}).catch(() => {});
}, {urls: ["http://*/*", "https://*/*"], types: ["media", "xmlhttprequest", "other"]}, ["responseHeaders"]);

function clearFrame(details) {
  serial(details.tabId, async () => {
    const items = details.frameId === 0 ? [] : (await getItems(details.tabId)).filter(i => i.frameId !== details.frameId);
    const frames = (await chrome.storage.session.get(`protected:${details.tabId}`))[`protected:${details.tabId}`] || [];
    await chrome.storage.session.set({[`protected:${details.tabId}`]: details.frameId === 0 ? [] : frames.filter(f => f !== details.frameId)});
    const blobs = (await chrome.storage.session.get(`blob:${details.tabId}`))[`blob:${details.tabId}`] || [];
    await chrome.storage.session.set({[`blob:${details.tabId}`]: details.frameId === 0 ? [] : blobs.filter(f => f !== details.frameId)});
    await saveItems(details.tabId, items);
  }).catch(() => {});
}
chrome.webNavigation.onCommitted.addListener(clearFrame);
chrome.webNavigation.onHistoryStateUpdated.addListener(clearFrame);
chrome.tabs.onRemoved.addListener(tabId => serial(tabId, () => chrome.storage.session.remove([keyFor(tabId), `protected:${tabId}`, `blob:${tabId}`])));

function native(message) {
  return new Promise(resolve => {
    let port;
    let finished = false;
    const finish = result => {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      port?.disconnect();
      resolve(result);
    };
    const timer = setTimeout(() => finish({ok: false, error: "下载器响应超时，请在桌面任务列表确认结果"}), 125000);
    try {
      port = chrome.runtime.connectNative(HOST);
      port.onMessage.addListener(finish);
      port.onDisconnect.addListener(() => {
        const error = chrome.runtime.lastError;
        finish({ok: false, error: error ? "未连接本地桥接，请先安装 BrowserBridge" : "本地桥接已断开"});
      });
      port.postMessage(message);
    } catch { finish({ok: false, error: "本地桥接不可用"}); }
  });
}

async function download(tabId, id, senderFrame) {
  if (!(await setting())) return {ok: false, error: "视频识别已关闭"};
  const item = (await getItems(tabId)).find(i => i.id === id);
  if (!item || (senderFrame !== undefined && item.frameId !== senderFrame)) return {ok: false, error: "视频资源已过期，请重新播放"};
  if (item.protected) return {ok: false, error: "受保护视频不支持下载"};
  if (!["mp4", "webm", "youtube", "x", "bilibili", "douyin", "hls", "dash"].includes(item.kind)) return {ok: false, error: "未找到完整视频地址，请选择 HLS/DASH 播放列表"};
  if (item.kind === "bilibili") {
    const frame = await chrome.webNavigation.getFrame({tabId, frameId: item.frameId}).catch(() => null);
    if (bilibiliUrl(frame?.url) !== item.url) return {ok: false, error: "视频分 P 已切换，请刷新资源列表"};
  }
  if (item.kind === "douyin") {
    const frame = await chrome.webNavigation.getFrame({tabId, frameId: item.frameId}).catch(() => null);
    const current = douyinUrl(frame?.url);
    if (!isDouyinPage(frame?.url) || (current && current !== item.url)) return {ok: false, error: "抖音视频已切换，请刷新资源列表"};
  }
  const sendKey = `${tabId}:${id}`;
  if (sending.has(sendKey)) return sending.get(sendKey);
  const request = (async () => {
    if (["youtube", "x", "bilibili"].includes(item.kind)) {
      const connection = await native({action: "ping"});
      if (!connection.capabilities?.includes(item.kind)) return {ok: false, error: "请更新并重启桌面下载器，视频模块尚未就绪"};
      const {videoHeight} = await chrome.storage.local.get({videoHeight: 720});
      const height = [480, 720, 1080].includes(videoHeight) ? videoHeight : 720;
      return native({action: "download", request_id: `${item.id}-${height}`,
        url: item.kind === "bilibili" ? bilibiliUrl(item.url) : item.kind === "x" ? xUrl(item.url) : youtubeUrl(item.url),
        kind: item.kind, filename: item.name, height});
    }
    const headers = {"User-Agent": navigator.userAgent};
    const segmented = ["hls", "dash"].includes(item.kind);
    const scopedContext = segmented || item.kind === "douyin";
    if (scopedContext) {
      const connection = await native({action: "ping"});
      if (!connection.capabilities?.includes(item.kind)) return {ok: false, error: item.kind === "douyin"
        ? "请更新并重启桌面下载器，抖音模块尚未就绪" : "请更新并重启桌面下载器，流媒体模块尚未就绪"};
    }
    let video;
    if (item.kind === "douyin") {
      const context = await chrome.tabs.sendMessage(tabId, {action: "douyinContext", videoUrl: item.url}, {frameId: item.frameId})
        .catch(() => ({ok: false, error: "请刷新抖音视频详情页后重试"}));
      if (!context.ok) return context;
      const frame = await chrome.webNavigation.getFrame({tabId, frameId: item.frameId}).catch(() => null);
      const current = douyinUrl(frame?.url);
      if (!isDouyinPage(frame?.url) || (current && current !== item.url)) return {ok: false, error: "抖音视频已切换，请刷新资源列表"};
      const latest = (await getItems(tabId)).find(candidate => candidate.id === item.id && candidate.url === item.url);
      if (!latest || !(await setting())) return {ok: false, error: "视频资源已过期或识别已关闭，请刷新页面"};
      if (latest.protected) return {ok: false, error: "受保护视频不支持下载"};
      video = context.video;
    }
    try {
      const pageUrl = new URL(item.pageUrl);
      if (["http:", "https:"].includes(pageUrl.protocol)) {
        pageUrl.hash = "";
        // Never broaden the browser's usual cross-origin referrer disclosure.
        headers.Referer = pageUrl.origin === new URL(item.url).origin ? pageUrl.href : pageUrl.origin + "/";
      }
    } catch { /* DOM-only candidates may not have an HTTP referrer. */ }
    const stores = await chrome.cookies.getAllCookieStores();
    const storeId = stores.find(store => store.tabIds.includes(tabId))?.id;
    if (!storeId) return {ok: false, error: "无法确认当前标签页的授权环境，请刷新视频页后重试"};
    const cookies = await chrome.cookies.getAll({url: item.url, storeId});
    const ordinary = cookies.filter(cookie => !cookie.partitionKey).sort((a, b) => b.path.length - a.path.length);
    if (scopedContext) {
      const {videoHeight} = await chrome.storage.local.get({videoHeight: 720});
      const height = [480, 720, 1080].includes(videoHeight) ? videoHeight : 720;
      return native({action: "download", request_id: `${item.id}-${height}`, url: item.url,
        filename: item.name, kind: item.kind, height, headers, ...(video ? {video} : {}),
        cookies: ordinary.filter(cookie => cookie.name).map(({name, value, domain, path, secure, hostOnly}) => ({name, value, domain, path, secure, hostOnly}))});
    }
    if (ordinary.length) headers.Cookie = ordinary.map(cookie => `${cookie.name}=${cookie.value}`).join("; ");
    return native({action: "download", request_id: item.id, url: item.url,
      filename: item.name, kind: item.kind, headers});
  })().catch(() => ({ok: false, error: "读取当前网站授权信息失败"}));
  sending.set(sendKey, request);
  try { return await request; } finally { sending.delete(sendKey); }
}

async function handle(message, sender) {
  if (sender.id !== chrome.runtime.id || !message || typeof message !== "object") return {ok: false};
  const popup = sender.url === chrome.runtime.getURL("popup.html");
  const tabId = popup ? message.tabId : sender.tab?.id;
  if (message.action === "setting" && popup) {
    if ([480, 720, 1080].includes(message.videoHeight)) await chrome.storage.local.set({videoHeight: message.videoHeight});
    if (typeof message.enabled === "boolean") {
      await chrome.storage.local.set({enabled: message.enabled});
      if (!message.enabled) {
        await chrome.storage.session.clear();
        for (const tab of await chrome.tabs.query({})) await chrome.action.setBadgeText({tabId: tab.id, text: ""}).catch(() => {});
      }
    }
    return {ok: true, enabled: await setting()};
  }
  if (message.action === "ping" && popup) return native({action: "ping"});
  if (!Number.isInteger(tabId)) return {ok: false};
  if (message.action === "observe" && sender.tab) {
    if (typeof message.url !== "string" || message.url.length > 16384) return {ok: false};
    await record(tabId, {url: message.url, mime: String(message.mime || "").slice(0, 100),
      frameId: sender.frameId, pageUrl: sender.url, documentId: sender.documentId, size: 0});
    return {ok: true};
  }
  if (message.action === "protected" && sender.tab) {
    await serial(tabId, async () => {
      const key = `protected:${tabId}`;
      const frames = (await chrome.storage.session.get(key))[key] || [];
      await chrome.storage.session.set({[key]: [...new Set([...frames, sender.frameId])]});
      await saveItems(tabId, (await getItems(tabId)).map(i => i.frameId === sender.frameId ? {...i, protected: true} : i));
    });
    return {ok: true};
  }
  if (message.action === "blob" && sender.tab) {
    await serial(tabId, async () => {
      if (!(await setting())) return;
      const frame = await chrome.webNavigation.getFrame({tabId, frameId: sender.frameId}).catch(() => null);
      if (!frame || frame.documentId !== sender.documentId) return;
      const key = `blob:${tabId}`;
      const frames = (await chrome.storage.session.get(key))[key] || [];
      await chrome.storage.session.set({[key]: [...new Set([...frames, sender.frameId])]});
    });
    return {ok: true};
  }
  if (message.action === "list") {
    // MessageSender.url can retain the document's original URL after pushState.
    const page = popup ? await chrome.tabs.get(tabId)
      : {...await chrome.webNavigation.getFrame({tabId, frameId: sender.frameId}), title: sender.tab?.title};
    const onX = isXPage(page.url);
    const onDouyin = isDouyinPage(page.url);
    const biliUrl = bilibiliUrl(page.url);
    const pageUrl = onX ? xUrl(popup ? page.url : message.postUrl)
      : onDouyin ? douyinUrl(popup ? page.url : message.postUrl) : biliUrl || youtubeUrl(page.url);
    const pageKind = onX ? "x" : onDouyin ? "douyin" : biliUrl ? "bilibili" : "youtube";
    if (pageUrl && await setting()) {
      await serial(tabId, async () => {
        const frameId = popup ? 0 : sender.frameId;
        const frames = (await chrome.storage.session.get(`protected:${tabId}`))[`protected:${tabId}`] || [];
        const current = (await getItems(tabId)).filter(i => !["youtube", "bilibili"].includes(i.kind) || i.frameId !== frameId || i.url === pageUrl);
        await saveItems(tabId, upsert(current, {id: crypto.randomUUID(), url: pageUrl,
          name: onX ? `X-${new URL(pageUrl).pathname.split("/status/")[1].replaceAll("/", "-")}`
            : onDouyin ? `抖音-${new URL(pageUrl).pathname.split("/")[2]}`
            : biliUrl ? `${(page.title || `Bilibili-${new URL(biliUrl).pathname.split("/")[2]}`).slice(0, 120)} - P${new URL(biliUrl).searchParams.get("p")}`
            : (page.title || `YouTube-${new URL(pageUrl).searchParams.get("v")}`).replace(/ - YouTube$/, "").slice(0, 140),
          kind: pageKind, frameId, pageUrl: page.url, size: 0, protected: frames.includes(frameId)}));
      });
    }
    const items = (await getItems(tabId)).filter(i => Date.now() - i.seen < 1800000);
    const states = await chrome.storage.session.get([`blob:${tabId}`, `protected:${tabId}`]);
    const applies = frames => frames?.some(frame => popup || frame === sender.frameId);
    const emptyMessage = applies(states[`protected:${tabId}`]) ? "检测到受保护视频，本版不支持下载"
      : onX ? "未确认视频所属帖子，请打开该视频的帖子详情后重试"
      : onDouyin ? "未确认当前视频，请打开该视频的详情页后重试"
      : applies(states[`blob:${tabId}`]) ? "检测到浏览器内视频源（blob），尚未获取可下载直链" : "当前页面尚未发现视频资源";
    const {videoHeight} = await chrome.storage.local.get({videoHeight: 720});
    return {ok: true, enabled: await setting(), emptyMessage, videoHeight,
      items: items.filter(i => (popup || i.frameId === sender.frameId) &&
        (pageUrl ? i.kind === pageKind && i.url === pageUrl : onX || onDouyin ? popup && i.kind === pageKind : true)).map(publicItem)};
  }
  if (message.action === "download") return download(tabId, message.id, popup ? undefined : sender.frameId);
  return {ok: false};
}

chrome.runtime.onMessage.addListener((message, sender, reply) => {
  handle(message, sender).then(reply).catch(() => reply({ok: false, error: "视频助手暂时不可用，请刷新页面"}));
  return true;
});
