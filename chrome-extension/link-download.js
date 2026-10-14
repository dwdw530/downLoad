// Manual "使用 daw 下载器下载此链接" entry for ordinary HTTP/HTTPS links.
// Only an explicit user click on the browser context menu reaches the native host;
// normal navigation, "save as" and the browser's own downloads stay untouched.

import {youtubeUrl, xUrl, bilibiliUrl, douyinUrl, isDouyinPage} from "./media.js";

export const MENU_ID = "daw-download-link-v1";
export const FILE_FEATURE = "file_download_v1";
export const FILE_STATUS_KEY = "fileDownloadStatus";

const MENU_TITLE = "使用 daw 下载器下载此链接";
const MAX_URL_LENGTH = 16384;
const MAX_FILENAME_LENGTH = 160;
const STATUS_LIMIT = 512;
// Rendered 128x128 PNG written as a text constant: notification SVGs are not portable enough.
const ICON_URL = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAIAAAACACAYAAADDPmHLAAABqUlEQVR42u3dgW3CUABDQe/AeEzC9CBWiEBK8u5LXsA+tZCk6fbj83g93/K/7CzHGFEYyo5CUG4UgjLDEBQYRqC4MAKFhREoKo5ASWEACoojUE4YgGLiCJQSBqCQOAJlAKAQAAQAAUBiABQRR6AEABQBgAAgAAgAAoAAIAAIALfKkQMAAAAAAAAAAFxruLMdAMII/AQII/ArIIzAZ4AwAh8Cwwh8Cwgj8DUwjMB1gDACF4LCCFwJDCNwKTiMwL2AMAI3g8II3A0MI3A7OIzA8wBhBB4ICSPwRJBHwgAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAH8dDIA3hAAQeEGUt4SFR/emUKNfEsOM3sYwo7cxzOhtDDN6G8OM3sYwo7cxzOhtDDN6G4P/HBoPAAAoAQABQAAQAAQAAUAAEAAEAAFAABAABAABQAAQAAQAAUBuAuB7FBEeHwAAAABAGQAIABIFAEF8fAAAgKA+PgAAQFAfHwAAIKiPD4HxITA+BMYHwfAgGB4Ew4Nx56E/t72391vhk4gAAAAASUVORK5CYII=";

let registeredDownloader;

const MESSAGES = {
  sending: "正在发送到 daw 下载器…",
  invalid_url: "仅支持 HTTP/HTTPS 的网页链接",
  video_only: "请在视频资源列表中使用视频入口下载此地址",
  navigated: "链接所在页面已切换，请重新右键",
  context: "无法确认当前标签页的授权环境，请刷新页面后重试",
  unsupported: "请更新并重启桌面下载器和 BrowserBridge，再使用此菜单",
  connection: "无法连接本地桥接，请检查桌面下载器和 BrowserBridge 是否已安装",
  failed: "无法下载此文件：链接已失效、需登录或文件大小未知",
  unknown: "结果尚未确认，请先查看桌面任务列表",
  error: "发送失败，请稍后重试",
  menu_error: "链接下载菜单暂不可用，请重新加载扩展",
};

function videoOnlyLink(value) {
  if (youtubeUrl(value) || xUrl(value) || bilibiliUrl(value) || douyinUrl(value) || isDouyinPage(value)) return true;
  const parsed = new URL(value);
  const host = parsed.hostname.toLowerCase().replace(/\.$/, "");
  let path = parsed.pathname;
  try { path = decodeURIComponent(path); } catch { /* Keep the original path on legacy encodings. */ }
  return /\.(m3u8|mpd)$/i.test(path) || host === "douyinvod.com" || host.endsWith(".douyinvod.com");
}

function documentUrl(value) {
  if (!validateLinkUrl(value)) return "";
  const parsed = new URL(value);
  parsed.hash = "";
  return parsed.href;
}

