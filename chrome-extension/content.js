(() => {
  if (globalThis.__laowangVideoAssistant) return;
  globalThis.__laowangVideoAssistant = true;
  const overlays = new Map();
  let enabled = true;
  let contextAlive = true;
  let pageUrl = location.href;
  const send = async message => {
    try { return await chrome.runtime.sendMessage(message); }
    catch { contextAlive = false; return {ok: false, error: "扩展已更新，请刷新页面"}; }
  };
  if (["douyin.com", "www.douyin.com"].includes(location.hostname)) {
    const requests = new Map();
    const remember = entries => {
      for (const entry of entries) {
        try {
          const url = new URL(entry.name);
          const ids = url.searchParams.getAll("aweme_id");
          if (entry.name.length > 16384 || url.origin !== location.origin ||
              url.pathname !== "/aweme/v1/web/aweme/detail/" || ids.length !== 1 || !/^[1-9][0-9]{0,24}$/.test(ids[0])) continue;
          requests.delete(ids[0]);
          requests.set(ids[0], entry.name);
          if (requests.size > 20) requests.delete(requests.keys().next().value);
        } catch { /* Ignore unrelated resource entries. */ }
      }
    };
    remember(performance.getEntriesByType("resource"));
    const observer = new PerformanceObserver(list => remember(list.getEntries()));
    observer.observe({type: "resource", buffered: true});
    chrome.runtime.onMessage.addListener((message, sender, reply) => {
      if (sender.id !== chrome.runtime.id || message?.action !== "douyinContext") return;
      (async () => {
        const id = /^https:\/\/www\.douyin\.com\/video\/([1-9][0-9]{0,24})$/.exec(message.videoUrl || "")?.[1];
        if (!id) return {ok: false, error: "无效的抖音视频地址"};
        const page = location.href;
        remember(performance.getEntriesByType("resource"));
        const endpoint = requests.get(id);
        if (!endpoint) return {ok: false, error: "未获取当前视频信息，请打开该视频的详情页并刷新后重试"};
        // Replay only the same-origin detail request this document already made; do not patch page fetch/XHR.
        const response = await fetch(endpoint, {credentials: "same-origin", redirect: "error", signal: AbortSignal.timeout(10000)});
        if (!response.ok) throw new Error("Detail request failed");
        const text = await response.text();
        if (text.length > 2 * 1024 * 1024) throw new Error("Detail response is too large");
        const detail = JSON.parse(text).aweme_detail;
        if (location.href !== page || detail?.aweme_id !== id) return {ok: false, error: "抖音视频已切换，请刷新资源列表"};
        const duration = detail.video?.duration / 1000;
        const formats = [];
        const seen = new Set();
        for (const variant of (Array.isArray(detail.video?.bit_rate) ? detail.video.bit_rate : []).slice(0,40)) {
          if (variant.format !== "mp4") continue;
          const address = variant.play_addr;
          for (const value of (Array.isArray(address?.url_list) ? address.url_list : []).slice(0,4)) {
            try {
              const url = new URL(value);
              if (typeof value !== "string" || value.length > 16384 || url.protocol !== "https:" ||
                  url.username || url.password || (url.port && url.port !== "443") ||
                  !url.hostname.endsWith(".douyinvod.com") || seen.has(value) || formats.length >= 64) continue;
              seen.add(value);
              formats.push({url: value, width: address.width, height: address.height, vcodec: variant.is_h265 ? "h265" : "h264"});
            } catch { /* Keep only playable CDN candidates; the desktop verifies the result. */ }
          }
        }
        if (!formats.length || !Number.isFinite(duration) || duration <= 0) return {ok: false, error: "未找到完整音视频资源，请刷新视频详情页后重试"};
        return {ok: true, video: {id, duration, formats}};
      })().then(reply).catch(() => reply({ok: false, error: "抖音视频信息获取失败，请刷新视频详情页后重试"}));
      return true;
    });
  }
  const styles = `
    :host {all:initial;position:fixed!important;z-index:2147483646!important;pointer-events:none!important;font:13px "Microsoft YaHei",sans-serif!important;letter-spacing:0!important;}
    :host([hidden]) {display:none!important}
    :host([data-open]) {z-index:2147483647!important}
    * {box-sizing:border-box;letter-spacing:0} [hidden]{display:none!important}
    button {font:inherit;cursor:pointer;border:0} button:focus-visible {outline:3px solid #f4c95d;outline-offset:2px}
    .trigger {pointer-events:auto;width:132px;height:34px;background:#13795b;color:#fff;border:1px solid #ffffff80;border-radius:5px;display:flex;align-items:center;justify-content:center;gap:7px;box-shadow:0 2px 8px #0005}
    .trigger:hover {background:#096647} img {width:16px;height:16px}
    .panel {pointer-events:auto;position:absolute;right:0;top:40px;width:min(350px,calc(100vw - 20px));max-height:340px;overflow:auto;background:#fff;color:#20252b;border:1px solid #cbd1d7;border-radius:6px;box-shadow:0 6px 24px #0003}
    header {display:flex;align-items:center;justify-content:space-between;padding:10px 12px;border-bottom:1px solid #e4e7ea;font-weight:600}
    .close {width:28px;height:28px;background:transparent;color:#535d66;font-size:22px}
    .row {padding:12px;border-bottom:1px solid #edf0f2;display:grid;grid-template-columns:minmax(0,1fr) 68px;gap:10px;align-items:center}
    .name {overflow-wrap:anywhere;line-height:1.5}.meta {font-size:11px;color:#69737d;margin-top:4px}
    .download {background:#13795b;color:white;padding:7px 8px;border-radius:4px;min-height:32px}
    button:disabled {cursor:default;opacity:.55}.notice {padding:12px;line-height:1.6;overflow-wrap:anywhere;color:#59626a}
    .error {color:#a32e35}.success {color:#13795b}
  `;
  function text(tag, value, className) {
    const node = document.createElement(tag);
    node.textContent = value;
    if (className) node.className = className;
    return node;
  }
  function position(video, state) {
    const rect = video.getBoundingClientRect();
    const fullscreen = document.fullscreenElement;
    const visible = enabled && contextAlive && rect.width >= 150 && rect.height >= 85 &&
      rect.bottom > 40 && rect.top < innerHeight - 40 && rect.right > 20 && rect.left < innerWidth - 20 &&
      (!fullscreen || fullscreen !== video);
    state.host.hidden = !visible;
    if (!visible) return;
    const parent = fullscreen && fullscreen.contains(video) ? fullscreen : document.documentElement;
    if (state.host.parentNode !== parent) parent.append(state.host);
    const left = Math.max(10, Math.min(innerWidth - 142, rect.right - 142));
    const top = Math.max(10, Math.min(innerHeight - 44, rect.top + 10));
    state.host.style.setProperty("left", `${left}px`, "important");
    state.host.style.setProperty("top", `${top}px`, "important");
    const width = Math.min(350, innerWidth - 20);
    state.panel.style.right = left + 132 < width + 10 ? `${left + 132 - width - 10}px` : "0px";
    state.panel.style.maxHeight = `${Math.max(90, Math.min(340, innerHeight - top - 54))}px`;
  }
  function videoPostFor(video) {
    if (["douyin.com", "www.douyin.com"].includes(location.hostname)) {
      const valid = value => {
        try {
          const url = new URL(value, location.href);
          if (!["http:", "https:"].includes(url.protocol) || url.username || url.password ||
              (url.port && !["80", "443"].includes(url.port)) ||
              !["douyin.com", "www.douyin.com"].includes(url.hostname)) return null;
          const match = /^\/video\/([1-9][0-9]{0,24})\/?$/.exec(url.pathname);
          const ids = url.searchParams.getAll("modal_id");
          const id = match?.[1] || (url.pathname === "/" && ids.length === 1 ? ids[0] : "");
          return /^[1-9][0-9]{0,24}$/.test(id) ? `https://www.douyin.com/video/${id}` : null;
        } catch { return null; }
      };
      const current = valid(location.href);
      if (current) return current;
      // A feed may reuse a video element. Only accept an unambiguous link near this player.
      for (let parent = video.parentElement; parent; parent = parent.parentElement) {
        if (parent.querySelectorAll("video").length > 1) break;
        const urls = [...new Set([...parent.querySelectorAll('a[href*="/video/"]')]
          .map(link => valid(link.href)).filter(Boolean))];
        if (urls.length === 1) return urls[0];
        if (urls.length > 1) break;
      }
      return null;
    }
    if (!["x.com", "www.x.com", "twitter.com", "www.twitter.com", "mobile.twitter.com"].includes(location.hostname)) return null;
    const valid = value => {
      try {
        const url = new URL(value, location.href);
        return url.origin === location.origin && /^\/[A-Za-z0-9_]{1,15}\/status\/[0-9]{1,25}(?:\/video\/[1-4])?\/?$/.test(url.pathname)
          ? url.origin + url.pathname : null;
      } catch { return null; }
    };
    const linked = video.closest('a[href*="/status/"]');
    if (linked && valid(linked.href)) return valid(linked.href);
    const article = video.closest('article, [data-testid="tweet"]');
    if (!article) return valid(location.href);
    // Walk out from this player, not across the feed. Ambiguous quoted posts fail closed.
    for (let parent = video.parentElement; parent; parent = parent.parentElement) {
      const links = [...parent.querySelectorAll('a[href*="/status/"]')];
      const videoLinks = links.filter(link => /\/video\/[1-4]\/?$/.test(new URL(link.href).pathname));
      const candidates = videoLinks.length ? videoLinks : links.filter(link => link.querySelector('time'));
      const urls = [...new Set(candidates.map(link => valid(link.href)).filter(Boolean))];
      if (urls.length === 1) return urls[0];
      if (urls.length > 1 || parent === article) return null;
    }
    return null;
  }
  async function observe(video) {
    const postUrl = videoPostFor(video);
    if (postUrl) await send({action: "list", postUrl});
    const sources = [{src: video.currentSrc || video.src, type: ""}, ...Array.from(video.querySelectorAll("source"), source => ({src: source.src, type: source.type}))];
    for (const source of sources) if (/^https?:\/\//i.test(source.src)) {
      await send({action: "observe", url: source.src, mime: source.type});
    }
    if (sources.some(source => source.src.startsWith("blob:"))) await send({action: "blob"});
    if (video.mediaKeys) await send({action: "protected"});
  }
  async function open(video, state) {
    const requestedPage = location.href;
    const postUrl = videoPostFor(video);
    state.postUrl = postUrl;
    state.panel.hidden = false;
    state.host.setAttribute("data-open", "");
    state.trigger.setAttribute("aria-expanded", "true");
    state.status.textContent = "";
    state.body.replaceChildren(text("div", "正在识别视频资源…", "notice"));
    await observe(video);
    const result = await send({action: "list", postUrl});
    if (!state.host.isConnected || location.href !== requestedPage || videoPostFor(video) !== postUrl) return;
    enabled = result.enabled !== false;
    if (!result.ok) { state.body.replaceChildren(text("div", result.error || "视频助手不可用", "notice error")); return; }
    let items = result.items || [];
    const matching = items.filter(item => item.url === (video.currentSrc || video.src));
    if (matching.length) items = matching;
    state.body.replaceChildren();
    if (!items.length) state.body.append(text("div", result.emptyMessage, "notice"));
    for (const item of items) {
      const row = text("div", "", "row");
      const info = text("div", "");
      info.append(text("div", item.name, "name"));
      const reason = item.protected ? "受保护视频，不支持下载" : ["youtube", "x", "bilibili", "douyin", "hls", "dash"].includes(item.kind) ? `最高 ${result.videoHeight || 720}p · MP4 ${["x", "hls", "dash"].includes(item.kind) ? "视频" : "音视频"}`
        : !["mp4", "webm"].includes(item.kind) ? item.unsupportedReason : item.sizeLabel;
      info.append(text("div", `${item.kindLabel} · ${reason}`, "meta"));
      const button = text("button", "下载", "download");
      button.type = "button";
      button.disabled = item.protected || !["mp4", "webm", "youtube", "x", "bilibili", "douyin", "hls", "dash"].includes(item.kind);
      button.addEventListener("click", async event => {
        if (!event.isTrusted) return;
        if (location.href !== requestedPage || videoPostFor(video) !== postUrl) { await open(video, state); return; }
        button.disabled = true;
        button.textContent = "发送中";
        state.status.className = "notice";
        state.status.textContent = "正在连接daw下载器…";
        const response = await send({action: "download", id: item.id});
        state.status.className = response.ok ? "notice success" : "notice error";
        state.status.textContent = response.ok ? `已加入下载：${response.filename}` : response.error || "发送失败";
        button.textContent = response.ok ? "已发送" : "重试";
        button.disabled = !!response.ok;
      });
      row.append(info, button);
      state.body.append(row);
    }
    state.close.focus();
    position(video, state);
  }
  function attach(video) {
    if (overlays.has(video)) return;
    const host = document.createElement("laowang-video-assistant");
    const shadow = host.attachShadow({mode: "open"});
    const style = document.createElement("style");
    style.textContent = styles;
    const trigger = text("button", "", "trigger");
    trigger.type = "button";
    trigger.title = "使用daw下载器下载视频";
    trigger.setAttribute("aria-expanded", "false");
    const icon = document.createElement("img");
    icon.src = chrome.runtime.getURL("icons/download.svg");
    icon.alt = "";
    trigger.append(icon, text("span", "下载视频"));
    const panel = text("section", "", "panel");
    panel.setAttribute("role", "region");
    panel.setAttribute("aria-label", "视频资源");
    panel.hidden = true;
    const header = text("header", "视频资源");
    const close = text("button", "×", "close");
    close.type = "button";
    close.title = "关闭资源列表";
    close.setAttribute("aria-label", "关闭资源列表");
    header.append(close);
    const body = text("div", "");
    const status = text("div", "", "notice");
    status.setAttribute("role", "status");
    panel.append(header, body, status);
    shadow.append(style, trigger, panel);
    const state = {host, trigger, panel, body, status, close};
    overlays.set(video, state);
    const dismiss = () => { panel.hidden = true; host.removeAttribute("data-open"); trigger.setAttribute("aria-expanded", "false"); trigger.focus(); };
    close.addEventListener("click", dismiss);
    shadow.addEventListener("keydown", event => { if (event.key === "Escape") dismiss(); });
    trigger.addEventListener("click", event => {
      if (!event.isTrusted) return;
      event.stopPropagation();
      if (panel.hidden) open(video, state); else dismiss();
    });
    video.addEventListener("encrypted", () => send({action: "protected"}));
    video.addEventListener("loadedmetadata", () => observe(video));
    video.addEventListener("play", () => observe(video));
    position(video, state);
    observe(video);
  }
  function scan() {
    if (pageUrl !== location.href) {
      pageUrl = location.href;
      for (const state of overlays.values()) {
        state.panel.hidden = true;
        state.host.removeAttribute("data-open");
        state.trigger.setAttribute("aria-expanded", "false");
        state.body.replaceChildren();
        state.status.textContent = "";
      }
    }
    for (const [video, state] of overlays) {
      if (!video.isConnected || !contextAlive) { state.host.remove(); overlays.delete(video); }
      else position(video, state);
    }
    if (enabled && contextAlive) document.querySelectorAll("video").forEach(attach);
  }
  let scheduled = false;
  const schedule = () => {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(() => { scheduled = false; scan(); });
  };
  new MutationObserver(schedule).observe(document.documentElement, {childList: true, subtree: true, attributes: true, attributeFilter: ["src"]});
  addEventListener("scroll", schedule, true);
  addEventListener("resize", schedule);
  addEventListener("popstate", schedule);
  document.addEventListener("fullscreenchange", schedule);
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === "local" && changes.enabled) { enabled = changes.enabled.newValue !== false; scan(); }
  });
  send({action: "list"}).then(result => { enabled = result.enabled !== false; scan(); });
  setInterval(scan, 1500);
})();
