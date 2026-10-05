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
  async function observe(video) {
    const sources = [{src: video.currentSrc || video.src, type: ""}, ...Array.from(video.querySelectorAll("source"), source => ({src: source.src, type: source.type}))];
    for (const source of sources) if (/^https?:\/\//i.test(source.src)) {
      await send({action: "observe", url: source.src, mime: source.type});
    }
    if (sources.some(source => source.src.startsWith("blob:"))) await send({action: "blob"});
    if (video.mediaKeys) await send({action: "protected"});
  }
  async function open(video, state) {
    const requestedPage = location.href;
    state.panel.hidden = false;
    state.host.setAttribute("data-open", "");
    state.trigger.setAttribute("aria-expanded", "true");
    state.body.replaceChildren(text("div", "正在识别视频资源…", "notice"));
    await observe(video);
    const result = await send({action: "list"});
    if (!state.host.isConnected || location.href !== requestedPage) return;
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
      const reason = item.protected ? "受保护视频，不支持下载" : item.kind === "youtube" ? `最高 ${result.videoHeight || 720}p · MP4 音视频`
        : !["mp4", "webm"].includes(item.kind) ? item.unsupportedReason : item.sizeLabel;
      info.append(text("div", `${item.kindLabel} · ${reason}`, "meta"));
      const button = text("button", "下载", "download");
      button.type = "button";
      button.disabled = item.protected || !["mp4", "webm", "youtube"].includes(item.kind);
      button.addEventListener("click", async event => {
        if (!event.isTrusted) return;
        button.disabled = true;
        button.textContent = "发送中";
        state.status.className = "notice";
        state.status.textContent = "正在连接老王下载器…";
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
    trigger.title = "使用老王下载器下载视频";
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