/** Accept only plain HTTP/HTTPS links; parsing is used for validation, never to rewrite the URL. */
export function validateLinkUrl(value) {
  if (typeof value !== "string" || !value || value.length > MAX_URL_LENGTH) return false;
  if (/[\u0000-\u001f\u007f]/.test(value)) return false;
  let parsed;
  try { parsed = new URL(value); } catch { return false; }
  if (!["http:", "https:"].includes(parsed.protocol) || !parsed.hostname) return false;
  return !parsed.username && !parsed.password;
}

/** Mirror the browser's usual referrer trimming; never disclose a page to a downgraded target. */
export function refererFor(frameUrl, linkUrl) {
  let page;
  let target;
  try {
    page = new URL(frameUrl);
    target = new URL(linkUrl);
  } catch { return ""; }
  if (!["http:", "https:"].includes(page.protocol) || !["http:", "https:"].includes(target.protocol)) return "";
  if (page.protocol === "https:" && target.protocol !== "https:") return "";
  page.hash = "";
  return page.origin === target.origin ? page.href : page.origin + "/";
}

/** Forward only unpartitioned, named cookies in path-priority order. */
export function cookieHeader(cookies) {
  return (cookies || [])
    .filter(cookie => cookie && !cookie.partitionKey && cookie.name)
    .sort((a, b) => (b.path || "").length - (a.path || "").length)
    .map(cookie => `${cookie.name}=${cookie.value ?? ""}`)
    .join("; ");
}

function safeName(value) {
  if (typeof value !== "string") return "";
  return value.replace(/[\u0000-\u001f\u007f]/g, "").slice(0, MAX_FILENAME_LENGTH);
}

export function createLinkDownloader(native, api = globalThis.chrome, userAgent = globalThis.navigator?.userAgent || "") {
  const inFlight = new Map();
  const subscriptions = new Set();

  function notify(notificationId, message) {
    try {
      if (!api.notifications?.create) return;
      api.notifications.create(notificationId,
        {type: "basic", iconUrl: ICON_URL, title: "daw 下载器", message: String(message).slice(0, STATUS_LIMIT)},
        () => { void api.runtime.lastError; });
    } catch { /* Notification support is optional feedback, never a reason to fail the task. */ }
  }

  async function report(notificationId, requestId, outcome) {
    // Only sanitized fields: no URL, cookie, referer or request body is persisted.
    const entry = {
      ok: outcome.ok === true,
      code: outcome.code || (outcome.ok ? "ok" : "failed"),
      filename: outcome.ok ? safeName(outcome.filename) : "",
      // Native error text is not a persistence format: unexpected replies may contain signed URLs.
      message: outcome.ok ? "已加入下载队列" : MESSAGES[outcome.code] || MESSAGES.error,
      requestId,
      time: Date.now()
    };
    notify(notificationId, entry.ok ? `已加入下载队列${entry.filename ? "：" + entry.filename : ""}` : entry.message);
    try { await api.storage.local.set({[FILE_STATUS_KEY]: entry}); } catch { /* Feedback is best effort. */ }
    return entry;
  }

  async function submit(info, tab) {
    if (!info || info.menuItemId !== MENU_ID) return null;
    if (!tab || !Number.isInteger(tab.id)) return null;
    const url = info.linkUrl;
    const requestId = `file-${crypto.randomUUID()}`;
    const notificationId = `daw-link-${requestId}`;
    if (!validateLinkUrl(url)) {
      return report(notificationId, requestId, {ok: false, code: "invalid_url", error: "仅支持 HTTP/HTTPS 的网页链接"});
    }
    if (videoOnlyLink(url)) return report(notificationId, requestId, {ok: false, code: "video_only"});
    const tabId = tab.id;
    const frameId = Number.isInteger(info.frameId) ? info.frameId : 0;
    const lockKey = `${tabId}:${frameId}:${url}`;
    // One in-flight request per tab/frame/full link; quick double clicks share the result.
    if (inFlight.has(lockKey)) return inFlight.get(lockKey);
    let dispatched = false;
    const job = (async () => {
      await report(notificationId, requestId, {ok: false, code: "sending"});
      const frame = await api.webNavigation.getFrame({tabId, frameId}).catch(() => null);
      const frameDocument = frame?.documentId;
      const clickedPage = documentUrl(info.frameUrl || (frameId === 0 ? info.pageUrl : ""));
      if (!frame || !frameDocument || !clickedPage || documentUrl(frame.url) !== clickedPage) {
        return report(notificationId, requestId, {ok: false, code: "navigated"});
      }
      const headers = {"User-Agent": userAgent};
      const referer = refererFor(frame.url, url);
      if (referer) headers.Referer = referer;
      const connection = await native({action: "ping"});
      if (!Array.isArray(connection?.features) || !connection.features.includes(FILE_FEATURE)) {
        const knownComponent = connection?.installed === true || Array.isArray(connection?.capabilities);
        return report(notificationId, requestId, {ok: false, code: knownComponent ? "unsupported" : "connection"});
      }
      const stores = await api.cookies.getAllCookieStores();
      const storeId = stores.find(store => store.tabIds?.includes(tabId))?.id;
      if (!storeId) {
        return report(notificationId, requestId, {ok: false, code: "context", error: "无法确认当前标签页的授权环境，请刷新页面后重试"});
      }
      const cookies = await api.cookies.getAll({url, storeId});
      const cookie = cookieHeader(cookies);
      if (cookie) headers.Cookie = cookie;
      const current = await api.webNavigation.getFrame({tabId, frameId}).catch(() => null);
      if (!current || current.documentId !== frameDocument || documentUrl(current.url) !== clickedPage) {
        return report(notificationId, requestId, {ok: false, code: "navigated", error: "链接所在页面已切换，请重新右键"});
      }
      dispatched = true;
      const response = await native({action: "download_file", request_id: requestId, url, headers});
      if (response?.ok) return report(notificationId, requestId, {ok: true, filename: response.filename});
      const error = String(response?.error || "");
      if (error.includes("不支持的操作")) {
        return report(notificationId, requestId, {ok: false, code: "unsupported", error: "请更新并重启桌面下载器和 BrowserBridge，再使用此菜单"});
      }
      if (error.includes("超时") || error.includes("尚未确认")) {
        return report(notificationId, requestId, {ok: false, code: "unknown", error: "结果尚未确认，请先查看桌面任务列表"});
      }
      const rejection = error === "请在视频资源列表中使用视频入口下载此地址" ? "video_only"
        : error === "浏览器请求信息无效或不安全，已取消本次下载" ? "context"
        : error === "链接无效：仅支持不带用户信息的 HTTP/HTTPS 直链" ? "invalid_url"
        : error === "无法下载此文件：链接已失效、需登录或文件大小未知" || error === "下载器正在退出" ? "failed"
        : "unknown";
      return report(notificationId, requestId, {ok: false, code: rejection});
    })().catch(() => report(notificationId, requestId, {ok: false, code: dispatched ? "unknown" : "error"}));
    inFlight.set(lockKey, job);
    try { return await job; } finally { inFlight.delete(lockKey); }
  }

  function ensureMenu() {
    try {
      // A fixed ID keeps registration idempotent: a duplicate create reports a recoverable error only.
      api.contextMenus.create({
        id: MENU_ID,
        title: MENU_TITLE,
        contexts: ["link"],
        targetUrlPatterns: ["http://*/*", "https://*/*"],
        documentUrlPatterns: ["http://*/*", "https://*/*"]
      }, () => { void api.runtime.lastError; });
    } catch { /* The next worker start recreates the menu; other context menus are never cleared. */ }
  }

  const onClick = (info, tab) => { submit(info, tab).catch(() => {}); };
  function install() {
    for (const [event, handler] of [[api.contextMenus?.onClicked, onClick],
      [api.runtime?.onInstalled, ensureMenu], [api.runtime?.onStartup, ensureMenu]]) {
      if (!event || subscriptions.has(event)) continue;
      try {
        event.addListener(handler);
        subscriptions.add(event);
      } catch {
        void report("daw-link-menu", "menu", {ok: false, code: "menu_error"});
      }
    }
    ensureMenu();
  }

  return {submit, ensureMenu, install};
}

export function registerLinkDownloadMenu(native) {
  registeredDownloader ||= createLinkDownloader(native);
  registeredDownloader.install();
  return registeredDownloader;
}
